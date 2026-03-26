# pi-coding-agent

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

- `src/pi_code_agent/` - canonical Python package
- `src/pi_code_agent/runtime/` - runtime and orchestration entrypoints
- `src/pi_code_agent/tools/` - coding tools
- `src/pi_code_agent/session/` - session persistence
- `src/pi_code_agent/rpc/` - RPC transport
- `src/pi_code_agent/contracts/` - public contract helpers and schemas
- `tests/` - unit tests first, e2e later
- `docs/` - scope, architecture, contracts, ADRs, development

## Setup

```bash
uv sync --extra dev --extra test
uv run ruff check .
uv run pytest
```

## Docs

- `docs/README.md`
- `docs/goal.md`
- `docs/architecture.md`
- `docs/contracts.md`
- `docs/grounding.md`
- `docs/development.md`
- `docs/adr/`
