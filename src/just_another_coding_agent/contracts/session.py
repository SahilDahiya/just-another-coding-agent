from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.messages import ModelMessage

from .platform import ShellFamily
from .run_events import RunEvent
from .thinking import ThinkingSetting

SESSION_FORMAT_VERSION = 7


class _SessionEntryBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SessionHeaderEntry(_SessionEntryBase):
    type: Literal["session_header"] = "session_header"
    version: Literal[SESSION_FORMAT_VERSION] = SESSION_FORMAT_VERSION
    workspace_root: str
    shell_family: ShellFamily = "posix"


class SessionRunEntry(_SessionEntryBase):
    type: Literal["session_run"] = "session_run"
    run_id: str
    prompt: str
    thinking: ThinkingSetting | None = None


class SessionMessagesEntry(_SessionEntryBase):
    type: Literal["session_messages"] = "session_messages"
    run_id: str
    messages: list[ModelMessage]


class SessionEventEntry(_SessionEntryBase):
    type: Literal["session_event"] = "session_event"
    run_id: str
    event: RunEvent


class SessionCompactionSummary(_SessionEntryBase):
    current_objective: str | None = None
    established_facts: list[str] = Field(default_factory=list)
    user_preferences: list[str] = Field(default_factory=list)
    important_paths: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    unresolved_work: list[str] = Field(default_factory=list)


class SessionCompactionEntry(_SessionEntryBase):
    type: Literal["session_compaction"] = "session_compaction"
    compaction_id: str
    summarized_through_run_id: str
    first_kept_run_id: str | None = None
    summary: SessionCompactionSummary


SessionEntry = Annotated[
    SessionHeaderEntry
    | SessionRunEntry
    | SessionMessagesEntry
    | SessionEventEntry
    | SessionCompactionEntry,
    Field(discriminator="type"),
]


class SessionRunRecord(_SessionEntryBase):
    run_id: str
    prompt: str
    thinking: ThinkingSetting | None = None
    messages: list[ModelMessage]
    events: list[RunEvent]


class LoadedSession(_SessionEntryBase):
    header: SessionHeaderEntry
    runs: list[SessionRunRecord]
    compactions: list[SessionCompactionEntry] = Field(default_factory=list)

    @property
    def message_history(self) -> list[ModelMessage]:
        return [message for run in self.runs for message in run.messages]

    @property
    def thinking(self) -> ThinkingSetting | None:
        for run in reversed(self.runs):
            if run.thinking is not None:
                return run.thinking
        return None

    @property
    def latest_compaction(self) -> SessionCompactionEntry | None:
        if not self.compactions:
            return None
        return self.compactions[-1]


__all__ = [
    "LoadedSession",
    "SESSION_FORMAT_VERSION",
    "SessionCompactionEntry",
    "SessionCompactionSummary",
    "SessionEntry",
    "SessionEventEntry",
    "SessionHeaderEntry",
    "SessionMessagesEntry",
    "SessionRunEntry",
    "SessionRunRecord",
]
