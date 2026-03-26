from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .run_events import RunEvent

SESSION_FORMAT_VERSION = 1


class _SessionEntryBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SessionHeaderEntry(_SessionEntryBase):
    type: Literal["session_header"] = "session_header"
    version: Literal[SESSION_FORMAT_VERSION] = SESSION_FORMAT_VERSION


class SessionRunEntry(_SessionEntryBase):
    type: Literal["session_run"] = "session_run"
    run_id: str
    prompt: str


class SessionEventEntry(_SessionEntryBase):
    type: Literal["session_event"] = "session_event"
    run_id: str
    event: RunEvent


SessionEntry = Annotated[
    SessionHeaderEntry | SessionRunEntry | SessionEventEntry,
    Field(discriminator="type"),
]


class SessionRunRecord(_SessionEntryBase):
    run_id: str
    prompt: str
    events: list[RunEvent]


class LoadedSession(_SessionEntryBase):
    header: SessionHeaderEntry
    runs: list[SessionRunRecord]


__all__ = [
    "LoadedSession",
    "SESSION_FORMAT_VERSION",
    "SessionEntry",
    "SessionEventEntry",
    "SessionHeaderEntry",
    "SessionRunEntry",
    "SessionRunRecord",
]
