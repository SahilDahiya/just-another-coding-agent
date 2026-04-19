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
from just_another_coding_agent.contracts.sandbox import EffectiveCapabilities
from just_another_coding_agent.contracts.thinking import ThinkingSetting
from just_another_coding_agent.contracts.tools import CANONICAL_TOOL_NAMES
from just_another_coding_agent.runtime.models import resolve_canonical_model
from just_another_coding_agent.runtime.prompt_layers import build_base_product_prompt
from just_another_coding_agent.runtime.tool_args import (
    CanonicalValidatedToolArgsCapability,
)
from just_another_coding_agent.tools._workspace import normalize_workspace_root
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.registry import build_canonical_toolset

CANONICAL_AGENT_OUTPUT_RETRIES = 1_000_000
CANONICAL_AGENT_TOOL_CORRECTION_RETRIES = 2
_UNSET = object()

CANONICAL_AGENT_INSTRUCTIONS = build_base_product_prompt()


def build_static_agent_instructions(
    *,
    tool_names: Sequence[str] = CANONICAL_TOOL_NAMES,
) -> str:
    if tuple(tool_names) == CANONICAL_TOOL_NAMES:
        return CANONICAL_AGENT_INSTRUCTIONS
    return build_base_product_prompt(tool_names=tool_names)


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
    effective_capabilities: EffectiveCapabilities | None | object = _UNSET,
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
    if (
        effective_capabilities is not _UNSET
        and effective_capabilities is not None
    ):
        sections.extend(
            [
                "Current filesystem access: "
                f"{effective_capabilities.filesystem_access}",
                f"Current network access: {effective_capabilities.network_access}",
                "Current execution isolation: "
                f"{effective_capabilities.execution_isolation}",
                f"Current approval policy: {effective_capabilities.approval_mode}",
            ]
        )
        if (
            effective_capabilities.execution_isolation == "sandboxed"
            and effective_capabilities.filesystem_access
            in {"read_only", "workspace_write"}
        ):
            sections.extend(
                [
                    (
                        "Shell sandbox path note: sandboxed shell preserves "
                        "host-visible path semantics for mounted roots."
                    ),
                    (
                        "Tool coverage note: default-mode read-side tools "
                        "can inspect paths anywhere on disk without approval. "
                        "Shell stays sandboxed and supports approval-backed "
                        "extra filesystem roots for explicit outside-workspace "
                        "paths, and write-side tools still use backend file "
                        "operations with scoped approval for outside-workspace "
                        "paths."
                    ),
                ]
            )

    return "\n".join(sections)


def build_canonical_instructions(
    *,
    workspace_root: Path | str,
    current_date: date | None = None,
    shell_family: ShellFamily | None = None,
    tool_names: Sequence[str] = CANONICAL_TOOL_NAMES,
) -> str:
    return "\n".join(
        [
            build_static_agent_instructions(tool_names=tool_names),
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
    instructions: str | None = None,
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
        instructions=(
            build_static_agent_instructions(tool_names=tool_names)
            if instructions is None
            else instructions
        ),
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
    "CANONICAL_AGENT_TOOL_CORRECTION_RETRIES",
    "CANONICAL_AGENT_INSTRUCTIONS",
    "build_canonical_agent",
    "build_canonical_instructions",
    "build_runtime_context_text",
    "build_static_agent_instructions",
    "detect_current_timezone_label",
]
