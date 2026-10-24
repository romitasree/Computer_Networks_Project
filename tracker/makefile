CC = gcc
CFLAGS = -Wall -Wextra -pthread -O2
# Part 2 will add its file (e.g. tracker_queries.c) to SRCS
SRCS = tracker.c

tracker: $(SRCS) tracker.h
	$(CC) $(CFLAGS) -o tracker $(SRCS)

clean:
	rm -f tracker
