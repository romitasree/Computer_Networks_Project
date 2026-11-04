#ifndef NETWORK_CONFIG_H
#define NETWORK_CONFIG_H

#include <stddef.h>

#define CONFIG_PATH_MAX 1024
#define CONFIG_DEFAULT_REFRESH_SECONDS 900

typedef struct {
    unsigned short tracker_port;
    char tracker_ip[16];
    unsigned int refresh_seconds;
} ClientConfig;

typedef struct {
    unsigned short listen_port;
    char shared_directory[CONFIG_PATH_MAX];
} PeerServerConfig;

typedef struct {
    unsigned short listen_port;
    char tracker_directory[CONFIG_PATH_MAX];
} TrackerConfig;

/*
 * Return 0 on success or -1 on failure.
 * On failure, write a diagnostic into error when space is available.
 * Relative directory names are relative to the program's working directory.
 */
int config_load_client(
    const char *path, ClientConfig *out,
    char *error, size_t error_size
);

int config_load_peer_server(
    const char *path, PeerServerConfig *out,
    char *error, size_t error_size
);

int config_load_tracker(
    const char *path, TrackerConfig *out,
    char *error, size_t error_size
);

#endif
