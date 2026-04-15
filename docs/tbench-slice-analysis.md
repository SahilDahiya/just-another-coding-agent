# Terminal Bench Slice Analysis

read_when: you want to summarize or visualize repeated Terminal Bench
runs across slices, or you want to refresh the
`sahildahiya.me/jaca/evaluation` page.

## Purpose

We run the same Terminal Bench slice (`xhigh-a`, `xhigh-b`, `xhigh-c`
in the current cohort) multiple times against the same model. A single
run is noisy at task-level and slice-level; the signal lives in how
the slice behaves across repeated runs. This doc describes the single
data pipeline that turns the raw `jobs/` directory into structured
analysis artefacts — text tables, JSON, CSV, and a bubble chart — and
the one-shot wrapper that feeds the static dashboard.

Running log of outcomes and task-selection learnings lives in
[terminal-bench-journal.md](terminal-bench-journal.md). The Harbor
workflow that produces the `jobs/` output lives in
[harbor-terminal-bench.md](harbor-terminal-bench.md). This doc is
specifically about analyzing the runs after they have finished.

## Components

- `evaluations/scripts/analyze_slice_history.py` — the analyzer. Walks
  `jobs/`, builds a canonical in-memory model, and drives every output
  from it.
- `evaluations/scripts/update_tbench_dashboard.sh` — one-line wrapper that invokes
  the analyzer with `--json` and writes directly into
  `~/repos/sahildahiya.me/src/data/tbench-slice-history.json`.

## Design rule: one pipeline, many sinks

The analyzer never writes the same aggregate twice. The flow is:

```
jobs/<job-dir>/result.json
        │
        ▼
   RunSummary         (per-run dataclass)
        │
        ▼
   SliceAggregate     (per-slice grouping + derived metrics)
        │
        ├──► text report       (emit_text_report)
        ├──► JSON              (write_json)
        ├──► CSV               (write_csv)
        └──► bubble chart PNG  (render_bubble_chart)
```

Pass rates, error rates, pass-rate spread, and the per-task outcome
matrix are computed once on `SliceAggregate` and reused by every sink.
The Astro dashboard consumes the JSON directly and does not recompute
any of those aggregates. If you add a new metric, add it to
`SliceAggregate` and let every consumer inherit it.

## Completeness filter

A run is treated as *complete* when all three hold:

- `finished_at` is present in `result.json`
- `n_trials > 0`
- `n_errors / n_trials <= 0.50`

Incomplete runs are dropped by default and reported on stderr. Common
reasons a run is dropped:

- the run is still in progress (`finished_at` missing)
- the harness crashed immediately and every trial errored
- a provider outage or auth failure erroed out more than half the slice

Pass `--include-incomplete` to see them in the text report and JSON.
The current job fixture drops four runs: one `xhigh-a` provider
outage, two `xhigh-b` harness-crash duplicates, and one in-progress
`xhigh-b` run.

## Command reference

```bash
# text report to stdout, nothing else:
uv run python -m evaluations.scripts.analyze_slice_history

# write the dashboard JSON into a temp path and a bubble chart PNG:
uv run --with matplotlib python -m evaluations.scripts.analyze_slice_history \
  --json /tmp/tbench.json --plot /tmp/tbench.png

# restrict to one slice:
uv run python -m evaluations.scripts.analyze_slice_history --slice xhigh-b

# restrict to one model (substring match on the job-name model prefix):
uv run python -m evaluations.scripts.analyze_slice_history --model gpt54

# include incomplete / degraded runs:
uv run python -m evaluations.scripts.analyze_slice_history --include-incomplete

# quiet mode — suppress the text report, useful for scripted JSON export:
uv run python -m evaluations.scripts.analyze_slice_history \
  --quiet --json /tmp/tbench.json
```

Flags:

| flag | purpose |
|------|---------|
| `--jobs-dir PATH` | override `jobs/` location (default: repo root) |
| `--slice NAME` | restrict to slice(s); repeatable |
| `--model STR` | substring match on the job-name model prefix |
| `--include-incomplete` | disable the completeness filter |
| `--json PATH` | write structured JSON to path |
| `--csv PATH` | write per-run rows to CSV path |
| `--plot PATH` | write bubble-chart PNG (requires `matplotlib`) |
| `--quiet` | suppress the text report |

## JSON schema

The JSON the analyzer writes is the sole data source the dashboard
consumes. Shape:

```jsonc
{
  "generated_at": "2026-04-14T23:44:32Z",
  "model": "gpt54-chatgpt",
  "slices": {
    "xhigh-a": {
      "slice": "xhigh-a",
      "n_runs": 6,
      "pass_rate_min": 0.5667,
      "pass_rate_max": 0.7333,
      "pass_rate_spread": 0.1667,
      "runs": [
        {
          "job_name": "gpt54-chatgpt-xhigh-a-pass-1-20260410-203704",
          "pass_num": 1,
          "date": "2026-04-10",
          "started_at": "...",
          "finished_at": "...",
          "n_trials": 30,
          "n_errors": 4,
          "n_passed": 20,
          "n_failed": 6,
          "pass_rate": 0.6667,
          "error_rate": 0.1333,
          "wall_seconds": 4910.8,
          "passed_tasks": ["..."],
          "failed_tasks": ["..."],
          "is_complete": true
        }
      ],
      "task_history": {
        "adaptive-rejection-sampler": ["P","P","F","F","F","P"],
        "...": "..."
      },
      "task_stats": {
        "adaptive-rejection-sampler": {
          "n_pass": 3, "n_fail": 3, "n_missing": 0
        }
      }
    },
    "xhigh-b": { "...": "..." },
    "xhigh-c": { "...": "..." }
  }
}
```

`task_history` rows are aligned with the `runs` array in order — column
`i` of `task_history["some-task"]` corresponds to `runs[i]`. This lets
the dashboard render a per-task heatmap without recomputing alignment.

## Dashboard refresh workflow

One command regenerates the dashboard data:

```bash
evaluations/scripts/update_tbench_dashboard.sh
```

What it does:

1. resolves the sahildahiya.me repo (default `~/repos/sahildahiya.me`,
   override with `SAHIL_SITE=/path/to/site`)
2. runs `analyze_slice_history.py --quiet --json` into
   `src/data/tbench-slice-history.json`

After that, commit and push in the site repo — Cloudflare Pages
auto-deploys:

```bash
cd ~/repos/sahildahiya.me
git add src/data/tbench-slice-history.json
git commit -m "refresh tbench slice history"
git push
```

## Extending the pipeline

- **New per-run metric**: add the field to `RunSummary.to_dict()` and
  populate it from `result.json`. It will show up in the JSON
  automatically; add a text-table column only if useful.
- **New per-slice aggregate**: add a `@property` on `SliceAggregate`
  and include it in `SliceAggregate.to_dict()`. Dashboard components
  should read it from the JSON rather than recomputing.
- **New sink** (e.g. Markdown report, Grafana JSON, Slack digest):
  write a new function that takes `list[SliceAggregate]` and emits
  whatever format you need. Do not re-parse `result.json`.
- **New run identifier format**: the analyzer recognizes jobs via
  `JOB_NAME_RE` — `<model>-<slice>-pass-<N>-<timestamp>`. Extend the
  regex if you add a new slicing scheme, but keep the canonical
  `RunSummary` shape so every sink continues to work.

## What this pipeline is not

- It does not read agent transcripts, tool calls, or timings below the
  task-outcome layer. For per-task debugging, open the trial directory
  directly under `jobs/<run>/<task>/`.
- It does not decide which runs are "the release" vs "noise". That
  judgment lives in the terminal-bench-journal entries and in how you
  label runs when you start them.
- It does not write to the site repo except through the explicit
  `evaluations/scripts/update_tbench_dashboard.sh` wrapper. The site repo stays the
  source of truth for everything the site renders; this repo stays the
  source of truth for everything that describes the runs.
