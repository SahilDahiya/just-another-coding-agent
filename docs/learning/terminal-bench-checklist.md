# Terminal Bench Checklist

read_when: you want the full `terminal-bench@2.0` task list and the current pass/fail baseline

## Purpose

This file is the lightweight checklist for the current Terminal Bench baseline.

It answers three questions clearly:

- which tasks passed in the current tracked baseline
- which tasks were tried and failed in the current tracked baseline
- which tasks have not been tried in the current tracked baseline

For run details, artifacts, historical green runs, and task-picking notes, see [../terminal-bench-journal.md](../terminal-bench-journal.md).

## Legend

- `[x]` passed in the current tracked baseline
- `[-]` tried in the current tracked baseline and failed
- `[ ]` not yet tried in the current tracked baseline

## Summary

- Total cached `terminal-bench@2.0` tasks: `89`
- Current tracked baseline: Codex 9-task batch `jaca-codex-nine-20260326121757`
- Passed in current baseline: `7`
- Failed in current baseline: `2`
- Untried in current baseline: `80`

## Tasks

- [ ] `adaptive-rejection-sampler`
- [ ] `bn-fit-modify`
- [ ] `break-filter-js-from-html`
- [ ] `build-cython-ext`
- [ ] `build-pmars`
- [ ] `build-pov-ray`
- [ ] `caffe-cifar-10`
- [-] `cancel-async-tasks`
- [ ] `chess-best-move`
- [ ] `circuit-fibsqrt`
- [ ] `cobol-modernization`
- [ ] `code-from-image`
- [ ] `compile-compcert`
- [ ] `configure-git-webserver`
- [ ] `constraints-scheduling`
- [ ] `count-dataset-tokens`
- [ ] `crack-7z-hash`
- [ ] `custom-memory-heap-crash`
- [ ] `db-wal-recovery`
- [ ] `distribution-search`
- [ ] `dna-assembly`
- [ ] `dna-insert`
- [ ] `extract-elf`
- [ ] `extract-moves-from-video`
- [ ] `feal-differential-cryptanalysis`
- [ ] `feal-linear-cryptanalysis`
- [ ] `filter-js-from-html`
- [ ] `financial-document-processor`
- [ ] `fix-code-vulnerability`
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
- [ ] `large-scale-text-editing`
- [ ] `largest-eigenval`
- [ ] `llm-inference-batching-scheduler`
- [x] `log-summary-date-ranges`
- [ ] `mailman`
- [ ] `make-doom-for-mips`
- [ ] `make-mips-interpreter`
- [ ] `mcmc-sampling-stan`
- [ ] `merge-diff-arc-agi-task`
- [ ] `model-extraction-relu-logits`
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
- [ ] `polyglot-c-py`
- [ ] `polyglot-rust-c`
- [ ] `portfolio-optimization`
- [ ] `protein-assembly`
- [ ] `prove-plus-comm`
- [x] `pypi-server`
- [ ] `pytorch-model-cli`
- [ ] `pytorch-model-recovery`
- [ ] `qemu-alpine-ssh`
- [ ] `qemu-startup`
- [x] `query-optimize`
- [ ] `raman-fitting`
- [ ] `regex-chess`
- [-] `regex-log`
- [ ] `reshard-c4-data`
- [ ] `rstan-to-pystan`
- [ ] `sam-cell-seg`
- [ ] `sanitize-git-repo`
- [ ] `schemelike-metacircular-eval`
- [ ] `sparql-university`
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

1. Mark it `[x]` if it passed or `[-]` if it failed.
2. Leave all untouched tasks as `[ ]`.
3. Add the run details and learning to [../terminal-bench-journal.md](../terminal-bench-journal.md).
4. If the tracked baseline changes, update the summary line and then refresh the task statuses consistently.
