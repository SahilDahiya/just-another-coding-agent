from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.instrumented import InstrumentedModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

from just_another_coding_agent.contracts.platform import (
    ShellFamily,
    detect_default_shell_family,
)
from just_another_coding_agent.contracts.session import SessionTurnContextEntry
from just_another_coding_agent.contracts.thinking import ThinkingSetting
from just_another_coding_agent.runtime.agent import (
    build_runtime_context_text,
    detect_current_timezone_label,
)
from just_another_coding_agent.runtime.models import resolve_canonical_model
from just_another_coding_agent.tools._workspace import normalize_workspace_root

RUNTIME_CONTEXT_MESSAGE_HEADER = "Runtime context for this turn:"
RUNTIME_CONTEXT_UPDATE_MESSAGE_HEADER = "Runtime context update for this turn:"
_DIFFABLE_TURN_CONTEXT_CLEAR_REASONS = frozenset(
    {
        "model_mismatch",
        "thinking_mismatch",
        "workspace_root_mismatch",
        "shell_family_mismatch",
        "current_date_mismatch",
        "timezone_mismatch",
        "runtime_context_mismatch",
    }
)


@dataclass(frozen=True)
class TurnContextBaselineDecision:
    status: Literal["missing", "reused", "cleared"]
    reason: str
    entry: SessionTurnContextEntry | None = None


@dataclass(frozen=True)
class RuntimeContextInjectionPlan:
    before_history_messages: tuple[ModelMessage, ...]
    after_history_messages: tuple[ModelMessage, ...]


def evaluate_turn_context_baseline(
    *,
    entry: SessionTurnContextEntry | None,
    model: Any,
    workspace_root: Path | str,
    current_date: date | None = None,
    shell_family: ShellFamily | None = None,
    timezone: str | None = None,
    thinking: ThinkingSetting | None = None,
    has_persisted_history: bool = False,
) -> TurnContextBaselineDecision:
    if entry is None:
        return TurnContextBaselineDecision(
            status="missing",
            reason=(
                "no_active_turn_context"
                if has_persisted_history
                else "missing"
            ),
        )

    resolved_workspace_root = normalize_workspace_root(workspace_root)
    resolved_shell_family = shell_family or detect_default_shell_family()
    resolved_current_date = current_date or date.today()
    resolved_model = _describe_turn_context_model(model)
    resolved_timezone = (
        detect_current_timezone_label() if timezone is None else timezone
    )
    expected_runtime_context_text = build_runtime_context_text(
        workspace_root=resolved_workspace_root,
        current_date=resolved_current_date,
        shell_family=resolved_shell_family,
        timezone=resolved_timezone,
        model_label=resolved_model,
        thinking=thinking,
    )

    if entry.workspace_root != str(resolved_workspace_root):
        return TurnContextBaselineDecision(
            status="cleared",
            reason="workspace_root_mismatch",
            entry=entry,
        )
    if entry.model != resolved_model:
        return TurnContextBaselineDecision(
            status="cleared",
            reason="model_mismatch",
            entry=entry,
        )
    if entry.thinking != thinking:
        return TurnContextBaselineDecision(
            status="cleared",
            reason="thinking_mismatch",
            entry=entry,
        )
    if entry.shell_family != resolved_shell_family:
        return TurnContextBaselineDecision(
            status="cleared",
            reason="shell_family_mismatch",
            entry=entry,
        )
    if entry.current_date != resolved_current_date.isoformat():
        return TurnContextBaselineDecision(
            status="cleared",
            reason="current_date_mismatch",
            entry=entry,
        )
    if entry.timezone != resolved_timezone:
        return TurnContextBaselineDecision(
            status="cleared",
            reason="timezone_mismatch",
            entry=entry,
        )
    if entry.runtime_context_text != expected_runtime_context_text:
        return TurnContextBaselineDecision(
            status="cleared",
            reason="runtime_context_mismatch",
            entry=entry,
        )

    return TurnContextBaselineDecision(
        status="reused",
        reason="matched",
        entry=entry,
    )


def build_session_turn_context_entry(
    *,
    run_id: str,
    model: Any,
    workspace_root: Path | str,
    current_date: date | None = None,
    shell_family: ShellFamily | None = None,
    timezone: str | None = None,
    thinking: ThinkingSetting | None = None,
) -> SessionTurnContextEntry:
    resolved_workspace_root = normalize_workspace_root(workspace_root)
    resolved_shell_family = shell_family or detect_default_shell_family()
    resolved_current_date = current_date or date.today()
    resolved_timezone = (
        detect_current_timezone_label() if timezone is None else timezone
    )
    resolved_model = _describe_turn_context_model(model)
    runtime_context_text = build_runtime_context_text(
        workspace_root=resolved_workspace_root,
        current_date=resolved_current_date,
        shell_family=resolved_shell_family,
        timezone=resolved_timezone,
        model_label=resolved_model,
        thinking=thinking,
    )

    return SessionTurnContextEntry(
        run_id=run_id,
        model=resolved_model,
        thinking=thinking,
        workspace_root=str(resolved_workspace_root),
        shell_family=resolved_shell_family,
        current_date=resolved_current_date.isoformat(),
        timezone=resolved_timezone,
        runtime_context_text=runtime_context_text,
    )


def build_runtime_context_message(
    runtime_context_text: str,
) -> ModelMessage:
    return ModelResponse(
        parts=[
            TextPart(
                content=(
                    f"{RUNTIME_CONTEXT_MESSAGE_HEADER}\n"
                    f"{runtime_context_text}"
                )
            )
        ],
        model_name="jaca-runtime-context",
    )


def build_runtime_context_update_message(
    runtime_context_update_text: str,
) -> ModelMessage:
    return ModelResponse(
        parts=[
            TextPart(
                content=(
                    f"{RUNTIME_CONTEXT_UPDATE_MESSAGE_HEADER}\n"
                    f"{runtime_context_update_text}"
                )
            )
        ],
        model_name="jaca-runtime-context",
    )


def build_runtime_context_prefix_messages(
    *,
    entry: SessionTurnContextEntry | None = None,
    workspace_root: Path | str | None = None,
    current_date: date | None = None,
    shell_family: ShellFamily | None = None,
    timezone: str | None = None,
    model: Any | None = None,
    thinking: ThinkingSetting | None = None,
) -> list[ModelMessage]:
    if entry is not None:
        return [build_runtime_context_message(entry.runtime_context_text)]

    if workspace_root is None:
        raise ValueError(
            "workspace_root is required when building runtime context without an entry"
        )

    runtime_context_kwargs: dict[str, object] = {
        "workspace_root": workspace_root,
        "current_date": current_date,
        "shell_family": shell_family,
        "timezone": timezone,
        "thinking": thinking,
    }
    if model is not None:
        runtime_context_kwargs["model_label"] = _describe_turn_context_model(model)

    return [
        build_runtime_context_message(
            build_runtime_context_text(**runtime_context_kwargs)
        )
    ]


def build_runtime_context_injection_plan(
    *,
    baseline_decision: TurnContextBaselineDecision | None,
    model: Any,
    workspace_root: Path | str,
    current_date: date | None = None,
    shell_family: ShellFamily | None = None,
    timezone: str | None = None,
    thinking: ThinkingSetting | None = None,
) -> RuntimeContextInjectionPlan:
    resolved_timezone = (
        detect_current_timezone_label() if timezone is None else timezone
    )
    resolved_model = _describe_turn_context_model(model)
    current_runtime_context_text = build_runtime_context_text(
        workspace_root=workspace_root,
        current_date=current_date,
        shell_family=shell_family,
        timezone=resolved_timezone,
        model_label=resolved_model,
        thinking=thinking,
    )
    current_message = build_runtime_context_message(current_runtime_context_text)

    if baseline_decision is None:
        return RuntimeContextInjectionPlan(
            before_history_messages=(current_message,),
            after_history_messages=(),
        )

    if baseline_decision.status == "missing":
        return RuntimeContextInjectionPlan(
            before_history_messages=(current_message,),
            after_history_messages=(),
        )

    if baseline_decision.entry is None:
        raise RuntimeError(
            "Turn-context baseline decisions with status reused/cleared "
            "must retain the source entry"
        )

    previous_message = build_runtime_context_message(
        baseline_decision.entry.runtime_context_text
    )
    if baseline_decision.status == "reused":
        return RuntimeContextInjectionPlan(
            before_history_messages=(previous_message,),
            after_history_messages=(),
        )

    if baseline_decision.reason not in _DIFFABLE_TURN_CONTEXT_CLEAR_REASONS:
        return RuntimeContextInjectionPlan(
            before_history_messages=(current_message,),
            after_history_messages=(),
        )

    runtime_context_update_text = build_runtime_context_update_text(
        entry=baseline_decision.entry,
        model=model,
        workspace_root=workspace_root,
        current_date=current_date,
        shell_family=shell_family,
        timezone=resolved_timezone,
        thinking=thinking,
    )
    return RuntimeContextInjectionPlan(
        before_history_messages=(previous_message,),
        after_history_messages=(
            build_runtime_context_update_message(runtime_context_update_text),
        ),
    )


def build_runtime_context_update_text(
    *,
    entry: SessionTurnContextEntry,
    model: Any,
    workspace_root: Path | str,
    current_date: date | None = None,
    shell_family: ShellFamily | None = None,
    timezone: str | None = None,
    thinking: ThinkingSetting | None = None,
) -> str:
    resolved_workspace_root = normalize_workspace_root(workspace_root)
    resolved_shell_family = shell_family or detect_default_shell_family()
    resolved_current_date = current_date or date.today()
    resolved_timezone = (
        detect_current_timezone_label() if timezone is None else timezone
    )
    resolved_model = _describe_turn_context_model(model)
    update_lines: list[str] = []

    if entry.current_date != resolved_current_date.isoformat():
        update_lines.append(
            f"Current date changed to {resolved_current_date.isoformat()}"
        )
    if entry.timezone != resolved_timezone:
        update_lines.append(f"Current timezone changed to {resolved_timezone}")
    if entry.workspace_root != str(resolved_workspace_root):
        update_lines.append(
            f"Current workspace root changed to {resolved_workspace_root}"
        )
    if entry.shell_family != resolved_shell_family:
        shell_label = (
            "powershell" if resolved_shell_family == "powershell" else "posix (bash)"
        )
        update_lines.append(f"Current shell family changed to {shell_label}")
    if entry.model != resolved_model:
        update_lines.append(f"Current model changed to {resolved_model}")
    if entry.thinking != thinking:
        update_lines.append(
            "Current thinking setting changed to "
            f"{_thinking_update_label(thinking)}"
        )
    if (
        not update_lines
        and entry.runtime_context_text
        != build_runtime_context_text(
            workspace_root=workspace_root,
            current_date=resolved_current_date,
            shell_family=resolved_shell_family,
            timezone=resolved_timezone,
            model_label=resolved_model,
            thinking=thinking,
        )
    ):
        update_lines.append("Runtime context framing changed")
    if not update_lines:
        raise ValueError("Runtime context update text requires at least one change")
    return "\n".join(update_lines)


def _thinking_update_label(thinking: ThinkingSetting | None) -> str:
    if thinking is None:
        return "provider default"
    if thinking is True:
        return "enabled"
    if thinking is False:
        return "disabled"
    return thinking


def _describe_turn_context_model(model: Any) -> str:
    if isinstance(model, str):
        return model

    resolved_model = resolve_canonical_model(model)
    current = resolved_model
    while isinstance(current, (InstrumentedModel, WrapperModel)):
        current = current.wrapped

    if isinstance(current, OpenAIResponsesModel):
        return f"openai-responses:{current.model_name}"
    if isinstance(current, OpenAIChatModel):
        if isinstance(current._provider, OllamaProvider):
            return f"ollama:{current.model_name}"
        if isinstance(current._provider, OpenRouterProvider) or isinstance(
            current, OpenRouterModel
        ):
            return f"openrouter:{current.model_name}"
        if isinstance(current._provider, OpenAIProvider):
            return f"openai-chat:{current.model_name}"
        return f"OpenAIChatModel:{current.model_name}"
    if isinstance(current, AnthropicModel):
        return f"anthropic:{current.model_name}"

    model_name = getattr(current, "model_name", None)
    if isinstance(model_name, str) and model_name:
        if type(current).__name__ == "GoogleModel":
            return f"google:{model_name}"
        return f"{type(current).__name__}:{model_name}"

    return type(current).__name__


__all__ = [
    "RuntimeContextInjectionPlan",
    "TurnContextBaselineDecision",
    "build_runtime_context_injection_plan",
    "build_session_turn_context_entry",
    "build_runtime_context_message",
    "build_runtime_context_prefix_messages",
    "build_runtime_context_update_message",
    "build_runtime_context_update_text",
    "evaluate_turn_context_baseline",
    "RUNTIME_CONTEXT_MESSAGE_HEADER",
    "RUNTIME_CONTEXT_UPDATE_MESSAGE_HEADER",
]
