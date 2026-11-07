PYTHON ?= python3
.PHONY: all test test-team
all: test
test:
	cd .. && $(PYTHON) -m unittest discover -s part4/tests -p 'test_*.py' -v
test-team:
	cd .. && $(PYTHON) -m part4.check_team
