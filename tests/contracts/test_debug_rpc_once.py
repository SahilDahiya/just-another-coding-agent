import argparse
import json

import scripts.debug_rpc_once as debug_rpc_once


def test_main_cli_uses_created_session_id_for_run_start(
    monkeypatch, capsys, tmp_path
) -> None:
    created_session_id = "59a4d0b1dc8148deb2e5cea84be773a4"
    seen_requests: list[dict[str, object]] = []

    monkeypatch.setattr(
        debug_rpc_once,
        "parse_args",
        lambda: argparse.Namespace(
            prompt="hello",
            model="openai-responses:gpt-5.4",
            workspace_root=str(tmp_path),
            sessions_root=None,
            thinking="medium",
        ),
    )

    def fake_main(*, argv, input_stream, output_stream) -> int:
        request = json.loads(input_stream.readline())
        seen_requests.append(request)
        if request["command"] == "session.create":
            output_stream.write(
                json.dumps(
                    {
                        "type": "rpc_response",
                        "id": "req-create",
                        "response": {
                            "session_id": created_session_id,
                            "project_docs": [],
                        },
                    }
                )
                + "\n"
            )
            return 0

        assert request["command"] == "run.start"
        output_stream.write(
            json.dumps(
                {
                    "type": "rpc_event",
                    "id": "req-run",
                    "event": {"type": "run_succeeded", "run_id": "run-1"},
                }
            )
            + "\n"
        )
        return 0

    monkeypatch.setattr(debug_rpc_once, "main", fake_main)

    exit_code = debug_rpc_once.main_cli()

    assert exit_code == 0
    assert seen_requests == [
        {
            "id": "req-create",
            "command": "session.create",
            "payload": {},
        },
        {
            "id": "req-run",
            "command": "run.start",
            "payload": {
                "session_id": created_session_id,
                "prompt": "hello",
                "thinking": "medium",
            },
        },
    ]
    captured = capsys.readouterr()
    assert created_session_id in captured.out
    assert '"type": "rpc_event"' in captured.out


def test_resolve_prompts_supports_repeat_and_batched_input() -> None:
    args = argparse.Namespace(
        prompt_list=["hello", "run tests"],
        prompts="check logs || summarize\nship it",
    )

    assert debug_rpc_once._resolve_prompts(args) == [
        "hello",
        "run tests",
        "check logs",
        "summarize",
        "ship it",
    ]


def test_main_cli_reuses_session_for_multiple_prompts(
    monkeypatch, capsys, tmp_path
) -> None:
    created_session_id = "59a4d0b1dc8148deb2e5cea84be773a4"
    seen_requests: list[dict[str, object]] = []

    monkeypatch.setattr(
        debug_rpc_once,
        "parse_args",
        lambda: argparse.Namespace(
            prompt_list=["hello", "run tests"],
            prompts=None,
            model="openai-responses:gpt-5.4",
            workspace_root=str(tmp_path),
            sessions_root=None,
            thinking="medium",
        ),
    )

    def fake_main(*, argv, input_stream, output_stream) -> int:
        request = json.loads(input_stream.readline())
        seen_requests.append(request)
        if request["command"] == "session.create":
            output_stream.write(
                json.dumps(
                    {
                        "type": "rpc_response",
                        "id": "req-create",
                        "response": {
                            "session_id": created_session_id,
                            "project_docs": [],
                        },
                    }
                )
                + "\n"
            )
            return 0

        output_stream.write(
            json.dumps(
                {
                    "type": "rpc_event",
                    "id": request["id"],
                    "event": {"type": "run_succeeded", "run_id": request["id"]},
                }
            )
            + "\n"
        )
        return 0

    monkeypatch.setattr(debug_rpc_once, "main", fake_main)

    exit_code = debug_rpc_once.main_cli()

    assert exit_code == 0
    assert seen_requests == [
        {
            "id": "req-create",
            "command": "session.create",
            "payload": {},
        },
        {
            "id": "req-run-1",
            "command": "run.start",
            "payload": {
                "session_id": created_session_id,
                "prompt": "hello",
                "thinking": "medium",
            },
        },
        {
            "id": "req-run-2",
            "command": "run.start",
            "payload": {
                "session_id": created_session_id,
                "prompt": "run tests",
                "thinking": "medium",
            },
        },
    ]
    captured = capsys.readouterr()
    assert captured.out.count('"type": "rpc_event"') == 2
