from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from .auth import LocalSecretStoreStatus as RpcLocalSecretStoreStatus
from .auth import OAuthProviderStatus
from .auth import ProviderAuthStatus as AuthProviderStatus
from .model_catalog import ProviderName
from .run_events import RunEvent, SessionLifecycleEvent
from .sandbox import ApprovalDecision, ApprovalPolicy, PermissionState, SandboxPolicy
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


class TraceLogfireStatusPayload(_RpcModel):
    pass


class TraceLogfireStatusRequest(_RpcModel):
    id: str
    command: Literal["trace.logfire_status"]
    payload: TraceLogfireStatusPayload


class AuthPrepareFilePayload(_RpcModel):
    provider: ProviderName


class AuthPrepareFileRequest(_RpcModel):
    id: str
    command: Literal["auth.prepare_file"]
    payload: AuthPrepareFilePayload


class AuthSetPayload(_RpcModel):
    provider: ProviderName
    secret: str
    storage: Literal["file"] = "file"


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


class AuthLoginOpenAICodexStartPayload(_RpcModel):
    pass


class AuthLoginOpenAICodexStartRequest(_RpcModel):
    id: str
    command: Literal["auth.login_openai_codex.start"]
    payload: AuthLoginOpenAICodexStartPayload


class AuthLoginOpenAICodexCompletePayload(_RpcModel):
    flow_id: str
    callback_or_code: str


class AuthLoginOpenAICodexCompleteRequest(_RpcModel):
    id: str
    command: Literal["auth.login_openai_codex.complete"]
    payload: AuthLoginOpenAICodexCompletePayload


class AuthLoginOpenAICodexWaitPayload(_RpcModel):
    flow_id: str


class AuthLoginOpenAICodexWaitRequest(_RpcModel):
    id: str
    command: Literal["auth.login_openai_codex.wait"]
    payload: AuthLoginOpenAICodexWaitPayload


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


class PermissionGetPayload(_RpcModel):
    session_id: SessionId


class PermissionGetRequest(_RpcModel):
    id: str
    command: Literal["permission.get"]
    payload: PermissionGetPayload


class PermissionSetPayload(_RpcModel):
    session_id: SessionId
    sandbox_policy: SandboxPolicy | None = None
    approval_policy: ApprovalPolicy | None = None

    @model_validator(mode="after")
    def _validate_non_empty(self) -> "PermissionSetPayload":
        if self.sandbox_policy is None and self.approval_policy is None:
            raise ValueError(
                "permission.set requires at least one explicit policy override"
            )
        return self


class PermissionSetRequest(_RpcModel):
    id: str
    command: Literal["permission.set"]
    payload: PermissionSetPayload


class ApprovalSubmitPayload(_RpcModel):
    session_id: SessionId
    decision: ApprovalDecision


class ApprovalSubmitRequest(_RpcModel):
    id: str
    command: Literal["approval.submit"]
    payload: ApprovalSubmitPayload


class WorkspaceProjectDocsPayload(_RpcModel):
    pass


class WorkspaceProjectDocsRequest(_RpcModel):
    id: str
    command: Literal["workspace.project_docs"]
    payload: WorkspaceProjectDocsPayload


RpcRequest = Annotated[
    SessionCreateRequest
    | SessionCompactRequest
    | SessionNameRequest
    | SessionPreviewRequest
    | ModelCatalogRequest
    | AuthStatusRequest
    | TraceLogfireStatusRequest
    | AuthPrepareFileRequest
    | AuthSetRequest
    | AuthClearRequest
    | AuthLoginOpenAICodexStartRequest
    | AuthLoginOpenAICodexCompleteRequest
    | AuthLoginOpenAICodexWaitRequest
    | RunStartRequest
    | RunEnqueueRequest
    | RunInterruptRequest
    | PermissionGetRequest
    | PermissionSetRequest
    | ApprovalSubmitRequest
    | WorkspaceProjectDocsRequest,
    Field(discriminator="command"),
]


class SessionCreateResponse(_RpcModel):
    session_id: SessionId
    project_docs: list["WorkspaceProjectDoc"] = Field(default_factory=list)


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
    oauth_providers: list[OAuthProviderStatus]


class TraceLogfireStatusResponse(_RpcModel):
    installed: bool
    credentials_configured: bool


class AuthPrepareFileResponse(_RpcModel):
    provider: ProviderName
    env_key: str
    file_path: str
    created: bool
    file_snippet: str
    entry_snippet: str


class AuthSetResponse(_RpcModel):
    status: AuthProviderStatus


class AuthClearResponse(_RpcModel):
    status: AuthProviderStatus


class AuthLoginOpenAICodexStartResponse(_RpcModel):
    flow_id: str
    auth_url: str
    instructions: str


class AuthLoginOpenAICodexCompleteResponse(_RpcModel):
    status: OAuthProviderStatus


class AuthLoginOpenAICodexWaitResponse(_RpcModel):
    status: OAuthProviderStatus


class RunEnqueueResponse(_RpcModel):
    session_id: SessionId
    queued_count: int


class RunInterruptResponse(_RpcModel):
    session_id: SessionId
    promoted_count: int


class PermissionGetResponse(_RpcModel):
    session_id: SessionId
    permission_state: PermissionState


class PermissionSetResponse(_RpcModel):
    session_id: SessionId
    permission_state: PermissionState


class ApprovalSubmitResponse(_RpcModel):
    session_id: SessionId
    decision: ApprovalDecision


class WorkspaceProjectDoc(_RpcModel):
    path: str
    filename: str
    truncated: bool = False


class WorkspaceProjectDocsResponse(_RpcModel):
    documents: list[WorkspaceProjectDoc]


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
        | TraceLogfireStatusResponse
        | AuthPrepareFileResponse
        | AuthSetResponse
        | AuthClearResponse
        | AuthLoginOpenAICodexStartResponse
        | AuthLoginOpenAICodexCompleteResponse
        | AuthLoginOpenAICodexWaitResponse
        | RunStartResponse
        | RunEnqueueResponse
        | RunInterruptResponse
        | PermissionGetResponse
        | PermissionSetResponse
        | ApprovalSubmitResponse
        | WorkspaceProjectDocsResponse
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
    "AuthLoginOpenAICodexCompletePayload",
    "AuthLoginOpenAICodexCompleteRequest",
    "AuthLoginOpenAICodexCompleteResponse",
    "AuthLoginOpenAICodexWaitPayload",
    "AuthLoginOpenAICodexWaitRequest",
    "AuthLoginOpenAICodexWaitResponse",
    "AuthLoginOpenAICodexStartPayload",
    "AuthLoginOpenAICodexStartRequest",
    "AuthLoginOpenAICodexStartResponse",
    "AuthPrepareFilePayload",
    "AuthPrepareFileRequest",
    "AuthPrepareFileResponse",
    "AuthProviderStatus",
    "AuthSetPayload",
    "AuthSetRequest",
    "AuthSetResponse",
    "AuthStatusPayload",
    "AuthStatusRequest",
    "AuthStatusResponse",
    "TraceLogfireStatusPayload",
    "TraceLogfireStatusRequest",
    "TraceLogfireStatusResponse",
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
    "PermissionGetPayload",
    "PermissionGetRequest",
    "PermissionGetResponse",
    "PermissionSetPayload",
    "PermissionSetRequest",
    "PermissionSetResponse",
    "ApprovalSubmitPayload",
    "ApprovalSubmitRequest",
    "ApprovalSubmitResponse",
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
    "WorkspaceProjectDoc",
    "WorkspaceProjectDocsPayload",
    "WorkspaceProjectDocsRequest",
    "WorkspaceProjectDocsResponse",
    "SessionCreatePayload",
    "SessionCreateRequest",
    "SessionCreateResponse",
]
