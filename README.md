# just-another-coding-agent

read_when: you need the repo overview, scope, or setup commands

Terminal coding agent with a PydanticAI backend and a first-party Go TUI.

This repo preserves the product shape of pi's coding agent while rebuilding it as a clean Python implementation around PydanticAI. It does not inherit pi-mono's monorepo layout, extension ecosystem, or migration burden.

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

## Install

For normal use outside a repo checkout, prefer one of these published-package
paths:

```bash
uv tool install just-another-coding-agent
jaca
```

```bash
uvx --from just-another-coding-agent jaca
```

If you already use `pipx`, that isolated tool path works too:

```bash
pipx install just-another-coding-agent
```

- `uv tool install` is the persistent daily-use path
- `uvx` is the ephemeral no-install path
- published wheels already bundle `jaca-go` and `jaca-read-only-worker`, so
  normal installs do not require a local Go toolchain
- installed builds update explicitly with:

```bash
uv tool upgrade just-another-coding-agent
```

JACA does not auto-upgrade or self-reinstall on startup.
Installed `uv tool` builds may show a pre-launch notice before the TUI starts
when a newer published version is available. That notice only prints the exact
upgrade command and lets you either continue into the app or quit and update
outside the running app.

## Repo Setup

```bash
uv sync --extra dev --extra test
uv run ruff check .
uv run vulture src evaluations --min-confidence 80
go run honnef.co/go/tools/cmd/staticcheck@v0.6.0 ./...
uv run pytest
```

Or run the combined lint pass with:

```bash
make lint
```

That default `uv sync --extra dev --extra test` path is for the Python backend,
Terminal Bench flows, and headless evaluation work. It builds the persistent
`jaca-read-only-worker`, but it does not build the packaged `jaca-go` binary.

If you want to exercise the packaged TUI binary from a repo checkout, rebuild
the package explicitly with Go enabled:

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

In a repo checkout, `uv run jaca` is the canonical development launcher. The
interactive launcher talks to the Python backend over stdio RPC.

In a live repo checkout, `uv run jaca` prefers the installed `jaca-go` binary
when it is present, and otherwise falls back to `go run ./cmd/jaca` when `go`
is available. That keeps normal development unblocked even if the packaged TUI
binary has not been rebuilt yet.

Outside a repo checkout, the installed `jaca` command launches the installed
`jaca-go` binary.

If `uv run jaca` says the Go TUI binary is missing and `go` is not available in
the repo checkout, rebuild the environment with:

```bash
JACA_BUILD_TUI=1 uv sync --reinstall-package just-another-coding-agent --extra dev --extra test
```

## First Run

The TUI keeps non-secret provider, model, and trace preferences in
`~/.jaca/config.json`.
Provider secrets are backend-owned and stored in the local OS keychain by
default. When keychain storage is unavailable, JACA stores them in
`~/.jaca/auth.json` instead and explains why in the auth panel.
Environment variables remain the explicit override for headless, CI, and
evaluation flows.
On Linux/WSL, interactive `/login` requires a supported OS keychain backend
such as Secret Service via `gnome-keyring`.

On first launch without a usable saved login lane, JACA opens a centered chooser
panel before chat. The supported login lanes are:

- ChatGPT subscription via `/login openai-codex`
- OpenAI API key via `/login openai`
- Anthropic API key via `/login anthropic`

If a saved cloud-provider selection is still missing credentials, JACA starts
masked auth immediately at startup instead of waiting for the first
`/login` or `/model` command.
When auth starts, JACA opens a centered secure setup panel: provider-specific
labeling, masked input, no transcript/history capture for the secret, and
backend-owned storage on save.
On first run, the prompt footer also tells the user to press `Tab` to choose a
login lane or model directly from the prompt zone.

Inside `jaca`:

- `/login openai-codex` starts ChatGPT subscription login
- `/login openai` and `/login anthropic` prepare `~/.jaca/auth.json` if needed
  and show the exact JSON snippet to paste
- `/login status` shows whether each login lane is ready and where the current
  secret came from
- `/login clear <provider>` removes the stored local secret for that provider
- `/model` shows runnable models first, marks ready rows with a check, and
  uses public-style labels such as `gpt-5.4 | api` and `gpt-5.4 | oauth`
- `/model <provider:model>` switches the active model and aligns provider state
  to that model
- `/name <text>` assigns a durable backend-normalized session name such as `auth-store-cleanup` and keeps it unique within the current workspace
- `/session` shows the current durable session name, opaque session id, and any direct fork parent
- `/trace off` disables tracing
- `/trace local` stores spans locally under `~/.jaca/traces/`
- `/trace logfire` exports spans to Logfire

To continue a named or known session later:

```bash
jaca resume auth-store-cleanup
```

If you omit the reference, `jaca resume` shows the recent sessions from the
current workspace, even when there is only one session, caps the picker to the
most recent ten, and lets you choose one by number. This picker requires an
interactive terminal. Resumed and forked sessions also hydrate a bounded
recent-history preview into the transcript instead of trying to render the
entire saved session.

To branch a current-workspace session into a new one:

```bash
jaca fork auth-store-cleanup --name auth-store-cleanup-followup
```

If you omit the reference, `jaca fork` uses the same current-workspace picker as
`jaca resume`. Forked sessions keep durable lineage to their direct parent and
start as a new session with copied history instead of mutating the original
thread.

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

If interactive login starts on a machine without a supported OS keychain
backend, JACA goes directly to the local secret file flow and explains that it
is doing so because keychain storage is unavailable.

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
