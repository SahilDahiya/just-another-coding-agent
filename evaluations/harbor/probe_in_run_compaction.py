from __future__ import annotations

import argparse
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

DEFAULT_RPC_TRANSCRIPT_PATH = (
    "/tmp/just-another-coding-agent-sessions/exec-prompt-rpc-transcript.jsonl"
)
DEFAULT_EVENT_NAME = "in_run_compaction_completed"
DEFAULT_TAIL_MATCHES = 5


@dataclass(frozen=True)
class ProbeSnapshot:
    container: str
    transcript_path: str
    event_name: str
    total_matches: int
    matching_lines: tuple[tuple[int, str], ...]


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Probe a running Harbor task container for live in-run compaction "
            "events by inspecting the container-local RPC transcript."
        )
    )
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument(
        "--container",
        help="Exact running Docker container name to probe.",
    )
    target_group.add_argument(
        "--match",
        help="Substring that matches exactly one running Harbor container name.",
    )
    parser.add_argument(
        "--transcript-path",
        default=DEFAULT_RPC_TRANSCRIPT_PATH,
        help=(
            "Container-local RPC transcript path. Defaults to the Harbor "
            "session bundle transcript."
        ),
    )
    parser.add_argument(
        "--event-name",
        default=DEFAULT_EVENT_NAME,
        help="Event name substring to count in the transcript.",
    )
    parser.add_argument(
        "--tail-matches",
        type=int,
        default=DEFAULT_TAIL_MATCHES,
        help="How many recent matching lines to print.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Poll until interrupted and print when the match count changes.",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=2.0,
        help="Polling interval for --watch.",
    )
    return parser.parse_args(argv)


def list_running_containers(*, runner: Runner | None = None) -> list[str]:
    completed = _run_command(
        ["docker", "ps", "--format", "{{.Names}}"],
        runner=runner,
    )
    return [
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip()
    ]


def resolve_container_name(
    *,
    container: str | None,
    match: str | None,
    runner: Runner | None = None,
) -> str:
    if container is not None:
        return container
    if match is None:
        raise ValueError("Either container or match must be provided")
    normalized_match = match.lower()
    matches = [
        name
        for name in list_running_containers(runner=runner)
        if normalized_match in name.lower()
    ]
    if not matches:
        raise ValueError(f"No running Docker container matches {match!r}")
    if len(matches) > 1:
        rendered = ", ".join(sorted(matches))
        raise ValueError(
            f"Ambiguous Docker container match {match!r}: {rendered}"
        )
    return matches[0]


def read_container_transcript(
    *,
    container: str,
    transcript_path: str,
    runner: Runner | None = None,
) -> str:
    completed = _run_command(
        ["docker", "exec", container, "cat", transcript_path],
        runner=runner,
    )
    return completed.stdout


def collect_matching_lines(
    transcript_text: str,
    *,
    event_name: str,
    tail_matches: int,
) -> tuple[int, tuple[tuple[int, str], ...]]:
    matching_lines = tuple(
        (line_number, line)
        for line_number, line in enumerate(
            transcript_text.splitlines(),
            start=1,
        )
        if event_name in line
    )
    if tail_matches <= 0:
        return len(matching_lines), ()
    return len(matching_lines), matching_lines[-tail_matches:]


def build_probe_snapshot(
    *,
    container: str,
    transcript_path: str,
    event_name: str,
    tail_matches: int,
    runner: Runner | None = None,
) -> ProbeSnapshot:
    transcript_text = read_container_transcript(
        container=container,
        transcript_path=transcript_path,
        runner=runner,
    )
    total_matches, matching_lines = collect_matching_lines(
        transcript_text,
        event_name=event_name,
        tail_matches=tail_matches,
    )
    return ProbeSnapshot(
        container=container,
        transcript_path=transcript_path,
        event_name=event_name,
        total_matches=total_matches,
        matching_lines=matching_lines,
    )


def format_snapshot(snapshot: ProbeSnapshot) -> str:
    lines = [
        f"container: {snapshot.container}",
        f"transcript_path: {snapshot.transcript_path}",
        f"event_name: {snapshot.event_name}",
        f"total_matches: {snapshot.total_matches}",
    ]
    if snapshot.matching_lines:
        lines.append("recent_matches:")
        for line_number, line in snapshot.matching_lines:
            lines.append(f"  L{line_number}: {_truncate(line.strip())}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.watch:
        return _watch(args)

    container = resolve_container_name(
        container=args.container,
        match=args.match,
    )
    snapshot = build_probe_snapshot(
        container=container,
        transcript_path=args.transcript_path,
        event_name=args.event_name,
        tail_matches=args.tail_matches,
    )
    print(format_snapshot(snapshot))
    return 0


def _watch(args: argparse.Namespace) -> int:
    last_count: int | None = None
    while True:
        try:
            container = resolve_container_name(
                container=args.container,
                match=args.match,
            )
            snapshot = build_probe_snapshot(
                container=container,
                transcript_path=args.transcript_path,
                event_name=args.event_name,
                tail_matches=args.tail_matches,
            )
        except ValueError as error:
            print(str(error))
        except subprocess.CalledProcessError as error:
            print(_render_subprocess_error(error))
        else:
            if snapshot.total_matches != last_count:
                print(format_snapshot(snapshot))
                last_count = snapshot.total_matches
        time.sleep(args.poll_interval_seconds)


def _run_command(
    command: Sequence[str],
    *,
    runner: Runner | None,
) -> subprocess.CompletedProcess[str]:
    run = subprocess.run if runner is None else runner
    completed = run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            list(command),
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return completed


def _render_subprocess_error(error: subprocess.CalledProcessError) -> str:
    stderr = (error.stderr or "").strip()
    if stderr:
        return stderr
    return f"Command failed: {' '.join(str(part) for part in error.cmd)}"


def _truncate(text: str, *, limit: int = 200) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
