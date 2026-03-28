# Terminal Bench Checklist

read_when: you want the full `terminal-bench@2.0` task list and the current pass/fail baseline

## Purpose

This file is the lightweight checklist for the current Terminal Bench baseline.

It answers three questions clearly:

- which tasks passed at least once in the current tracked Codex exploration
- which tasks were tried in the current tracked Codex exploration and have no green run yet
- which tasks have not been tried in the current tracked Codex exploration

For run details, artifacts, historical green runs, and task-picking notes, see [../terminal-bench-journal.md](../terminal-bench-journal.md).

## Legend

- `[x]` passed at least once in the current tracked Codex exploration
- `[-]` tried in the current tracked Codex exploration and still red
- `[ ]` not yet tried in the current tracked Codex exploration

## Summary

- Total cached `terminal-bench@2.0` tasks: `89`
- Current tracked baseline: Codex Harbor exploration through the `thinking=high` failed-task rerun on `2026-03-26`
  - sources: `jaca-codex-nine-20260326121757`, `jaca-harbor-next20.thv1lw`, the related canary reruns, and `thinking-failed-rerun-20260326223900`
  - note: the later full submission attempt `submission-codex53-high-20260327-r2` is recorded in the journal only and intentionally excluded from this checklist because Docker pull-rate limiting and OpenAI quota failures contaminated the batch
- Passed in current tracked baseline: `21`
- Failed in current tracked baseline: `8`
- Untried in current tracked baseline: `60`

## Tasks

- [ ] `adaptive-rejection-sampler`
- [x] `bn-fit-modify`
- [x] `break-filter-js-from-html`
- [-] `build-cython-ext`
- [ ] `build-pmars`
- [ ] `build-pov-ray`
- [ ] `caffe-cifar-10`
- [-] `cancel-async-tasks`
- [ ] `chess-best-move`
- [x] `circuit-fibsqrt`
- [x] `cobol-modernization`
- [ ] `code-from-image`
- [ ] `compile-compcert`
- [ ] `configure-git-webserver`
- [ ] `constraints-scheduling`
- [x] `count-dataset-tokens`
- [ ] `crack-7z-hash`
- [ ] `custom-memory-heap-crash`
- [ ] `db-wal-recovery`
- [x] `distribution-search`
- [ ] `dna-assembly`
- [-] `dna-insert`
- [-] `extract-elf`
- [ ] `extract-moves-from-video`
- [ ] `feal-differential-cryptanalysis`
- [ ] `feal-linear-cryptanalysis`
- [-] `filter-js-from-html`
- [x] `financial-document-processor`
- [x] `fix-code-vulnerability`
- [x] `fix-git`
- [ ] `fix-ocaml-gc`
- [ ] `gcode-to-text`
- [x] `git-leak-recovery`
- [ ] `git-multibranch`
- [ ] `gpt2-codegolf`
- [ ] `headless-terminal`
- [ ] `hf-model-inference`
- [ ] `install-windows-3.11`
- [ ] `kv-store-grpc`
- [-] `large-scale-text-editing`
- [ ] `largest-eigenval`
- [ ] `llm-inference-batching-scheduler`
- [x] `log-summary-date-ranges`
- [ ] `mailman`
- [ ] `make-doom-for-mips`
- [ ] `make-mips-interpreter`
- [ ] `mcmc-sampling-stan`
- [ ] `merge-diff-arc-agi-task`
- [x] `model-extraction-relu-logits`
- [x] `modernize-scientific-stack`
- [ ] `mteb-leaderboard`
- [ ] `mteb-retrieve`
- [ ] `multi-source-data-merger`
- [ ] `nginx-request-logging`
- [x] `openssl-selfsigned-cert`
- [ ] `overfull-hbox`
- [ ] `password-recovery`
- [ ] `path-tracing`
- [ ] `path-tracing-reverse`
- [-] `polyglot-c-py`
- [-] `polyglot-rust-c`
- [ ] `portfolio-optimization`
- [ ] `protein-assembly`
- [x] `prove-plus-comm`
- [x] `pypi-server`
- [x] `pytorch-model-cli`
- [ ] `pytorch-model-recovery`
- [ ] `qemu-alpine-ssh`
- [ ] `qemu-startup`
- [x] `query-optimize`
- [ ] `raman-fitting`
- [ ] `regex-chess`
- [x] `regex-log`
- [ ] `reshard-c4-data`
- [x] `rstan-to-pystan`
- [ ] `sam-cell-seg`
- [ ] `sanitize-git-repo`
- [ ] `schemelike-metacircular-eval`
- [x] `sparql-university`
- [ ] `sqlite-db-truncate`
- [ ] `sqlite-with-gcov`
- [ ] `torch-pipeline-parallelism`
- [ ] `torch-tensor-parallelism`
- [ ] `train-fasttext`
- [ ] `tune-mjcf`
- [ ] `video-processing`
- [ ] `vulnerable-secret`
- [ ] `winning-avg-corewars`
- [ ] `write-compressor`

## Update Rule

When a task is run in the current tracked baseline:

1. Mark it `[x]` if it has any green run in the tracked baseline.
2. Mark it `[-]` only if it has been tried and still has no green run in the tracked baseline.
3. Leave all untouched tasks as `[ ]`.
4. Add the run details and learning to [../terminal-bench-journal.md](../terminal-bench-journal.md).
5. If the tracked baseline changes, update the summary line and then refresh the task statuses consistently.
