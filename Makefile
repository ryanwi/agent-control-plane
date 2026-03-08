PYTHON ?= uv run python
PYTEST ?= uv run pytest
RUFF ?= uv run ruff
MYPY ?= uv run mypy

.DEFAULT_GOAL := help

.PHONY: help sync docs-drift openapi-check test lint format typecheck check release-tag

help:
	@printf "Targets:\n"
	@printf "  make sync       - install/update dependencies (uv sync --extra dev)\n"
	@printf "  make docs-drift - verify AGENTS.md / CLAUDE.md / GEMINI.md stay aligned\n"
	@printf "  make openapi-check - validate OpenAPI contract files\n"
	@printf "  make test       - run test suite\n"
	@printf "  make lint       - run ruff checks\n"
	@printf "  make format     - run ruff formatter\n"
	@printf "  make typecheck  - run mypy\n"
	@printf "  make check      - run lint + typecheck + test\n"
	@printf "  make release-tag VERSION=x.y.z - verify + tag + push release\n"

sync:
	uv sync --extra dev

docs-drift:
	bash scripts/docs_drift_check.sh

openapi-check:
	uv run --extra dev python scripts/validate_openapi.py

test:
	$(PYTEST) -q

lint:
	$(RUFF) check src tests examples

format:
	$(RUFF) format src tests examples

typecheck:
	$(MYPY) src

check: docs-drift openapi-check lint typecheck test

release-tag:
	@if [ -z "$(VERSION)" ]; then \
		echo "VERSION is required. Example: make release-tag VERSION=0.9.4"; \
		exit 1; \
	fi
	$(PYTHON) scripts/release_tag.py --version "$(VERSION)"
