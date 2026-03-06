PYTHON ?= uv run python
PYTEST ?= uv run pytest
RUFF ?= uv run ruff
MYPY ?= uv run mypy

.DEFAULT_GOAL := help

.PHONY: help sync docs-drift test lint format typecheck check

help:
	@printf "Targets:\n"
	@printf "  make sync       - install/update dependencies (uv sync --extra dev)\n"
	@printf "  make docs-drift - verify AGENTS.md / CLAUDE.md / GEMINI.md stay aligned\n"
	@printf "  make test       - run test suite\n"
	@printf "  make lint       - run ruff checks\n"
	@printf "  make format     - run ruff formatter\n"
	@printf "  make typecheck  - run mypy\n"
	@printf "  make check      - run lint + typecheck + test\n"

sync:
	uv sync --extra dev

docs-drift:
	bash scripts/docs_drift_check.sh

test:
	$(PYTEST) -q

lint:
	$(RUFF) check src tests examples

format:
	$(RUFF) format src tests examples

typecheck:
	$(MYPY) src

check: docs-drift lint typecheck test
