# Harbor And Terminal Bench

read_when: you want to run this backend under Harbor locally or against selected Terminal Bench tasks

## Purpose

This document describes the supported Harbor adapter workflow for `just_another_coding_agent`.

The adapter path is intentionally thin:

- Harbor imports a custom installed agent from `just_another_coding_agent_adapters`
- that agent uploads the local repo source into the task container
- the container installs the backend package
- the adapter runs the one-shot wrapper `just-another-coding-agent-exec-prompt`
- the wrapper talks to the canonical stdio backend through `session.create` and `run.start`

This is an adapter around the existing backend contract, not a second execution architecture.

## Prerequisites

- `harbor` is installed locally
- the backend repo is available locally
- provider credentials are exported in the Harbor host process environment
- a Harbor-supported environment is available
  - local default is Docker
- the local source tree is importable by Harbor
  - easiest path: `PYTHONPATH=$PWD/src`

For OpenAI-hosted runs:

```bash
export OPENAI_API_KEY=...
```

Optional:

```bash
export OPENAI_BASE_URL=...
```

For Ollama-backed runs through PydanticAI's `ollama:` provider:

```bash
export OLLAMA_BASE_URL=https://ollama.com/v1
export OLLAMA_API_KEY=...
```

If you are using a self-hosted Ollama server instead of Ollama Cloud, the base URL must be reachable from inside the Harbor task container. `http://localhost:11434/v1` will not work from a Docker-isolated benchmark container unless that `localhost` is inside the same container.

## Canonical Model String

Use the exact backend model string that PydanticAI expects.

For the Codex model currently validated in this repo:

```text
openai-responses:gpt-5.3-codex
```

Do not rewrite this into Harbor-style provider/model syntax. The adapter passes the string through unchanged to the backend.

For Ollama Cloud, use the exact Ollama provider model string, for example:

```text
ollama:kimi-k2:1t-cloud
```

The adapter still passes that string through unchanged.

## Container Paths

Current adapter behavior inside the task container:

- workspace root: `.` relative to the task working directory
- sessions root: `/tmp/just-another-coding-agent-sessions`
- adapter log stream: `/logs/agent/just-another-coding-agent.txt`

Important implications:

- the workspace path is container-local and server-side only
- sessions are ephemeral unless you explicitly download them as Harbor artifacts

## Local Harbor Run

Use this to run against one local Harbor task or task directory:

```bash
PYTHONPATH=$PWD/src harbor run \
  --path /abs/path/to/task \
  --agent-import-path just_another_coding_agent_adapters.harbor.agent:JustAnotherCodingAgentHarborAgent \
  --model openai-responses:gpt-5.3-codex \
  --n-concurrent 1 \
  --job-name just-another-coding-agent-local-smoke \
  --artifact /logs/agent/just-another-coding-agent.txt \
  --artifact /tmp/just-another-coding-agent-sessions
```

What this does:

1. Harbor imports the custom installed agent from this repo.
2. The agent uploads `pyproject.toml`, `README.md`, and `src/` into the task container.
3. The install script installs the backend package in the container.
4. The run command launches `just-another-coding-agent-exec-prompt`.
5. The wrapper creates a backend session, runs one prompt, prints terminal output, and exits non-zero on canonical run failure.

## Terminal Bench Run

Use this to run against one selected Terminal Bench task:

```bash
PYTHONPATH=$PWD/src harbor run \
  --dataset terminal-bench@2.0 \
  --task-name <task-name> \
  --agent-import-path just_another_coding_agent_adapters.harbor.agent:JustAnotherCodingAgentHarborAgent \
  --model openai-responses:gpt-5.3-codex \
  --n-concurrent 1 \
  --job-name just-another-coding-agent-<task-name> \
  --artifact /logs/agent/just-another-coding-agent.txt \
  --artifact /tmp/just-another-coding-agent-sessions
```

For Ollama Cloud, swap the model string and ensure `OLLAMA_BASE_URL` plus `OLLAMA_API_KEY` are exported in the Harbor host process:

```bash
PYTHONPATH=$PWD/src harbor run \
  --dataset terminal-bench@2.0 \
  --task-name <task-name> \
  --agent-import-path just_another_coding_agent_adapters.harbor.agent:JustAnotherCodingAgentHarborAgent \
  --model 'ollama:kimi-k2:1t-cloud' \
  --n-concurrent 1 \
  --job-name just-another-coding-agent-<task-name> \
  --artifact /logs/agent/just-another-coding-agent.txt \
  --artifact /tmp/just-another-coding-agent-sessions
```

Notes:

- start with one task at a time
- keep `--n-concurrent 1` for first smoke runs
- keep the backend model string unchanged
- use downloaded session artifacts when you need to inspect a failed run

## Expected Artifacts

Harbor job output goes to the configured jobs directory, which defaults to:

```text
jobs/
```

Useful artifacts for this adapter path:

- `/logs/agent/just-another-coding-agent.txt`
  - combined one-shot wrapper output from inside the container
- `/tmp/just-another-coding-agent-sessions`
  - backend session JSONL files for the run

If you do not request `/tmp/just-another-coding-agent-sessions` as a Harbor artifact, those session files remain container-local and are discarded with the environment.

## Troubleshooting

- `ModuleNotFoundError` for `just_another_coding_agent_adapters`
  - run Harbor with `PYTHONPATH=$PWD/src`, or install the repo into the same Python environment Harbor uses
- backend model fails with OpenAI chat-completions errors
  - use `openai-responses:gpt-5.3-codex`, not `openai:gpt-5.3-codex`
- provider auth missing in the container
  - export `OPENAI_API_KEY` in the Harbor host process before `harbor run`
- missing session artifacts after a run
  - add `--artifact /tmp/just-another-coding-agent-sessions`
