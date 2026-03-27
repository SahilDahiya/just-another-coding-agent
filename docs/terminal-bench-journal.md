# Terminal Bench Journal

read_when: you want the running record of benchmark task picks, outcomes, and operational learnings

## Purpose

This file records actual benchmark runs and the practical lessons that should influence future task selection.

It is intentionally narrow:

- which tasks were run
- which model/provider path was used
- whether the run was green
- what was learned from the run

## Kimi K2 via Ollama Cloud

### Completed

- `fix-git`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: green
  - note: succeeded on rerunning the detached-HEAD recovery pattern and merged the recovered commit back into `master`
  - artifacts: `/tmp/pi-kimi-terminal-bench-fix-git.92caUo/just-another-coding-agent-fix-git-kimi`
- `regex-log`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: green
  - note: remained a low-risk regex/file-output task under Harbor and verified cleanly
  - artifacts: `/tmp/pi-kimi-terminal-bench-regex-log.DkPDld/just-another-coding-agent-regex-log-kimi`
- `log-summary-date-ranges`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: green
  - note: file-generation data task verified cleanly and is a good template for future scripting tasks
  - artifacts: `/tmp/pi-kimi-terminal-bench-log-summary.OjYax8/just-another-coding-agent-log-summary-kimi`
- `openssl-selfsigned-cert`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: green
  - note: explicit certificate-generation tasks are viable, but Kimi is noticeably slower on operational setup than on pure file-writing tasks
  - artifacts: `/tmp/pi-kimi-terminal-bench-openssl.qgOaGb/just-another-coding-agent-openssl-kimi`
- `cancel-async-tasks`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: green
  - note: code-generation tasks with a small Python surface and tight tests remain good candidates
  - artifacts: `/tmp/pi-kimi-terminal-bench-cancel-async.PWzp0v/just-another-coding-agent-cancel-async-kimi`
- `pypi-server`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: green
  - note: small packaging and service setup tasks can pass cleanly when the verifier surface is explicit
  - artifacts: `/tmp/pi-kimi-terminal-bench-pypi.FYUCs5/just-another-coding-agent-pypi-kimi`
- `query-optimize`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: green
  - note: query and scripting tasks can still pass even when they run long, but they are slower than narrow file-output tasks
  - artifacts: `/tmp/pi-kimi-terminal-bench-query-optimize.Qe55el/just-another-coding-agent-query-optimize-kimi`
- `git-leak-recovery`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: green
  - note: Git history surgery remains a good Kimi category when the task is precise and the verifier surface is narrow
  - artifacts: `/tmp/pi-kimi-terminal-bench-git-leak.Z9bY08/just-another-coding-agent-git-leak-kimi`
- `modernize-scientific-stack`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: green
  - note: modernization tasks with a concrete executable end state can pass cleanly even when they touch multiple files and packaging metadata
  - artifacts: `/tmp/pi-kimi-terminal-bench-modernize.uCxcu8/just-another-coding-agent-modernize-kimi`

### Failed

- `count-dataset-tokens`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: red
  - note: the agent produced a numeric answer, but it did not match the verifier
  - artifacts: `/tmp/pi-kimi-terminal-bench-count-tokens.5T2aXr/just-another-coding-agent-count-tokens-kimi`
- `nginx-request-logging`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: red
  - note: service-configuration tasks with several moving parts are still weak candidates
  - artifacts: `/tmp/pi-kimi-terminal-bench-nginx-logging.FNZgsh/just-another-coding-agent-nginx-logging-kimi`
- `sanitize-git-repo`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: red
  - note: Git cleanup tasks that require broader rewriting are noticeably less reliable than focused recovery tasks
  - artifacts: `/tmp/pi-kimi-terminal-bench-sanitize-git.BY5eJs/just-another-coding-agent-sanitize-git-kimi`
- `fix-code-vulnerability`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: red
  - note: this failed at the runtime/harness level rather than reaching a successful trial
  - artifacts: `/tmp/pi-kimi-terminal-bench-fix-code.OSvHwP/just-another-coding-agent-fix-code-kimi`
- `gcode-to-text`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: red
  - note: conversion tasks with a wider interpretation surface are poor early picks
  - artifacts: `/tmp/pi-kimi-terminal-bench-gcode.oM8tml/just-another-coding-agent-gcode-kimi`
- `multi-source-data-merger`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: red
  - note: data-merging tasks can fail early at the runtime level and are not the best hedge when a green count target matters
  - artifacts: `/tmp/pi-kimi-terminal-bench-multi-source.qAudh3/just-another-coding-agent-multi-source-kimi`
- `password-recovery`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: red
  - note: open-ended forensic recovery can burn the full agent timeout without converging
  - artifacts: `/tmp/pi-kimi-terminal-bench-password-recovery.7t0cXO/just-another-coding-agent-password-recovery-kimi`
- `vulnerable-secret`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: red
  - note: executable-secret extraction is a weak Kimi category compared with file-generation and Git tasks
  - artifacts: `/tmp/pi-kimi-terminal-bench-vulnerable-secret.gXij91/just-another-coding-agent-vulnerable-secret-kimi`
- `sqlite-db-truncate`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: red
  - note: binary recovery tasks are poor candidates for building a fast green set
  - artifacts: `/tmp/pi-kimi-terminal-bench-sqlite-truncate.m9A7Ha/just-another-coding-agent-sqlite-truncate-kimi`
- `configure-git-webserver`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: red
  - note: deployment-style service integration remains too brittle for the current model/backend pairing
  - artifacts: `/tmp/pi-kimi-terminal-bench-git-webserver.Q3O72s/just-another-coding-agent-git-webserver-kimi`
- `crack-7z-hash`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: red
  - note: archive cracking is not a good Kimi hedge task
  - artifacts: `/tmp/pi-kimi-terminal-bench-7z.3xwbBh/just-another-coding-agent-crack-7z-kimi`
- `overfull-hbox`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: red
  - note: LaTeX formatting repair did produce artifacts, but it did not eliminate the verifier-visible overfull warnings
  - artifacts: `/tmp/pi-kimi-terminal-bench-overfull.O0FGwO/just-another-coding-agent-overfull-kimi`
- `headless-terminal`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: red
  - note: small-interface coding tasks are only good picks when the method names are unambiguous; this run implemented the wrong public method surface and failed verifier tests immediately
  - artifacts: `/tmp/pi-kimi-terminal-bench-headless.e9ef03/just-another-coding-agent-headless-terminal-kimi`
- `build-pmars`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: red
  - note: the first run exposed a Harbor bootstrap bug on Debian images without working `ensurepip`; after fixing the adapter bootstrap, the rerun reached the task but still did not clear the verifier
  - artifacts: `/tmp/pi-kimi-terminal-bench-pmars-rerun.7b552c/just-another-coding-agent-build-pmars-kimi-rerun`
- `mteb-leaderboard`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: red
  - note: the rerun validated the managed Python 3.12 bootstrap path end to end, but the task still failed on benchmark output rather than setup
  - artifacts: `/tmp/pi-kimi-terminal-bench-mteb-leaderboard-rerun2.455b9c/just-another-coding-agent-mteb-leaderboard-kimi-rerun2`
- `merge-diff-arc-agi-task`
  - model: `ollama:kimi-k2:1t-cloud`
  - result: red
  - note: the agent reached the merge conflict state cleanly but did not resolve the conflicting implementations before verifier timeout
  - artifacts: `/tmp/pi-kimi-terminal-bench-merge-arc.f3fa0f/just-another-coding-agent-merge-diff-arc-kimi`

## GPT-5.3 Codex via OpenAI Responses

### Initial Tracked Codex Baseline

- batch: `jaca-codex-nine-20260326121757`
- summary: `/tmp/jaca-codex-nine-20260326121757.tsv`
- result: `7/9` green

Green in the initial tracked Codex baseline:

- `fix-git`
- `git-leak-recovery`
- `log-summary-date-ranges`
- `modernize-scientific-stack`
- `openssl-selfsigned-cert`
- `pypi-server`
- `query-optimize`

Red in the initial tracked Codex baseline:

- `cancel-async-tasks`
- `regex-log`

### Targeted Failure Analysis Reruns

- `regex-log`
  - model: `openai-responses:gpt-5.3-codex`
  - result: `2/5` green in `jaca-regex-log-codex-x5`
  - note: after prompt/runtime improvements the old fabricated-success failure disappeared, but the remaining reds were still semantic regex failures rather than tool failures
  - artifacts: `/home/dahiy/repos/urban-octo-guacamole/jobs/jaca-regex-log-codex-x5`
- `cancel-async-tasks`
  - model: `openai-responses:gpt-5.3-codex`
  - result: `0/5` green in `jaca-cancel-async-codex-x5`
  - note: the dominant failure is weak verification and the wrong asyncio cancellation pattern, not harness/tool failure
  - artifacts: `/home/dahiy/repos/urban-octo-guacamole/jobs/jaca-cancel-async-codex-x5`

### Exploratory 20-Task Harbor Batch

- batch: `jaca-harbor-next20.thv1lw`
- summary: `/tmp/jaca-harbor-next20.thv1lw/batch-results.tsv`
- canaries:
  - `fix-git` -> green
  - `log-summary-date-ranges` -> red
- main batch result: `4/20` green

Green in the exploratory 20-task batch:

- `count-dataset-tokens`
- `prove-plus-comm`
- `fix-code-vulnerability`
- `cobol-modernization`

Red in the exploratory 20-task batch:

- `filter-js-from-html`
- `break-filter-js-from-html`
- `model-extraction-relu-logits`
- `extract-elf`
- `large-scale-text-editing`
- `sparql-university`
- `build-cython-ext`
- `polyglot-c-py`
- `polyglot-rust-c`
- `pytorch-model-cli`
- `financial-document-processor`
- `bn-fit-modify`
- `distribution-search`
- `circuit-fibsqrt`
- `dna-insert`
- `rstan-to-pystan`

### Thinking High Failed-Task Rerun

- batch: `thinking-failed-rerun-20260326223900`
- model: `openai-responses:gpt-5.3-codex`
- setting: `thinking=high`
- result: `10/18` green, mean `0.556`
- result file: `/home/dahiy/repos/urban-octo-guacamole/jobs/thinking-failed-rerun-20260326223900/thinking-failed-rerun-20260326223900/result.json`

Green flips from the previously-red set:

- `bn-fit-modify`
- `break-filter-js-from-html`
- `circuit-fibsqrt`
- `distribution-search`
- `financial-document-processor`
- `model-extraction-relu-logits`
- `pytorch-model-cli`
- `regex-log`
- `rstan-to-pystan`
- `sparql-university`

Still red under `thinking=high`:

- `build-cython-ext`
- `cancel-async-tasks`
- `dna-insert`
- `extract-elf`
- `filter-js-from-html`
- `large-scale-text-editing`
- `polyglot-c-py`
- `polyglot-rust-c`

Notable detail:

- `extract-elf` recorded `reward=0.0` and also the batch-level lone `AgentTimeoutError`; Harbor still counted it as a completed red trial in the aggregate reward distribution.

## Operational Learnings

- For Harbor-backed Ollama Cloud runs, the container must receive both `OLLAMA_BASE_URL` and `OLLAMA_API_KEY`.
- Use `https://ollama.com/v1` as the base URL for Ollama Cloud.
- Kimi K2 is materially slower than `openai-responses:gpt-5.3-codex` on the same `fix-git` task, but it still completed successfully.
- Narrow file-output tasks and simple Git recovery tasks are the best first picks for building a green set.
- Narrow scripting and file-generation tasks continue to be the best Kimi queue candidates under Harbor.
- Small Python implementation tasks with explicit tests are also good Kimi candidates.
- Explicit operational tasks can pass, but their latency is higher and they should be mixed sparingly into the queue.
- Focused Git recovery beats broad Git sanitization.
- Open-ended forensics, reverse engineering, and binary recovery have been poor Kimi categories so far.
- Tasks with a single explicit output artifact and a narrow transformation still give the best signal-to-latency ratio.
- Harbor task images are not uniform: some Debian images allow `python3 -m venv --help` but still fail actual venv creation because `ensurepip` is missing. The adapter install script must treat venv creation itself as the probe and retry after installing `python3-venv`.
- Harbor task images are also not uniform on Python version: some run only Python 3.10, which cannot install this package directly because the project requires Python 3.12+. The adapter therefore needs a managed Python 3.12 bootstrap path instead of assuming the task image Python is usable.
- The managed Python 3.12 bootstrap path is now validated in a real Harbor run: `mteb-leaderboard` completed environment and agent setup successfully after switching away from the task image's Python 3.10, then failed only on task-specific output.
- Modernization and packaging tasks can be viable Kimi picks when the verifier is anchored to one runnable entrypoint rather than a large hidden behavioral surface.
- Interface-implementation tasks are riskier than they look when the model can satisfy the spirit of the prompt while still missing the exact contract names the verifier expects.
- Merge-resolution tasks with conflicting but individually plausible implementations are not low-risk Kimi picks; they burn time and often fail late.
- For Codex, a single green x1 run is not stable enough to treat as submission-ready. `log-summary-date-ranges` went green in the initial tracked batch and later red in the canary rerun.
- The recent prompt and tooling changes removed the old `regex-log` fabricated-success failure mode. The remaining failures are now about semantic correctness and weak behavioral checks.
- The recent prompt and tooling changes did not flip `cancel-async-tasks`; that task still fails because the model does not reliably choose the real SIGINT acceptance check.
- Several tasks that looked like low-risk extraction or transformation work were not actually low-risk under Codex in the 20-task scan: `filter-js-from-html`, `model-extraction-relu-logits`, `extract-elf`, and `large-scale-text-editing` all stayed red.
- The best new Codex candidates from the 20-task scan are `count-dataset-tokens`, `prove-plus-comm`, `fix-code-vulnerability`, and `cobol-modernization`.
- The official full-run Harbor command shape is now smoke-tested: a submission-safe `terminal-bench@2.0` run on `fix-git` completed green with `timeout_multiplier: 1.0`, no timeout overrides, and no resource overrides.
- Explicit `thinking=high` materially changed the failed-task picture: `10/18` previously-red tasks flipped green in one x1 rerun batch.
- The biggest thinking-enabled wins were task families that previously looked close but brittle: `distribution-search`, `pytorch-model-cli`, `break-filter-js-from-html`, `circuit-fibsqrt`, and `model-extraction-relu-logits`.
- `cancel-async-tasks` stayed red even with `thinking=high`, so that task is still blocked on verification or reasoning quality rather than lack of deliberation budget alone.

## Candidate Queue

Current lower-risk next picks:

- `count-dataset-tokens`
- `prove-plus-comm`
- `fix-code-vulnerability`
- `cobol-modernization`
- `fix-git`
- `git-leak-recovery`
- `modernize-scientific-stack`
- `openssl-selfsigned-cert`
- `pypi-server`
- `query-optimize`

Current avoid-for-now picks:

- `git-multibranch`
- `regex-chess`
- `headless-terminal`
- `largest-eigenval`
- `overfull-hbox`
- `build-pmars`
- `merge-diff-arc-agi-task`
- `mteb-leaderboard`
- `write-compressor`
- `password-recovery`
- `vulnerable-secret`
- `sqlite-db-truncate`
- `crack-7z-hash`
- `model-extraction-relu-logits`
- `extract-elf`
- `break-filter-js-from-html`
- `large-scale-text-editing`
- `sparql-university`
- `build-cython-ext`
- `polyglot-c-py`
- `polyglot-rust-c`
- `pytorch-model-cli`
- `financial-document-processor`
- `bn-fit-modify`
- `distribution-search`
- `circuit-fibsqrt`
- `dna-insert`
- `rstan-to-pystan`
- `cancel-async-tasks`
- `regex-log`

## Deferred Follow-Up

- Upstream `pi-mono` follow-up: its fuzzy `edit` path appears to replace against normalized content, which can rewrite unrelated surrounding smart quotes or spacing outside the intended match. This repo now has a regression test and local fix for that behavior. Revisit whether to open a `pi-mono` issue/PR after the current discussion thread is done.
