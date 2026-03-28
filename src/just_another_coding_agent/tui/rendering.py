"""Rendering helpers for the interactive TUI."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from rich.style import Style
from rich.text import Text

from just_another_coding_agent.contracts.thinking import ThinkingSetting

from .state import UiPhase, UiState
from .theme import DEFAULT_THEME
from .widgets import StatusBar, TranscriptLog


def build_phase_label(phase: UiPhase, motion_tick: int = 0) -> str:
    """Render the current phase, with restrained motion for active states."""
    if phase in {UiPhase.STREAMING, UiPhase.COMPACTING}:
        return f"{phase}{'.' * ((motion_tick % 3) + 1)}"
    if phase == UiPhase.COMPLETED:
        return "completed"
    return str(phase)


def build_prompt_marker_text(phase: UiPhase, motion_tick: int = 0) -> str:
    """Render the prompt marker for the current shell phase."""
    if phase == UiPhase.STREAMING:
        return ">>" if motion_tick % 2 == 0 else "> "
    if phase == UiPhase.COMPACTING:
        return "::" if motion_tick % 2 == 0 else ".:"
    if phase == UiPhase.COMPLETED:
        return "ok"
    if phase == UiPhase.INTERRUPTED:
        return "!!"
    if phase == UiPhase.ERROR:
        return "x "
    return "> "


def build_prompt_footer_text(phase: UiPhase) -> str:
    """Render one low-salience footer line for the prompt zone."""
    if phase == UiPhase.STREAMING:
        return "working  ctrl+c interrupt"
    if phase == UiPhase.COMPACTING:
        return "compacting session"
    if phase == UiPhase.COMPLETED:
        return "ready"
    if phase == UiPhase.INTERRUPTED:
        return "interrupted  enter next prompt"
    if phase == UiPhase.ERROR:
        return "last run failed  edit prompt or retry"
    return "ready  /help  up/down recall  ctrl+u clear"


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


def build_status_text(state: UiState, motion_tick: int = 0) -> str:
    """Build the current status-bar line from explicit UI state."""
    parts = [
        build_phase_label(state.phase, motion_tick),
        str(state.model),
        display_path(state.workspace_root),
    ]
    if state.thinking:
        parts.append(f"thinking={state.thinking}")
    if state.session_id:
        parts.append(f"session={state.session_id[:8]}")
    return " | ".join(parts)


def update_status_bar(
    status_bar: StatusBar,
    *,
    state: UiState,
    motion_tick: int = 0,
) -> None:
    """Render the current app state into the status bar."""
    status_bar.update(build_status_text(state, motion_tick))


def write_startup_banner(
    output: TranscriptLog,
    *,
    model: Any,
    workspace_root: Path,
    thinking: str | None,
) -> None:
    """Render the initial banner and provider hints."""
    headline = f"jaca  {display_path(workspace_root)}  |  model {model}"
    if thinking:
        headline += f"  |  thinking {thinking}"
    output.write_renderable(
        Text(headline + "\n", style=Style(color=DEFAULT_THEME.text_soft, bold=True)),
        headline + "\n",
    )

    model_str = str(model)
    if model_str.startswith("ollama"):
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        provider_line = f"ollama {base_url}"
        output.write_renderable(
            Text(provider_line + "\n", style=Style(color=DEFAULT_THEME.text_muted)),
            provider_line + "\n",
        )
        if "localhost" in base_url or "127.0.0.1" in base_url:
            local_hint = "local ollama, no key needed"
            output.write_renderable(
                Text(local_hint + "\n", style=Style(color=DEFAULT_THEME.text_muted)),
                local_hint + "\n",
            )
    elif model_str.startswith("openai") and not os.environ.get("OPENAI_API_KEY"):
        output.write("\n")
        output.write_renderable(
            Text(
                "no OPENAI_API_KEY\n",
                style=Style(color=DEFAULT_THEME.error),
            ),
            "no OPENAI_API_KEY\n",
        )
        output.write_renderable(
            Text(
                "use /provider openai <key>\n",
                style=Style(color=DEFAULT_THEME.text_muted),
            ),
            "use /provider openai <key>\n",
        )
    elif model_str.startswith("anthropic") and not os.environ.get(
        "ANTHROPIC_API_KEY"
    ):
        output.write("\n")
        output.write_renderable(
            Text(
                "no ANTHROPIC_API_KEY\n",
                style=Style(color=DEFAULT_THEME.error),
            ),
            "no ANTHROPIC_API_KEY\n",
        )
        output.write_renderable(
            Text(
                "use /provider anthropic <key>\n",
                style=Style(color=DEFAULT_THEME.text_muted),
            ),
            "use /provider anthropic <key>\n",
        )

    output.write("\n")


def write_user_turn(output: TranscriptLog, prompt: str) -> None:
    """Render one user prompt as the start of a compact transcript turn."""
    output.ensure_block_gap()
    user_line = Text()
    user_line.append(">", style=Style(color=DEFAULT_THEME.accent))
    user_line.append(" ")
    user_line.append(prompt, style=Style(color=DEFAULT_THEME.text, bold=True))
    user_line.append("\n")
    output.write_renderable(user_line, f"> {prompt}\n")


def build_tool_preview(
    tool_name: str,
    args: Any,
    *,
    args_valid: bool | None,
    activity: Any | None = None,
) -> str | None:
    """Build a short human-readable preview for a tool call."""
    activity_title = getattr(activity, "title", None)
    if isinstance(activity_title, str):
        normalized_title = " ".join(activity_title.split())
        if normalized_title == tool_name:
            return None
        prefix = f"{tool_name} "
        if normalized_title.startswith(prefix):
            return normalized_title[len(prefix) :]
        if normalized_title:
            return normalized_title

    if args_valid is False or not isinstance(args, dict):
        return None
    if tool_name == "bash":
        command = args.get("command")
        if isinstance(command, str) and command.strip():
            return _truncate_inline(command)
        return None
    key_by_tool = {
        "read": "path",
        "write": "path",
        "edit": "path",
        "grep": "pattern",
        "ls": "path",
        "find": "pattern",
    }
    key = key_by_tool.get(tool_name)
    value = args.get(key) if key is not None else None
    if isinstance(value, str) and value.strip():
        return _truncate_inline(value)
    return None


def build_tool_summary(activity: Any | None, fallback: str | None = None) -> str | None:
    """Prefer backend-owned activity summaries over transport-level text."""
    activity_summary = getattr(activity, "summary", None)
    if isinstance(activity_summary, str):
        normalized_summary = " ".join(activity_summary.split())
        if normalized_summary:
            return normalized_summary
    return fallback


def build_tool_duration(activity: Any | None) -> str | None:
    """Render backend-owned duration metadata for compact tool rows."""
    duration_ms = getattr(activity, "duration_ms", None)
    if not isinstance(duration_ms, int) or duration_ms < 0:
        return None
    if duration_ms < 1000:
        return f"{duration_ms}ms"
    return f"{duration_ms / 1000:.1f}s"


def _truncate_inline(text: str, *, limit: int = 56) -> str:
    """Collapse whitespace and truncate for compact transcript rows."""
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


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
        output.append_live_text(event.delta)  # type: ignore[union-attr]
    elif event.type == "tool_call_started":
        activity = getattr(event, "activity", None)
        output.start_tool_activity(
            event.tool_call_id,  # type: ignore[union-attr]
            event.tool_name,  # type: ignore[union-attr]
            build_tool_preview(
                event.tool_name,  # type: ignore[union-attr]
                event.args,  # type: ignore[union-attr]
                args_valid=event.args_valid,  # type: ignore[union-attr]
                activity=activity,
            ),
        )
    elif event.type == "tool_call_succeeded":
        activity = getattr(event, "activity", None)
        result = event.result  # type: ignore[union-attr]
        if isinstance(result, dict) and result.get("ok") is False:
            output.fail_tool_activity(
                event.tool_call_id,  # type: ignore[union-attr]
                event.tool_name,  # type: ignore[union-attr]
                build_tool_summary(
                    activity,
                    str(result.get("message", "")),
                )
                or "tool error",
                build_tool_duration(activity),
            )
        else:
            output.finish_tool_activity(
                event.tool_call_id,  # type: ignore[union-attr]
                build_tool_summary(activity),
                build_tool_duration(activity),
            )
    elif event.type == "tool_call_failed":
        activity = getattr(event, "activity", None)
        output.fail_tool_activity(
            event.tool_call_id,  # type: ignore[union-attr]
            event.tool_name,  # type: ignore[union-attr]
            build_tool_summary(
                activity,
                event.message,  # type: ignore[union-attr]
            )
            or "tool failure",
            build_tool_duration(activity),
        )
    elif event.type == "run_failed":
        output.end_live_text()
        output.write_line(f"error  {event.message}")  # type: ignore[union-attr]
    elif event.type == "run_succeeded":
        output.render_completed_assistant_markdown(  # type: ignore[union-attr]
            event.output_text
        )


__all__ = [
    "display_path",
    "build_phase_label",
    "build_prompt_footer_text",
    "build_prompt_marker_text",
    "build_status_text",
    "build_tool_duration",
    "build_tool_preview",
    "build_tool_summary",
    "resolve_thinking_setting",
    "update_status_bar",
    "write_startup_banner",
    "write_stream_event",
    "write_user_turn",
]
