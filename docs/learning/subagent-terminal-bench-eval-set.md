# Subagent Terminal Bench Eval Set

read_when: you are choosing Harbor reruns to evaluate subagent behavior changes

## Purpose

This file defines the current subagent-focused Terminal Bench evaluation cohort.

It is intentionally narrower than "all failed tasks". The goal is to measure
whether the current subagent contract helps on tasks with one bounded read-only
subproblem inside a broader task, not to mix in setup-dominant or obviously
direct-path failures.

The machine-readable task list lives in
[`evaluations/harbor/subagent_eval_set.txt`](../../evaluations/harbor/subagent_eval_set.txt).

## Source Runs

The source failure pool comes from these GPT-5.4 high-thinking Harbor jobs run
on April 6, 2026:

- `gpt54-chatgpt-high-a-pass-1-20260406-214307`
- `gpt54-chatgpt-high-b-pass-1-20260406-203832`
- `gpt54-chatgpt-high-c-pass-1-20260406-205141`

For this purpose, "failed" means the union of:

- `reward != 1.0`
- explicit Harbor exception buckets such as `AgentTimeoutError`

## Full Failed Pool

### Slice A

- `adaptive-rejection-sampler`
- `caffe-cifar-10`
- `compile-compcert`
- `configure-git-webserver`
- `db-wal-recovery`
- `dna-assembly`
- `extract-moves-from-video`
- `filter-js-from-html`
- `fix-code-vulnerability`

### Slice B

- `gcode-to-text`
- `gpt2-codegolf`
- `install-windows-3.11`
- `llm-inference-batching-scheduler`
- `make-doom-for-mips`
- `make-mips-interpreter`
- `mcmc-sampling-stan`
- `mteb-leaderboard`
- `mteb-retrieve`
- `overfull-hbox`
- `polyglot-c-py`

### Slice C

- `polyglot-rust-c`
- `pytorch-model-recovery`
- `qemu-alpine-ssh`
- `query-optimize`
- `raman-fitting`
- `rstan-to-pystan`
- `sam-cell-seg`
- `schemelike-metacircular-eval`
- `sqlite-with-gcov`
- `torch-pipeline-parallelism`
- `torch-tensor-parallelism`
- `train-fasttext`
- `tune-mjcf`
- `video-processing`

## Curated Eval Set

These are the tasks we should use for subagent behavior iteration right now:

### Slice A

- `extract-moves-from-video`
  - hidden-signal extraction from a large artifact
  - good fit for one bounded read-only child pass
- `db-wal-recovery`
  - recovery task with a bounded forensic subproblem
  - strong fit for evidence-returning child work
- `filter-js-from-html`
  - parent writes the sanitizer; child can search for dangerous cases and edge
    patterns
- `fix-code-vulnerability`
  - parent edits and tests; child can identify the vulnerable surface and report
    precise evidence

### Slice B

- `gcode-to-text`
  - already proved to be a natural subagent target in reruns
  - current failure mode moved from timeout to completed-but-wrong
- `llm-inference-batching-scheduler`
  - parent needs to write the plan output; child can inspect the cost model and
    shape constraints
- `overfull-hbox`
  - parent edits the document; child can inspect logs and isolate the exact
    warning source under the synonym-edit constraint

### Slice C

- `query-optimize`
  - bounded read-only subquestion around schema, plan, and query structure
- `raman-fitting`
  - evidence-heavy scientific extraction task with a plausible child fitting or
    peak-inspection pass
- `sam-cell-seg`
  - parent writes the script; child can inspect MobileSAM usage constraints and
    the input-output contract
- `video-processing`
  - hidden-signal extraction from a stable video setup
  - strong fit for one bounded child pass before the parent finalizes the
    detection logic

## Excluded For Now

These failures are intentionally not part of the current subagent cohort:

- setup-dominant or environment-dominant tasks
  - examples: `adaptive-rejection-sampler`, `caffe-cifar-10`,
    `compile-compcert`, `qemu-alpine-ssh`, `train-fasttext`,
    `rstan-to-pystan`, `tune-mjcf`
- direct-path tasks where a child is unlikely to add leverage
  - examples: `mteb-retrieve`, `mteb-leaderboard`, `install-windows-3.11`
- broad implementation tasks that are too diffuse for the current single
  read-only child contract
  - examples: `polyglot-c-py`, `polyglot-rust-c`,
    `torch-tensor-parallelism`, `schemelike-metacircular-eval`

## Current Rule

When evaluating subagent changes, prefer this cohort over the full failure pool.

Only widen the set when one of these changes happens:

- the current cohort is consistently green and no longer diagnostic
- subagent semantics expand beyond one bounded read-only child
- a new Harbor rerun shows a different failure family that clearly matches the
  current subagent contract
