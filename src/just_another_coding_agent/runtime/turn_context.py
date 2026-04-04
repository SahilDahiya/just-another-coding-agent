from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.instrumented import InstrumentedModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider

from just_another_coding_agent.contracts.platform import (
    ShellFamily,
    detect_default_shell_family,
)
from just_another_coding_agent.contracts.session import SessionTurnContextEntry
from just_another_coding_agent.contracts.thinking import ThinkingSetting
from just_another_coding_agent.runtime.agent import build_canonical_instructions
from just_another_coding_agent.runtime.models import resolve_canonical_model
from just_another_coding_agent.tools._workspace import normalize_workspace_root


@dataclass(frozen=True)
class TurnContextBaselineDecision:
    status: Literal["missing", "reused", "cleared"]
    reason: str
    entry: SessionTurnContextEntry | None = None


def evaluate_turn_context_baseline(
    *,
    entry: SessionTurnContextEntry | None,
    model: Any,
    workspace_root: Path | str,
    current_date: date | None = None,
    shell_family: ShellFamily | None = None,
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
    expected_instructions = build_canonical_instructions(
        workspace_root=resolved_workspace_root,
        current_date=resolved_current_date,
        shell_family=resolved_shell_family,
    )

    if entry.workspace_root != str(resolved_workspace_root):
        return TurnContextBaselineDecision(
            status="cleared",
            reason="workspace_root_mismatch",
        )
    if entry.model != resolved_model:
        return TurnContextBaselineDecision(
            status="cleared",
            reason="model_mismatch",
        )
    if entry.thinking != thinking:
        return TurnContextBaselineDecision(
            status="cleared",
            reason="thinking_mismatch",
        )
    if entry.shell_family != resolved_shell_family:
        return TurnContextBaselineDecision(
            status="cleared",
            reason="shell_family_mismatch",
        )
    if entry.current_date != resolved_current_date.isoformat():
        return TurnContextBaselineDecision(
            status="cleared",
            reason="current_date_mismatch",
        )
    if entry.instructions != expected_instructions:
        return TurnContextBaselineDecision(
            status="cleared",
            reason="instructions_mismatch",
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
    thinking: ThinkingSetting | None = None,
) -> SessionTurnContextEntry:
    resolved_workspace_root = normalize_workspace_root(workspace_root)
    resolved_shell_family = shell_family or detect_default_shell_family()
    resolved_current_date = current_date or date.today()
    instructions = build_canonical_instructions(
        workspace_root=resolved_workspace_root,
        current_date=resolved_current_date,
        shell_family=resolved_shell_family,
    )

    return SessionTurnContextEntry(
        run_id=run_id,
        model=_describe_turn_context_model(model),
        thinking=thinking,
        workspace_root=str(resolved_workspace_root),
        shell_family=resolved_shell_family,
        current_date=resolved_current_date.isoformat(),
        instructions=instructions,
    )


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
    "TurnContextBaselineDecision",
    "build_session_turn_context_entry",
    "evaluate_turn_context_baseline",
]
