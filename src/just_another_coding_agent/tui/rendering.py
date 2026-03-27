"""Rendering helpers for the interactive TUI."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from just_another_coding_agent.contracts.thinking import ThinkingSetting

from .state import UiState
from .widgets import StatusBar, TranscriptLog


def display_path(path: Path) -> str:
    """Render a path relative to the home directory when possible."""
    resolved = path.resolve()
    home = Path.home().resolve()
    resolved_str = str(resolved)
    home_str = str(home)
    if resolved_str == home_str:
        return "~"
    if resolved_str.startswith(home_str + "/"):
        return "~" + resolved_str[len(home_str) :]
    return resolved_str


def build_status_text(state: UiState) -> str:
    """Build the current status-bar line from explicit UI state."""
    parts = [
        f"[bold]state[/bold] {state.phase}",
        f"[bold]model[/bold] {state.model}",
        f"[bold]workspace[/bold] {display_path(state.workspace_root)}",
    ]
    if state.thinking:
        parts.append(f"[bold]thinking[/bold] {state.thinking}")
    if state.session_id:
        parts.append(f"[bold]session[/bold] {state.session_id[:8]}...")
    return "  ".join(parts)


def update_status_bar(status_bar: StatusBar, *, state: UiState) -> None:
    """Render the current app state into the status bar."""
    status_bar.update(build_status_text(state))


def write_startup_banner(
    output: TranscriptLog,
    *,
    model: Any,
    workspace_root: Path,
    thinking: str | None,
) -> None:
    """Render the initial banner and provider hints."""
    output.write_line(f"jaca  {display_path(workspace_root)}")
    output.write_line(f"model {model}")
    if thinking:
        output.write_line(f"thinking {thinking}")

    model_str = str(model)
    if model_str.startswith("ollama"):
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        output.write_line(f"ollama {base_url}")
        if "localhost" in base_url or "127.0.0.1" in base_url:
            output.write_line("local ollama, no key needed")
    elif model_str.startswith("openai") and not os.environ.get("OPENAI_API_KEY"):
        output.write("\n")
        output.write_line("no OPENAI_API_KEY")
        output.write_line("use /provider openai <key>")
    elif model_str.startswith("anthropic") and not os.environ.get(
        "ANTHROPIC_API_KEY"
    ):
        output.write("\n")
        output.write_line("no ANTHROPIC_API_KEY")
        output.write_line("use /provider anthropic <key>")

    output.write("\n")


def resolve_thinking_setting(thinking: str | None) -> ThinkingSetting | None:
    """Convert TUI thinking strings into the runtime contract value."""
    if thinking is None:
        return None
    if thinking == "true":
        return True
    if thinking == "false":
        return False
    return thinking


def write_stream_event(output: TranscriptLog, event: Any) -> None:
    """Render one streamed runtime event into the transcript."""
    if event.type == "assistant_text_delta":
        output.write(event.delta, scroll_end=True)  # type: ignore[union-attr]
    elif event.type == "tool_call_started":
        output.write_line(f"  [{event.tool_name}]")  # type: ignore[union-attr]
    elif event.type == "tool_call_succeeded":
        result = event.result  # type: ignore[union-attr]
        if isinstance(result, dict) and result.get("ok") is False:
            output.write_line(f"  tool error: {result.get('message', '')}")
    elif event.type == "run_failed":
        output.write("\n")
        output.write_line(f"ERROR: {event.message}")  # type: ignore[union-attr]
    elif event.type == "run_succeeded":
        output.write("\n")


__all__ = [
    "display_path",
    "build_status_text",
    "resolve_thinking_setting",
    "update_status_bar",
    "write_startup_banner",
    "write_stream_event",
]
