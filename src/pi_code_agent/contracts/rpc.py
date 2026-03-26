from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from .run_events import RunEvent


class _RpcModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RunStartPayload(_RpcModel):
    prompt: str


class RunStartRequest(_RpcModel):
    id: str
    command: Literal["run.start"]
    payload: RunStartPayload


class RpcEventEnvelope(_RpcModel):
    type: Literal["rpc_event"] = "rpc_event"
    id: str
    event: RunEvent


class RpcErrorEnvelope(_RpcModel):
    type: Literal["rpc_error"] = "rpc_error"
    id: str | None
    error_type: str
    message: str


__all__ = [
    "RpcErrorEnvelope",
    "RpcEventEnvelope",
    "RunStartPayload",
    "RunStartRequest",
]
