SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

PY ?= python3
export PYTHONPATH := tools

# actionlint is installed from its release tarball and checked against the
# hash recorded here, rather than run through a marketplace action. A tool
# whose job is to notice unpinned dependencies should not arrive as one.
ACTIONLINT_VERSION := 1.7.12
ACTIONLINT_SHA256  := 8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8
ACTIONLINT_URL     := https://github.com/rhysd/actionlint/releases/download/v$(ACTIONLINT_VERSION)/actionlint_$(ACTIONLINT_VERSION)_linux_amd64.tar.gz

# Upstream revisions the vendored schemas were taken from. `schemas-check`
# compares against exactly these; `schemas-refresh` moves them.
SCHEMASTORE_REF := cd8aa06c45eacd397835176606512d3401cd1a9f
GITLAB_REF      := v19.3.1-ee
SCHEMASTORE_RAW := https://raw.githubusercontent.com/SchemaStore/schemastore/$(SCHEMASTORE_REF)/src/schemas/json
GITLAB_RAW      := https://gitlab.com/gitlab-org/gitlab/-/raw/$(GITLAB_REF)/app/assets/javascripts/editor/schema/ci.json

TOOLS_DIR  := .tools
ACTIONLINT := $(TOOLS_DIR)/actionlint

.PHONY: help
help:
	@echo "demo             everything below, in the order a reviewer wants it"
	@echo "check            ci-lint over this repository"
	@echo "actionlint       actionlint over .github/workflows"
	@echo "lint             ruff, mypy and shellcheck"
	@echo "test             pytest with the coverage gate"
	@echo "rules            print the rule catalogue"
	@echo "reject           show the checker rejecting the fixtures and the before-examples"
	@echo "schemas-check    compare the vendored schemas with their pinned upstream"
	@echo "schemas-refresh  re-download them and update the pins by hand afterwards"
	@echo "clean            remove downloaded tools and test output"

.PHONY: demo
demo: check actionlint reject test
	@echo
	@echo "templates validated, invariants enforced, fixtures rejected, tests green"

.PHONY: check
check:
	$(PY) -m ci_lint check --root .

.PHONY: rules
rules:
	@$(PY) -m ci_lint rules

# Runs the checker over everything it is supposed to reject and reports
# success only when every one of them was in fact rejected. A linter is worth
# nothing until it has been shown to say no.
.PHONY: reject
reject:
	@failed=0; \
	for f in tests/fixtures/bad/workflows/*.yml tests/fixtures/bad/gitlab/*.yml \
	         tests/fixtures/bad/actions/*/action.yml examples/*/*.before.yml; do \
	  if $(PY) -m ci_lint check --root . "$$f" >/dev/null 2>&1; then \
	    echo "ACCEPTED (should not be): $$f"; failed=1; \
	  else \
	    echo "rejected: $$f"; \
	  fi; \
	done; \
	exit $$failed

$(ACTIONLINT):
	@mkdir -p $(TOOLS_DIR)
	curl -sSfL -o $(TOOLS_DIR)/actionlint.tar.gz "$(ACTIONLINT_URL)"
	echo "$(ACTIONLINT_SHA256)  $(TOOLS_DIR)/actionlint.tar.gz" | sha256sum -c -
	tar -xzf $(TOOLS_DIR)/actionlint.tar.gz -C $(TOOLS_DIR) actionlint
	rm -f $(TOOLS_DIR)/actionlint.tar.gz

.PHONY: actionlint
actionlint: $(ACTIONLINT)
	$(ACTIONLINT) -color .github/workflows/*.yml

.PHONY: lint
lint:
	ruff check .
	ruff format --check .
	mypy
	shellcheck .github/actions/semver-next/next.sh

.PHONY: test
test:
	$(PY) -m pytest --cov --cov-report=term-missing

.PHONY: schemas-check
schemas-check:
	@tmp=$$(mktemp -d); \
	curl -sSfL -o "$$tmp/github-workflow.json" "$(SCHEMASTORE_RAW)/github-workflow.json"; \
	curl -sSfL -o "$$tmp/github-action.json"   "$(SCHEMASTORE_RAW)/github-action.json"; \
	curl -sSfL -o "$$tmp/gitlab-ci.json"       "$(GITLAB_RAW)"; \
	status=0; \
	for f in github-workflow.json github-action.json gitlab-ci.json; do \
	  if cmp -s "schemas/$$f" "$$tmp/$$f"; then echo "unchanged: $$f"; \
	  else echo "DIFFERS from the pinned upstream: $$f"; status=1; fi; \
	done; \
	rm -rf "$$tmp"; exit $$status

.PHONY: schemas-refresh
schemas-refresh:
	curl -sSfL -o schemas/github-workflow.json "$(SCHEMASTORE_RAW)/github-workflow.json"
	curl -sSfL -o schemas/github-action.json   "$(SCHEMASTORE_RAW)/github-action.json"
	curl -sSfL -o schemas/gitlab-ci.json       "$(GITLAB_RAW)"
	@echo "refreshed at the pins in this Makefile; move SCHEMASTORE_REF/GITLAB_REF first to take newer ones"
	@echo "then update schemas/SOURCES.md with the new revisions and hashes"

.PHONY: clean
clean:
	rm -rf $(TOOLS_DIR) .pytest_cache .mypy_cache .ruff_cache coverage.xml .coverage
	rm -rf tools/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
