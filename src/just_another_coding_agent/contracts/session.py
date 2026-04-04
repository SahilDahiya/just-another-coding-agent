from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pydantic_ai.messages import ModelMessage

from .platform import ShellFamily
from .run_events import RunEvent
from .thinking import ThinkingSetting

SESSION_FORMAT_VERSION = 10
SessionName = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"),
]


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


class SessionInfoEntry(_SessionEntryBase):
    type: Literal["session_info"] = "session_info"
    name: SessionName


class SessionForkEntry(_SessionEntryBase):
    type: Literal["session_fork"] = "session_fork"
    forked_from_session_id: str
    forked_from_run_id: str | None = None


class SessionMessagesEntry(_SessionEntryBase):
    type: Literal["session_messages"] = "session_messages"
    run_id: str
    messages: list[ModelMessage]


class SessionEventEntry(_SessionEntryBase):
    type: Literal["session_event"] = "session_event"
    run_id: str
    event: RunEvent


class SessionCompactionEntry(_SessionEntryBase):
    type: Literal["session_compaction"] = "session_compaction"
    compaction_id: str
    compacted_through_run_id: str
    replacement_messages: list[ModelMessage]


SessionEntry = Annotated[
    SessionHeaderEntry
    | SessionForkEntry
    | SessionInfoEntry
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
    fork: SessionForkEntry | None = None
    name: SessionName | None = None
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


class SessionMetadata(_SessionEntryBase):
    session_id: str
    name: SessionName | None = None
    created_at: datetime
    updated_at: datetime
    forked_from_session_id: str | None = None
    consecutive_auto_compaction_failures: int = 0


class SessionPreviewEntry(_SessionEntryBase):
    kind: Literal["user", "assistant", "error"]
    text: str


class SessionPreview(_SessionEntryBase):
    session_id: str
    entries: list[SessionPreviewEntry]
    truncated: bool = False


__all__ = [
    "LoadedSession",
    "SESSION_FORMAT_VERSION",
    "SessionCompactionEntry",
    "SessionEntry",
    "SessionEventEntry",
    "SessionForkEntry",
    "SessionHeaderEntry",
    "SessionInfoEntry",
    "SessionMessagesEntry",
    "SessionMetadata",
    "SessionName",
    "SessionPreview",
    "SessionPreviewEntry",
    "SessionRunEntry",
    "SessionRunRecord",
]
