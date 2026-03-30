# Harbor And Terminal Bench

read_when: you want to run this backend under Harbor locally or against selected Terminal Bench tasks

## Purpose

This document describes the supported Harbor adapter workflow for `just_another_coding_agent`.

The adapter path is intentionally thin:

- Harbor imports a custom installed agent from the root `evaluations` package
- that agent uploads the local repo source into the task container
- the container installs the backend package
- the adapter runs the one-shot wrapper `just-another-coding-agent-exec-prompt`
- the wrapper launches the backend in `--headless` stdio-RPC mode and talks to it through `session.create` and `run.start`
- the wrapper can also forward an optional explicit `thinking` setting into `run.start`

This is an adapter around the existing backend contract, not a second execution architecture.

The benchmark-specific workflow guidance lives in this adapter layer, not in the
repo-root `AGENTS.md`. The one-shot wrapper prepends a small benchmark workflow section
to the user prompt before `run.start`, so Terminal Bench behavior stays adapter-owned.

## Prerequisites

- `harbor` is installed locally
- the backend repo is available locally
- provider credentials are exported in the Harbor host process environment
- a Harbor-supported environment is available
  - local default is Docker
- the local source tree is importable by Harbor
  - easiest path: `PYTHONPATH=$PWD/src:$PWD`

For OpenAI-hosted runs:

```bash
export OPENAI_API_KEY=...
```

Optional:

```bash
export OPENAI_BASE_URL=...
export JUST_ANOTHER_CODING_AGENT_THINKING=high
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

If you want the Harbor adapter to forward an explicit thinking setting into the
one-shot wrapper and `run.start`, export:

```bash
export JUST_ANOTHER_CODING_AGENT_THINKING=high
```

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
PYTHONPATH=$PWD/src:$PWD harbor run \
  --path /abs/path/to/task \
  --agent-import-path evaluations.harbor.agent:JustAnotherCodingAgentHarborAgent \
  --model openai-responses:gpt-5.3-codex \
  --n-concurrent 1 \
  --job-name just-another-coding-agent-local-smoke \
  --artifact /logs/agent/just-another-coding-agent.txt \
  --artifact /tmp/just-another-coding-agent-sessions
```

What this does:

1. Harbor imports the custom installed agent from this repo.
2. The agent uploads `pyproject.toml`, `README.md`, `src/`, `evaluations/`, and a prebuilt `jaca-read-only-worker` helper into the task container.
3. The install script installs the backend package in the container without requiring a Go toolchain there; it points packaging at the uploaded prebuilt read-only worker explicitly.
4. The run command launches `just-another-coding-agent-exec-prompt`.
5. The wrapper creates a backend session, runs one prompt, prints terminal output, and exits non-zero on canonical run failure.

If you need to set thinking explicitly when using the one-shot wrapper directly, use:

```bash
python -m evaluations.bench.exec_prompt \
  --model openai-responses:gpt-5.3-codex \
  --thinking high \
  -C /abs/path/to/workspace \
  "solve it"
```

## Terminal Bench Run

Use this to run against one selected Terminal Bench task:

```bash
PYTHONPATH=$PWD/src:$PWD harbor run \
  --dataset terminal-bench@2.0 \
  --task-name <task-name> \
  --agent-import-path evaluations.harbor.agent:JustAnotherCodingAgentHarborAgent \
  --model openai-responses:gpt-5.3-codex \
  --n-concurrent 1 \
  --job-name just-another-coding-agent-<task-name> \
  --artifact /logs/agent/just-another-coding-agent.txt \
  --artifact /tmp/just-another-coding-agent-sessions
```

For Ollama Cloud, swap the model string and ensure `OLLAMA_BASE_URL` plus `OLLAMA_API_KEY` are exported in the Harbor host process:

```bash
PYTHONPATH=$PWD/src:$PWD harbor run \
  --dataset terminal-bench@2.0 \
  --task-name <task-name> \
  --agent-import-path evaluations.harbor.agent:JustAnotherCodingAgentHarborAgent \
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
- export `JUST_ANOTHER_CODING_AGENT_THINKING=high` when you want submission-style `thinking=high` runs through the checked-in Harbor adapter
- use downloaded session artifacts when you need to inspect a failed run

## Simple Harness

For day-to-day use, prefer the short harness:

```bash
evaluations/scripts/tb2_glm5.sh
```

It wraps the longer launchers and gives you two commands:

```bash
# Full-dataset run/status.
evaluations/scripts/tb2_glm5.sh run <submission-id>
evaluations/scripts/tb2_glm5.sh status <submission-id>

# One or more slices from task files.
evaluations/scripts/tb2_glm5.sh run <submission-id> tasks/a.txt
evaluations/scripts/tb2_glm5.sh status <submission-id> tasks/a.txt
evaluations/scripts/tb2_glm5.sh run <submission-id> tasks/a.txt tasks/b.txt tasks/c.txt

# Optional number of passes to run in one invocation.
evaluations/scripts/tb2_glm5.sh run <submission-id> --passes 2 tasks/a.txt
```

What it does:

- with no task files, it delegates to the full-dataset launcher
- with one or more task files, it delegates to the slice launcher for each file
- `status` never starts Harbor
- `run` starts Harbor and records only completed jobs

Examples:

```bash
# Run the next full pass.
evaluations/scripts/tb2_glm5.sh run glm5-high

# Check full-bundle status.
evaluations/scripts/tb2_glm5.sh status glm5-high

# Run one pass for three fixed slices.
evaluations/scripts/tb2_glm5.sh run glm5-high tasks/a.txt tasks/b.txt tasks/c.txt

# Check one slice.
evaluations/scripts/tb2_glm5.sh status glm5-high tasks/a.txt
```

Starter slice files can live at:

- `tasks/a.txt`
- `tasks/b.txt`
- `tasks/c.txt`

## Full Submission Run

For the full Terminal Bench 2.0 submission-style GLM-5 run, use the checked-in
launcher:

```bash
evaluations/scripts/run_tb2_submission_glm5.sh
```

What it does:

- loads `.env` if present
- defaults to `ollama:glm-5:cloud`
- defaults to `JUST_ANOTHER_CODING_AGENT_THINKING=high`
- treats a submission as a bundle of intact Harbor jobs, one trial-per-task pass
  per job
- by default runs `1` pass per invocation, with `--n-attempts 1`
- records only completed pass jobs under a submission bundle manifest
- writes Harbor jobs under `jobs/`
- writes the submission bundle state under `jobs/submission-bundles/`

This is deliberately not in-place resume. If you stop a run mid-pass, that
partial Harbor job is left on disk but is not recorded in the bundle. Rerunning
the launcher starts the next needed pass from the last completed recorded pass.

Useful knobs:

```bash
# Show bundle status without starting Harbor.
ACTION=status evaluations/scripts/run_tb2_submission_glm5.sh

# Use a stable bundle id across reruns.
SUBMISSION_ID=glm5-high evaluations/scripts/run_tb2_submission_glm5.sh

# Run two completed passes in one invocation.
PASSES_PER_RUN=2 evaluations/scripts/run_tb2_submission_glm5.sh

# Change the target number of trials per task.
TARGET_TRIALS=5 evaluations/scripts/run_tb2_submission_glm5.sh
```

Submission guidance:

- only submit the completed Harbor job directories recorded in the bundle
  manifest
- do not splice trials from different Harbor jobs into one job directory
- interrupted current-pass jobs are for local analysis only unless you
  explicitly decide to package them separately after validating they satisfy the
  official submission rules

## Sliced Submission Run

If you want to submit in batches, use the slice launcher:

```bash
TASK_FILE=tasks/slice-a.txt SUBMISSION_ID=glm5-high \
evaluations/scripts/run_tb2_submission_glm5_slice.sh
```

The task file must be newline-delimited:

```text
fix-git
regex-log
log-summary-date-ranges
```

What the slice launcher does:

- treats one slice as a fixed task list
- runs one Harbor pass per invocation by default, with `--n-attempts 1`
- records only completed pass jobs for that slice
- resumes from the last completed pass when rerun with the same
  `SUBMISSION_ID` and `TASK_FILE`
- stores slice bundle state under
  `jobs/submission-bundles/<submission-id>/slices/<slice-name>/`

Recommended batching pattern:

- split the dataset into fixed slices once, for example `a.txt`, `b.txt`, `c.txt`
- keep slice membership stable for the whole submission campaign
- run pass 1 for each slice, then pass 2 for each slice, and so on until pass 5

Useful commands:

```bash
# Show status for one slice.
ACTION=status TASK_FILE=tasks/slice-a.txt SUBMISSION_ID=glm5-high \
evaluations/scripts/run_tb2_submission_glm5_slice.sh

# Run two slice passes back-to-back.
PASSES_PER_RUN=2 TASK_FILE=tasks/slice-a.txt SUBMISSION_ID=glm5-high \
evaluations/scripts/run_tb2_submission_glm5_slice.sh

# Override the derived slice name if needed.
SLICE_NAME=first-50 TASK_FILE=tasks/slice-a.txt SUBMISSION_ID=glm5-high \
evaluations/scripts/run_tb2_submission_glm5_slice.sh
```

Submission guidance for slices:

- only submit the intact Harbor job directories recorded in each slice manifest
- do not splice trials from different Harbor jobs into one job directory
- to reach the leaderboard minimum, each task still needs `5` trials overall
- the official submission repo accepts a job or folder of jobs, so separate
  intact slice jobs are valid as long as you keep them intact

Expected prerequisites before you launch it:

- `OLLAMA_API_KEY` is exported or present in `.env`
- `docker login` has already been run, to avoid image pull-rate limiting during
  Harbor task setup

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
  - `exec-prompt-phases.json` with wrapper-side phase timestamps
  - `exec-prompt-rpc-transcript.jsonl` with the raw stdio RPC exchange

Important diagnostic note:

- session JSONL now appends `session_run` and `session_event` lines as the run
  streams, and appends `session_messages` only after terminal completion
- cancellation that unwinds through the session coordinator now finalizes as
  terminal `run_failed`, but crashes or external termination before
  finalization can still leave an incomplete trailing run on disk, and
  authoritative `load_session(...)` will fail hard instead of silently hiding it
- for timeout investigations, check `exec-prompt-phases.json` and
  `exec-prompt-rpc-transcript.jsonl` before assuming the backend never started

If you do not request `/tmp/just-another-coding-agent-sessions` as a Harbor artifact, those session files remain container-local and are discarded with the environment.

## Troubleshooting

- `ModuleNotFoundError` for `evaluations`
  - run Harbor with `PYTHONPATH=$PWD/src:$PWD`, or install the repo into the same Python environment Harbor uses
- backend model fails with OpenAI chat-completions errors
  - use `openai-responses:gpt-5.3-codex`, not `openai:gpt-5.3-codex`
- provider auth missing in the container
  - export `OPENAI_API_KEY` in the Harbor host process before `harbor run`
- missing session artifacts after a run
  - add `--artifact /tmp/just-another-coding-agent-sessions`
- timed out run shows only a session header
  - inspect `exec-prompt-phases.json` and `exec-prompt-rpc-transcript.jsonl`
    first; the run may have progressed into a long blocking tool call before
    Harbor timed it out
