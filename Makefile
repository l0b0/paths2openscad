MAKE = /usr/bin/make

NAME = $(notdir $(CURDIR))

PYTHON_VERSION = 2.7.8
VIRTUALENV_VERSION = 1.11.6

.PHONY: all
all: test

.PHONY: test
test: virtualenv
	. virtualenv/bin/activate && \
		pip install --requirement python-test-requirements.txt && \
		$(MAKE) METHOD=git python-pep8

.PHONY: clean
clean: clean-build clean-download clean-virtualenv

.PHONY: clean-build
clean-build:
	$(RM) -r build

.PHONY: clean-download
clean-download:
	$(RM) -r download

.PHONY: clean-virtualenv
clean-virtualenv:
	$(RM) virtualenv
	$(RM) -r virtualenv-*/

include make-includes/python.mk
include make-includes/variables.mk
