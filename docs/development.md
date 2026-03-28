# Development

read_when: you need environment setup, commands, CI, or test workflow

## Runtime

- Python `3.12`
- Go `1.23`
- `uv` for environment management
- `ruff` for linting
- `pytest` for tests

## Commands

- Install dependencies: `uv sync --extra dev --extra test`
- Install the Go TUI binary explicitly: `JACA_BUILD_TUI=1 uv sync --reinstall-package just-another-coding-agent --extra dev --extra test`
- Lint: `uv run ruff check .`
- Format: `uv run ruff format .`
- Test: `uv run pytest`
- Test Go packages: `go test ./...`
- Run the canonical interactive launcher: `uv run jaca`
- Run the Python headless backend directly: `uv run just-another-coding-agent --headless`
- Run the Go TUI client directly: `go run ./cmd/jaca --backend-command-json='["uv","run","python","-m","just_another_coding_agent"]'`

## Go TUI

The first-party TUI is now implemented in Go as a thin client over the
canonical Python headless backend.

- The Go entrypoint is `cmd/jaca`
- The Go client packages live under `internal/jaca/`
- The default Python install path stays Go-free so Harbor and headless evaluation installs still work
- `JACA_BUILD_TUI=1 uv sync --reinstall-package just-another-coding-agent --extra dev --extra test` builds and installs the platform-native `jaca-go` binary for the current environment
- `uv run jaca` launches that installed binary through the Python console-script entrypoint
- The Go client requires an explicit backend command and the canonical launcher passes `["<python>", "-m", "just_another_coding_agent"]`
- The Go client launches the Python backend over stdio RPC with `--headless`
- Corrupt `~/.jaca/config.json` now fails fast at startup instead of being ignored
- `ctrl+c` during an active Go TUI run is warning-only today; it does not claim backend cancellation, and a second `ctrl+c` quits the UI

## Environment

- Copy `.env.example` to `.env` if you need local provider credentials.
- Current foundation expects:
  - `OPENAI_API_KEY`
  - `ANTHROPIC_API_KEY`
- Common optional runtime env vars:
  - `OPENAI_BASE_URL`
  - `OLLAMA_BASE_URL`
  - `OLLAMA_API_KEY`
  - `JACA_TRACE=1` to wrap resolved models with opt-in OpenTelemetry instrumentation and emit canonical run/tool spans for watchdog analysis

## CI

The initial CI contract is:

- install project dependencies
- run `ruff check`
- run `pytest`

## Test Strategy

- Unit tests first
- Contract tests before implementation-detail tests
- E2E tests after the unit-contract foundation exists
