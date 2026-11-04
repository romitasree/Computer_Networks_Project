#include "protocol.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void check(int condition, const char *message)
{
    if (!condition) {
        fprintf(stderr, "FAIL: %s\n", message);
        exit(EXIT_FAILURE);
    }
}

int main(void)
{
    char error[256] = {0};
    char output[PROTOCOL_LINE_MAX];
    TrackerRequest request;

    const char *valid[] = {
        "<REQ LIST>\n",
        "<GET movie.avi.track>\n",
        "<createtracker movie.avi 4096 Sample_movie d41d8cd98f00b204e9800998ecf8427e 127.0.0.1 5001>\n",
        "<updatetracker movie.avi 0 1024 127.0.0.1 5002>\n"
    };

    size_t valid_count = sizeof(valid) / sizeof(valid[0]);

    for (size_t i = 0; i < valid_count; i++) {
        check(protocol_parse_request(
            valid[i], &request, error, sizeof(error)) == 0, error);

        int length = protocol_format_request(
            &request, output, sizeof(output), error, sizeof(error));

        check(length >= 0, error);
        check((size_t)length == strlen(valid[i]), "formatted byte count");
        check(strcmp(output, valid[i]) == 0, "exact wire format");

        printf("PASS: request %zu round trip\n", i + 1);
    }

    /* Check parsed values, independently of the formatter. */
    check(protocol_parse_request(
        valid[2], &request, error, sizeof(error)) == 0, error);

    check(request.type == REQUEST_CREATE, "CREATE request type");
    check(strcmp(request.filename, "movie.avi") == 0, "CREATE filename");
    check(request.filesize == 4096, "CREATE filesize");
    check(strcmp(request.description, "Sample_movie") == 0,
          "CREATE description");
    check(strcmp(request.md5, "d41d8cd98f00b204e9800998ecf8427e") == 0,
          "CREATE checksum");
    check(strcmp(request.ip, "127.0.0.1") == 0, "CREATE address");
    check(request.port == 5001, "CREATE port");
    puts("PASS: CREATE field values");

    check(protocol_parse_request(
        valid[3], &request, error, sizeof(error)) == 0, error);

    check(request.type == REQUEST_UPDATE, "UPDATE request type");
    check(request.start_byte == 0 && request.end_byte == 1024,
          "UPDATE byte offsets");
    check(request.port == 5002, "UPDATE port");
    puts("PASS: UPDATE field values");

    check(protocol_parse_request(
        "GET movie.avi.track", &request, error, sizeof(error)) == 0,
        "manual unframed GET");
    check(request.type == REQUEST_GET, "manual GET type");

    check(protocol_parse_request(
        "<REQ LIST>\r\n", &request, error, sizeof(error)) == 0,
        "CRLF request");
    check(request.type == REQUEST_LIST, "LIST request type");
    puts("PASS: manual command and CRLF");

    check(protocol_parse_request(
        "<updatetracker f 0 18446744073709551615 127.0.0.1 65535>\n",
        &request, error, sizeof(error)) == 0,
        "maximum unsigned 64-bit offset");

    check(request.end_byte == UINT64_MAX, "maximum offset value");
    check(request.port == 65535, "maximum port value");
    puts("PASS: numeric upper boundaries");

    const char *invalid[] = {
        "",
        "<REQ LIST",
        "<REQ LIST>>\n",
        "<REQ LIST extra>\n",
        "<GET>\n",
        "<GET movie.avi>\n",
        "<GET ../movie.track>\n",
        "<GET .track>\n",
        "<unknown command>\n",
        "<REQ LIST>\n<REQ LIST>\n",
        "<createtracker f 1 description badmd5 127.0.0.1 5001>\n",
        "<createtracker f -1 description d41d8cd98f00b204e9800998ecf8427e 127.0.0.1 5001>\n",
        "<updatetracker f 10 5 127.0.0.1 5001>\n",
        "<updatetracker f 0 10 999.0.0.1 5001>\n",
        "<updatetracker f 0 10 127.0.0.1 0>\n",
        "<updatetracker f 0 10 127.0.0.1 65536>\n",
        "<updatetracker f 0 18446744073709551616 127.0.0.1 5001>\n",
        "<updatetracker f 0 10 127.0.0.1 5001 extra>\n"
    };

    size_t invalid_count = sizeof(invalid) / sizeof(invalid[0]);

    for (size_t i = 0; i < invalid_count; i++) {
        memset(&request, 0, sizeof(request));
        request.type = REQUEST_GET;
        strcpy(request.filename, "unchanged.track");
        error[0] = '\0';

        check(protocol_parse_request(
            invalid[i], &request, error, sizeof(error)) == -1,
            "reject malformed request");

        check(error[0] != '\0', "explain malformed request");
        check(request.type == REQUEST_GET &&
              strcmp(request.filename, "unchanged.track") == 0,
              "leave output unchanged on failure");

        printf("PASS: malformed request %zu rejected\n", i + 1);
    }

    char oversized[PROTOCOL_LINE_MAX + 1];
    memset(oversized, 'a', sizeof(oversized) - 1);
    oversized[sizeof(oversized) - 1] = '\0';

    check(protocol_parse_request(
        oversized, &request, error, sizeof(error)) == -1,
        "reject oversized request");
    puts("PASS: oversized request rejected");

    memset(&request, 0, sizeof(request));
    request.type = REQUEST_LIST;

    char exact[12];
    check(protocol_format_request(
        &request, exact, sizeof(exact), error, sizeof(error)) == 11,
        "exact-size output buffer");
    check(strcmp(exact, "<REQ LIST>\n") == 0, "exact-size buffer contents");

    char small[11];
    check(protocol_format_request(
        &request, small, sizeof(small), error, sizeof(error)) == -1,
        "reject insufficient output buffer");
    check(small[0] == '\0', "clear truncated output");
    puts("PASS: output buffer boundaries");

    request.type = REQUEST_CREATE;
    strcpy(request.filename, "movie.avi");
    strcpy(request.description, "description\ninjection");
    strcpy(request.md5, "d41d8cd98f00b204e9800998ecf8427e");
    strcpy(request.ip, "127.0.0.1");
    request.port = 5001;

    check(protocol_format_request(
        &request, output, sizeof(output), error, sizeof(error)) == -1,
        "reject newline injection");

    memset(request.description, 'x', sizeof(request.description));
    check(protocol_format_request(
        &request, output, sizeof(output), error, sizeof(error)) == -1,
        "reject unterminated description");
    puts("PASS: unsafe formatter fields rejected");

    check(protocol_parse_request(
        NULL, &request, error, sizeof(error)) == -1,
        "reject missing input");
    check(protocol_parse_request(
        "<REQ LIST>\n", NULL, error, sizeof(error)) == -1,
        "reject missing output");
    check(protocol_format_request(
        NULL, output, sizeof(output), error, sizeof(error)) == -1,
        "reject missing request");
    puts("PASS: missing arguments rejected");

    puts("All protocol request tests passed.");
    return EXIT_SUCCESS;
}
