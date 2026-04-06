from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .auth import LocalSecretStoreStatus as RpcLocalSecretStoreStatus
from .auth import ProviderAuthStatus as AuthProviderStatus
from .model_catalog import ProviderName
from .run_events import RunEvent, SessionLifecycleEvent
from .session import SessionName
from .session import SessionPreview as SessionPreviewResponse
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


class SessionNamePayload(_RpcModel):
    session_id: SessionId
    name: str


class SessionNameRequest(_RpcModel):
    id: str
    command: Literal["session.name"]
    payload: SessionNamePayload


class SessionPreviewPayload(_RpcModel):
    session_id: SessionId


class SessionPreviewRequest(_RpcModel):
    id: str
    command: Literal["session.preview"]
    payload: SessionPreviewPayload


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


class RunStartResponse(_RpcModel):
    session_id: SessionId


class RunEnqueuePayload(_RpcModel):
    session_id: SessionId
    prompt: str
    mode: Literal["later", "next"] = "later"


class RunEnqueueRequest(_RpcModel):
    id: str
    command: Literal["run.enqueue"]
    payload: RunEnqueuePayload


class RunInterruptPayload(_RpcModel):
    session_id: SessionId
    promote_queued_steer: bool = False


class RunInterruptRequest(_RpcModel):
    id: str
    command: Literal["run.interrupt"]
    payload: RunInterruptPayload


RpcRequest = Annotated[
    SessionCreateRequest
    | SessionCompactRequest
    | SessionNameRequest
    | SessionPreviewRequest
    | ModelCatalogRequest
    | AuthStatusRequest
    | AuthSetRequest
    | AuthClearRequest
    | RunStartRequest
    | RunEnqueueRequest
    | RunInterruptRequest,
    Field(discriminator="command"),
]


class SessionCreateResponse(_RpcModel):
    session_id: SessionId


class SessionCompactResponse(_RpcModel):
    compaction_id: str
    compacted_through_run_id: str


class SessionNameResponse(_RpcModel):
    session_id: SessionId
    name: SessionName


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


class RunEnqueueResponse(_RpcModel):
    session_id: SessionId
    queued_count: int


class RunInterruptResponse(_RpcModel):
    session_id: SessionId
    promoted_count: int


class RpcResponseEnvelope(_RpcModel):
    type: Literal["rpc_response"] = "rpc_response"
    id: str
    response: (
        SessionCreateResponse
        | SessionCompactResponse
        | SessionNameResponse
        | SessionPreviewResponse
        | ModelCatalogResponse
        | AuthStatusResponse
        | AuthSetResponse
        | AuthClearResponse
        | RunStartResponse
        | RunEnqueueResponse
        | RunInterruptResponse
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
    "RunStartResponse",
    "RunEnqueuePayload",
    "RunEnqueueRequest",
    "RunEnqueueResponse",
    "RunInterruptPayload",
    "RunInterruptRequest",
    "RunInterruptResponse",
    "SessionId",
    "SessionCompactPayload",
    "SessionCompactRequest",
    "SessionCompactResponse",
    "SessionPreviewPayload",
    "SessionPreviewRequest",
    "SessionPreviewResponse",
    "SessionNamePayload",
    "SessionNameRequest",
    "SessionNameResponse",
    "SessionCreatePayload",
    "SessionCreateRequest",
    "SessionCreateResponse",
]
