#ifndef TRACKER_PROTOCOL_REPLY_H
#define TRACKER_PROTOCOL_REPLY_H

#include "protocol.h"

typedef enum {
    REPLY_CREATE,
    REPLY_UPDATE,
    REPLY_LIST_BEGIN,
    REPLY_LIST_ITEM,
    REPLY_LIST_END,
    REPLY_GET_BEGIN,
    REPLY_GET_END
} ReplyType;

typedef enum {
    STATUS_SUCC,
    STATUS_FAIL,
    STATUS_FERR
} ReplyStatus;

typedef struct {
    ReplyType type;

    /* Used by CREATE and UPDATE replies. */
    ReplyStatus status;

    /* UPDATE filename or LIST item filename. */
    char filename[PROTOCOL_FILENAME_MAX + 1];

    /* LIST header count or LIST item's one-based index. */
    uint64_t number;

    /* Used by LIST items. */
    uint64_t filesize;

    /*
     * LIST item: checksum of the shared file.
     * GET END: checksum of the transferred tracker-file bytes.
     * These are different checksum purposes.
     */
    char md5[PROTOCOL_MD5_LENGTH + 1];
} TrackerReply;

/*
 * Format one reply line, including angle brackets and LF:
 *
 * <createtracker succ|fail|ferr>
 * <updatetracker filename succ|fail|ferr>
 * <REP LIST count>
 * <index filename filesize md5>
 * <REP LIST END>
 * <REP GET BEGIN>
 * <REP GET END md5>
 *
 * Returns bytes written, excluding the terminating NUL,
 * or -1 on invalid fields or insufficient space.
 */
int protocol_format_reply(
    const TrackerReply *reply,
    char *buffer,
    size_t buffer_size,
    char *error,
    size_t error_size
);

/*
 * Parse one framed reply line with optional LF or CRLF.
 * Returns 0 on success or -1 on invalid input.
 * Leaves *out unchanged on failure.
 *
 * This handles control lines, not the tracker-file body.
 * The GET receiver must separately collect the tracker bytes
 * and verify their MD5 against the GET END checksum.
 *
 * The LIST receiver must separately check that item indexes
 * and the received item count match the LIST header.
 */
int protocol_parse_reply(
    const char *line,
    TrackerReply *out,
    char *error,
    size_t error_size
);

#endif
