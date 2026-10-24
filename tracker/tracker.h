/*
 * tracker.h - shared declarations for the tracker server.
 *
 * Part 1 (Romita): server core, createtracker, updatetracker, sconfig.
 * Part 2 (Lohita): LIST + GET handlers and the tracker-file parser.
 *
 * Lohita only has to provide handle_list_req() and handle_get_req() below.
 * tracker.c ships "weak" placeholder versions so Part 1 builds and runs alone;
 * as soon as a real (strong) definition is linked in, it silently replaces them.
 */
#ifndef TRACKER_H
#define TRACKER_H

#define MAXLINE      4096   /* max length of one protocol message          */
#define MAXPATH      1024
#define MAXTRACK     (MAXPATH + 256)   /* dir + filename + ".track" */
#define DEFAULT_DEAD_PEER_SECS 900  /* peer is dead if no update for this long */

/* Values read from sconfig (set once at startup, read-only afterwards). */
extern int  server_port;                 /* port the tracker listens on        */
extern char torrents_dir[MAXPATH];       /* folder holding the .track files    */
extern int  dead_peer_timeout;           /* seconds before a peer is "dead"    */

/* Serialises every read/modify/write of a .track file across worker threads.
 * Part 2's GET/LIST code should lock it while reading a tracker file too.    */
#include <pthread.h>
extern pthread_mutex_t tracker_file_lock;

/* ---- Implemented by Part 2 (Lohita) ---- */
void handle_list_req(int sock);                   /* reply to <REQ LIST>       */
void handle_get_req(int sock, const char *name);  /* reply to <GET x.track>    */

#endif
