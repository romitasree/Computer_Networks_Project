#include "protocol_reply.h"

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
    TrackerReply reply;

    const char *valid[] = {
        "<createtracker succ>\n",
        "<createtracker fail>\n",
        "<createtracker ferr>\n",
        "<updatetracker movie.avi succ>\n",
        "<updatetracker movie.avi fail>\n",
        "<updatetracker movie.avi ferr>\n",
        "<REP LIST 0>\n",
        "<REP LIST 2>\n",
        "<1 movie.avi 4096 d41d8cd98f00b204e9800998ecf8427e>\n",
        "<REP LIST END>\n",
        "<REP GET BEGIN>\n",
        "<REP GET END d41d8cd98f00b204e9800998ecf8427e>\n"
    };

    const ReplyType types[] = {
        REPLY_CREATE, REPLY_CREATE, REPLY_CREATE,
        REPLY_UPDATE, REPLY_UPDATE, REPLY_UPDATE,
        REPLY_LIST_BEGIN, REPLY_LIST_BEGIN, REPLY_LIST_ITEM,
        REPLY_LIST_END, REPLY_GET_BEGIN, REPLY_GET_END
    };

    size_t count = sizeof(valid) / sizeof(valid[0]);

    for (size_t i = 0; i < count; i++) {
        check(protocol_parse_reply(
            valid[i], &reply, error, sizeof(error)) == 0, error);

        check(reply.type == types[i], "parsed reply type");

        int length = protocol_format_reply(
            &reply, output, sizeof(output), error, sizeof(error));

        check(length >= 0, error);
        check((size_t)length == strlen(valid[i]), "formatted byte count");
        check(strcmp(output, valid[i]) == 0, "exact reply wire format");

        printf("PASS: reply %zu round trip\n", i + 1);
    }

    check(protocol_parse_reply(
        valid[2], &reply, error, sizeof(error)) == 0, error);
    check(reply.status == STATUS_FERR, "CREATE ferr status");

    check(protocol_parse_reply(
        valid[3], &reply, error, sizeof(error)) == 0, error);
    check(reply.status == STATUS_SUCC, "UPDATE success status");
    check(strcmp(reply.filename, "movie.avi") == 0, "UPDATE filename");

    check(protocol_parse_reply(
        valid[6], &reply, error, sizeof(error)) == 0, error);
    check(reply.number == 0, "empty LIST count");

    check(protocol_parse_reply(
        valid[8], &reply, error, sizeof(error)) == 0, error);
    check(reply.number == 1, "LIST item index");
    check(reply.filesize == 4096, "LIST item filesize");
    check(strcmp(reply.filename, "movie.avi") == 0, "LIST item filename");
    check(strcmp(reply.md5, "d41d8cd98f00b204e9800998ecf8427e") == 0,
          "LIST item checksum");
    puts("PASS: parsed reply field values");

    check(protocol_parse_reply(
        "<REP GET BEGIN>\r\n",
        &reply, error, sizeof(error)) == 0, "CRLF reply");
    puts("PASS: Windows line endings");

    const char *invalid[] = {
        "",
        "REP LIST 0",
        "<>",
        "<REP LIST>",
        "<REP LIST -1>",
        "<REP LIST 18446744073709551616>",
        "<REP LIST END extra>",
        "<createtracker unknown>",
        "<createtracker succ extra>",
        "<updatetracker movie.avi>",
        "<updatetracker ../movie.avi succ>",
        "<updatetracker movie.avi unknown>",
        "<REP GET>",
        "<REP GET END>",
        "<REP GET END badmd5>",
        "<REP GET END g41d8cd98f00b204e9800998ecf8427e>",
        "<0 movie.avi 1 d41d8cd98f00b204e9800998ecf8427e>",
        "<1 movie.avi -1 d41d8cd98f00b204e9800998ecf8427e>",
        "<1 ../movie.avi 1 d41d8cd98f00b204e9800998ecf8427e>",
        "<1 movie.avi 1 badmd5>",
        "<REP LIST 0>\n<REP LIST END>\n",
        "<<REP LIST 0>>"
    };

    count = sizeof(invalid) / sizeof(invalid[0]);

    for (size_t i = 0; i < count; i++) {
        memset(&reply, 0, sizeof(reply));
        reply.type = REPLY_UPDATE;
        strcpy(reply.filename, "unchanged");
        error[0] = '\0';

        check(protocol_parse_reply(
            invalid[i], &reply, error, sizeof(error)) == -1,
            "reject malformed reply");

        check(error[0] != '\0', "explain malformed reply");
        check(reply.type == REPLY_UPDATE &&
              strcmp(reply.filename, "unchanged") == 0,
              "leave output unchanged on failure");

        printf("PASS: malformed reply %zu rejected\n", i + 1);
    }

    char oversized[PROTOCOL_LINE_MAX + 1];
    memset(oversized, 'x', sizeof(oversized) - 1);
    oversized[sizeof(oversized) - 1] = '\0';

    check(protocol_parse_reply(
        oversized, &reply, error, sizeof(error)) == -1,
        "reject oversized reply");
    puts("PASS: oversized reply rejected");

    memset(&reply, 0, sizeof(reply));
    reply.type = REPLY_LIST_END;

    char exact[16];
    check(protocol_format_reply(
        &reply, exact, sizeof(exact), error, sizeof(error)) == 15,
        "exact-size reply buffer");
    check(strcmp(exact, "<REP LIST END>\n") == 0,
          "exact-size reply contents");

    char small[15];
    check(protocol_format_reply(
        &reply, small, sizeof(small), error, sizeof(error)) == -1,
        "reject insufficient reply buffer");
    check(small[0] == '\0', "clear truncated reply");
    puts("PASS: reply buffer boundaries");

    reply.type = REPLY_CREATE;
    reply.status = (ReplyStatus)99;
    check(protocol_format_reply(
        &reply, output, sizeof(output), error, sizeof(error)) == -1,
        "reject invalid status");

    reply.type = REPLY_GET_END;
    memset(reply.md5, 'a', sizeof(reply.md5));
    check(protocol_format_reply(
        &reply, output, sizeof(output), error, sizeof(error)) == -1,
        "reject unterminated checksum");
    puts("PASS: invalid formatter fields rejected");

    check(protocol_parse_reply(
        NULL, &reply, error, sizeof(error)) == -1,
        "reject missing reply input");
    check(protocol_parse_reply(
        "<REP LIST END>", NULL, error, sizeof(error)) == -1,
        "reject missing reply output");
    check(protocol_format_reply(
        NULL, output, sizeof(output), error, sizeof(error)) == -1,
        "reject missing reply");
    puts("PASS: missing arguments rejected");

    puts("All protocol reply tests passed.");
    return EXIT_SUCCESS;
}
