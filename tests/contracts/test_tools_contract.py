from just_another_coding_agent.contracts import tools as tool_contracts


def test_contracts_tools_only_exports_public_contract_types() -> None:
    assert set(tool_contracts.__all__) == {
        "CANONICAL_TOOL_NAMES",
        "CanonicalToolName",
        "ToolDeniedResult",
        "ToolErrorResult",
        "make_tool_denied_result",
        "make_tool_error_result",
    }

    for name in (
        "ReadToolInput",
        "WriteToolInput",
        "EditToolInput",
        "BashToolInput",
        "GrepToolInput",
        "LsToolInput",
        "FindToolInput",
    ):
        assert not hasattr(tool_contracts, name)


def test_make_tool_denied_result_supports_minimal_model_visible_policy_fields() -> None:
    result = tool_contracts.make_tool_denied_result(
        message="Approval denied. The command was not run.",
        approval_kind="command_execution",
        subject="curl https://example.com",
        retry_same_request_allowed=False,
    )

    assert result == {
        "ok": False,
        "outcome": "denied",
        "denial_type": "approval_denied",
        "message": "Approval denied. The command was not run.",
        "approval_kind": "command_execution",
        "subject": "curl https://example.com",
        "retry_same_request_allowed": False,
    }
