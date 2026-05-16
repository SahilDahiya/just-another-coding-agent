from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

MCP_TOOL_NAME_PREFIX = "mcp__"
JACA_ONBOARDING_MCP_SERVER_ID = "jaca_onboarding"
JACA_ONBOARDING_MCP_TOOL_NAMES = (
    "mcp__jaca_onboarding__ask_mcq_question",
    "mcp__jaca_onboarding__generate_mcq_from_teaching_packets",
    "mcp__jaca_onboarding__publish_teaching_packet",
)

McpCallSource = Literal["top_level_model", "code_mode"]
McpFailureKind = Literal[
    "auth_failed",
    "config_failed",
    "startup_failed",
    "discovery_failed",
    "tool_failed",
    "resource_failed",
]
McpAuthKind = Literal["bearer_env", "oauth"]
McpAuthFailureReason = Literal[
    "missing_bearer_env",
    "oauth_login_required",
    "oauth_refresh_failed",
    "unsupported",
]
McpToolApprovalMode = Literal["auto", "prompt", "approve"]

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


class McpMountedToolIdentity(_McpContractModel):
    server_id: str = Field(min_length=1)
    raw_tool_name: str = Field(min_length=1)
    model_tool_name: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_mounted_identity(self) -> "McpMountedToolIdentity":
        _validate_mcp_name(value=self.server_id, field_name="server_id")
        parsed = parse_mcp_model_tool_name(self.model_tool_name)
        if parsed.server_id != self.server_id:
            raise ValueError(
                "model_tool_name server id must match mounted MCP server_id"
            )
        return self

    @property
    def model_identity(self) -> McpToolIdentity:
        return parse_mcp_model_tool_name(self.model_tool_name)


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


class McpServerToolConfig(_McpContractModel):
    approval_mode: McpToolApprovalMode | None = None


class McpOAuthConfig(_McpContractModel):
    type: Literal["oauth"] = "oauth"
    callback_port: int = Field(default=1456, ge=1024, le=65535)
    scopes: list[str] = Field(default_factory=list)
    client_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _validate_oauth_config(self) -> "McpOAuthConfig":
        for scope in self.scopes:
            if scope.strip() == "":
                raise ValueError("OAuth MCP scopes must not be empty")
        return self


class McpStreamableHttpTransport(_McpContractModel):
    type: Literal["streamable_http"] = "streamable_http"
    url: str = Field(min_length=1)
    bearer_token_env_var: str | None = Field(default=None, min_length=1)
    oauth: McpOAuthConfig | None = None

    @model_validator(mode="after")
    def _validate_url(self) -> "McpStreamableHttpTransport":
        if not self.url.startswith(("http://", "https://")):
            raise ValueError(
                "streamable HTTP MCP transport url must start with http:// or https://"
            )
        if self.bearer_token_env_var is not None and self.oauth is not None:
            raise ValueError(
                "streamable HTTP MCP transport must not configure both "
                "bearer_token_env_var and oauth"
            )
        return self


class McpStdioTransport(_McpContractModel):
    type: Literal["stdio"] = "stdio"
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = Field(default=None, min_length=1)


McpTransport = Annotated[
    McpStreamableHttpTransport | McpStdioTransport,
    Field(discriminator="type"),
]


class McpServerConfig(_McpContractModel):
    server_id: str = Field(min_length=1)
    transport: McpTransport
    enabled: bool = True
    required: bool = False
    startup_timeout_sec: float | None = Field(default=None, gt=0)
    tool_timeout_sec: float | None = Field(default=None, gt=0)
    enabled_tools: list[str] | None = None
    disabled_tools: list[str] | None = None
    default_tool_approval: McpToolApprovalMode | None = None
    tools: dict[str, McpServerToolConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_config(self) -> "McpServerConfig":
        _validate_mcp_name(value=self.server_id, field_name="server_id")
        enabled_tools = set(_validate_raw_tool_names(self.enabled_tools))
        disabled_tools = set(_validate_raw_tool_names(self.disabled_tools))
        overlapping_tools = enabled_tools & disabled_tools
        if overlapping_tools:
            raise ValueError(
                "enabled_tools and disabled_tools must not contain the same tool"
            )
        for raw_tool_name in self.tools:
            _validate_raw_tool_name(raw_tool_name)
        return self


class McpAuthFailure(_McpContractModel):
    auth_kind: McpAuthKind
    reason: McpAuthFailureReason
    recovery_hint: str = Field(min_length=1)
    env_var: str | None = Field(default=None, min_length=1)
    provider_error_code: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _validate_auth_failure(self) -> "McpAuthFailure":
        if self.auth_kind == "bearer_env":
            if self.reason != "missing_bearer_env":
                raise ValueError(
                    "bearer_env MCP auth failures must use missing_bearer_env"
                )
            if self.env_var is None:
                raise ValueError("missing_bearer_env requires env_var")
            if self.provider_error_code is not None:
                raise ValueError(
                    "bearer_env MCP auth failures must not carry provider_error_code"
                )
            return self

        if self.reason == "missing_bearer_env":
            raise ValueError("missing_bearer_env requires bearer_env auth_kind")
        if self.env_var is not None:
            raise ValueError("OAuth MCP auth failures must not carry env_var")
        return self


class McpFailure(_McpContractModel):
    kind: McpFailureKind
    error_type: str = Field(min_length=1)
    message: str = Field(min_length=1)
    server_id: str | None = None
    tool_name: str | None = None
    resource_uri: str | None = None
    auth: McpAuthFailure | None = None

    @model_validator(mode="after")
    def _validate_failure_subject(self) -> "McpFailure":
        if self.server_id is not None:
            _validate_mcp_name(value=self.server_id, field_name="server_id")
        if self.tool_name is not None:
            _validate_mcp_name(value=self.tool_name, field_name="tool_name")
        if self.kind in {
            "auth_failed",
            "config_failed",
            "startup_failed",
            "discovery_failed",
        }:
            if self.server_id is None:
                raise ValueError(f"{self.kind} failures require server_id")
            if self.tool_name is not None or self.resource_uri is not None:
                raise ValueError(
                    f"{self.kind} failures must not carry tool_name or resource_uri"
                )
            if self.kind == "auth_failed":
                if self.auth is None:
                    raise ValueError("auth_failed failures require auth")
            elif self.auth is not None:
                raise ValueError(f"{self.kind} failures must not carry auth")
        if self.kind == "tool_failed":
            if self.server_id is None or self.tool_name is None:
                raise ValueError("tool_failed failures require server_id and tool_name")
            if self.resource_uri is not None or self.auth is not None:
                raise ValueError(
                    "tool_failed failures must not carry resource_uri or auth"
                )
        if self.kind == "resource_failed":
            if self.server_id is None or self.resource_uri is None:
                raise ValueError(
                    "resource_failed failures require server_id and resource_uri"
                )
            if self.tool_name is not None or self.auth is not None:
                raise ValueError(
                    "resource_failed failures must not carry tool_name or auth"
                )
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


def _validate_raw_tool_name(raw_tool_name: str) -> str:
    if raw_tool_name == "":
        raise ValueError("raw MCP tool names must not be empty")
    return raw_tool_name


def _validate_raw_tool_names(raw_tool_names: list[str] | None) -> list[str]:
    if raw_tool_names is None:
        return []
    for raw_tool_name in raw_tool_names:
        _validate_raw_tool_name(raw_tool_name)
    return raw_tool_names


__all__ = [
    "JACA_ONBOARDING_MCP_SERVER_ID",
    "JACA_ONBOARDING_MCP_TOOL_NAMES",
    "MCP_TOOL_NAME_PREFIX",
    "McpAuthFailure",
    "McpAuthFailureReason",
    "McpAuthKind",
    "McpCallSource",
    "McpFailure",
    "McpFailureKind",
    "McpMountedToolIdentity",
    "McpOAuthConfig",
    "McpServerConfig",
    "McpServerToolConfig",
    "McpStdioTransport",
    "McpStreamableHttpTransport",
    "McpToolApprovalMode",
    "McpToolCallProvenance",
    "McpToolIdentity",
    "McpTransport",
    "make_mcp_model_tool_name",
    "parse_mcp_model_tool_name",
]
