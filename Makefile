.PHONY: setup lint lint-python lint-go format test

STATICCHECK_VERSION := v0.6.0

setup:
	uv sync --extra dev --extra test

lint:
	$(MAKE) lint-python
	$(MAKE) lint-go

lint-python:
	uv run ruff check .
	uv run vulture src evaluations --min-confidence 80

lint-go:
	go run honnef.co/go/tools/cmd/staticcheck@$(STATICCHECK_VERSION) ./...

format:
	uv run ruff format .

test:
	uv run pytest
