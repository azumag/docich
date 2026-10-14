/*
 * Docich CEA-608 caption injector
 *
 * This file is part of FFmpeg when applied by the Docich build patch.
 *
 * FFmpeg is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public
 * License as published by the Free Software Foundation; either
 * version 2.1 of the License, or (at your option) any later version.
 *
 * FFmpeg is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Lesser General Public License for more details.
 */

#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/un.h>
#include <unistd.h>

#include <caption/caption.h>
#include <caption/cea708.h>
#include <caption/eia608.h>
#include <caption/mpeg.h>

#include "libavutil/avstring.h"
#include "libavutil/base64.h"
#include "libavutil/opt.h"
#include "libavutil/time.h"

#include "avfilter.h"
#include "ccfifo.h"
#include "internal.h"
#include "video.h"

#define DOCICHCC_PROTOCOL_VERSION 1
#define DOCICHCC_MAX_MESSAGE 4096
#define DOCICHCC_MAX_TEXT 65
#define DOCICHCC_MAX_TRIPLETS 128
#define DOCICHCC_MAX_EXECUTION_ID 128

typedef enum DocichCCFinalize {
    FINALIZE_NONE,
    FINALIZE_CLEAR,
    FINALIZE_RESET,
} DocichCCFinalize;

typedef struct DocichCCContext {
    const AVClass *class;
    char *socket_path;
    CCFifo cc_fifo;
    int cc_fifo_ready;
    int enabled;
    int listener_fd;
    int client_fd;
    int owns_socket;
    dev_t socket_device;
    ino_t socket_inode;
    char client_buffer[DOCICHCC_MAX_MESSAGE + 1];
    size_t client_size;

    char execution_id[DOCICHCC_MAX_EXECUTION_ID + 1];
    int prepared_page;
    int visible_page;

    uint64_t enqueued_triplets;
    uint64_t emitted_triplets;
    uint64_t pending_target;
    int pending_page;
    int64_t pending_started_us;
    char pending_event[16];
    DocichCCFinalize pending_finalize;
} DocichCCContext;

#define OFFSET(x) offsetof(DocichCCContext, x)
#define FLAGS AV_OPT_FLAG_FILTERING_PARAM | AV_OPT_FLAG_VIDEO_PARAM

static const AVOption docichcc_options[] = {
    { "socket", "Unix socket used for caption control", OFFSET(socket_path),
      AV_OPT_TYPE_STRING, { .str = "/run/user/1000/docich/ffmpeg-cc.sock" }, 0, 0, FLAGS },
    { NULL }
};

AVFILTER_DEFINE_CLASS(docichcc);

static void close_client(DocichCCContext *s)
{
    if (s->client_fd >= 0)
        close(s->client_fd);
    s->client_fd = -1;
    s->client_size = 0;
    s->pending_target = 0;
    s->pending_event[0] = '\0';
    s->pending_finalize = FINALIZE_NONE;
}

static int set_nonblocking_cloexec(int fd)
{
    int flags = fcntl(fd, F_GETFL, 0);
    int fdflags = fcntl(fd, F_GETFD, 0);
    if (flags < 0 || fdflags < 0 ||
        fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0 ||
        fcntl(fd, F_SETFD, fdflags | FD_CLOEXEC) < 0)
        return AVERROR(errno);
#ifdef SO_NOSIGPIPE
    {
        int value = 1;
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &value, sizeof(value));
    }
#endif
    return 0;
}

/* Return 1 when a listener answers, 0 only when the socket is demonstrably
 * stale, and a negative error when it is unsafe to decide.  Connecting and
 * immediately closing is harmless to the single-request protocol. */
static int socket_path_is_live(const char *path)
{
    struct sockaddr_un address = { 0 };
    int fd;
    int ret;
    int saved_errno;

    fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0)
        return AVERROR(errno);
    ret = set_nonblocking_cloexec(fd);
    if (ret < 0) {
        close(fd);
        return ret;
    }
    address.sun_family = AF_UNIX;
    av_strlcpy(address.sun_path, path, sizeof(address.sun_path));
    ret = connect(fd, (struct sockaddr *)&address, sizeof(address));
    saved_errno = errno;
    close(fd);
    if (!ret || saved_errno == EINPROGRESS || saved_errno == EALREADY ||
        saved_errno == EISCONN)
        return 1;
    if (saved_errno == ECONNREFUSED || saved_errno == ENOENT)
        return 0;
    return AVERROR(saved_errno);
}

static int send_wire(int fd, const char *wire)
{
    size_t remaining = strlen(wire);
    const char *cursor = wire;
    while (remaining) {
        ssize_t written;
#ifdef MSG_NOSIGNAL
        written = send(fd, cursor, remaining, MSG_NOSIGNAL);
#else
        written = send(fd, cursor, remaining, 0);
#endif
        if (written < 0) {
            if (errno == EINTR)
                continue;
            return AVERROR(errno);
        }
        if (!written)
            return AVERROR(EPIPE);
        cursor += written;
        remaining -= written;
    }
    return 0;
}

static void send_error_and_close(DocichCCContext *s, const char *code,
                                 const char *message)
{
    char response[512];
    if (s->client_fd < 0)
        return;
    snprintf(response, sizeof(response),
             "{\"v\":1,\"event\":\"error\",\"code\":\"%s\","
             "\"message\":\"%s\"}\n", code, message);
    send_wire(s->client_fd, response);
    close_client(s);
}

static int send_event(DocichCCContext *s, const char *event, int page,
                      int64_t elapsed_us)
{
    char response[320];
    if (page >= 0) {
        snprintf(response, sizeof(response),
                 "{\"v\":1,\"event\":\"%s\",\"executionId\":\"%s\","
                 "\"page\":%d,\"elapsedMs\":%.3f}\n",
                 event, s->execution_id, page, elapsed_us / 1000.0);
    } else {
        snprintf(response, sizeof(response),
                 "{\"v\":1,\"event\":\"%s\",\"elapsedMs\":%.3f}\n",
                 event, elapsed_us / 1000.0);
    }
    return send_wire(s->client_fd, response);
}

static const char *json_value(const char *json, const char *key)
{
    char needle[96];
    const char *found;
    snprintf(needle, sizeof(needle), "\"%s\"", key);
    found = strstr(json, needle);
    if (!found)
        return NULL;
    found += strlen(needle);
    while (*found == ' ' || *found == '\t')
        found++;
    if (*found++ != ':')
        return NULL;
    while (*found == ' ' || *found == '\t')
        found++;
    return found;
}

static int json_string(const char *json, const char *key, char *output,
                       size_t output_size)
{
    const char *value = json_value(json, key);
    size_t length = 0;
    if (!value || *value++ != '"')
        return AVERROR(EINVAL);
    while (value[length] && value[length] != '"') {
        if (value[length] == '\\' || (unsigned char)value[length] < 0x20)
            return AVERROR(EINVAL);
        length++;
    }
    if (value[length] != '"' || !length || length >= output_size)
        return AVERROR(EINVAL);
    memcpy(output, value, length);
    output[length] = '\0';
    return 0;
}

static int json_integer(const char *json, const char *key, long *output)
{
    const char *value = json_value(json, key);
    char *end;
    long parsed;
    if (!value)
        return AVERROR(EINVAL);
    errno = 0;
    parsed = strtol(value, &end, 10);
    if (errno || end == value || (*end != ',' && *end != '}' &&
                                  *end != ' ' && *end != '\t'))
        return AVERROR(EINVAL);
    *output = parsed;
    return 0;
}

static int valid_execution_id(const char *value)
{
    size_t length = strlen(value);
    if (!length || length > DOCICHCC_MAX_EXECUTION_ID)
        return 0;
    for (size_t i = 0; i < length; i++) {
        unsigned char c = value[i];
        if (!(c >= 'A' && c <= 'Z') && !(c >= 'a' && c <= 'z') &&
            !(c >= '0' && c <= '9') && c != '.' && c != '_' &&
            c != ':' && c != '-')
            return 0;
    }
    return 1;
}

static int validate_text(const uint8_t *text, int length)
{
    int columns = 0;
    int lines = 1;
    if (length < 1 || length > DOCICHCC_MAX_TEXT)
        return AVERROR(EINVAL);
    for (int i = 0; i < length; i++) {
        if (text[i] == '\n') {
            if (!columns || ++lines > 2)
                return AVERROR(EINVAL);
            columns = 0;
        } else if (text[i] < 0x20 || text[i] > 0x7e || ++columns > 32) {
            return AVERROR(EINVAL);
        }
    }
    return columns ? 0 : AVERROR(EINVAL);
}

static int append_sei_triplets(sei_t *sei, uint8_t *triplets,
                               int max_triplets)
{
    int count = 0;
    for (sei_message_t *message = sei_message_head(sei); message;
         message = sei_message_next(message)) {
        cea708_t cea708;
        if (sei_message_type(message) != sei_type_user_data_registered_itu_t_t35)
            continue;
        cea708_init(&cea708, 0);
        if (cea708_parse_h264(sei_message_data(message),
                              sei_message_size(message), &cea708) == LIBCAPTION_ERROR)
            return AVERROR_INVALIDDATA;
        for (int i = 0; i < cea708_cc_count(&cea708.user_data); i++) {
            cea708_cc_type_t type;
            int valid;
            uint16_t data = cea708_cc_data(&cea708.user_data, i, &valid, &type);
            if (!valid || type != cc_type_ntsc_cc_field_1)
                continue;
            if (count >= max_triplets)
                return AVERROR(ENOSPC);
            triplets[count * 3] = 0xfc;
            triplets[count * 3 + 1] = data >> 8;
            triplets[count * 3 + 2] = data & 0xff;
            count++;
        }
    }
    return count;
}

static int caption_triplets(const char *text, uint8_t *triplets,
                            int max_triplets)
{
    caption_frame_t frame;
    sei_t sei;
    int rows = strchr(text, '\n') ? 2 : 1;
    int row = SCREEN_ROWS - rows;
    int column = 0;
    int count;

    caption_frame_init(&frame);
    frame.write = &frame.front;
    for (const char *cursor = text; *cursor; cursor++) {
        if (*cursor == '\n') {
            row++;
            column = 0;
            continue;
        }
        if (!caption_frame_write_char(&frame, row, column++,
                                      eia608_style_white, 0, cursor))
            return AVERROR_INVALIDDATA;
    }

    sei_from_caption_frame(&sei, &frame);
    count = append_sei_triplets(&sei, triplets, max_triplets);
    sei_free(&sei);
    if (count < 2)
        return AVERROR_INVALIDDATA;

    /* libcaption terminates pop-on captions with a duplicated EOC.  prepare
     * deliberately leaves those two tuples out; commit queues them later. */
    return count - 2;
}

static int clear_triplets(uint8_t *triplets, int max_triplets)
{
    sei_t sei;
    int count;
    sei_init(&sei, 0);
    sei_from_caption_clear(&sei);
    count = append_sei_triplets(&sei, triplets, max_triplets);
    sei_free(&sei);
    return count;
}

static int enqueue_triplets(DocichCCContext *s, uint8_t *triplets, int count)
{
    int ret;
    if (count <= 0 || count > DOCICHCC_MAX_TRIPLETS)
        return AVERROR(EINVAL);
    if (av_fifo_can_write(s->cc_fifo.cc_608_fifo) < count)
        return AVERROR(ENOSPC);
    ret = ff_ccfifo_extractbytes(&s->cc_fifo, triplets, count * 3);
    if (ret < 0)
        return ret;
    s->enqueued_triplets += count;
    return 0;
}

static int enqueue_commit(DocichCCContext *s)
{
    uint8_t triplets[6];
    uint16_t eoc = eia608_control_command(eia608_control_end_of_caption, 0);
    for (int i = 0; i < 2; i++) {
        triplets[i * 3] = 0xfc;
        triplets[i * 3 + 1] = eoc >> 8;
        triplets[i * 3 + 2] = eoc & 0xff;
    }
    return enqueue_triplets(s, triplets, 2);
}

static int start_pending(DocichCCContext *s, const char *event, int page,
                         DocichCCFinalize finalize)
{
    av_strlcpy(s->pending_event, event, sizeof(s->pending_event));
    s->pending_target = s->enqueued_triplets;
    s->pending_page = page;
    s->pending_started_us = av_gettime_relative();
    s->pending_finalize = finalize;
    if (send_event(s, "accepted", page, 0) < 0) {
        close_client(s);
        return AVERROR(EPIPE);
    }
    return 0;
}

static int handle_prepare(DocichCCContext *s, const char *json,
                          const char *execution_id, int page)
{
    char encoded[256];
    uint8_t decoded[DOCICHCC_MAX_TEXT + 1];
    uint8_t triplets[DOCICHCC_MAX_TRIPLETS * 3];
    int decoded_length;
    int count;
    int ret;

    if (json_string(json, "textBase64", encoded, sizeof(encoded)) < 0) {
        send_error_and_close(s, "BAD_TEXT", "textBase64 is required");
        return 0;
    }
    decoded_length = av_base64_decode(decoded, encoded, DOCICHCC_MAX_TEXT);
    if (decoded_length < 0 || validate_text(decoded, decoded_length) < 0) {
        send_error_and_close(s, "BAD_TEXT", "caption must be ASCII, 32 columns by 2 lines");
        return 0;
    }
    decoded[decoded_length] = '\0';

    if (s->execution_id[0] && strcmp(s->execution_id, execution_id)) {
        send_error_and_close(s, "ACTIVE_EXECUTION", "reset or clear the active execution first");
        return 0;
    }
    if (s->prepared_page >= 0) {
        send_error_and_close(s, "PAGE_ALREADY_PREPARED", "commit the prepared page first");
        return 0;
    }

    count = caption_triplets((char *)decoded, triplets, DOCICHCC_MAX_TRIPLETS);
    if (count < 0) {
        send_error_and_close(s, "BAD_TEXT", "caption could not be encoded");
        return count;
    }
    ret = enqueue_triplets(s, triplets, count);
    if (ret < 0) {
        send_error_and_close(s, "QUEUE_FULL", "caption could not be queued");
        return ret;
    }
    av_strlcpy(s->execution_id, execution_id, sizeof(s->execution_id));
    s->prepared_page = page;
    return start_pending(s, "prepared", page, FINALIZE_NONE);
}

static int handle_request(AVFilterContext *ctx, const char *json)
{
    DocichCCContext *s = ctx->priv;
    char operation[16];
    char execution_id[DOCICHCC_MAX_EXECUTION_ID + 1] = "";
    long version;
    long page = -1;
    uint8_t triplets[DOCICHCC_MAX_TRIPLETS * 3];
    int count;
    int ret;

    if (json_integer(json, "v", &version) < 0 ||
        version != DOCICHCC_PROTOCOL_VERSION ||
        json_string(json, "op", operation, sizeof(operation)) < 0) {
        send_error_and_close(s, "BAD_REQUEST", "protocol version and op are required");
        return 0;
    }

    if (!strcmp(operation, "reset")) {
        count = clear_triplets(triplets, DOCICHCC_MAX_TRIPLETS);
        if (count < 0) {
            send_error_and_close(s, "ENCODE_ERROR", "reset could not be encoded");
            return count;
        }
        ret = enqueue_triplets(s, triplets, count);
        if (ret < 0) {
            send_error_and_close(s, "QUEUE_FULL", "reset could not be queued");
            return ret;
        }
        s->prepared_page = -1;
        s->visible_page = -1;
        s->execution_id[0] = '\0';
        return start_pending(s, "reset", -1, FINALIZE_RESET);
    }

    if (json_string(json, "executionId", execution_id,
                    sizeof(execution_id)) < 0 || !valid_execution_id(execution_id)) {
        send_error_and_close(s, "BAD_EXECUTION", "executionId is invalid");
        return 0;
    }
    if (!strcmp(operation, "prepare") || !strcmp(operation, "commit")) {
        if (json_integer(json, "page", &page) < 0 || page < 0 || page > 31) {
            send_error_and_close(s, "BAD_PAGE", "page must be between 0 and 31");
            return 0;
        }
    }
    if (!strcmp(operation, "prepare"))
        return handle_prepare(s, json, execution_id, (int)page);

    if (strcmp(s->execution_id, execution_id)) {
        send_error_and_close(s, "STALE_EXECUTION", "executionId is not active");
        return 0;
    }
    if (!strcmp(operation, "commit")) {
        if (s->prepared_page != page) {
            send_error_and_close(s, "PAGE_NOT_PREPARED", "prepare this page before commit");
            return 0;
        }
        if ((ret = enqueue_commit(s)) < 0) {
            send_error_and_close(s, "QUEUE_FULL", "commit could not be queued");
            return ret;
        }
        s->visible_page = (int)page;
        s->prepared_page = -1;
        return start_pending(s, "committed", (int)page, FINALIZE_NONE);
    }
    if (!strcmp(operation, "clear")) {
        count = clear_triplets(triplets, DOCICHCC_MAX_TRIPLETS);
        if (count < 0) {
            send_error_and_close(s, "ENCODE_ERROR", "clear could not be encoded");
            return count;
        }
        ret = enqueue_triplets(s, triplets, count);
        if (ret < 0) {
            send_error_and_close(s, "QUEUE_FULL", "clear could not be queued");
            return ret;
        }
        s->prepared_page = -1;
        s->visible_page = -1;
        return start_pending(s, "cleared", -1, FINALIZE_CLEAR);
    }

    send_error_and_close(s, "BAD_OPERATION", "unsupported operation");
    return 0;
}

static void complete_pending(DocichCCContext *s)
{
    int64_t elapsed_us;
    if (s->client_fd < 0 || !s->pending_event[0] ||
        s->emitted_triplets < s->pending_target)
        return;
    elapsed_us = av_gettime_relative() - s->pending_started_us;
    send_event(s, s->pending_event, s->pending_page, elapsed_us);
    if (s->pending_finalize == FINALIZE_CLEAR ||
        s->pending_finalize == FINALIZE_RESET) {
        s->execution_id[0] = '\0';
        s->cc_fifo.cc_detected = 0;
    }
    close_client(s);
}

static void service_socket(AVFilterContext *ctx)
{
    DocichCCContext *s = ctx->priv;
    ssize_t received;
    char *newline;

    complete_pending(s);
    if (s->client_fd >= 0 && s->pending_event[0])
        return;

    if (s->client_fd < 0) {
        s->client_fd = accept(s->listener_fd, NULL, NULL);
        if (s->client_fd < 0) {
            if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)
                av_log(ctx, AV_LOG_WARNING, "docichcc accept failed: %s\n", av_err2str(AVERROR(errno)));
            return;
        }
        if (set_nonblocking_cloexec(s->client_fd) < 0) {
            close_client(s);
            return;
        }
    }

    while (s->client_size < DOCICHCC_MAX_MESSAGE) {
        received = recv(s->client_fd, s->client_buffer + s->client_size,
                        DOCICHCC_MAX_MESSAGE - s->client_size, 0);
        if (received > 0) {
            s->client_size += received;
            continue;
        }
        if (!received) {
            close_client(s);
            return;
        }
        if (errno == EINTR)
            continue;
        if (errno != EAGAIN && errno != EWOULDBLOCK)
            close_client(s);
        break;
    }

    s->client_buffer[s->client_size] = '\0';
    newline = memchr(s->client_buffer, '\n', s->client_size);
    if (!newline) {
        if (s->client_size == DOCICHCC_MAX_MESSAGE)
            send_error_and_close(s, "MESSAGE_TOO_LARGE", "request exceeds 4KiB");
        return;
    }
    *newline = '\0';
    handle_request(ctx, s->client_buffer);
}

static int setup_listener(AVFilterContext *ctx)
{
    DocichCCContext *s = ctx->priv;
    struct sockaddr_un address = { 0 };
    struct stat existing;
    int fd;
    int live;

    if (!s->socket_path || s->socket_path[0] != '/' ||
        strlen(s->socket_path) >= sizeof(address.sun_path)) {
        av_log(ctx, AV_LOG_WARNING, "docichcc disabled: socket path is invalid\n");
        return 0;
    }
    if (!lstat(s->socket_path, &existing)) {
        if (!S_ISSOCK(existing.st_mode) || existing.st_uid != geteuid()) {
            av_log(ctx, AV_LOG_WARNING,
                   "docichcc disabled: refusing to replace socket path\n");
            return 0;
        }
        live = socket_path_is_live(s->socket_path);
        if (live) {
            av_log(ctx, AV_LOG_WARNING,
                   "docichcc disabled: refusing to replace active socket path\n");
            return 0;
        }
        if (unlink(s->socket_path) < 0 && errno != ENOENT) {
            av_log(ctx, AV_LOG_WARNING, "docichcc disabled: stale socket could not be removed\n");
            return 0;
        }
    } else if (errno != ENOENT) {
        av_log(ctx, AV_LOG_WARNING, "docichcc disabled: socket path could not be inspected\n");
        return 0;
    }

    fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0 || set_nonblocking_cloexec(fd) < 0) {
        if (fd >= 0)
            close(fd);
        av_log(ctx, AV_LOG_WARNING, "docichcc disabled: listener could not be created\n");
        return 0;
    }
    address.sun_family = AF_UNIX;
    av_strlcpy(address.sun_path, s->socket_path, sizeof(address.sun_path));
    if (bind(fd, (struct sockaddr *)&address, sizeof(address)) < 0 ||
        chmod(s->socket_path, 0600) < 0 || listen(fd, 4) < 0) {
        av_log(ctx, AV_LOG_WARNING, "docichcc disabled: listener setup failed: %s\n",
               av_err2str(AVERROR(errno)));
        close(fd);
        if (!lstat(s->socket_path, &existing) && S_ISSOCK(existing.st_mode) &&
            existing.st_uid == geteuid())
            unlink(s->socket_path);
        return 0;
    }
    if (lstat(s->socket_path, &existing) < 0 ||
        !S_ISSOCK(existing.st_mode) || existing.st_uid != geteuid()) {
        av_log(ctx, AV_LOG_WARNING,
               "docichcc disabled: listener ownership could not be verified\n");
        close(fd);
        return 0;
    }
    s->listener_fd = fd;
    s->owns_socket = 1;
    s->socket_device = existing.st_dev;
    s->socket_inode = existing.st_ino;
    s->enabled = 1;
    av_log(ctx, AV_LOG_INFO, "docichcc listening on %s\n", s->socket_path);
    return 0;
}

static int config_input(AVFilterLink *link)
{
    AVFilterContext *ctx = link->dst;
    DocichCCContext *s = ctx->priv;
    AVRational frame_rate = link->frame_rate;
    int ret;

    if (!frame_rate.num || !frame_rate.den)
        frame_rate = av_inv_q(link->time_base);
    ret = ff_ccfifo_init(&s->cc_fifo, frame_rate, ctx);
    if (ret < 0)
        return ret;
    s->cc_fifo_ready = 1;
    if (s->cc_fifo.passthrough) {
        av_log(ctx, AV_LOG_WARNING,
               "docichcc disabled: frame rate %d/%d is unsupported\n",
               frame_rate.num, frame_rate.den);
        return 0;
    }
    return setup_listener(ctx);
}

static av_cold int init(AVFilterContext *ctx)
{
    DocichCCContext *s = ctx->priv;
    s->listener_fd = -1;
    s->client_fd = -1;
    s->prepared_page = -1;
    s->visible_page = -1;
    s->pending_page = -1;
    return 0;
}

static int filter_frame(AVFilterLink *inlink, AVFrame *frame)
{
    AVFilterContext *ctx = inlink->dst;
    DocichCCContext *s = ctx->priv;
    AVFilterLink *outlink = ctx->outputs[0];
    uint64_t available;
    int emitted;
    int ret;

    if (!s->enabled)
        return ff_filter_frame(outlink, frame);

    av_frame_remove_side_data(frame, AV_FRAME_DATA_A53_CC);
    service_socket(ctx);
    available = s->enqueued_triplets - s->emitted_triplets;
    ret = ff_ccfifo_inject(&s->cc_fifo, frame);
    if (ret < 0) {
        av_log(ctx, AV_LOG_WARNING, "docichcc injection failed: %s\n", av_err2str(ret));
        return ff_filter_frame(outlink, frame);
    }
    emitted = FFMIN((uint64_t)s->cc_fifo.expected_608, available);
    s->emitted_triplets += emitted;
    complete_pending(s);
    return ff_filter_frame(outlink, frame);
}

static av_cold void uninit(AVFilterContext *ctx)
{
    DocichCCContext *s = ctx->priv;
    struct stat existing;
    close_client(s);
    if (s->listener_fd >= 0)
        close(s->listener_fd);
    if (s->owns_socket && !lstat(s->socket_path, &existing) &&
        S_ISSOCK(existing.st_mode) && existing.st_uid == geteuid() &&
        existing.st_dev == s->socket_device &&
        existing.st_ino == s->socket_inode)
        unlink(s->socket_path);
    if (s->cc_fifo_ready)
        ff_ccfifo_uninit(&s->cc_fifo);
}

static const AVFilterPad docichcc_inputs[] = {
    {
        .name = "default",
        .type = AVMEDIA_TYPE_VIDEO,
        .filter_frame = filter_frame,
        .config_props = config_input,
    },
};

const AVFilter ff_vf_docichcc = {
    .name = "docichcc",
    .description = NULL_IF_CONFIG_SMALL("Inject synchronized CEA-608 captions over a Unix socket"),
    .init = init,
    .uninit = uninit,
    .priv_size = sizeof(DocichCCContext),
    .priv_class = &docichcc_class,
    FILTER_INPUTS(docichcc_inputs),
    FILTER_OUTPUTS(ff_video_default_filterpad),
};
