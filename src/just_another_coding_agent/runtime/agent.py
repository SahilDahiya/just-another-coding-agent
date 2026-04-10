from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic_ai import Agent

from just_another_coding_agent.contracts.platform import (
    ShellFamily,
    detect_default_shell_family,
)
from just_another_coding_agent.contracts.thinking import ThinkingSetting
from just_another_coding_agent.contracts.tools import CANONICAL_TOOL_NAMES
from just_another_coding_agent.runtime.models import resolve_canonical_model
from just_another_coding_agent.runtime.tool_args import (
    CanonicalValidatedToolArgsCapability,
)
from just_another_coding_agent.tools._workspace import normalize_workspace_root
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.registry import build_canonical_toolset

CANONICAL_AGENT_OUTPUT_RETRIES = 1_000_000
CANONICAL_AGENT_TOOL_CORRECTION_RETRIES = 2
CANONICAL_STATIC_PROMPT_MAX_CHARS = 2_400
_UNSET = object()

CANONICAL_AGENT_INSTRUCTIONS = "\n".join(
    [
        (
            "You are a headless coding assistant operating inside one "
            "configured workspace."
        ),
        "Use only these tools: read, write, edit, shell, grep, ls, find.",
        "Prefer read to examine files instead of shelling out just to view files.",
        (
            "Use edit for precise surgical changes; it tries exact matching "
            "first and then a normalized fallback for minor formatting differences."
        ),
        "Use write only for new files or complete rewrites.",
        "Use grep for content search across files.",
        "Use ls for bounded directory listings.",
        "Use find for file discovery by glob pattern.",
        "Use shell for builds, commands, and verification.",
        (
            "Use read with offset and limit for large files instead of "
            "pulling everything at once."
        ),
        (
            "If a tool returns an object with ok: false, treat it as an "
            "operational error and decide the next corrective step yourself."
        ),
        (
            "Do not claim you created, edited, or saved a file unless you "
            "actually used write or edit, or verified the result with read or shell."
        ),
        (
            "After code changes or required file outputs, run the smallest "
            "relevant verification step before concluding."
        ),
        (
            "When the user asks to run tests, lint, or another obvious "
            "verification step, run the narrowest relevant command directly; "
            "inspect first only if the command or scope is ambiguous."
        ),
        "Do not invent tools or alternate behaviors.",
        "Do not rely on fallbacks.",
        "Only uncaught tool failures end the run automatically.",
        "Default response style: brief, direct, and outcome-first.",
        (
            "Do not restate the user's request or narrate routine process "
            "unless that context is necessary."
        ),
        (
            "During work, keep progress updates to one short sentence focused "
            "on the next action or concrete finding."
        ),
        (
            "Final answers should usually be one short paragraph: state what "
            "changed or what you found, then mention verification or blockers."
        ),
        (
            "Use bullets only when there are multiple distinct findings, "
            "steps, or options."
        ),
        (
            "If no files changed, answer the question directly without a "
            "change-style summary."
        ),
        "Refer to files clearly by path.",
        "For read, write, and edit, relative paths resolve from the workspace root.",
        "shell runs in the workspace root and no tool is a filesystem sandbox.",
    ]
)


def build_static_agent_instructions() -> str:
    return CANONICAL_AGENT_INSTRUCTIONS


def _shell_family_prompt_label(shell_family: ShellFamily) -> str:
    if shell_family == "powershell":
        return "powershell"
    return "posix (bash)"


def _thinking_prompt_label(thinking: ThinkingSetting | None) -> str:
    if thinking is None:
        return "provider default"
    if thinking is True:
        return "enabled"
    if thinking is False:
        return "disabled"
    return thinking


def detect_current_timezone_label() -> str:
    current_time = datetime.now().astimezone()
    tzinfo = current_time.tzinfo
    if tzinfo is None:
        return "unknown"
    zone_key = getattr(tzinfo, "key", None)
    if isinstance(zone_key, str) and zone_key:
        return zone_key
    zone_name = getattr(tzinfo, "zone", None)
    if isinstance(zone_name, str) and zone_name:
        return zone_name
    label = tzinfo.tzname(current_time)
    if isinstance(label, str) and label:
        return label
    fallback = str(tzinfo)
    return fallback or "unknown"


def build_runtime_context_text(
    *,
    workspace_root: Path | str,
    current_date: date | None = None,
    shell_family: ShellFamily | None = None,
    timezone: str | None | object = _UNSET,
    model_label: str | object = _UNSET,
    thinking: ThinkingSetting | None | object = _UNSET,
) -> str:
    root = normalize_workspace_root(workspace_root)
    resolved_date = current_date or date.today()
    effective_shell_family = shell_family or detect_default_shell_family()

    sections = [f"Current date: {resolved_date.isoformat()}"]
    if timezone is not _UNSET:
        timezone_label = timezone if timezone is not None else "unknown"
        sections.append(f"Current timezone: {timezone_label}")
    sections.extend(
        [
            f"Current workspace root: {root}",
            (
                "Current shell family: "
                f"{_shell_family_prompt_label(effective_shell_family)}"
            ),
        ]
    )
    if model_label is not _UNSET:
        sections.append(f"Current model: {model_label}")
    if thinking is not _UNSET:
        sections.append(
            f"Current thinking setting: {_thinking_prompt_label(thinking)}"
        )

    return "\n".join(sections)


def build_canonical_instructions(
    *,
    workspace_root: Path | str,
    current_date: date | None = None,
    shell_family: ShellFamily | None = None,
) -> str:
    return "\n".join(
        [
            build_static_agent_instructions(),
            build_runtime_context_text(
                workspace_root=workspace_root,
                current_date=current_date,
                shell_family=shell_family,
            ),
        ]
    )


def build_canonical_agent(
    *,
    model: Any,
    workspace_root: Path | str,
    current_date: date | None = None,
    shell_family: ShellFamily | None = None,
    tool_names: Sequence[str] = CANONICAL_TOOL_NAMES,
) -> Agent[WorkspaceDeps, str]:
    normalize_workspace_root(workspace_root)
    resolved_model = resolve_canonical_model(model)

    # The canonical agent returns plain assistant text, not structured output.
    # Codex/pi-style interaction keeps the run alive until the model chooses to
    # stop; we do not want PydanticAI's output-validation retry ceiling to be
    # the effective stop condition for this plain-string agent. If this agent
    # ever stops being `output_type=str`, this policy should be revisited rather
    # than silently inherited by a structured-output path.
    #
    # Malformed tool correction is runtime-owned. The framework should not hide
    # extra retry loops for invented tool names or malformed args inside one
    # provider run; the runtime restarts from a sanitized boundary instead.
    agent = Agent(
        resolved_model,
        output_type=str,
        retries=0,
        output_retries=CANONICAL_AGENT_OUTPUT_RETRIES,
        instructions=build_static_agent_instructions(),
        deps_type=WorkspaceDeps,
        toolsets=[build_canonical_toolset(tool_names)],
        capabilities=[CanonicalValidatedToolArgsCapability()],
    )
    if agent.output_type is not str:
        raise RuntimeError(
            "Canonical agent output retry policy only applies to plain string "
            "output. Revisit `output_retries` before changing `output_type`."
        )
    return agent


__all__ = [
    "CANONICAL_AGENT_OUTPUT_RETRIES",
    "CANONICAL_STATIC_PROMPT_MAX_CHARS",
    "CANONICAL_AGENT_TOOL_CORRECTION_RETRIES",
    "CANONICAL_AGENT_INSTRUCTIONS",
    "build_canonical_agent",
    "build_canonical_instructions",
    "build_runtime_context_text",
    "build_static_agent_instructions",
    "detect_current_timezone_label",
]
