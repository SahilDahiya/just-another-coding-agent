from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

MCP_TOOL_NAME_PREFIX = "mcp__"
JACA_ONBOARDING_MCP_SERVER_ID = "jaca_onboarding"

McpCallSource = Literal["top_level_model", "code_mode"]
McpFailureKind = Literal[
    "startup_failed",
    "discovery_failed",
    "tool_failed",
    "resource_failed",
]

_MCP_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class _McpContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _validate_mcp_name(*, value: str, field_name: str) -> str:
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    if "__" in value:
        raise ValueError(f"{field_name} must not contain '__'")
    if not _MCP_NAME_PATTERN.fullmatch(value):
        raise ValueError(
            f"{field_name} must start with a lowercase letter and contain only "
            "lowercase letters, digits, and single underscores"
        )
    return value


class McpToolIdentity(_McpContractModel):
    server_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_identity(self) -> "McpToolIdentity":
        _validate_mcp_name(value=self.server_id, field_name="server_id")
        _validate_mcp_name(value=self.tool_name, field_name="tool_name")
        return self

    @computed_field
    @property
    def model_tool_name(self) -> str:
        return make_mcp_model_tool_name(
            server_id=self.server_id,
            tool_name=self.tool_name,
        )


class McpToolCallProvenance(_McpContractModel):
    source: McpCallSource
    parent_tool_call_id: str | None = None
    code_mode_cell_id: str | None = None

    @model_validator(mode="after")
    def _validate_source_fields(self) -> "McpToolCallProvenance":
        if self.source == "top_level_model":
            if (
                self.parent_tool_call_id is not None
                or self.code_mode_cell_id is not None
            ):
                raise ValueError(
                    "top-level MCP calls must not carry Code Mode parent fields"
                )
            return self

        if self.parent_tool_call_id is None:
            raise ValueError("Code Mode MCP calls require parent_tool_call_id")
        if self.code_mode_cell_id is None:
            raise ValueError("Code Mode MCP calls require code_mode_cell_id")
        return self


class McpFailure(_McpContractModel):
    kind: McpFailureKind
    error_type: str = Field(min_length=1)
    message: str = Field(min_length=1)
    server_id: str | None = None
    tool_name: str | None = None
    resource_uri: str | None = None

    @model_validator(mode="after")
    def _validate_failure_subject(self) -> "McpFailure":
        if self.server_id is not None:
            _validate_mcp_name(value=self.server_id, field_name="server_id")
        if self.tool_name is not None:
            _validate_mcp_name(value=self.tool_name, field_name="tool_name")
        if self.kind in {"startup_failed", "discovery_failed"}:
            if self.server_id is None:
                raise ValueError(f"{self.kind} failures require server_id")
            if self.tool_name is not None or self.resource_uri is not None:
                raise ValueError(
                    f"{self.kind} failures must not carry tool_name or resource_uri"
                )
        if self.kind == "tool_failed":
            if self.server_id is None or self.tool_name is None:
                raise ValueError("tool_failed failures require server_id and tool_name")
            if self.resource_uri is not None:
                raise ValueError("tool_failed failures must not carry resource_uri")
        if self.kind == "resource_failed":
            if self.server_id is None or self.resource_uri is None:
                raise ValueError(
                    "resource_failed failures require server_id and resource_uri"
                )
            if self.tool_name is not None:
                raise ValueError("resource_failed failures must not carry tool_name")
        return self


def make_mcp_model_tool_name(*, server_id: str, tool_name: str) -> str:
    _validate_mcp_name(value=server_id, field_name="server_id")
    _validate_mcp_name(value=tool_name, field_name="tool_name")
    return f"{MCP_TOOL_NAME_PREFIX}{server_id}__{tool_name}"


def parse_mcp_model_tool_name(model_tool_name: str) -> McpToolIdentity:
    if not model_tool_name.startswith(MCP_TOOL_NAME_PREFIX):
        raise ValueError("MCP model tool names must start with 'mcp__'")

    remainder = model_tool_name.removeprefix(MCP_TOOL_NAME_PREFIX)
    parts = remainder.split("__")
    if len(parts) != 2:
        raise ValueError("MCP model tool names must have form 'mcp__server__tool'")

    server_id, tool_name = parts
    return McpToolIdentity(server_id=server_id, tool_name=tool_name)


__all__ = [
    "JACA_ONBOARDING_MCP_SERVER_ID",
    "MCP_TOOL_NAME_PREFIX",
    "McpCallSource",
    "McpFailure",
    "McpFailureKind",
    "McpToolCallProvenance",
    "McpToolIdentity",
    "make_mcp_model_tool_name",
    "parse_mcp_model_tool_name",
]
