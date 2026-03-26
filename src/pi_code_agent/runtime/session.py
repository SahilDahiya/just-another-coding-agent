from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

from pydantic_ai import capture_run_messages

from pi_code_agent.contracts.run_events import RunEvent
from pi_code_agent.contracts.tools import CANONICAL_TOOL_NAMES
from pi_code_agent.runtime.agent import build_canonical_agent
from pi_code_agent.runtime.run import stream_run_events
from pi_code_agent.session.jsonl import append_run_to_session, load_session
from pi_code_agent.tools._workspace import normalize_workspace_root


async def stream_session_run_events(
    *,
    model: Any,
    workspace_root: Path | str,
    session_path: Path,
    prompt: str,
    tool_names: Sequence[str] = CANONICAL_TOOL_NAMES,
) -> AsyncIterator[RunEvent]:
    """Stream one run and persist it only after terminal completion.

    Callers that stop consuming before a terminal event do not get a partial
    session append; append-only persistence requires a fully observed run.
    """
    normalized_workspace_root = normalize_workspace_root(workspace_root)
    loaded_session = None
    if session_path.exists():
        loaded_session = load_session(
            path=session_path,
            workspace_root=normalized_workspace_root,
        )

    agent = build_canonical_agent(
        model=model,
        workspace_root=normalized_workspace_root,
        tool_names=tool_names,
    )
    emitted_events: list[RunEvent] = []

    with capture_run_messages() as messages:
        async for event in stream_run_events(
            agent=agent,
            prompt=prompt,
            message_history=(
                loaded_session.message_history if loaded_session is not None else None
            ),
        ):
            emitted_events.append(event)
            yield event

    append_run_to_session(
        path=session_path,
        workspace_root=normalized_workspace_root,
        prompt=prompt,
        events=emitted_events,
        messages=messages,
    )


__all__ = ["stream_session_run_events"]
