from __future__ import annotations

import subprocess

import pytest

import evaluations.harbor.probe_in_run_compaction as probe
from evaluations.harbor.probe_in_run_compaction import (
    DEFAULT_EVENT_NAME,
    build_probe_snapshot,
    collect_matching_lines,
    format_snapshot,
    main,
    resolve_container_name,
)


def test_resolve_container_name_returns_unique_substring_match() -> None:
    def fake_run(command, *, capture_output, text, check):
        del capture_output, text, check
        assert command == ["docker", "ps", "--format", "{{.Names}}"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="task-a-main-1\ntask-b-main-1\n",
            stderr="",
        )

    resolved = resolve_container_name(
        container=None,
        match="task-b",
        runner=fake_run,
    )

    assert resolved == "task-b-main-1"


def test_resolve_container_name_matches_case_insensitively() -> None:
    def fake_run(command, *, capture_output, text, check):
        del capture_output, text, check
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="log-summary-date-ranges__paybjrk-main-1\n",
            stderr="",
        )

    resolved = resolve_container_name(
        container=None,
        match="log-summary-date-ranges__pAYBJRk",
        runner=fake_run,
    )

    assert resolved == "log-summary-date-ranges__paybjrk-main-1"


def test_resolve_container_name_rejects_ambiguous_match() -> None:
    def fake_run(command, *, capture_output, text, check):
        del capture_output, text, check
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="task-a-main-1\ntask-a-sidecar-1\n",
            stderr="",
        )

    with pytest.raises(ValueError, match="Ambiguous Docker container match"):
        resolve_container_name(
            container=None,
            match="task-a",
            runner=fake_run,
        )


def test_collect_matching_lines_counts_and_limits_tail() -> None:
    transcript = "\n".join(
        [
            '{"event":"tool_call_started"}',
            '{"event":"in_run_compaction_completed","count":1}',
            '{"event":"tool_call_updated"}',
            '{"event":"in_run_compaction_completed","count":2}',
        ]
    )

    total_matches, matching_lines = collect_matching_lines(
        transcript,
        event_name=DEFAULT_EVENT_NAME,
        tail_matches=1,
    )

    assert total_matches == 2
    assert matching_lines == (
        (4, '{"event":"in_run_compaction_completed","count":2}'),
    )


def test_collect_matching_lines_ignores_substring_inside_other_payload_text() -> None:
    transcript = "\n".join(
        [
            '{"type":"rpc_event","payload":{"event":{"type":"tool_call_succeeded"},"result":"{\\"type\\":\\"in_run_compaction_completed\\"}"}}',
            '{"type":"rpc_event","payload":{"event":{"type":"in_run_compaction_completed"}}}',
        ]
    )

    total_matches, matching_lines = collect_matching_lines(
        transcript,
        event_name=DEFAULT_EVENT_NAME,
        tail_matches=5,
    )

    assert total_matches == 1
    assert matching_lines == (
        (
            2,
            '{"type":"rpc_event","payload":{"event":{"type":"in_run_compaction_completed"}}}',
        ),
    )


def test_build_probe_snapshot_reads_container_transcript() -> None:
    def fake_run(command, *, capture_output, text, check):
        del capture_output, text, check
        assert command == [
            "docker",
            "exec",
            "task-a-main-1",
            "cat",
            "/tmp/transcript.jsonl",
        ]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"event":"in_run_compaction_completed","count":1}\n',
            stderr="",
        )

    snapshot = build_probe_snapshot(
        container="task-a-main-1",
        transcript_path="/tmp/transcript.jsonl",
        event_name=DEFAULT_EVENT_NAME,
        tail_matches=3,
        runner=fake_run,
    )

    assert snapshot.container == "task-a-main-1"
    assert snapshot.total_matches == 1
    assert snapshot.matching_lines == (
        (1, '{"event":"in_run_compaction_completed","count":1}'),
    )


def test_format_snapshot_renders_recent_matches() -> None:
    def fake_run(command, *, capture_output, text, check):
        del capture_output, text, check
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"event":"in_run_compaction_completed","count":1}\n',
            stderr="",
        )

    snapshot = build_probe_snapshot(
        container="task-a-main-1",
        transcript_path="/tmp/transcript.jsonl",
        event_name=DEFAULT_EVENT_NAME,
        tail_matches=3,
        runner=fake_run,
    )

    rendered = format_snapshot(snapshot)

    assert "container: task-a-main-1" in rendered
    assert "total_matches: 1" in rendered
    assert "recent_matches:" in rendered
    assert 'L1: {"event":"in_run_compaction_completed","count":1}' in rendered


def test_main_renders_clean_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        probe,
        "resolve_container_name",
        lambda **_: "task-a-main-1",
    )

    def fake_build_probe_snapshot(**_: object) -> object:
        raise subprocess.CalledProcessError(
            1,
            ["docker", "exec", "task-a-main-1", "cat", "/tmp/transcript.jsonl"],
            output="",
            stderr="cat: /tmp/transcript.jsonl: No such file or directory\n",
        )

    monkeypatch.setattr(
        probe,
        "build_probe_snapshot",
        fake_build_probe_snapshot,
    )

    exit_code = main(["--container", "task-a-main-1"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert (
        captured.out.strip()
        == "cat: /tmp/transcript.jsonl: No such file or directory"
    )
