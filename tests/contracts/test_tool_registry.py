import json

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.edit import EDIT_TOOL
from just_another_coding_agent.tools.find import FIND_TOOL
from just_another_coding_agent.tools.grep import GREP_TOOL
from just_another_coding_agent.tools.ls import LS_TOOL
from just_another_coding_agent.tools.onboarding_question import (
    ASK_MCQ_QUESTION_TOOL,
)
from just_another_coding_agent.tools.read import READ_TOOL
from just_another_coding_agent.tools.registry import (
    PARALLEL_CANONICAL_TOOL_NAMES,
    SEQUENTIAL_CANONICAL_TOOL_NAMES,
    UnknownToolError,
    build_canonical_toolset,
    list_canonical_tool_names,
    list_onboarding_tool_names,
)
from just_another_coding_agent.tools.shell import SHELL_TOOL
from just_another_coding_agent.tools.subagent import SUBAGENT_TOOL
from just_another_coding_agent.tools.teaching_packet import (
    PUBLISH_TEACHING_PACKET_TOOL,
)
from just_another_coding_agent.tools.write import WRITE_TOOL

CANONICAL_CORE_TOOL_SCHEMA_MAX_CHARS = 8_500


def _model_visible_tool_schema_payload(function_tools: object) -> str:
    payload = [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters_json_schema": tool.parameters_json_schema,
        }
        for tool in function_tools
    ]
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def test_registry_exposes_canonical_tool_names() -> None:
    assert list_canonical_tool_names() == (
        "read",
        "write",
        "edit",
        "shell",
        "grep",
        "ls",
        "find",
        "subagent",
    )


def test_registry_exposes_onboarding_only_tool_names() -> None:
    assert list_onboarding_tool_names() == (
        "ask_mcq_question",
        "generate_mcq_from_teaching_packets",
        "publish_teaching_packet",
    )


def test_build_canonical_toolset_rejects_unknown_tool_name() -> None:
    with pytest.raises(UnknownToolError, match="nope"):
        build_canonical_toolset(["nope"])


def test_registry_exposes_explicit_parallel_tool_policy() -> None:
    assert set(PARALLEL_CANONICAL_TOOL_NAMES).isdisjoint(
        SEQUENTIAL_CANONICAL_TOOL_NAMES
    )
    assert set(PARALLEL_CANONICAL_TOOL_NAMES) | set(
        SEQUENTIAL_CANONICAL_TOOL_NAMES
    ) == set(list_canonical_tool_names())

    assert READ_TOOL.sequential is False
    assert GREP_TOOL.sequential is False
    assert FIND_TOOL.sequential is False
    assert LS_TOOL.sequential is False
    assert WRITE_TOOL.sequential is True
    assert EDIT_TOOL.sequential is True
    assert SHELL_TOOL.sequential is True
    assert SUBAGENT_TOOL.sequential is True
    assert ASK_MCQ_QUESTION_TOOL.sequential is True
    assert PUBLISH_TEACHING_PACKET_TOOL.sequential is True


def test_build_canonical_toolset_registers_implemented_tools_with_pydanticai(
    tmp_path,
) -> None:
    model = TestModel(call_tools=[], custom_output_text="ok")
    agent = Agent(
        model,
        toolsets=[
            build_canonical_toolset(
                [
                    "read",
                    "write",
                    "edit",
                    "shell",
                    "grep",
                    "ls",
                    "find",
                    "subagent",
                ]
            )
        ],
        deps_type=WorkspaceDeps,
    )

    agent.run_sync("What tools are available?", deps=WorkspaceDeps(tmp_path))

    function_tools = model.last_model_request_parameters.function_tools
    tool_names = [tool.name for tool in function_tools]
    assert tool_names == [
        "read",
        "write",
        "edit",
        "shell",
        "grep",
        "ls",
        "find",
        "subagent",
    ]


def test_build_canonical_toolset_exposes_rich_model_facing_tool_descriptions(
    tmp_path,
) -> None:
    model = TestModel(call_tools=[], custom_output_text="ok")
    agent = Agent(
        model,
        toolsets=[
            build_canonical_toolset(
                [
                    "read",
                    "write",
                    "edit",
                    "shell",
                    "grep",
                    "ls",
                    "find",
                    "subagent",
                    "ask_mcq_question",
                    "generate_mcq_from_teaching_packets",
                    "publish_teaching_packet",
                ]
            )
        ],
        deps_type=WorkspaceDeps,
    )

    agent.run_sync("What tools are available?", deps=WorkspaceDeps(tmp_path))

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
        function_tools["read"].parameters_json_schema["properties"]["path"]["minLength"]
        == 1
    )
    assert (
        function_tools["read"].parameters_json_schema["properties"]["offset"]["anyOf"][
            0
        ]["minimum"]
        == 1
    )
    assert function_tools["read"].parameters_json_schema["properties"]["limit"][
        "description"
    ] == (
        "Optional maximum number of lines to read before read's own\n"
        "truncation ceiling."
    )
    assert (
        function_tools["read"].parameters_json_schema["properties"]["limit"]["anyOf"][
            0
        ]["minimum"]
        == 1
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
    assert (
        function_tools["write"].parameters_json_schema["properties"]["path"][
            "minLength"
        ]
        == 1
    )

    assert function_tools["edit"].description == (
        "Edit a UTF-8 text file by replacing exactly one occurrence of "
        "old_text with new_text. Exact matching is tried first; if that "
        "fails, the tool falls back to normalized matching that tolerates "
        "BOM differences, LF versus CRLF, trailing whitespace, and common "
        "Unicode quote, dash, and space variants while preserving "
        "surrounding file content outside the replaced region. Zero or "
        "multiple matches return an error result. new_text may be empty "
        "to delete the matched text. Use this for precise surgical changes."
    )
    assert function_tools["edit"].parameters_json_schema["properties"]["old_text"][
        "description"
    ] == (
        "Existing text to replace. Exact matching is tried first;\n"
        "a normalized fallback handles BOM, line endings, and minor\n"
        "Unicode formatting differences."
    )
    assert (
        function_tools["edit"].parameters_json_schema["properties"]["path"]["minLength"]
        == 1
    )
    assert (
        function_tools["edit"].parameters_json_schema["properties"]["old_text"][
            "minLength"
        ]
        == 1
    )

    assert function_tools["shell"].description == (
        "Execute a local shell command in the workspace root using the "
        "configured shell family. posix commands run with bash; "
        "powershell commands run with PowerShell. Returns combined stdout "
        "and stderr on success. Non-zero exits and timeouts become error "
        "results. Large output is truncated to the last 2000 lines or 50 "
        "KiB, and the full output is saved to a temp file."
    )
    assert (
        function_tools["shell"].parameters_json_schema["properties"]["timeout"][
            "description"
        ]
        == "Optional timeout in seconds before the command is stopped."
    )
    assert (
        function_tools["shell"].parameters_json_schema["properties"]["command"][
            "minLength"
        ]
        == 1
    )
    assert (
        function_tools["shell"].parameters_json_schema["properties"]["timeout"][
            "anyOf"
        ][0]["exclusiveMinimum"]
        == 0
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
    assert (
        function_tools["grep"].parameters_json_schema["properties"]["pattern"][
            "minLength"
        ]
        == 1
    )
    assert (
        function_tools["grep"].parameters_json_schema["properties"]["limit"]["minimum"]
        == 1
    )

    assert function_tools["ls"].description == (
        "List directory contents in alphabetical order. Includes dotfiles "
        "and adds '/' suffixes for directories. Output is bounded to 500 "
        "entries or 50 KiB."
    )
    assert function_tools["ls"].parameters_json_schema["properties"]["limit"][
        "description"
    ] == (
        "Maximum number of entries to return before ls's own byte\nceiling is applied."
    )
    assert (
        function_tools["ls"].parameters_json_schema["properties"]["limit"]["minimum"]
        == 1
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
    assert (
        function_tools["find"].parameters_json_schema["properties"]["pattern"][
            "minLength"
        ]
        == 1
    )
    assert (
        function_tools["find"].parameters_json_schema["properties"]["limit"]["minimum"]
        == 1
    )

    assert function_tools["subagent"].description == (
        "Run one ephemeral subagent for a bounded side task. Use it for "
        "focused investigation or verification, not broad multi-step work. "
        "The child uses the same workspace, model, and thinking, never gets "
        "write or edit, and returns one final report. Default to "
        "spawn_mode='fork' so the child can build on the parent's current "
        "conversation context; use 'fresh' only for an independent pass. "
        "Request capability='shell' when the child needs local commands or "
        "scripts beyond read, grep, find, and ls."
    )
    assert (
        function_tools["subagent"].parameters_json_schema["properties"]["name"][
            "description"
        ]
        == "Short kebab-case session name for the child run."
    )
    assert (
        function_tools["subagent"].parameters_json_schema["properties"]["task"][
            "description"
        ]
        == (
            "Bounded task for the child run to complete. Include the exact "
            "goal, relevant files or artifacts, constraints, stop condition, "
            "and desired report shape when needed."
        )
    )
    assert (
        function_tools["subagent"].parameters_json_schema["properties"][
            "spawn_mode"
        ]["description"]
        == (
            "Child context mode. Defaults to 'fork' so the child "
            "inherits the parent's current conversation context; use "
            "'fresh' for an independent clean-room pass."
        )
    )
    assert (
        function_tools["subagent"].parameters_json_schema["properties"][
            "capability"
        ]["description"]
        == (
            "Child tool capability. Use 'default' for read/grep/find/ls only "
            "or 'shell' when the child also needs shell commands."
        )
    )
    assert function_tools["ask_mcq_question"].description == (
        "Present one multiple-choice question, wait for the user's selection, "
        "persist it, and return the result. Supply linked teaching packet ids, "
        "four options, a zero-based correct_index, and a short explanation. "
        "Do not reveal the correct answer before calling the tool."
    )
    assert (
        function_tools["ask_mcq_question"].parameters_json_schema[
            "properties"
        ]["correct_index"]["maximum"]
        == 3
    )
    assert (
        function_tools["ask_mcq_question"].parameters_json_schema[
            "properties"
        ]["packet_ids"]["minItems"]
        == 1
    )
    assert function_tools["generate_mcq_from_teaching_packets"].description == (
        "Generate one multiple-choice question draft from teaching packet ids "
        "published earlier in this same run. Returns packet_ids, question, "
        "four options, correct_index, and explanation so the result can be "
        "passed to ask_mcq_question."
    )
    assert (
        function_tools["generate_mcq_from_teaching_packets"].parameters_json_schema[
            "properties"
        ]["packet_ids"]["maxItems"]
        == 3
    )
    assert function_tools["publish_teaching_packet"].description == (
        "Publish one curated teaching packet into the transcript by resolving "
        "2 to 5 code-file snippet references into canonical file text. "
        "Documentation paths are not allowed. Supply a short title, one "
        "concept, one or more relationship statements, and snippet "
        "references with path, start_line, and end_line."
    )
    assert (
        function_tools["publish_teaching_packet"].parameters_json_schema[
            "properties"
        ]["concept"]["minLength"]
        == 1
    )
    assert (
        function_tools["publish_teaching_packet"].parameters_json_schema[
            "properties"
        ]["relationships"]["minItems"]
        == 1
    )
    assert (
        function_tools["publish_teaching_packet"].parameters_json_schema[
            "properties"
        ]["snippets"]["minItems"]
        == 2
    )
    assert (
        function_tools["publish_teaching_packet"].parameters_json_schema[
            "properties"
        ]["snippets"]["maxItems"]
        == 5
    )


def test_canonical_core_tool_schemas_stay_direct_and_within_budget(tmp_path) -> None:
    model = TestModel(call_tools=[], custom_output_text="ok")
    agent = Agent(
        model,
        toolsets=[
            build_canonical_toolset(
                [
                    "read",
                    "write",
                    "edit",
                    "shell",
                    "grep",
                    "ls",
                    "find",
                    "subagent",
                ]
            )
        ],
        deps_type=WorkspaceDeps,
    )

    agent.run_sync("What tools are available?", deps=WorkspaceDeps(tmp_path))

    function_tools = model.last_model_request_parameters.function_tools
    assert [tool.name for tool in function_tools] == [
        "read",
        "write",
        "edit",
        "shell",
        "grep",
        "ls",
        "find",
        "subagent",
    ]
    assert (
        len(_model_visible_tool_schema_payload(function_tools))
        <= CANONICAL_CORE_TOOL_SCHEMA_MAX_CHARS
    )
