#include "config.h"

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

static void write_fixture(const char *text)
{
    FILE *file = fopen("tests/config-fixture.tmp", "w");
    check(file != NULL, "create temporary test configuration");
    check(fputs(text, file) >= 0, "write temporary test configuration");
    check(fclose(file) == 0, "close temporary test configuration");
}

int main(void)
{
    char error[256] = {0};
    TrackerConfig tracker;

    check(config_load_tracker(
        "config/tracker/sconfig",
        &tracker, error, sizeof(error)) == 0, error);

    check(tracker.listen_port == 3490, "tracker port");
    check(strcmp(tracker.tracker_directory, "torrents") == 0,
          "tracker directory");

    puts("PASS: tracker configuration");

    for (int number = 1; number <= 3; number++) {
        char path[128];
        ClientConfig client;
        PeerServerConfig server;

        snprintf(path, sizeof(path),
                 "config/peer%d/clientThreadConfig.cfg", number);

        check(config_load_client(
            path, &client, error, sizeof(error)) == 0, error);

        check(client.tracker_port == 3490, "client tracker port");
        check(strcmp(client.tracker_ip, "127.0.0.1") == 0,
              "client tracker address");
        check(client.refresh_seconds == 900, "refresh interval");

        snprintf(path, sizeof(path),
                 "config/peer%d/serverThreadConfig.cfg", number);

        check(config_load_peer_server(
            path, &server, error, sizeof(error)) == 0, error);

        check(server.listen_port == 5000 + number, "peer listening port");
        check(strcmp(server.shared_directory, "shared") == 0,
              "peer shared directory");

        printf("PASS: peer %d configuration\n", number);
    }

    ClientConfig client;

    /* Windows line endings and surrounding whitespace are accepted. */
    write_fixture(" 3490 \r\n127.0.0.1\r\n900\r\n");

    check(config_load_client(
        "tests/config-fixture.tmp",
        &client, error, sizeof(error)) == 0, error);

    check(client.tracker_port == 3490 &&
          client.refresh_seconds == 900, "CRLF configuration values");

    puts("PASS: Windows line endings");

    const char *invalid[] = {
        "0\n127.0.0.1\n900\n",
        "65536\n127.0.0.1\n900\n",
        "-1\n127.0.0.1\n900\n",
        "abc\n127.0.0.1\n900\n",
        "3490\n999.0.0.1\n900\n",
        "3490\n127.0.0.1\n0\n",
        "3490\n127.0.0.1\n-5\n",
        "3490\n127.0.0.1\n",
        "3490\n\n900\n",
        "3490\n127.0.0.1\n900\nunexpected\n"
    };

    size_t count = sizeof(invalid) / sizeof(invalid[0]);

    for (size_t i = 0; i < count; i++) {
        write_fixture(invalid[i]);
        client.tracker_port = 1234;
        error[0] = '\0';

        check(config_load_client(
            "tests/config-fixture.tmp",
            &client, error, sizeof(error)) == -1,
            "reject invalid configuration");

        check(error[0] != '\0', "provide an error explanation");
        check(client.tracker_port == 1234,
              "leave output unchanged after failure");

        printf("PASS: invalid configuration %zu rejected\n", i + 1);
    }

    /* This temporary file belongs only to this test. */
    check(remove("tests/config-fixture.tmp") == 0,
          "remove temporary test configuration");

    error[0] = '\0';

    check(config_load_client(
        "tests/config-fixture.tmp",
        &client, error, sizeof(error)) == -1,
        "reject missing configuration file");

    check(error[0] != '\0', "explain missing configuration file");
    puts("PASS: missing configuration file rejected");

    puts("All configuration tests passed.");
    return EXIT_SUCCESS;
}
