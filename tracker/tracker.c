/*
 * tracker.c - Part 1: Tracker Server Core
 *
 * What this file does:
 *   1. Reads sconfig (port + shared "torrents" folder).
 *   2. Listens for TCP connections; every connection gets its own worker
 *      thread (pthread), which serves exactly one request and then closes.
 *   3. Handles  createtracker  (make a new <file>.track, refuse duplicates)
 *      and      updatetracker  (add/refresh a peer, drop dead peers).
 *   4. Hands LIST and GET to Part 2's handlers.
 *
 * Build: make        Run: ./tracker
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>
#include <time.h>
#include <pthread.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include "tracker.h"

int  server_port = 3490;
char torrents_dir[MAXPATH] = "torrents";
int  dead_peer_timeout = DEFAULT_DEAD_PEER_SECS;
pthread_mutex_t tracker_file_lock = PTHREAD_MUTEX_INITIALIZER;

/* Everything a worker thread needs to know about its connection. */
struct conn { int fd; struct sockaddr_in addr; };

/* ------------------------------------------------------------------ */
/* Placeholder LIST/GET handlers (weak = replaced by Part 2's versions) */
/* ------------------------------------------------------------------ */
__attribute__((weak)) void handle_list_req(int sock) {
    const char *m = "<REP LIST 0>\n<REP LIST END>\n";   /* "no files" */
    if (write(sock, m, strlen(m)) < 0) { /* ignore */ }
}
__attribute__((weak)) void handle_get_req(int sock, const char *name) {
    (void)name;
    const char *m = "<GET invalid>\n";
    if (write(sock, m, strlen(m)) < 0) { /* ignore */ }
}

/* ------------------------------------------------------------------ */
/* Small helpers                                                       */
/* ------------------------------------------------------------------ */

/* Send a whole string (write() may send less than asked). */
static void send_str(int fd, const char *s) {
    size_t left = strlen(s);
    while (left > 0) {
        ssize_t n = write(fd, s, left);
        if (n < 0) { if (errno == EINTR) continue; return; }
        s += n; left -= n;
    }
}

/* Read one line (up to '\n') from the socket. Returns length, or -1 on error/empty. */
static int read_line(int fd, char *buf, int max) {
    int len = 0;
    while (len < max - 1) {
        char c;
        ssize_t n = read(fd, &c, 1);
        if (n < 0) { if (errno == EINTR) continue; return -1; }
        if (n == 0) break;              /* peer closed */
        if (c == '\n') break;
        buf[len++] = c;
    }
    buf[len] = '\0';
    return len > 0 ? len : -1;
}

/* Turn "<cmd a b c>" or "cmd a b c" into tokens. Returns token count. */
static int tokenize(char *line, char *tok[], int maxtok) {
    int n = 0;
    char *p = line, *save = NULL;
    while (*p == ' ' || *p == '\r' || *p == '<') p++;           /* strip leading '<' */
    size_t l = strlen(p);
    while (l > 0 && (p[l-1] == '>' || p[l-1] == '\r' || p[l-1] == ' ')) p[--l] = '\0';
    for (char *t = strtok_r(p, " ", &save); t && n < maxtok; t = strtok_r(NULL, " ", &save))
        tok[n++] = t;
    return n;
}

/* Reject names that could escape the torrents folder. */
static int safe_name(const char *n) {
    return n[0] != '\0' && !strchr(n, '/') && !strchr(n, '\\') && strstr(n, "..") == NULL
           && strlen(n) < 200;
}

static void track_path(char *out, size_t sz, const char *filename) {
    snprintf(out, sz, "%s/%s.track", torrents_dir, filename);
}

/* ------------------------------------------------------------------ */
/* createtracker                                                       */
/* <createtracker filename filesize description md5 ip port>           */
/* ------------------------------------------------------------------ */
static void handle_createtracker(int sock, char *tok[], int ntok) {
    /* tok[0]=cmd 1=filename 2=filesize 3=description 4=md5 5=ip 6=port */
    if (ntok < 7 || !safe_name(tok[1]) || atol(tok[2]) < 0 || atoi(tok[6]) <= 0) {
        send_str(sock, "<createtracker fail>\n");
        return;
    }
    char path[MAXTRACK];
    track_path(path, sizeof path, tok[1]);

    pthread_mutex_lock(&tracker_file_lock);
    /* O_EXCL makes "check if exists" + "create" one atomic step. */
    int fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0644);
    if (fd < 0) {
        pthread_mutex_unlock(&tracker_file_lock);
        send_str(sock, errno == EEXIST ? "<createtracker ferr>\n" : "<createtracker fail>\n");
        return;
    }
    FILE *f = fdopen(fd, "w");
    if (!f) { close(fd); unlink(path); pthread_mutex_unlock(&tracker_file_lock);
              send_str(sock, "<createtracker fail>\n"); return; }

    /* A seed has the whole file: bytes 0..filesize. Timestamp = now. */
    fprintf(f, "Filename: %s\nFilesize: %s\nDescription: %s\nMD5: %s\n"
               "#list of peers follows next\n%s:%s:0:%s:%ld\n",
            tok[1], tok[2], tok[3], tok[4], tok[5], tok[6], tok[2], (long)time(NULL));
    int bad = (fclose(f) != 0);
    if (bad) unlink(path);
    pthread_mutex_unlock(&tracker_file_lock);

    send_str(sock, bad ? "<createtracker fail>\n" : "<createtracker succ>\n");
    if (!bad) printf("[tracker] created %s for %s:%s\n", path, tok[5], tok[6]);
}

/* ------------------------------------------------------------------ */
/* updatetracker                                                       */
/* <updatetracker filename start_byte end_byte ip port>                */
/* ------------------------------------------------------------------ */

/* Is this line a peer entry ("ip:port:start:end:timestamp")? */
static int parse_peer(const char *line, char *ip, int *port, long *s, long *e, long *ts) {
    char ipbuf[64];
    return sscanf(line, " %63[^:]:%d:%ld:%ld:%ld", ipbuf, port, s, e, ts) == 5
           && (strcpy(ip, ipbuf), 1);
}

static void handle_updatetracker(int sock, char *tok[], int ntok) {
    /* tok[0]=cmd 1=filename 2=start 3=end 4=ip 5=port */
    char reply[MAXLINE];
    if (ntok < 6 || !safe_name(tok[1])) {
        snprintf(reply, sizeof reply, "<updatetracker %s fail>\n", ntok > 1 ? tok[1] : "?");
        send_str(sock, reply);
        return;
    }
    const char *status = "fail";
    char path[MAXTRACK], tmp[MAXTRACK + 8];
    track_path(path, sizeof path, tok[1]);
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    long now = (long)time(NULL);
    long new_s = atol(tok[2]), new_e = atol(tok[3]);
    int  new_port = atoi(tok[5]);

    pthread_mutex_lock(&tracker_file_lock);
    FILE *in = fopen(path, "r");
    if (!in) {
        status = (errno == ENOENT) ? "ferr" : "fail";      /* no such tracker file */
    } else {
        FILE *out = fopen(tmp, "w");
        if (out) {
            char line[MAXLINE], ip[64];
            int port, found = 0;
            long s, e, ts;
            while (fgets(line, sizeof line, in)) {
                if (!parse_peer(line, ip, &port, &s, &e, &ts)) {
                    fputs(line, out);                       /* header/comment: copy as-is */
                    continue;
                }
                if (!strcmp(ip, tok[4]) && port == new_port) {
                    /* Same peer: refresh range + timestamp. */
                    fprintf(out, "%s:%d:%ld:%ld:%ld\n", ip, port, new_s, new_e, now);
                    found = 1;
                } else if (now - ts > dead_peer_timeout) {
                    printf("[tracker] purged dead peer %s:%d from %s\n", ip, port, tok[1]);
                } else {
                    fputs(line, out);                       /* still alive: keep */
                }
            }
            if (!found)                                     /* new peer: add entry */
                fprintf(out, "%s:%d:%ld:%ld:%ld\n", tok[4], new_port, new_s, new_e, now);
            int werr = ferror(out);
            if (fclose(out) == 0 && !werr && rename(tmp, path) == 0)
                status = "succ";
            else
                unlink(tmp);
        }
        fclose(in);
    }
    pthread_mutex_unlock(&tracker_file_lock);

    snprintf(reply, sizeof reply, "<updatetracker %s %s>\n", tok[1], status);
    send_str(sock, reply);
    if (!strcmp(status, "succ"))
        printf("[tracker] updated %s from %s:%s\n", tok[1], tok[4], tok[5]);
}

/* ------------------------------------------------------------------ */
/* Worker thread: serve ONE request, then close                        */
/* ------------------------------------------------------------------ */
static void *worker(void *arg) {
    struct conn *c = arg;
    char line[MAXLINE], copy[MAXLINE], *tok[10];

    if (read_line(c->fd, line, sizeof line) < 0) { close(c->fd); free(c); return NULL; }
    printf("[tracker] request from %s: %s\n", inet_ntoa(c->addr.sin_addr), line);

    strcpy(copy, line);
    int n = tokenize(copy, tok, 10);
    if (n == 0) {
        /* nothing to do */
    } else if (n >= 2 && !strcasecmp(tok[0], "REQ") && !strcasecmp(tok[1], "LIST")) {
        handle_list_req(c->fd);
    } else if (n >= 2 && !strcasecmp(tok[0], "GET")) {
        handle_get_req(c->fd, tok[1]);
    } else if (!strcasecmp(tok[0], "createtracker")) {
        handle_createtracker(c->fd, tok, n);
    } else if (!strcasecmp(tok[0], "updatetracker")) {
        handle_updatetracker(c->fd, tok, n);
    } else {
        send_str(c->fd, "<error unknown command>\n");
    }
    close(c->fd);                                   /* one request per connection */
    free(c);
    return NULL;
}

/* ------------------------------------------------------------------ */
/* sconfig: line 1 = port, line 2 = torrents dir, (optional) line 3 =  */
/* dead-peer timeout in seconds                                        */
/* ------------------------------------------------------------------ */
static int read_config(const char *file) {
    FILE *f = fopen(file, "r");
    if (!f) { perror("cannot open sconfig"); return -1; }
    char l1[128] = "", l2[MAXPATH] = "", l3[64] = "";
    if (!fgets(l1, sizeof l1, f) || !fgets(l2, sizeof l2, f)) {
        fprintf(stderr, "sconfig needs at least 2 lines (port, folder)\n");
        fclose(f); return -1;
    }
    if (fgets(l3, sizeof l3, f) && atoi(l3) > 0) dead_peer_timeout = atoi(l3);
    fclose(f);
    l2[strcspn(l2, "\r\n")] = '\0';
    server_port = atoi(l1);
    if (server_port <= 0 || server_port > 65535 || l2[0] == '\0') {
        fprintf(stderr, "invalid values in sconfig\n"); return -1;
    }
    strncpy(torrents_dir, l2, sizeof torrents_dir - 1);
    torrents_dir[sizeof torrents_dir - 1] = '\0';
    return 0;
}

int main(void) {
    if (read_config("sconfig") < 0) return 1;
    if (mkdir(torrents_dir, 0755) < 0 && errno != EEXIST) { perror("torrents dir"); return 1; }

    int sockid = socket(AF_INET, SOCK_STREAM, 0);
    if (sockid < 0) { perror("socket cannot be created"); return 1; }
    int yes = 1;
    setsockopt(sockid, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof yes);

    struct sockaddr_in server_addr;
    memset(&server_addr, 0, sizeof server_addr);
    server_addr.sin_family = AF_INET;
    server_addr.sin_port = htons(server_port);
    server_addr.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(sockid, (struct sockaddr *)&server_addr, sizeof server_addr) < 0) {
        perror("bind failure"); return 1;
    }
    if (listen(sockid, 32) < 0) { perror("tracker cannot listen"); return 1; }
    printf("Tracker listening on port %d, shared folder '%s', dead-peer timeout %ds\n",
           server_port, torrents_dir, dead_peer_timeout);

    for (;;) {                                      /* accept loop */
        struct conn *c = malloc(sizeof *c);
        if (!c) continue;
        socklen_t len = sizeof c->addr;
        c->fd = accept(sockid, (struct sockaddr *)&c->addr, &len);
        if (c->fd < 0) { free(c); if (errno != EINTR) perror("accept"); continue; }

        pthread_t tid;                              /* one worker thread per connection */
        if (pthread_create(&tid, NULL, worker, c) != 0) {
            perror("pthread_create"); close(c->fd); free(c); continue;
        }
        pthread_detach(tid);                        /* thread cleans itself up */
    }
}
