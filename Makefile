PYTHON ?= python3

.PHONY: all check test

all: check

check:
	PYTHONPYCACHEPREFIX="$(CURDIR)/.pycache" $(PYTHON) -m compileall -q peer_download

test:
	PYTHONPYCACHEPREFIX="$(CURDIR)/.pycache" $(PYTHON) -m unittest discover -s tests -v
