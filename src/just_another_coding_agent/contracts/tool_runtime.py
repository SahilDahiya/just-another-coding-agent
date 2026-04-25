from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Literal, Protocol, TypeAlias, TypeVar, runtime_checkable

from just_another_coding_agent.contracts.sandbox import ApprovalRequest
from just_another_coding_agent.contracts.sandbox_plan import SandboxExecutionPlan

CtxT = TypeVar("CtxT")
OutT = TypeVar("OutT")


@dataclass(frozen=True)
class SkipApproval:
    kind: Literal["skip"] = "skip"


@dataclass(frozen=True)
class NeedsApproval:
    request: ApprovalRequest
    denied_message: str
    missing_requester_message: str
    kind: Literal["needs_approval"] = "needs_approval"


@dataclass(frozen=True)
class ForbiddenApproval:
    request: ApprovalRequest
    denied_message: str
    kind: Literal["forbidden"] = "forbidden"


ExecApprovalRequirement: TypeAlias = (
    SkipApproval | NeedsApproval | ForbiddenApproval
)


@runtime_checkable
class Approvable(Protocol):
    def approval_requirement(self) -> ExecApprovalRequirement: ...


@runtime_checkable
class Sandboxable(Protocol):
    @property
    def sandbox_plan(self) -> SandboxExecutionPlan: ...


@runtime_checkable
class ToolRuntime(Approvable, Sandboxable, Protocol, Generic[CtxT, OutT]):
    async def run(self, ctx: CtxT) -> OutT: ...


__all__ = [
    "Approvable",
    "ExecApprovalRequirement",
    "ForbiddenApproval",
    "NeedsApproval",
    "Sandboxable",
    "SkipApproval",
    "ToolRuntime",
]
