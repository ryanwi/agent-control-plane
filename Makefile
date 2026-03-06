PYTHON ?= uv run python
PYTEST ?= uv run pytest
RUFF ?= uv run ruff
MYPY ?= uv run mypy

.PHONY: sync test lint format typecheck check

sync:
	uv sync --extra dev

test:
	$(PYTEST) -q

lint:
	$(RUFF) check src tests

format:
	$(RUFF) format src tests

typecheck:
	$(MYPY) src

check: lint typecheck test
