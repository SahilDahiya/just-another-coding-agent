from just_another_coding_agent.contracts import tools as tool_contracts


def test_contracts_tools_only_exports_public_contract_types() -> None:
    assert set(tool_contracts.__all__) == {
        "CANONICAL_TOOL_NAMES",
        "CanonicalToolName",
        "ToolErrorResult",
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
