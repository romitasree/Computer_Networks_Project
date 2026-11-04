#include "config.h"

#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
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

/* Remove surrounding whitespace, including Windows CRLF endings. */
static void trim(char *text)
{
    char *start = text;
    while (isspace((unsigned char)*start)) {
        start++;
    }

    size_t length = strlen(start);
    while (length > 0 && isspace((unsigned char)start[length - 1])) {
        length--;
    }

    memmove(text, start, length);
    text[length] = '\0';
}

/* Read the required lines and reject extra nonempty settings. */
static int read_fields(
    const char *path,
    char fields[][CONFIG_PATH_MAX],
    size_t count,
    char *error,
    size_t error_size)
{
    if (path == NULL) {
        return fail(error, error_size, "Configuration path is missing");
    }

    FILE *file = fopen(path, "r");
    if (file == NULL) {
        return fail(error, error_size, "Cannot open %s: %s",
                    path, strerror(errno));
    }

    for (size_t i = 0; i < count; i++) {
        if (fgets(fields[i], CONFIG_PATH_MAX, file) == NULL) {
            fclose(file);
            return fail(error, error_size,
                        "%s: cannot read required line %zu", path, i + 1);
        }

        size_t length = strlen(fields[i]);

        if (length == CONFIG_PATH_MAX - 1 &&
            fields[i][length - 1] != '\n') {
            fclose(file);
            return fail(error, error_size,
                        "%s: line %zu is too long", path, i + 1);
        }

        trim(fields[i]);

        if (fields[i][0] == '\0') {
            fclose(file);
            return fail(error, error_size,
                        "%s: line %zu cannot be empty", path, i + 1);
        }
    }

    int ch;
    while ((ch = fgetc(file)) != EOF) {
        if (!isspace((unsigned char)ch)) {
            fclose(file);
            return fail(error, error_size,
                        "%s: unexpected extra settings", path);
        }
    }

    if (ferror(file)) {
        fclose(file);
        return fail(error, error_size, "%s: read error", path);
    }

    if (fclose(file) != 0) {
        return fail(error, error_size, "%s: close error", path);
    }

    return 0;
}

static int positive_number(
    const char *text,
    unsigned long maximum,
    unsigned long *out)
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
    unsigned long value = strtoul(text, &end, 10);

    if (errno != 0 || *end != '\0' ||
        value == 0 || value > maximum) {
        return -1;
    }

    *out = value;
    return 0;
}

int config_load_client(
    const char *path, ClientConfig *out,
    char *error, size_t error_size)
{
    char fields[3][CONFIG_PATH_MAX];
    unsigned long port;
    unsigned long interval;
    struct in_addr address;

    if (out == NULL) {
        return fail(error, error_size, "Client output pointer is missing");
    }

    if (read_fields(path, fields, 3, error, error_size) != 0) {
        return -1;
    }

    if (positive_number(fields[0], 65535, &port) != 0) {
        return fail(error, error_size,
                    "%s: tracker port must be 1 through 65535", path);
    }

    if (inet_pton(AF_INET, fields[1], &address) != 1) {
        return fail(error, error_size,
                    "%s: tracker address must be a valid IPv4 address", path);
    }

    if (positive_number(fields[2], UINT_MAX, &interval) != 0) {
        return fail(error, error_size,
                    "%s: refresh interval must be a positive integer", path);
    }

    ClientConfig result = {0};
    result.tracker_port = (unsigned short)port;
    snprintf(result.tracker_ip, sizeof(result.tracker_ip), "%s", fields[1]);
    result.refresh_seconds = (unsigned int)interval;

    *out = result;
    return 0;
}

int config_load_peer_server(
    const char *path, PeerServerConfig *out,
    char *error, size_t error_size)
{
    char fields[2][CONFIG_PATH_MAX];
    unsigned long port;

    if (out == NULL) {
        return fail(error, error_size, "Peer output pointer is missing");
    }

    if (read_fields(path, fields, 2, error, error_size) != 0) {
        return -1;
    }

    if (positive_number(fields[0], 65535, &port) != 0) {
        return fail(error, error_size,
                    "%s: peer port must be 1 through 65535", path);
    }

    PeerServerConfig result = {0};
    result.listen_port = (unsigned short)port;
    snprintf(result.shared_directory,
             sizeof(result.shared_directory), "%s", fields[1]);

    *out = result;
    return 0;
}

int config_load_tracker(
    const char *path, TrackerConfig *out,
    char *error, size_t error_size)
{
    char fields[2][CONFIG_PATH_MAX];
    unsigned long port;

    if (out == NULL) {
        return fail(error, error_size, "Tracker output pointer is missing");
    }

    if (read_fields(path, fields, 2, error, error_size) != 0) {
        return -1;
    }

    if (positive_number(fields[0], 65535, &port) != 0) {
        return fail(error, error_size,
                    "%s: tracker port must be 1 through 65535", path);
    }

    TrackerConfig result = {0};
    result.listen_port = (unsigned short)port;
    snprintf(result.tracker_directory,
             sizeof(result.tracker_directory), "%s", fields[1]);

    *out = result;
    return 0;
}
