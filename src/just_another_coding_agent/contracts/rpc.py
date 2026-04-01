from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from just_another_coding_agent.auth import (
    LocalSecretStoreStatus as RpcLocalSecretStoreStatus,
)
from just_another_coding_agent.auth import (
    ProviderAuthStatus as AuthProviderStatus,
)

from .model_catalog import ProviderName
from .run_events import RunEvent, SessionLifecycleEvent
from .session import SessionCompactionSummary as SessionCompactSummary
from .thinking import ThinkingSetting

SessionId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]


class _RpcModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SessionCreatePayload(_RpcModel):
    pass


class SessionCreateRequest(_RpcModel):
    id: str
    command: Literal["session.create"]
    payload: SessionCreatePayload


class SessionCompactPayload(_RpcModel):
    session_id: SessionId


class SessionCompactRequest(_RpcModel):
    id: str
    command: Literal["session.compact"]
    payload: SessionCompactPayload


class ModelCatalogPayload(_RpcModel):
    pass


class ModelCatalogRequest(_RpcModel):
    id: str
    command: Literal["model.catalog"]
    payload: ModelCatalogPayload


class AuthStatusPayload(_RpcModel):
    pass


class AuthStatusRequest(_RpcModel):
    id: str
    command: Literal["auth.status"]
    payload: AuthStatusPayload


class AuthSetPayload(_RpcModel):
    provider: ProviderName
    secret: str
    storage: Literal["keychain", "file"] = "keychain"


class AuthSetRequest(_RpcModel):
    id: str
    command: Literal["auth.set"]
    payload: AuthSetPayload


class AuthClearPayload(_RpcModel):
    provider: ProviderName


class AuthClearRequest(_RpcModel):
    id: str
    command: Literal["auth.clear"]
    payload: AuthClearPayload


class RunStartPayload(_RpcModel):
    session_id: SessionId
    prompt: str
    thinking: ThinkingSetting | None = None


class RunStartRequest(_RpcModel):
    id: str
    command: Literal["run.start"]
    payload: RunStartPayload


RpcRequest = Annotated[
    SessionCreateRequest
    | SessionCompactRequest
    | ModelCatalogRequest
    | AuthStatusRequest
    | AuthSetRequest
    | AuthClearRequest
    | RunStartRequest,
    Field(discriminator="command"),
]


class SessionCreateResponse(_RpcModel):
    session_id: SessionId


class SessionCompactResponse(_RpcModel):
    compaction_id: str
    summarized_through_run_id: str
    first_kept_run_id: str | None
    summary: SessionCompactSummary


class ModelCatalogModel(_RpcModel):
    model_id: str
    description: str


class ModelCatalogProvider(_RpcModel):
    provider: ProviderName
    default_model_id: str
    models: list[ModelCatalogModel]


class ModelCatalogResponse(_RpcModel):
    providers: list[ModelCatalogProvider]


class AuthStatusResponse(_RpcModel):
    providers: list[AuthProviderStatus]
    local_secret_store: RpcLocalSecretStoreStatus


class AuthSetResponse(_RpcModel):
    status: AuthProviderStatus


class AuthClearResponse(_RpcModel):
    status: AuthProviderStatus


class RpcResponseEnvelope(_RpcModel):
    type: Literal["rpc_response"] = "rpc_response"
    id: str
    response: (
        SessionCreateResponse
        | SessionCompactResponse
        | ModelCatalogResponse
        | AuthStatusResponse
        | AuthSetResponse
        | AuthClearResponse
    )


class RpcEventEnvelope(_RpcModel):
    type: Literal["rpc_event"] = "rpc_event"
    id: str
    event: RunEvent | SessionLifecycleEvent


class RpcErrorEnvelope(_RpcModel):
    type: Literal["rpc_error"] = "rpc_error"
    id: str | None
    error_type: str
    message: str


__all__ = [
    "AuthClearPayload",
    "AuthClearRequest",
    "AuthClearResponse",
    "AuthProviderStatus",
    "AuthSetPayload",
    "AuthSetRequest",
    "AuthSetResponse",
    "AuthStatusPayload",
    "AuthStatusRequest",
    "AuthStatusResponse",
    "RpcLocalSecretStoreStatus",
    "RpcErrorEnvelope",
    "RpcEventEnvelope",
    "RpcRequest",
    "RpcResponseEnvelope",
    "ModelCatalogModel",
    "ModelCatalogPayload",
    "ModelCatalogProvider",
    "ModelCatalogRequest",
    "ModelCatalogResponse",
    "RunStartPayload",
    "RunStartRequest",
    "SessionId",
    "SessionCompactPayload",
    "SessionCompactRequest",
    "SessionCompactResponse",
    "SessionCompactSummary",
    "SessionCreatePayload",
    "SessionCreateRequest",
    "SessionCreateResponse",
]
