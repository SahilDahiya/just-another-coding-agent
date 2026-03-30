# just-another-coding-agent

read_when: you need the repo overview, scope, or setup commands

Python-native coding-agent backend built around PydanticAI, with a thin first-party Go terminal UI.

This repo preserves the backend product shape of pi's coding agent while rebuilding it as a clean Python implementation around PydanticAI. It does not inherit pi-mono's monorepo layout, extension ecosystem, or migration burden.

It is intentionally narrow: coding-agent backend first, a thin first-party terminal UI, strict contracts, no fallbacks, and no compatibility glue. PydanticAI should provide as much of the agent machinery as possible; local code exists to define and enforce the coding-agent product contract.

## Scope

- Headless coding-agent runtime
- Thin first-party terminal UI built on the same runtime
- File and shell tools
- Streaming run events
- Session persistence
- JSON-over-stdio RPC for non-Python consumers

## Non-goals

- General-purpose agent framework work
- Backward compatibility layers
- Legacy migration shims

## Project Layout

- `cmd/jaca/` - Go TUI entrypoint
- `internal/jaca/` - Go TUI client, rendering, config, and RPC bridge
- `src/just_another_coding_agent/` - canonical Python package
- `src/just_another_coding_agent/runtime/` - runtime and orchestration entrypoints
- `src/just_another_coding_agent/tools/` - coding tools
- `src/just_another_coding_agent/session/` - session persistence
- `src/just_another_coding_agent/rpc/` - RPC transport
- `src/just_another_coding_agent/contracts/` - public contract helpers and schemas
- `tests/` - unit tests first, e2e later
- `docs/` - scope, architecture, contracts, ADRs, development

## Setup

```bash
uv sync --extra dev --extra test
uv run ruff check .
uv run pytest
```

That default `uv sync --extra dev --extra test` path is for the Python backend,
Harbor, and headless evaluation flows. It stays Go-free.

If you want the interactive TUI too, rebuild the package explicitly with Go
enabled:

```bash
JACA_BUILD_TUI=1 uv sync --reinstall-package just-another-coding-agent --extra dev --extra test
```

## Run

Launch the long-lived stdio RPC server with explicit backend configuration:

```bash
uv run just-another-coding-agent \
  --model <provider:model> \
  --workspace-root /abs/path/to/workspace \
  --sessions-root /abs/path/to/sessions
```

The process reads one JSON RPC request per stdin line and writes one or more JSON lines to stdout.

Launch the first-party terminal UI:

```bash
uv run jaca
```

The interactive launcher talks to the Python backend over stdio RPC.

In a live repo checkout, `uv run jaca` prefers `go run ./cmd/jaca` when `go`
is available so the TUI always reflects current source.

Outside a repo checkout, `uv run jaca` launches the installed `jaca-go` binary.

If `uv run jaca` says the Go TUI binary is missing, rebuild the environment with:

```bash
JACA_BUILD_TUI=1 uv sync --reinstall-package just-another-coding-agent --extra dev --extra test
```

## First Run

The TUI keeps provider, model, and trace preferences in `~/.jaca/config.json`.

Inside `uv run jaca`:

- `/provider ollama` selects local Ollama and requires no key
- `/provider openai` selects OpenAI and starts masked auth if `OPENAI_API_KEY` is missing
- `/provider anthropic` selects Anthropic and starts masked auth if `ANTHROPIC_API_KEY` is missing
- `/auth openai` or `/auth anthropic` stores credentials without echoing the secret into the transcript
- `/model <provider:model>` switches the active model and aligns provider state to that model
- `/trace off` disables tracing
- `/trace local` stores spans locally under `~/.jaca/traces/`
- `/trace logfire` exports spans to Logfire

Tracing is off by default. Local and Logfire tracing both require the optional
trace dependency:

```bash
uv sync --extra trace
```

For `logfire` mode, authenticate first:

```bash
uv run logfire auth
uv run logfire projects use <project>
```

For direct Go TUI development, pass the backend command explicitly:

```bash
go run ./cmd/jaca \
  --backend-command-json='["uv","run","python","-m","just_another_coding_agent"]'
```

## Docs

- `docs/README.md`
- `docs/goal.md`
- `docs/tui.md`
- `docs/architecture.md`
- `docs/contracts.md`
- `docs/grounding.md`
- `docs/development.md`
- `docs/adr/`
