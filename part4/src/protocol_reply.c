#include "protocol_reply.h"

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

static int number(const char *text, uint64_t *out)
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

static const char *status_text(ReplyStatus status)
{
    switch (status) {
        case STATUS_SUCC: return "succ";
        case STATUS_FAIL: return "fail";
        case STATUS_FERR: return "ferr";
        default: return NULL;
    }
}

static int parse_status(const char *text, ReplyStatus *out)
{
    if (strcmp(text, "succ") == 0) {
        *out = STATUS_SUCC;
    } else if (strcmp(text, "fail") == 0) {
        *out = STATUS_FAIL;
    } else if (strcmp(text, "ferr") == 0) {
        *out = STATUS_FERR;
    } else {
        return -1;
    }

    return 0;
}

static int validate(
    const TrackerReply *reply, char *error, size_t error_size)
{
    if (reply == NULL) {
        return fail(error, error_size, "Reply is missing");
    }

    switch (reply->type) {
        case REPLY_CREATE:
            if (status_text(reply->status) == NULL) {
                return fail(error, error_size, "Invalid CREATE status");
            }
            return 0;

        case REPLY_UPDATE:
            if (status_text(reply->status) == NULL ||
                !valid_filename(reply->filename, sizeof(reply->filename))) {
                return fail(error, error_size,
                            "Invalid UPDATE filename or status");
            }
            return 0;

        case REPLY_LIST_ITEM:
            if (reply->number == 0 ||
                !valid_filename(reply->filename, sizeof(reply->filename)) ||
                !valid_md5(reply->md5, sizeof(reply->md5))) {
                return fail(error, error_size, "Invalid LIST item");
            }
            return 0;

        case REPLY_GET_END:
            if (!valid_md5(reply->md5, sizeof(reply->md5))) {
                return fail(error, error_size, "Invalid GET checksum");
            }
            return 0;

        case REPLY_LIST_BEGIN:
        case REPLY_LIST_END:
        case REPLY_GET_BEGIN:
            return 0;

        default:
            return fail(error, error_size, "Unknown reply type");
    }
}

int protocol_format_reply(
    const TrackerReply *reply,
    char *buffer,
    size_t buffer_size,
    char *error,
    size_t error_size)
{
    if (buffer == NULL || buffer_size == 0) {
        return fail(error, error_size, "Output buffer is missing");
    }

    buffer[0] = '\0';

    if (validate(reply, error, error_size) != 0) {
        return -1;
    }

    int written;

    switch (reply->type) {
        case REPLY_CREATE:
            written = snprintf(buffer, buffer_size,
                "<createtracker %s>\n", status_text(reply->status));
            break;

        case REPLY_UPDATE:
            written = snprintf(buffer, buffer_size,
                "<updatetracker %s %s>\n",
                reply->filename, status_text(reply->status));
            break;

        case REPLY_LIST_BEGIN:
            written = snprintf(buffer, buffer_size,
                "<REP LIST %" PRIu64 ">\n", reply->number);
            break;

        case REPLY_LIST_ITEM:
            written = snprintf(buffer, buffer_size,
                "<%" PRIu64 " %s %" PRIu64 " %s>\n",
                reply->number, reply->filename,
                reply->filesize, reply->md5);
            break;

        case REPLY_LIST_END:
            written = snprintf(buffer, buffer_size, "<REP LIST END>\n");
            break;

        case REPLY_GET_BEGIN:
            written = snprintf(buffer, buffer_size, "<REP GET BEGIN>\n");
            break;

        case REPLY_GET_END:
            written = snprintf(buffer, buffer_size,
                "<REP GET END %s>\n", reply->md5);
            break;

        default:
            return fail(error, error_size, "Unknown reply type");
    }

    if (written < 0 || (size_t)written >= buffer_size ||
        written >= PROTOCOL_LINE_MAX) {
        buffer[0] = '\0';
        return fail(error, error_size,
                    "Output buffer is too small or reply is too long");
    }

    return written;
}

int protocol_parse_reply(
    const char *line,
    TrackerReply *out,
    char *error,
    size_t error_size)
{
    if (line == NULL || out == NULL) {
        return fail(error, error_size, "Reply input or output is missing");
    }

    size_t length = 0;
    while (length < PROTOCOL_LINE_MAX && line[length] != '\0') {
        length++;
    }

    if (length == PROTOCOL_LINE_MAX) {
        return fail(error, error_size, "Reply is too long");
    }

    char text[PROTOCOL_LINE_MAX];
    memcpy(text, line, length + 1);

    if (length > 0 && text[length - 1] == '\n') {
        text[--length] = '\0';

        if (length > 0 && text[length - 1] == '\r') {
            text[--length] = '\0';
        }
    }

    if (length < 2 || text[0] != '<' || text[length - 1] != '>') {
        return fail(error, error_size, "Reply must use <...> framing");
    }

    text[length - 1] = '\0';
    char *cursor = text + 1;

    for (const char *p = cursor; *p != '\0'; p++) {
        unsigned char ch = (unsigned char)*p;

        if (*p == '<' || *p == '>' ||
            (iscntrl(ch) && *p != '\t')) {
            return fail(error, error_size, "Invalid reply framing or control character");
        }
    }

    char *tokens[5];
    size_t count = 0;

    while (*cursor != '\0') {
        while (*cursor == ' ' || *cursor == '\t') {
            cursor++;
        }

        if (*cursor == '\0') {
            break;
        }

        if (count == sizeof(tokens) / sizeof(tokens[0])) {
            return fail(error, error_size, "Too many reply fields");
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
        return fail(error, error_size, "Reply is empty");
    }

    TrackerReply result = {0};

    if (strcmp(tokens[0], "createtracker") == 0) {
        result.type = REPLY_CREATE;

        if (count != 2 || parse_status(tokens[1], &result.status) != 0) {
            return fail(error, error_size, "Invalid CREATE reply");
        }
    } else if (strcmp(tokens[0], "updatetracker") == 0) {
        result.type = REPLY_UPDATE;

        if (count != 3 ||
            copy_token(result.filename, sizeof(result.filename),
                       tokens[1]) != 0 ||
            parse_status(tokens[2], &result.status) != 0) {
            return fail(error, error_size, "Invalid UPDATE reply");
        }
    } else if (strcmp(tokens[0], "REP") == 0) {
        if (count == 3 && strcmp(tokens[1], "LIST") == 0) {
            if (strcmp(tokens[2], "END") == 0) {
                result.type = REPLY_LIST_END;
            } else {
                result.type = REPLY_LIST_BEGIN;

                if (number(tokens[2], &result.number) != 0) {
                    return fail(error, error_size, "Invalid LIST count");
                }
            }
        } else if (count == 3 &&
                   strcmp(tokens[1], "GET") == 0 &&
                   strcmp(tokens[2], "BEGIN") == 0) {
            result.type = REPLY_GET_BEGIN;
        } else if (count == 4 &&
                   strcmp(tokens[1], "GET") == 0 &&
                   strcmp(tokens[2], "END") == 0) {
            result.type = REPLY_GET_END;

            if (copy_token(result.md5, sizeof(result.md5), tokens[3]) != 0) {
                return fail(error, error_size, "GET checksum is too long");
            }
        } else {
            return fail(error, error_size, "Unknown REP reply");
        }
    } else {
        result.type = REPLY_LIST_ITEM;

        if (count != 4 ||
            number(tokens[0], &result.number) != 0 ||
            copy_token(result.filename, sizeof(result.filename),
                       tokens[1]) != 0 ||
            number(tokens[2], &result.filesize) != 0 ||
            copy_token(result.md5, sizeof(result.md5), tokens[3]) != 0) {
            return fail(error, error_size, "Invalid LIST item");
        }
    }

    if (validate(&result, error, error_size) != 0) {
        return -1;
    }

    *out = result;
    return 0;
}
