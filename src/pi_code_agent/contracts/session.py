from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.messages import ModelMessage

from .run_events import RunEvent

SESSION_FORMAT_VERSION = 2


class _SessionEntryBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SessionHeaderEntry(_SessionEntryBase):
    type: Literal["session_header"] = "session_header"
    version: Literal[SESSION_FORMAT_VERSION] = SESSION_FORMAT_VERSION
    workspace_root: str


class SessionRunEntry(_SessionEntryBase):
    type: Literal["session_run"] = "session_run"
    run_id: str
    prompt: str


class SessionMessagesEntry(_SessionEntryBase):
    type: Literal["session_messages"] = "session_messages"
    run_id: str
    messages: list[ModelMessage]


class SessionEventEntry(_SessionEntryBase):
    type: Literal["session_event"] = "session_event"
    run_id: str
    event: RunEvent


SessionEntry = Annotated[
    SessionHeaderEntry | SessionRunEntry | SessionMessagesEntry | SessionEventEntry,
    Field(discriminator="type"),
]


class SessionRunRecord(_SessionEntryBase):
    run_id: str
    prompt: str
    messages: list[ModelMessage]
    events: list[RunEvent]


class LoadedSession(_SessionEntryBase):
    header: SessionHeaderEntry
    runs: list[SessionRunRecord]

    @property
    def message_history(self) -> list[ModelMessage]:
        return [message for run in self.runs for message in run.messages]


__all__ = [
    "LoadedSession",
    "SESSION_FORMAT_VERSION",
    "SessionEntry",
    "SessionEventEntry",
    "SessionHeaderEntry",
    "SessionMessagesEntry",
    "SessionRunEntry",
    "SessionRunRecord",
]
