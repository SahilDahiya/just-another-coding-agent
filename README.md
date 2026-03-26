# just-another-coding-agent

read_when: you need the repo overview, scope, or setup commands

Python-native, headless coding-agent backend built around PydanticAI.

This repo preserves the backend product shape of pi's coding agent while rebuilding it as a clean Python implementation around PydanticAI. It does not inherit pi-mono's monorepo layout, UI layers, extension ecosystem, or migration burden.

It is intentionally narrow: coding-agent backend, strict contracts, no fallbacks, no compatibility glue, no UI. PydanticAI should provide as much of the agent machinery as possible; local code exists to define and enforce the coding-agent product contract.

## Scope

- Headless coding-agent runtime
- File and shell tools
- Streaming run events
- Session persistence
- JSON-over-stdio RPC for non-Python consumers

## Non-goals

- UI of any kind
- General-purpose agent framework work
- Backward compatibility layers
- Legacy migration shims

## Project Layout

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

## Run

Launch the long-lived stdio RPC server with explicit backend configuration:

```bash
uv run just-another-coding-agent \
  --model <provider:model> \
  --workspace-root /abs/path/to/workspace \
  --sessions-root /abs/path/to/sessions
```

The process reads one JSON RPC request per stdin line and writes one or more JSON lines to stdout.

## Docs

- `docs/README.md`
- `docs/goal.md`
- `docs/architecture.md`
- `docs/contracts.md`
- `docs/grounding.md`
- `docs/development.md`
- `docs/adr/`
