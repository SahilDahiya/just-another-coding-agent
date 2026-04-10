from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

type SubagentEvidenceConfidence = Literal["low", "medium", "high"]

SUBAGENT_EVIDENCE_FIELD_NAMES = (
    "direct_evidence",
    "inference",
    "confidence",
    "ambiguities",
    "recommended_followup",
)


class SubagentEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    direct_evidence: list[str] = Field(min_length=1)
    inference: str = Field(min_length=1)
    confidence: SubagentEvidenceConfidence
    ambiguities: list[str] = Field(default_factory=list)
    recommended_followup: str = Field(min_length=1)


__all__ = [
    "SUBAGENT_EVIDENCE_FIELD_NAMES",
    "SubagentEvidence",
    "SubagentEvidenceConfidence",
]
