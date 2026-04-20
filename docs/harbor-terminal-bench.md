# Harbor And Terminal Bench

read_when: you want to run this backend under Harbor locally or against selected Terminal Bench tasks

## Purpose

This document describes the supported Harbor adapter workflow for `just_another_coding_agent`.

The adapter path is intentionally thin:

- Harbor imports a custom installed agent from the root `evaluations` package
- that agent uploads the local repo source into the task container
- the container installs the backend package
- the adapter runs the one-shot wrapper `just-another-coding-agent-exec-prompt`
- the wrapper launches the backend in `--headless` stdio-RPC mode and talks to it through `workspace.trust_accept`, `session.create`, `permission.set`, and `run.start`
- the wrapper can also forward an optional explicit `thinking` setting into `run.start`

This is an adapter around the existing backend contract, not a second execution architecture.

The benchmark-specific workflow guidance lives in this adapter layer, not in the
repo-root `AGENTS.md`. The one-shot wrapper prepends a small benchmark workflow section
to the user prompt before `run.start`, so Terminal Bench behavior stays adapter-owned.

For Harbor and Terminal Bench runs, the one-shot wrapper also bootstraps trust
and permissions before `run.start`:

- workspace trust: `workspace.trust_accept`

- sandbox policy: `danger_full_access`
- approval policy: `never`

This is deliberate. Harbor tasks are unattended benchmark runs, not interactive
operator sessions, so they must not block on approval prompts. The benchmark
adapter therefore accepts the workspace trust boundary up front, gives the
agent the full task-local access it needs, and disables approval for the
lifetime of that session. These overrides are adapter-owned and do not change
the canonical interactive default described in
[contracts.md](contracts.md).

After runs finish, aggregate analysis across repeated runs of the same
slice (pass-rate spread, per-task history, dashboard refresh) goes
through [tbench-slice-analysis.md](tbench-slice-analysis.md).

## Prerequisites

- `harbor` is installed locally
- the backend repo is available locally
- provider credentials are available on the Harbor host
  - OpenAI and Anthropic API-key lanes read from host env or `~/.jaca/auth.json`
  - ChatGPT OAuth lanes read from host `~/.jaca/oauth.json`
- Logfire credentials are available in the Harbor host process environment or host home directory
  - easiest path: `uv run logfire auth` and `uv run logfire projects use <project>`
  - explicit path: `export LOGFIRE_TOKEN=...`
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
export JACA_SESSION_AUTO_COMPACTION_CONTEXT_WINDOW_UTILIZATION=0.1
export JACA_HARBOR_SESSIONS_ROOT=/tmp/.jaca/harbor-sessions
export LOGFIRE_SERVICE_NAME=jaca-harbor
```

For ChatGPT subscription runs:

- log in interactively first with `/login openai-codex`
- Harbor will forward the current `openai-codex` OAuth credentials from the host into the task container for `openai-responses:* -chatgpt` models

Harbor tasks always export traces to Logfire. The adapter forces `JACA_TRACE_MODE=logfire` inside the task container and forwards a Logfire token from the Harbor host. By default, Harbor traces use `service.name=jaca-harbor`, which separates them from normal interactive chat traces that use the default backend service name. Logfire scrubbing is disabled for this path, so trace attributes are sent exactly as emitted by the wrapper and backend. The one-shot wrapper emits one explicit `jaca.exec_prompt` span per Harbor task with model, workspace, prompt hash, bounded prompt preview, session id, and terminal status metadata, then flushes Logfire before the task process exits so short-lived Harbor tasks are visible reliably.

The wrapper span is created from the active OpenTelemetry tracer, and the wrapper forwards W3C trace context into the headless backend process. A healthy Harbor trace should therefore include:

- `jaca.exec_prompt` for the one-shot wrapper lifetime
- `jaca.run` for the canonical backend run
- `jaca.model_request` for each model round-trip inside that run
- `jaca.tool` for each tool call started by the backend

For session-backed backend runs, the `jaca.run`, `jaca.model_request`, and
`jaca.tool` spans also carry `jaca.session_id`, which makes it possible to
group multiple traces that belong to the same durable session.

When Harbor metadata is present in the task environment, those same backend
spans also carry:

- `jaca.harbor.job_name`
- `jaca.harbor.submission_id`
- `jaca.harbor.slice_name`
- `jaca.harbor.task_name`

That makes it possible to filter child spans directly by Harbor labels instead
of only through the root `jaca.exec_prompt` span.

If you want a different Harbor-specific service name, set `LOGFIRE_SERVICE_NAME` in the Harbor host process before launching `harbor run`.

## Canonical Model String

Use the exact backend model string that PydanticAI expects.

For the OpenAI API-key Codex model currently validated in this repo:

```text
openai-responses:gpt-5.3-codex
```

For the ChatGPT subscription lane:

```text
openai-responses:gpt-5.4-chatgpt
```

Do not rewrite this into Harbor-style provider/model syntax. The adapter passes the string through unchanged to the backend.

If you want the Harbor adapter to forward an explicit thinking setting into the
one-shot wrapper and `run.start`, export:

```bash
export JUST_ANOTHER_CODING_AGENT_THINKING=high
```

## Container Paths

Current adapter behavior inside the task container:

- workspace root: `.` relative to the task working directory
- sessions root: `/tmp/.jaca/harbor-sessions`
- adapter log stream: `/logs/agent/just-another-coding-agent.txt`

Important implications:

- the workspace path is container-local and server-side only
- sessions are ephemeral unless you explicitly download them as Harbor artifacts
- `/logs/agent/just-another-coding-agent.txt` is a liveness stream, not the full transcript
  - it should show early `exec_prompt` status markers such as `subprocess started`, `session created`, `run.start sent`, and the first observed RPC/tool/assistant activity
  - the detailed canonical RPC transcript still lives under `/tmp/.jaca/harbor-sessions`

If you want a different container-local sessions root, export
`JACA_HARBOR_SESSIONS_ROOT=/abs/path/in/container` before `harbor run`. The
adapter rejects relative paths so it never creates the session bundle under the
task workspace by accident.

## Local Harbor Run

Use this to run against one local Harbor task or task directory:

```bash
PYTHONPATH=$PWD/src:$PWD harbor run \
  --path /abs/path/to/task \
  --agent-import-path evaluations.harbor.agent:JustAnotherCodingAgentHarborAgent \
  --model openai-responses:gpt-5.4-chatgpt \
  --n-concurrent 1 \
  --job-name just-another-coding-agent-local-smoke \
  --artifact /logs/agent/just-another-coding-agent.txt \
  --artifact /tmp/.jaca/harbor-sessions
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

## Local A/B

Use this when you want one small before/after experiment on the same prompt
without starting a full Harbor job:

```bash
TMP_ROOT="$(mktemp -d)"
PROMPT="Inspect docs/contracts.md and summarize the top-level tool surface."

uv run python -m evaluations.bench.exec_prompt \
  --model openai-responses:gpt-5.3-codex \
  --thinking high \
  --sessions-root "$TMP_ROOT/baseline" \
  -C "$PWD" \
  "$PROMPT"

MY_FEATURE_FLAG=on uv run python -m evaluations.bench.exec_prompt \
  --model openai-responses:gpt-5.3-codex \
  --thinking high \
  --sessions-root "$TMP_ROOT/candidate" \
  -C "$PWD" \
  "$PROMPT"

uv run python -m evaluations.bench.exec_prompt_report \
  --compare \
  "$TMP_ROOT/baseline" \
  "$TMP_ROOT/candidate"
```

What the report compares:

- terminal success or failure
- wrapper wall-clock time from `run.start` to the terminal event
- backend transcript elapsed time when the terminal event includes it
- tool-call count
- token usage from the terminal `run_succeeded` event

For Harbor-backed runs, download the `/tmp/.jaca/harbor-sessions` artifact from
each job and point `evaluations.bench.exec_prompt_report` at those extracted
session roots the same way.

For a more reusable local or Harbor-side feature-experiment workflow, see
[ab-testing-harness.md](ab-testing-harness.md).

## Local Prompt Slice A/B

Use this when you want a repeatable local prompt cohort instead of a one-off
A/B:

```bash
OUT_ROOT="$(mktemp -d)"

uv run python -m evaluations.bench.prompt_ab_harness \
  /tmp/example-slice.json \
  --model openai-responses:gpt-5.4-chatgpt \
  -C "$PWD" \
  --output-root "$OUT_ROOT" \
  --candidate-env MY_FEATURE_FLAG=on \
  --candidate-label feature-on
```

Artifacts:

- one folder per prompt under `--output-root`
- `baseline/` and candidate subfolders for each prompt
- `summary.json` at the output root with aggregate counts and per-prompt
  comparisons
- aggregate totals in `summary.json` for tool calls, wall clock, transcript
  elapsed time, and total tokens when available

Recommended use:

- start with one or two prompts
- use candidate env overrides only for the feature under test
- only grow to a Harbor slice after the local signal is directionally stable
- treat a single Harbor task as a probe, not a promotion decision
- after the probe looks good, freeze the prompt and run a user-selected
  multi-task holdout slice before claiming a win

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
  --artifact /tmp/.jaca/harbor-sessions
```

Notes:

- start with one task at a time
- keep `--n-concurrent 1` for first smoke runs
- keep the backend model string unchanged
- export `JUST_ANOTHER_CODING_AGENT_THINKING=high` when you want submission-style `thinking=high` runs through the checked-in Harbor adapter
- export `JACA_SESSION_AUTO_COMPACTION_CONTEXT_WINDOW_UTILIZATION=<value>` when you want to probe in-run compaction at a lower threshold without editing backend code
- use downloaded session artifacts when you need to inspect a failed run

## Simple Harness

For day-to-day use, prefer the short harness:

```bash
evaluations/scripts/tb2_submission.sh
```

It wraps the longer neutral launchers and gives you two commands:

```bash
# Full-dataset run/status.
MODEL=<model> evaluations/scripts/tb2_submission.sh run <submission-id>
MODEL=<model> evaluations/scripts/tb2_submission.sh status <submission-id>

# One or more slices from task files.
MODEL=<model> evaluations/scripts/tb2_submission.sh run <submission-id> tasks/a.txt
MODEL=<model> evaluations/scripts/tb2_submission.sh status <submission-id> tasks/a.txt
MODEL=<model> evaluations/scripts/tb2_submission.sh run <submission-id> tasks/a.txt tasks/b.txt tasks/c.txt

# Optional number of passes to run in one invocation.
MODEL=<model> evaluations/scripts/tb2_submission.sh run <submission-id> --passes 2 tasks/a.txt
```

What it does:

- with no task files, it delegates to the full-dataset launcher
- with one or more task files, it delegates to the slice launcher for each file
- `status` never starts Harbor
- `run` starts Harbor and records only completed jobs

Examples:

```bash
# Run the next full pass with an explicit model.
MODEL=openai-responses:gpt-5.4-chatgpt evaluations/scripts/tb2_submission.sh run chatgpt-high

# Check full-bundle status.
MODEL=openai-responses:gpt-5.4-chatgpt evaluations/scripts/tb2_submission.sh status chatgpt-high

# Run one pass for three fixed slices.
MODEL=openai-responses:gpt-5.4-chatgpt evaluations/scripts/tb2_submission.sh run chatgpt-high tasks/a.txt tasks/b.txt tasks/c.txt

# Check one slice.
MODEL=openai-responses:gpt-5.4-chatgpt evaluations/scripts/tb2_submission.sh status chatgpt-high tasks/a.txt
```

Model-specific convenience wrappers are also available:

- `evaluations/scripts/tb2_glm5.sh`
- `evaluations/scripts/tb2_gpt54_chatgpt.sh`

The repo ships starter slice files at:

- `tasks/a.txt`
- `tasks/b.txt`
- `tasks/c.txt`

For the dedicated GLM-5 submission lane, use:

```bash
evaluations/scripts/tb2_glm5.sh
```

It presets:

- `MODEL=ollama:glm-5:cloud`
- `JUST_ANOTHER_CODING_AGENT_THINKING=high`
- `SUBMISSION_ID=glm5-high`
- `N_CONCURRENT=5`

For the dedicated ChatGPT `gpt-5.4` submission lane, use:

```bash
evaluations/scripts/tb2_gpt54_chatgpt.sh
```

It presets:

- `MODEL=openai-responses:gpt-5.4-chatgpt`
- `JUST_ANOTHER_CODING_AGENT_THINKING=high`
- `SUBMISSION_ID=gpt54-chatgpt-high`
- `N_CONCURRENT=5`

Examples:

```bash
evaluations/scripts/tb2_gpt54_chatgpt.sh run gpt54-chatgpt-high
evaluations/scripts/tb2_gpt54_chatgpt.sh status gpt54-chatgpt-high
evaluations/scripts/tb2_gpt54_chatgpt.sh run gpt54-chatgpt-high --passes 1 tasks/b.txt
```

## Full Submission Run

For the full Terminal Bench 2.0 submission-style run, use the checked-in
generic launcher:

```bash
evaluations/scripts/run_tb2_submission.sh
```

What it does:

- loads `.env` if present
- requires `MODEL` to be set explicitly unless you use a model-specific wrapper
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

Before opening a leaderboard PR, build one final submission tree locally and
validate that tree completely. Do not populate a PR ref incrementally. The
validator bot runs on every PR update, so partial uploads create noisy failure
comments that do not help.

Canonical clean-submission flow:

1. finish all local Harbor passes and slice repairs
2. assemble one final submission tree under
   `submissions/terminal-bench/2.0/<agent>__<model>/`
3. run a final local tree validator against that assembled tree
4. only then upload and open the PR

Useful knobs:

```bash
# Show bundle status without starting Harbor.
MODEL=openai-responses:gpt-5.4-chatgpt ACTION=status evaluations/scripts/run_tb2_submission.sh

# Use a stable bundle id across reruns.
MODEL=openai-responses:gpt-5.4-chatgpt SUBMISSION_ID=chatgpt-high evaluations/scripts/run_tb2_submission.sh

# Run two completed passes in one invocation.
MODEL=openai-responses:gpt-5.4-chatgpt PASSES_PER_RUN=2 evaluations/scripts/run_tb2_submission.sh

# Change the target number of trials per task.
MODEL=openai-responses:gpt-5.4-chatgpt TARGET_TRIALS=5 evaluations/scripts/run_tb2_submission.sh
```

## Final Submission Preflight

Assemble one final local submission tree from completed bundle manifests:

```bash
python evaluations/scripts/build_tb2_submission_tree.py \
  /tmp/tb2-submission/submissions/terminal-bench/2.0/just-another-coding-agent__GLM-5 \
  --jobs-dir jobs \
  --bundle-dir jobs/submission-bundles/glm5-high \
  --agent-url https://github.com/SahilDahiya/just-another-coding-agent \
  --agent-display-name just-another-coding-agent \
  --agent-org-display-name "Sahil Dahiya" \
  --model-name glm-5 \
  --model-provider zhipu \
  --model-display-name "GLM 5" \
  --model-org-display-name "Zhipu"
```

Then validate the final assembled tree before any upload:

```bash
python evaluations/scripts/validate_tb2_submission_tree.py \
  /tmp/tb2-submission/submissions/terminal-bench/2.0/just-another-coding-agent__GLM-5 \
  --expected-unique-tasks 89 \
  --min-trials-per-task 5
```

What the final tree validator checks:

- `metadata.yaml` exists and contains the required leaderboard fields
- at least one Harbor job directory exists in the submission root
- every trial dir has a readable `result.json`
- every trial dir contains additional run artifacts
- every job uses `timeout_multiplier == 1.0`
- no forbidden timeout or resource overrides are present
- no task checksum drift exists inside the final submission tree
- every task checksum meets the minimum trial count
- the final submission matches the expected unique task count

This is the local gate that should pass before opening a PR. If it fails, fix
the bundle locally and rebuild the submission tree instead of creating or
updating a PR.

## Existing Bundle Validation

The existing bundle validator still matters during pass collection:

```bash
python evaluations/scripts/validate_tb2_bundle.py jobs/<job-a> jobs/<job-b> ...
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
MODEL=openai-responses:gpt-5.4-chatgpt TASK_FILE=tasks/slice-a.txt SUBMISSION_ID=chatgpt-high \
evaluations/scripts/run_tb2_submission_slice.sh
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
MODEL=openai-responses:gpt-5.4-chatgpt ACTION=status TASK_FILE=tasks/slice-a.txt SUBMISSION_ID=chatgpt-high \
evaluations/scripts/run_tb2_submission_slice.sh

# Run two slice passes back-to-back.
MODEL=openai-responses:gpt-5.4-chatgpt PASSES_PER_RUN=2 TASK_FILE=tasks/slice-a.txt SUBMISSION_ID=chatgpt-high \
evaluations/scripts/run_tb2_submission_slice.sh

# Override the derived slice name if needed.
MODEL=openai-responses:gpt-5.4-chatgpt SLICE_NAME=first-50 TASK_FILE=tasks/slice-a.txt SUBMISSION_ID=chatgpt-high \
evaluations/scripts/run_tb2_submission_slice.sh
```

Submission guidance for slices:

- only submit the intact Harbor job directories recorded in each slice manifest
- do not splice trials from different Harbor jobs into one job directory
- to reach the leaderboard minimum, each task still needs `5` trials overall
- the official submission repo accepts a job or folder of jobs, so separate
  intact slice jobs are valid as long as you keep them intact

Expected prerequisites before you launch it:

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
- `/tmp/.jaca/harbor-sessions`
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

If you do not request `/tmp/.jaca/harbor-sessions` as a Harbor artifact, those
session files remain container-local and are discarded with the environment.

For live probing while the task is still running, inspect the same transcript in
the running container instead of waiting for artifact flush:

```bash
uv run python -m evaluations.harbor.probe_in_run_compaction \
  --match log-summary-date-ranges__
```

Useful flags:

- `--watch` to poll until interrupted
- `--event-name in_run_compaction_completed` to count in-run compaction events
- `--transcript-path /tmp/.jaca/harbor-sessions/exec-prompt-rpc-transcript.jsonl` to override the default transcript path

## Troubleshooting

- `ModuleNotFoundError` for `evaluations`
  - run Harbor with `PYTHONPATH=$PWD/src:$PWD`, or install the repo into the same Python environment Harbor uses
- backend model fails with OpenAI chat-completions errors
  - use `openai-responses:gpt-5.3-codex`, not `openai:gpt-5.3-codex`
- provider auth missing in the container
  - export `OPENAI_API_KEY` in the Harbor host process before `harbor run`
- missing session artifacts after a run
  - add `--artifact /tmp/.jaca/harbor-sessions`
- timed out run shows only a session header
  - inspect `exec-prompt-phases.json` and `exec-prompt-rpc-transcript.jsonl`
    first; the run may have progressed into a long blocking tool call before
    Harbor timed it out
