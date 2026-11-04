#include "protocol.h"

#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

static int fail(char *error, size_t size, const char *format, ...)
{
    if (error != NULL && size > 0) {
        va_list args;
        va_start(args, format);
        vsnprintf(error, size, format, args);
        va_end(args);
    }
    return -1;
}

/* Require a terminated, nonempty token that cannot alter message framing. */
static int valid_token(const char *text, size_t capacity)
{
    const char *end = memchr(text, '\0', capacity);

    if (end == NULL || end == text) {
        return 0;
    }

    for (const char *p = text; p < end; p++) {
        unsigned char ch = (unsigned char)*p;

        if (isspace(ch) || iscntrl(ch) || ch == '<' || ch == '>') {
            return 0;
        }
    }

    return 1;
}

/* Protocol filenames are basenames, not arbitrary filesystem paths. */
static int valid_filename(const char *text, size_t capacity)
{
    return valid_token(text, capacity)
        && strcmp(text, ".") != 0
        && strcmp(text, "..") != 0
        && strchr(text, '/') == NULL
        && strchr(text, '\\') == NULL;
}

static int valid_md5(const char *text, size_t capacity)
{
    if (!valid_token(text, capacity) ||
        strlen(text) != PROTOCOL_MD5_LENGTH) {
        return 0;
    }

    for (size_t i = 0; i < PROTOCOL_MD5_LENGTH; i++) {
        if (!isxdigit((unsigned char)text[i])) {
            return 0;
        }
    }

    return 1;
}

static int parse_number(const char *text, uint64_t *out)
{
    if (*text == '\0') {
        return -1;
    }

    for (const char *p = text; *p != '\0'; p++) {
        if (!isdigit((unsigned char)*p)) {
            return -1;
        }
    }

    errno = 0;
    char *end = NULL;
    uintmax_t value = strtoumax(text, &end, 10);

    if (errno != 0 || *end != '\0' || value > UINT64_MAX) {
        return -1;
    }

    *out = (uint64_t)value;
    return 0;
}

static int copy_token(char *out, size_t capacity, const char *text)
{
    size_t length = strlen(text);

    if (length >= capacity) {
        return -1;
    }

    memcpy(out, text, length + 1);
    return 0;
}

/* Shared validation keeps parsing and formatting consistent. */
static int validate(
    const TrackerRequest *request,
    char *error,
    size_t error_size)
{
    if (request == NULL) {
        return fail(error, error_size, "Request is missing");
    }

    switch (request->type) {
        case REQUEST_LIST:
            return 0;

        case REQUEST_CREATE:
        case REQUEST_UPDATE:
        case REQUEST_GET:
            break;

        default:
            return fail(error, error_size, "Unknown request type");
    }

    if (!valid_filename(request->filename, sizeof(request->filename))) {
        return fail(error, error_size, "Invalid filename");
    }

    if (request->type == REQUEST_GET) {
        size_t length = strlen(request->filename);

        if (length <= 6 ||
            strcmp(request->filename + length - 6, ".track") != 0) {
            return fail(error, error_size,
                        "GET requires a filename ending in .track");
        }

        return 0;
    }

    struct in_addr address;

    if (!valid_token(request->ip, sizeof(request->ip)) ||
        inet_pton(AF_INET, request->ip, &address) != 1) {
        return fail(error, error_size, "Invalid peer IPv4 address");
    }

    if (request->port == 0) {
        return fail(error, error_size, "Peer port cannot be zero");
    }

    if (request->type == REQUEST_CREATE) {
        if (!valid_token(request->description,
                         sizeof(request->description))) {
            return fail(error, error_size,
                        "Description must be one nonempty token");
        }

        if (!valid_md5(request->md5, sizeof(request->md5))) {
            return fail(error, error_size,
                        "MD5 must contain exactly 32 hexadecimal characters");
        }
    }

    if (request->type == REQUEST_UPDATE &&
        request->start_byte > request->end_byte) {
        return fail(error, error_size,
                    "Start byte cannot exceed end byte");
    }

    return 0;
}

int protocol_parse_request(
    const char *line,
    TrackerRequest *out,
    char *error,
    size_t error_size)
{
    if (line == NULL || out == NULL) {
        return fail(error, error_size, "Request input or output is missing");
    }

    size_t length = 0;
    while (length < PROTOCOL_LINE_MAX && line[length] != '\0') {
        length++;
    }

    if (length == PROTOCOL_LINE_MAX) {
        return fail(error, error_size, "Request line is too long");
    }

    char text[PROTOCOL_LINE_MAX];
    memcpy(text, line, length + 1);

    /* Strip one optional LF or CRLF terminator. */
    if (length > 0 && text[length - 1] == '\n') {
        text[--length] = '\0';

        if (length > 0 && text[length - 1] == '\r') {
            text[--length] = '\0';
        }
    }

    for (size_t i = 0; i < length; i++) {
        if (text[i] == '\n' || text[i] == '\r') {
            return fail(error, error_size,
                        "Expected one complete request, not multiple lines");
        }
    }

    char *start = text;

    while (*start == ' ' || *start == '\t') {
        start++;
    }

    char *end = text + length;
    while (end > start && (end[-1] == ' ' || end[-1] == '\t')) {
        *--end = '\0';
    }

    if (*start == '<') {
        if (end - start < 2 || end[-1] != '>') {
            return fail(error, error_size, "Unclosed request framing");
        }

        start++;
        *--end = '\0';
    }

    if (strchr(start, '<') != NULL || strchr(start, '>') != NULL) {
        return fail(error, error_size, "Unexpected framing character");
    }

    char *tokens[8];
    size_t count = 0;
    char *cursor = start;

    while (*cursor != '\0') {
        while (*cursor == ' ' || *cursor == '\t') {
            cursor++;
        }

        if (*cursor == '\0') {
            break;
        }

        if (count == sizeof(tokens) / sizeof(tokens[0])) {
            return fail(error, error_size, "Too many request fields");
        }

        tokens[count++] = cursor;

        while (*cursor != '\0' && *cursor != ' ' && *cursor != '\t') {
            cursor++;
        }

        if (*cursor != '\0') {
            *cursor++ = '\0';
        }
    }

    if (count == 0) {
        return fail(error, error_size, "Request is empty");
    }

    TrackerRequest result = {0};
    uint64_t port = 0;

    if (strcmp(tokens[0], "REQ") == 0) {
        if (count != 2 || strcmp(tokens[1], "LIST") != 0) {
            return fail(error, error_size, "Expected REQ LIST");
        }

        result.type = REQUEST_LIST;
    } else if (strcmp(tokens[0], "GET") == 0) {
        if (count != 2) {
            return fail(error, error_size, "GET requires one filename");
        }

        result.type = REQUEST_GET;

        if (copy_token(result.filename, sizeof(result.filename),
                       tokens[1]) != 0) {
            return fail(error, error_size, "Filename is too long");
        }
    } else if (strcmp(tokens[0], "createtracker") == 0) {
        if (count != 7) {
            return fail(error, error_size,
                        "createtracker requires six fields");
        }

        result.type = REQUEST_CREATE;

        if (copy_token(result.filename, sizeof(result.filename),
                       tokens[1]) != 0 ||
            copy_token(result.description, sizeof(result.description),
                       tokens[3]) != 0 ||
            copy_token(result.md5, sizeof(result.md5),
                       tokens[4]) != 0 ||
            copy_token(result.ip, sizeof(result.ip),
                       tokens[5]) != 0) {
            return fail(error, error_size, "Request field is too long");
        }

        if (parse_number(tokens[2], &result.filesize) != 0 ||
            parse_number(tokens[6], &port) != 0 ||
            port == 0 || port > 65535) {
            return fail(error, error_size,
                        "Invalid filesize or port");
        }

        result.port = (unsigned short)port;
    } else if (strcmp(tokens[0], "updatetracker") == 0) {
        if (count != 6) {
            return fail(error, error_size,
                        "updatetracker requires five fields");
        }

        result.type = REQUEST_UPDATE;

        if (copy_token(result.filename, sizeof(result.filename),
                       tokens[1]) != 0 ||
            copy_token(result.ip, sizeof(result.ip),
                       tokens[4]) != 0) {
            return fail(error, error_size, "Request field is too long");
        }

        if (parse_number(tokens[2], &result.start_byte) != 0 ||
            parse_number(tokens[3], &result.end_byte) != 0 ||
            parse_number(tokens[5], &port) != 0 ||
            port == 0 || port > 65535) {
            return fail(error, error_size,
                        "Invalid byte offsets or port");
        }

        result.port = (unsigned short)port;
    } else {
        return fail(error, error_size, "Unknown request command");
    }

    if (validate(&result, error, error_size) != 0) {
        return -1;
    }

    *out = result;
    return 0;
}

int protocol_format_request(
    const TrackerRequest *request,
    char *buffer,
    size_t buffer_size,
    char *error,
    size_t error_size)
{
    if (buffer == NULL || buffer_size == 0) {
        return fail(error, error_size, "Output buffer is missing");
    }

    buffer[0] = '\0';

    if (validate(request, error, error_size) != 0) {
        return -1;
    }

    int written;

    switch (request->type) {
        case REQUEST_LIST:
            written = snprintf(buffer, buffer_size, "<REQ LIST>\n");
            break;

        case REQUEST_GET:
            written = snprintf(buffer, buffer_size,
                               "<GET %s>\n", request->filename);
            break;

        case REQUEST_CREATE:
            written = snprintf(
                buffer, buffer_size,
                "<createtracker %s %" PRIu64 " %s %s %s %u>\n",
                request->filename,
                request->filesize,
                request->description,
                request->md5,
                request->ip,
                (unsigned int)request->port);
            break;

        case REQUEST_UPDATE:
            written = snprintf(
                buffer, buffer_size,
                "<updatetracker %s %" PRIu64 " %" PRIu64 " %s %u>\n",
                request->filename,
                request->start_byte,
                request->end_byte,
                request->ip,
                (unsigned int)request->port);
            break;

        default:
            return fail(error, error_size, "Unknown request type");
    }

    if (written < 0 || (size_t)written >= buffer_size ||
        written >= PROTOCOL_LINE_MAX) {
        buffer[0] = '\0';
        return fail(error, error_size,
                    "Output buffer is too small or message is too long");
    }

    return written;
}
