#ifndef TRACKER_PROTOCOL_H
#define TRACKER_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

#define PROTOCOL_FILENAME_MAX 255
#define PROTOCOL_DESCRIPTION_MAX 255
#define PROTOCOL_MD5_LENGTH 32
#define PROTOCOL_IPV4_MAX 15
#define PROTOCOL_LINE_MAX 2048

typedef enum {
    REQUEST_CREATE,
    REQUEST_UPDATE,
    REQUEST_LIST,
    REQUEST_GET
} RequestType;

typedef struct {
    RequestType type;

    /*
     * CREATE and UPDATE use the shared file's name.
     * GET uses its tracker filename, including ".track".
     */
    char filename[PROTOCOL_FILENAME_MAX + 1];

    uint64_t filesize;

    /*
     * Descriptions occupy one protocol token.
     * Use underscores instead of spaces.
     * Use "-" when no description is supplied.
     */
    char description[PROTOCOL_DESCRIPTION_MAX + 1];

    char md5[PROTOCOL_MD5_LENGTH + 1];
    char ip[PROTOCOL_IPV4_MAX + 1];
    unsigned short port;

    /*
     * Byte offsets are transferred exactly as supplied.
     * The peer/downloader and tracker must agree on range semantics.
     */
    uint64_t start_byte;
    uint64_t end_byte;
} TrackerRequest;

/*
 * Parse one complete request line.
 *
 * Accepts the required <...> framing, with LF or CRLF.
 * Also accepts an unframed command for manual testing.
 *
 * Returns 0 on success or -1 on invalid input.
 * Leaves *out unchanged on failure.
 * Writes an explanation to error when space is available.
 *
 * The caller must assemble a complete line from TCP data before
 * calling this function. One recv() is not necessarily one message.
 */
int protocol_parse_request(
    const char *line,
    TrackerRequest *out,
    char *error,
    size_t error_size
);

/*
 * Validate and format a request using the required wire framing:
 *
 * <createtracker filename filesize description md5 ip port>\n
 * <updatetracker filename start end ip port>\n
 * <REQ LIST>\n
 * <GET filename.track>\n
 *
 * Returns the number of output bytes, excluding the terminating NUL,
 * or -1 on invalid fields or insufficient buffer space.
 */
int protocol_format_request(
    const TrackerRequest *request,
    char *buffer,
    size_t buffer_size,
    char *error,
    size_t error_size
);

#endif
