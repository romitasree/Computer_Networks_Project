# Makefile for the tracker server (Part 1).
# Python needs no compilation: `make` checks the setup and creates the
# ./tracker launcher so the spec's  `make -f makefile` + `./tracker`  works.

PYTHON ?= python3

.PHONY: all check run fresh test demo clean help

all: check tracker
	@echo "Ready.  Start the tracker with:  ./tracker   (or: make run)"

check:
	@$(PYTHON) -c "import sys; sys.exit('Python 3.8+ is required') if sys.version_info < (3, 8) else None"
	@$(PYTHON) -c "import ast; ast.parse(open('tracker.py').read())"
	@echo "Python OK, tracker.py syntax OK"

# Launcher script (generated, not committed): ./tracker [options]
tracker:
	@printf '#!/bin/sh\nexec %s "$$(dirname "$$0")/tracker.py" "$$@"\n' '$(PYTHON)' > tracker
	@chmod +x tracker

run:
	$(PYTHON) tracker.py

fresh:
	$(PYTHON) tracker.py --fresh

test:
	$(PYTHON) test_tracker.py

demo:
	$(PYTHON) demo_multithread.py

clean:
	rm -rf __pycache__ tracker torrents

help:
	@echo "make          check setup and create ./tracker"
	@echo "make run      start the tracker (reads ./sconfig)"
	@echo "make fresh    start the tracker after deleting old *.track files"
	@echo "make test     run the automated tests"
	@echo "make demo     run the multi-threading demo (tracker must be running)"
	@echo "make clean    remove generated files"
