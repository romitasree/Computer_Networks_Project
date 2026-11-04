CC = clang
AR = ar
CPPFLAGS += -Iinclude
CFLAGS += -std=c11 -Wall -Wextra -Werror

OBJECTS = build/config.o build/protocol.o build/protocol_reply.o
LIBRARY = build/libpart4.a
TESTS = build/test_config build/test_protocol build/test_protocol_reply
HEADERS = include/config.h include/protocol.h include/protocol_reply.h

.PHONY: all test clean

all: $(LIBRARY) $(TESTS)

build:
	mkdir -p build

build/%.o: src/%.c $(HEADERS) | build
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

$(LIBRARY): $(OBJECTS)
	$(AR) rcs $@ $(OBJECTS)

build/test_config: tests/test_config.c $(LIBRARY) $(HEADERS)
	$(CC) $(CPPFLAGS) $(CFLAGS) $< $(LIBRARY) -o $@

build/test_protocol: tests/test_protocol.c $(LIBRARY) $(HEADERS)
	$(CC) $(CPPFLAGS) $(CFLAGS) $< $(LIBRARY) -o $@

build/test_protocol_reply: tests/test_protocol_reply.c $(LIBRARY) $(HEADERS)
	$(CC) $(CPPFLAGS) $(CFLAGS) $< $(LIBRARY) -o $@

test: all
	./build/test_config
	./build/test_protocol
	./build/test_protocol_reply

clean:
	rm -rf build
