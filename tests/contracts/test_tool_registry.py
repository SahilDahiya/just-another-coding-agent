import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from just_another_coding_agent.tools.registry import (
    UnknownToolError,
    build_canonical_toolset,
    list_canonical_tool_names,
)


def test_registry_exposes_canonical_tool_names() -> None:
    assert list_canonical_tool_names() == (
        "read",
        "write",
        "edit",
        "bash",
        "grep",
        "ls",
        "find",
    )


def test_build_canonical_toolset_rejects_unknown_tool_name() -> None:
    with pytest.raises(UnknownToolError, match="nope"):
        build_canonical_toolset(["nope"], workspace_root=".")


def test_build_canonical_toolset_registers_implemented_tools_with_pydanticai(
    tmp_path,
) -> None:
    model = TestModel(call_tools=[], custom_output_text="ok")
    agent = Agent(
        model,
        toolsets=[
            build_canonical_toolset(
                ["read", "write", "edit", "bash", "grep", "ls", "find"],
                workspace_root=tmp_path,
            )
        ],
    )

    agent.run_sync("What tools are available?")

    function_tools = model.last_model_request_parameters.function_tools
    tool_names = [tool.name for tool in function_tools]
    assert tool_names == ["read", "write", "edit", "bash", "grep", "ls", "find"]


def test_build_canonical_toolset_exposes_rich_model_facing_tool_descriptions(
    tmp_path,
) -> None:
    model = TestModel(call_tools=[], custom_output_text="ok")
    agent = Agent(
        model,
        toolsets=[
            build_canonical_toolset(
                ["read", "write", "edit", "bash", "grep", "ls", "find"],
                workspace_root=tmp_path,
            )
        ],
    )

    agent.run_sync("What tools are available?")

    function_tools = {
        tool.name: tool for tool in model.last_model_request_parameters.function_tools
    }

    assert function_tools["read"].description == (
        "Read a UTF-8 text file. Supports line-based offset and limit. "
        "When limit is omitted, output is bounded to 2000 lines or 50 KiB "
        "with continuation hints using offset."
    )
    assert (
        function_tools["read"].parameters_json_schema["properties"]["offset"][
            "description"
        ]
        == "Optional 1-indexed line number to start reading from."
    )
    assert (
        function_tools["read"].parameters_json_schema["properties"]["limit"][
            "description"
        ]
        == (
            "Optional maximum number of lines to read before read's own\n"
            "truncation ceiling."
        )
    )

    assert function_tools["write"].description == (
        "Create or overwrite an entire UTF-8 text file. Creates parent "
        "directories automatically. Use write for new files or complete "
        "rewrites."
    )
    assert (
        function_tools["write"].parameters_json_schema["properties"]["content"][
            "description"
        ]
        == "Full UTF-8 file contents to write."
    )

    assert function_tools["edit"].description == (
        "Edit a UTF-8 text file by replacing exactly one occurrence of "
        "old_text with new_text. Exact matching is tried first; if that "
        "fails, the tool falls back to normalized matching that tolerates "
        "BOM differences, LF versus CRLF, trailing whitespace, and common "
        "Unicode quote, dash, and space variants. Zero or multiple matches "
        "return an error result. new_text may be empty to delete the "
        "matched text. Use this for precise surgical changes."
    )
    assert (
        function_tools["edit"].parameters_json_schema["properties"]["old_text"][
            "description"
        ]
        == (
            "Existing text to replace. Exact matching is tried first;\n"
            "a normalized fallback handles BOM, line endings, and minor\n"
            "Unicode formatting differences."
        )
    )

    assert function_tools["bash"].description == (
        "Execute a local bash command in the workspace root. Returns "
        "combined stdout and stderr on success. Non-zero exits and "
        "timeouts become error results. Large output is truncated to the "
        "last 2000 lines or 50 KiB, and the full output is saved to a "
        "temp file."
    )
    assert (
        function_tools["bash"].parameters_json_schema["properties"]["timeout"][
            "description"
        ]
        == "Optional timeout in seconds before the command is stopped."
    )

    assert function_tools["grep"].description == (
        "Search UTF-8 text files for a pattern using ripgrep. Returns matching "
        "lines with relative file paths and line numbers. Respects .gitignore "
        "and bounds output to 100 matches or 50 KiB."
    )
    assert (
        function_tools["grep"].parameters_json_schema["properties"]["pattern"][
            "description"
        ]
        == "Pattern to search for as a regex or literal string."
    )

    assert function_tools["ls"].description == (
        "List directory contents in alphabetical order. Includes dotfiles "
        "and adds '/' suffixes for directories. Output is bounded to 500 "
        "entries or 50 KiB."
    )
    assert (
        function_tools["ls"].parameters_json_schema["properties"]["limit"][
            "description"
        ]
        == (
            "Maximum number of entries to return before ls's own byte\n"
            "ceiling is applied."
        )
    )

    assert function_tools["find"].description == (
        "Find files by glob pattern using ripgrep-backed file discovery. "
        "Returns paths relative to the searched directory, respects "
        ".gitignore, and bounds output to 1000 results or 50 KiB."
    )
    assert (
        function_tools["find"].parameters_json_schema["properties"]["pattern"][
            "description"
        ]
        == "Glob pattern to match, such as '*.py' or 'src/**/*.ts'."
    )
