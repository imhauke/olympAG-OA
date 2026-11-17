.PHONY: install lint format typecheck test test-fast cov check samples samples-check eval

install:
	uv sync --locked

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy

test:
	uv run pytest -q

test-fast:
	uv run pytest -x -q tests/unit

cov:
	uv run pytest -q --cov --cov-report=term --cov-report=xml

# The same gate runs locally and in CI.
check: lint typecheck cov

samples:
	uv run python -m samples.generate

samples-check:
	uv run python -m samples.generate --check
	uv run pytest -q tests/test_samples.py

eval:
	uv run python -m evals.run $(EVAL_ARGS)
