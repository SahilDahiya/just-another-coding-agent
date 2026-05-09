from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from just_another_coding_agent.contracts.rpc import (
    RpcErrorEnvelope,
    RpcResponseEnvelope,
    WorkspaceProjectDoc,
    WorkspaceProjectDocsRequest,
    WorkspaceProjectDocsResponse,
    WorkspaceTrustAcceptRequest,
    WorkspaceTrustAcceptResponse,
    WorkspaceTrustStatusRequest,
    WorkspaceTrustStatusResponse,
)
from just_another_coding_agent.rpc.context import _RpcContext
from just_another_coding_agent.runtime.project_docs import (
    load_workspace_project_docs,
)
from just_another_coding_agent.runtime.workspace_trust import (
    accept_workspace_trust,
    resolve_workspace_trust_target,
    workspace_trust_status,
)


def _workspace_is_trusted(workspace_root: Path | str) -> bool:
    return workspace_trust_status(workspace_root).trusted


def _workspace_project_docs_root(workspace_root: Path | str) -> Path:
    return resolve_workspace_trust_target(workspace_root)


def _workspace_untrusted_error(
    *,
    request_id: str,
    workspace_root: Path | str,
) -> RpcErrorEnvelope:
    status = workspace_trust_status(workspace_root)
    return RpcErrorEnvelope(
        id=request_id,
        error_type="WorkspaceUntrusted",
        message=(
            "Workspace is not trusted yet. Accept trust for "
            f"{status.trust_target} before loading project instructions or "
            "starting a session."
        ),
    )


async def _handle_workspace_project_docs(
    request: WorkspaceProjectDocsRequest,
    ctx: _RpcContext,
) -> AsyncIterator[str]:
    if not _workspace_is_trusted(ctx.workspace_root):
        yield _workspace_untrusted_error(
            request_id=request.id,
            workspace_root=ctx.workspace_root,
        ).model_dump_json()
        return
    yield RpcResponseEnvelope(
        id=request.id,
        response=WorkspaceProjectDocsResponse(
            documents=[
                WorkspaceProjectDoc(
                    path=str(doc.path),
                    filename=doc.filename,
                    truncated=doc.truncated,
                )
                for doc in load_workspace_project_docs(
                    _workspace_project_docs_root(ctx.workspace_root)
                )
            ]
        ),
    ).model_dump_json()


async def _handle_workspace_trust_status(
    request: WorkspaceTrustStatusRequest,
    ctx: _RpcContext,
) -> AsyncIterator[str]:
    status = workspace_trust_status(ctx.workspace_root)
    yield RpcResponseEnvelope(
        id=request.id,
        response=WorkspaceTrustStatusResponse(
            trusted=status.trusted,
            trust_target=status.trust_target,
        ),
    ).model_dump_json()


async def _handle_workspace_trust_accept(
    request: WorkspaceTrustAcceptRequest,
    ctx: _RpcContext,
) -> AsyncIterator[str]:
    status = accept_workspace_trust(ctx.workspace_root)
    yield RpcResponseEnvelope(
        id=request.id,
        response=WorkspaceTrustAcceptResponse(
            trusted=status.trusted,
            trust_target=status.trust_target,
        ),
    ).model_dump_json()
