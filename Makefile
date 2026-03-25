.PHONY: setup lint format test

setup:
	uv sync --extra dev --extra test

lint:
	uv run ruff check .

format:
	uv run ruff format .

test:
	uv run pytest
