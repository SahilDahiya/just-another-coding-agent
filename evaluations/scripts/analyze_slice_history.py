"""Summarize Terminal Bench slice history across repeated runs.

Single data pipeline: walks the local ``jobs/`` directory, builds a
canonical list of ``RunSummary`` records, and drives every downstream
artefact (text table, JSON dump, CSV, bubble chart PNG) from that one
in-memory model. Aggregates are computed once and baked into the JSON
so downstream consumers (the sahildahiya.me dashboard, notebooks) do
not recompute pass rates, error rates, or per-task histories.

Incomplete runs (missing ``finished_at``, or more than half of trials
errored out, or the harness crashed with zero rewards) are excluded by
default. Pass ``--include-incomplete`` to see them.

Usage::

    uv run --with matplotlib python -m evaluations.scripts.analyze_slice_history
    uv run --with matplotlib python -m evaluations.scripts.analyze_slice_history \\
        --json /tmp/tbench.json --plot /tmp/tbench.png

``evaluations/scripts/update_tbench_dashboard.sh`` invokes this with the dashboard
JSON path so the site data file regenerates in one command.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Structured job names carry four dimensions:
#   {model}-{thinking}-{slice}-pass-{N}-{timestamp}
# where model ∈ {glm5, gpt54-chatgpt}, thinking ∈ {medium, high, xhigh},
# and slice ∈ {a, b, c}. Smoke tests, one-offs, and fix-branches do not
# match this shape and are skipped by the analyzer.
JOB_NAME_RE = re.compile(
    r"^(?P<model>glm5|gpt54-chatgpt)"
    r"-(?P<thinking>medium|high|xhigh)"
    r"-(?P<slice>[a-c])"
    r"-pass-(?P<pass_num>\d+)"
    r"-(?P<ts>\d{8}-\d{6})$"
)
TRIAL_SUFFIX_RE = re.compile(r"__[A-Za-z0-9]+$")

EVAL_KEY = "just-another-coding-agent__terminal-bench"

# A run with more than this fraction of trials in n_errors is treated as
# a degraded / aborted run (upstream outage, harness crash) rather than
# a usable data point.
MAX_ERROR_FRACTION = 0.5


def strip_trial_suffix(task_id: str) -> str:
    return TRIAL_SUFFIX_RE.sub("", task_id)


@dataclass
class RunSummary:
    job_name: str
    model: str
    slice: str
    pass_num: int
    thinking: str
    started_at: datetime | None
    finished_at: datetime | None
    n_trials: int
    n_errors: int
    passed: set[str] = field(default_factory=set)
    failed: set[str] = field(default_factory=set)
    model_name: str | None = None
    agent_kwargs: dict[str, Any] = field(default_factory=dict)
    task_walls: dict[str, float] = field(default_factory=dict)

    @property
    def cohort(self) -> str:
        """Stable identifier for (model, thinking) used for grouping."""
        return f"{self.model}/{self.thinking}"

    @property
    def n_complete(self) -> int:
        return len(self.passed) + len(self.failed)

    @property
    def pass_rate(self) -> float:
        if self.n_trials == 0:
            return 0.0
        return len(self.passed) / self.n_trials

    @property
    def error_rate(self) -> float:
        if self.n_trials == 0:
            return 0.0
        return self.n_errors / self.n_trials

    @property
    def wall_seconds(self) -> float | None:
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None

    @property
    def date_label(self) -> str:
        if self.started_at:
            return self.started_at.strftime("%Y-%m-%d")
        return "?"

    @property
    def is_complete(self) -> bool:
        if self.finished_at is None:
            return False
        if self.n_trials == 0:
            return False
        if self.error_rate > MAX_ERROR_FRACTION:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_name": self.job_name,
            "model": self.model,
            "thinking": self.thinking,
            "cohort": self.cohort,
            "pass_num": self.pass_num,
            "date": self.date_label,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": (
                self.finished_at.isoformat() if self.finished_at else None
            ),
            "model_name": self.model_name,
            "agent_kwargs": self.agent_kwargs,
            "n_trials": self.n_trials,
            "n_errors": self.n_errors,
            "n_passed": len(self.passed),
            "n_failed": len(self.failed),
            "pass_rate": round(self.pass_rate, 4),
            "error_rate": round(self.error_rate, 4),
            "wall_seconds": (
                round(self.wall_seconds, 1) if self.wall_seconds is not None else None
            ),
            "passed_tasks": sorted(self.passed),
            "failed_tasks": sorted(self.failed),
            "is_complete": self.is_complete,
            # Per-task wall-clock in seconds, keyed by task name
            # (suffix-stripped). Populated by walking each trial
            # subdirectory's result.json; missing for trials that
            # errored before producing a timestamped result.
            "task_walls": {k: round(v, 1) for k, v in self.task_walls.items()},
        }


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def load_run(job_dir: Path) -> RunSummary | None:
    match = JOB_NAME_RE.match(job_dir.name)
    if match is None:
        return None

    result_path = job_dir / "result.json"
    if not result_path.exists():
        return None

    try:
        result = json.loads(result_path.read_text())
    except json.JSONDecodeError:
        return None

    stats = result.get("stats", {})
    eval_stats = stats.get("evals", {}).get(EVAL_KEY, {})
    # Top-level stats.n_trials is the slice size; eval_stats.n_trials is
    # only the trials that produced a reward (errors excluded there).
    # Use the slice size so pass% and error% share the same denominator.
    n_trials = int(stats.get("n_trials") or eval_stats.get("n_trials") or 0)
    n_errors = int(stats.get("n_errors") or eval_stats.get("n_errors") or 0)

    rewards = eval_stats.get("reward_stats", {}).get("reward", {})
    passed = {strip_trial_suffix(t) for t in rewards.get("1.0", [])}
    failed = {strip_trial_suffix(t) for t in rewards.get("0.0", [])}

    # Agent config lives in the job-level config.json; capture it so the
    # dashboard can group by model / thinking / kwargs without re-reading.
    model_name: str | None = None
    agent_kwargs: dict[str, Any] = {}
    config_path = job_dir / "config.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
            agents = config.get("agents") or []
            if agents:
                agent = agents[0]
                model_name = agent.get("model_name")
                agent_kwargs = agent.get("kwargs") or {}
        except json.JSONDecodeError:
            pass

    # Walk every trial directory in this job to pull per-task
    # wall-clock. A trial subdirectory contains its own result.json
    # with started_at and finished_at. Missing or malformed files are
    # skipped silently — latency data is best-effort and the dashboard
    # renders fine without it.
    task_walls: dict[str, float] = {}
    for trial_dir in job_dir.iterdir():
        if not trial_dir.is_dir():
            continue
        trial_result = trial_dir / "result.json"
        if not trial_result.exists():
            continue
        try:
            trial_data = json.loads(trial_result.read_text())
        except json.JSONDecodeError:
            continue
        task_name = trial_data.get("task_name")
        if not task_name:
            # Fall back to directory name stripped of trial suffix.
            task_name = strip_trial_suffix(trial_dir.name)
        started = _parse_iso(trial_data.get("started_at"))
        finished = _parse_iso(trial_data.get("finished_at"))
        if not started or not finished:
            continue
        wall = (finished - started).total_seconds()
        if wall > 0:
            task_walls[task_name] = wall

    return RunSummary(
        job_name=job_dir.name,
        model=match["model"],
        slice=match["slice"],
        pass_num=int(match["pass_num"]),
        thinking=match["thinking"],
        started_at=_parse_iso(result.get("started_at")),
        finished_at=_parse_iso(result.get("finished_at")),
        n_trials=n_trials,
        n_errors=n_errors,
        passed=passed,
        failed=failed,
        model_name=model_name,
        agent_kwargs=agent_kwargs,
        task_walls=task_walls,
    )


def collect_runs(jobs_dir: Path) -> list[RunSummary]:
    runs: list[RunSummary] = []
    for child in sorted(jobs_dir.iterdir()):
        if not child.is_dir():
            continue
        summary = load_run(child)
        if summary is not None:
            runs.append(summary)
    runs.sort(key=lambda r: (r.slice, r.started_at or datetime.min, r.pass_num))
    return runs


# --- pipeline: the canonical aggregate model that drives every output


@dataclass
class SliceAggregate:
    slice: str
    runs: list[RunSummary]

    @property
    def pass_rates(self) -> list[float]:
        return [r.pass_rate for r in self.runs]

    @property
    def pass_rate_min(self) -> float:
        return min(self.pass_rates) if self.pass_rates else 0.0

    @property
    def pass_rate_max(self) -> float:
        return max(self.pass_rates) if self.pass_rates else 0.0

    @property
    def pass_rate_spread(self) -> float:
        return self.pass_rate_max - self.pass_rate_min

    @property
    def task_history(self) -> dict[str, list[str]]:
        """Outcome string ("P"/"F"/"-") per task across this slice's runs."""
        tasks: set[str] = set()
        for run in self.runs:
            tasks |= run.passed | run.failed
        result: dict[str, list[str]] = {}
        for task in sorted(tasks):
            row: list[str] = []
            for run in self.runs:
                if task in run.passed:
                    row.append("P")
                elif task in run.failed:
                    row.append("F")
                else:
                    row.append("-")
            result[task] = row
        return result

    @property
    def task_stats(self) -> dict[str, dict[str, int]]:
        stats: dict[str, dict[str, int]] = {}
        for task, row in self.task_history.items():
            stats[task] = {
                "n_pass": row.count("P"),
                "n_fail": row.count("F"),
                "n_missing": row.count("-"),
            }
        return stats

    def to_dict(self) -> dict[str, Any]:
        return {
            "slice": self.slice,
            "n_runs": len(self.runs),
            "pass_rate_min": round(self.pass_rate_min, 4),
            "pass_rate_max": round(self.pass_rate_max, 4),
            "pass_rate_spread": round(self.pass_rate_spread, 4),
            "runs": [r.to_dict() for r in self.runs],
            "task_history": self.task_history,
            "task_stats": self.task_stats,
        }


def aggregate_by_slice(runs: list[RunSummary]) -> list[SliceAggregate]:
    by_slice: dict[str, list[RunSummary]] = {}
    for r in runs:
        by_slice.setdefault(r.slice, []).append(r)
    return [
        SliceAggregate(slice=name, runs=by_slice[name]) for name in sorted(by_slice)
    ]


# --- downstream artefacts all consume SliceAggregate / RunSummary


def format_slice_table(agg: SliceAggregate) -> str:
    if not agg.runs:
        return "(no runs)"
    headers = [
        "pass",
        "date",
        "passed/n",
        "pass%",
        "err%",
        "wall",
        "Δ F→P",
        "Δ P→F",
    ]
    rows: list[list[str]] = []
    prev: RunSummary | None = None
    for run in agg.runs:
        flips_up = ""
        flips_down = ""
        if prev is not None:
            up = sorted(prev.failed & run.passed)
            down = sorted(prev.passed & run.failed)
            flips_up = f"+{len(up)}"
            flips_down = f"-{len(down)}" if down else "0"
        wall = f"{run.wall_seconds/60:.0f}m" if run.wall_seconds else "?"
        rows.append(
            [
                str(run.pass_num),
                run.date_label,
                f"{len(run.passed)}/{run.n_trials}",
                f"{run.pass_rate*100:5.1f}",
                f"{run.error_rate*100:5.1f}",
                wall,
                flips_up,
                flips_down,
            ]
        )
        prev = run

    widths = [
        max(len(h), max(len(r[i]) for r in rows)) for i, h in enumerate(headers)
    ]
    lines = [
        "  ".join(h.ljust(w) for h, w in zip(headers, widths)),
        "  ".join("-" * w for w in widths),
    ]
    for r in rows:
        lines.append("  ".join(c.ljust(w) for c, w in zip(r, widths)))
    return "\n".join(lines)


def emit_text_report(aggregates: list[SliceAggregate]) -> None:
    for agg in aggregates:
        print(f"== {agg.slice} ({len(agg.runs)} runs) ==")
        print(format_slice_table(agg))
        if agg.runs:
            print(
                f"pass-rate range: {agg.pass_rate_min*100:.1f}%"
                f" → {agg.pass_rate_max*100:.1f}%"
                f" (spread {agg.pass_rate_spread*100:.1f}pp)"
            )
        print()


def write_json(
    aggregates: list[SliceAggregate], model: str | None, path: Path
) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "slices": {agg.slice: agg.to_dict() for agg in aggregates},
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")


def write_csv(runs: list[RunSummary], path: Path) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "slice",
                "pass_num",
                "job_name",
                "started_at",
                "n_trials",
                "n_complete",
                "passed",
                "failed",
                "n_errors",
                "pass_rate",
                "error_rate",
                "wall_seconds",
            ]
        )
        for r in runs:
            writer.writerow(
                [
                    r.slice,
                    r.pass_num,
                    r.job_name,
                    r.started_at.isoformat() if r.started_at else "",
                    r.n_trials,
                    r.n_complete,
                    len(r.passed),
                    len(r.failed),
                    r.n_errors,
                    f"{r.pass_rate:.4f}",
                    f"{r.error_rate:.4f}",
                    f"{r.wall_seconds:.1f}" if r.wall_seconds else "",
                ]
            )


def render_bubble_chart(
    aggregates: list[SliceAggregate], out_path: Path
) -> None:
    import matplotlib.pyplot as plt
    from matplotlib import dates as mdates

    fig, axes = plt.subplots(
        len(aggregates), 1, figsize=(10, 3 * len(aggregates)), sharex=True
    )
    if len(aggregates) == 1:
        axes = [axes]

    for ax, agg in zip(axes, aggregates):
        runs = [r for r in agg.runs if r.started_at]
        if not runs:
            ax.set_title(f"{agg.slice} (no dated runs)")
            continue
        xs = [r.started_at for r in runs]
        ys = [r.pass_rate * 100 for r in runs]
        sizes = [max(r.n_trials, 1) * 15 for r in runs]
        colors = [r.error_rate for r in runs]

        scatter = ax.scatter(
            xs,
            ys,
            s=sizes,
            c=colors,
            cmap="Reds",
            vmin=0.0,
            vmax=max(0.2, max(colors) if colors else 0.2),
            edgecolors="black",
            linewidths=0.5,
            alpha=0.85,
        )
        for r in runs:
            ax.annotate(
                f"p{r.pass_num}",
                (r.started_at, r.pass_rate * 100),
                textcoords="offset points",
                xytext=(6, 4),
                fontsize=8,
            )
        ax.set_title(f"{agg.slice}")
        ax.set_ylabel("pass rate (%)")
        ax.set_ylim(0, 100)
        ax.grid(True, linestyle=":", alpha=0.5)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        cbar = fig.colorbar(scatter, ax=ax, pad=0.01)
        cbar.set_label("error rate", fontsize=8)

    axes[-1].set_xlabel("run start")
    fig.suptitle("Terminal Bench slice history — bubble = n_trials")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out_path, dpi=150)
    print(f"wrote {out_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--jobs-dir",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "jobs",
        help="directory containing job output folders",
    )
    parser.add_argument(
        "--slice",
        action="append",
        dest="slices",
        help="restrict to specific slice(s); repeatable",
    )
    parser.add_argument(
        "--model",
        help="restrict to runs whose job-name model prefix contains this string",
    )
    parser.add_argument(
        "--include-incomplete",
        action="store_true",
        help=(
            "include runs that did not finish or had >50%% error rate "
            "(default: exclude)"
        ),
    )
    parser.add_argument("--json", type=Path, help="write structured JSON to path")
    parser.add_argument("--csv", type=Path, help="write per-run rows to CSV path")
    parser.add_argument("--plot", type=Path, help="write bubble chart PNG to path")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress the text report (useful when only writing JSON/CSV)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.jobs_dir.is_dir():
        print(f"jobs dir not found: {args.jobs_dir}", file=sys.stderr)
        return 2

    runs = collect_runs(args.jobs_dir)
    if args.slices:
        wanted = set(args.slices)
        runs = [r for r in runs if r.slice in wanted]
    if args.model:
        runs = [r for r in runs if args.model in r.model]
    if not args.include_incomplete:
        dropped = [r.job_name for r in runs if not r.is_complete]
        runs = [r for r in runs if r.is_complete]
        if dropped:
            print(
                f"# dropped {len(dropped)} incomplete run(s): "
                f"{', '.join(dropped)}",
                file=sys.stderr,
            )

    if not runs:
        print("no runs matched filters", file=sys.stderr)
        return 1

    aggregates = aggregate_by_slice(runs)
    model = runs[0].model if runs else None

    if not args.quiet:
        emit_text_report(aggregates)

    if args.json:
        write_json(aggregates, model, args.json)
        print(f"wrote {args.json}")
    if args.csv:
        write_csv(runs, args.csv)
        print(f"wrote {args.csv}")
    if args.plot:
        render_bubble_chart(aggregates, args.plot)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
