"""Explicit UI state for the interactive TUI."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any


class UiPhase(StrEnum):
    """Named high-level phases for the TUI shell."""

    IDLE = "idle"
    STREAMING = "streaming"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    ERROR = "error"
    COMPACTING = "compacting"


@dataclass(frozen=True, slots=True)
class UiState:
    """Small explicit state model for the TUI shell."""

    model: Any
    workspace_root: Path
    thinking: str | None
    session_id: str | None = None
    phase: UiPhase = UiPhase.IDLE

    def with_model(self, model: Any) -> UiState:
        return replace(self, model=model)

    def with_thinking(self, thinking: str | None) -> UiState:
        return replace(self, thinking=thinking)

    def with_session_id(self, session_id: str | None) -> UiState:
        return replace(self, session_id=session_id)

    def with_phase(self, phase: UiPhase) -> UiState:
        return replace(self, phase=phase)


__all__ = ["UiPhase", "UiState"]
