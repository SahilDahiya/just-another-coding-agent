# Development

read_when: you need environment setup, commands, CI, or test workflow

## Runtime

- Python `3.12`
- `uv` for environment management
- `ruff` for linting
- `pytest` for tests

## Commands

- Install dependencies: `uv sync --extra dev --extra test`
- Lint: `uv run ruff check .`
- Format: `uv run ruff format .`
- Test: `uv run pytest`

## Environment

- Copy `.env.example` to `.env` if you need local provider credentials.
- Current foundation expects:
  - `OPENAI_API_KEY`
  - `ANTHROPIC_API_KEY`
- Common optional runtime env vars:
  - `OPENAI_BASE_URL`
  - `OLLAMA_BASE_URL`
  - `OLLAMA_API_KEY`
  - `JACA_TRACE=1` to wrap resolved models with opt-in OpenTelemetry instrumentation

## CI

The initial CI contract is:

- install project dependencies
- run `ruff check`
- run `pytest`

## Test Strategy

- Unit tests first
- Contract tests before implementation-detail tests
- E2E tests after the unit-contract foundation exists
