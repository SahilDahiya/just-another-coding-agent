from __future__ import annotations

import unicodedata
from pathlib import Path

from pydantic_ai import Tool

from just_another_coding_agent.contracts.tools import (
    EditToolInput,
    make_tool_error_result,
)
from just_another_coding_agent.tools._workspace import (
    normalize_workspace_root,
    resolve_workspace_path,
)


def strip_bom(content: str) -> tuple[str, str]:
    if content.startswith("\ufeff"):
        return "\ufeff", content[1:]
    return "", content


def detect_line_ending(content: str) -> str:
    crlf_index = content.find("\r\n")
    lf_index = content.find("\n")
    if lf_index == -1:
        return "\n"
    if crlf_index == -1:
        return "\n"
    return "\r\n" if crlf_index < lf_index else "\n"


def normalize_to_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def restore_line_endings(text: str, ending: str) -> str:
    if ending == "\r\n":
        return text.replace("\n", "\r\n")
    return text


def normalize_for_fuzzy_match(text: str) -> str:
    return _normalize_unicode_variants(unicodedata.normalize("NFKC", text))


def _normalize_unicode_variants(text: str) -> str:
    return (
        text.replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201A", "'")
        .replace("\u201B", "'")
        .replace("\u201C", '"')
        .replace("\u201D", '"')
        .replace("\u201E", '"')
        .replace("\u201F", '"')
        .replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2015", "-")
        .replace("\u2212", "-")
        .replace("\u00A0", " ")
        .replace("\u2002", " ")
        .replace("\u2003", " ")
        .replace("\u2004", " ")
        .replace("\u2005", " ")
        .replace("\u2006", " ")
        .replace("\u2007", " ")
        .replace("\u2008", " ")
        .replace("\u2009", " ")
        .replace("\u200A", " ")
        .replace("\u202F", " ")
        .replace("\u205F", " ")
        .replace("\u3000", " ")
    )


def trim_trailing_whitespace_per_line(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.split("\n"))


def build_fuzzy_view(text: str) -> tuple[str, list[tuple[int, int]]]:
    view_parts: list[str] = []
    spans: list[tuple[int, int]] = []
    lines = text.split("\n")
    source_index = 0

    for line_index, line in enumerate(lines):
        trimmed_line = line.rstrip()
        for char_offset, char in enumerate(trimmed_line):
            normalized_char = normalize_for_fuzzy_match(char)
            char_start = source_index + char_offset
            char_end = char_start + 1
            view_parts.append(normalized_char)
            spans.extend((char_start, char_end) for _ in normalized_char)

        source_index += len(line)
        if line_index < len(lines) - 1:
            view_parts.append("\n")
            spans.append((source_index, source_index + 1))
            source_index += 1

    return "".join(view_parts), spans


def execute_edit(*, tool_input: EditToolInput, workspace_root: Path | str) -> str:
    path = resolve_workspace_path(
        workspace_root=workspace_root,
        tool_path=tool_input.path,
    )
    raw_content = path.read_bytes().decode("utf-8")
    bom, content = strip_bom(raw_content)
    original_line_ending = detect_line_ending(content)
    normalized_content = normalize_to_lf(content)
    normalized_old_text = normalize_to_lf(tool_input.old_text)
    normalized_new_text = normalize_to_lf(tool_input.new_text)

    exact_occurrences = normalized_content.count(normalized_old_text)
    if exact_occurrences > 1:
        raise ValueError(
            "old_text must match exactly once in "
            f"{path}; found {exact_occurrences} occurrences"
        )

    if exact_occurrences == 1:
        base_content = normalized_content
        old_text_to_replace = normalized_old_text
        new_text_to_insert = normalized_new_text
    else:
        fuzzy_content, fuzzy_spans = build_fuzzy_view(normalized_content)
        fuzzy_old_text, _ = build_fuzzy_view(normalized_old_text)
        fuzzy_occurrences = fuzzy_content.count(fuzzy_old_text)
        if fuzzy_occurrences != 1:
            raise ValueError(
                "old_text must match exactly once in "
                f"{path}; found {fuzzy_occurrences} occurrences"
            )
        match_start = fuzzy_content.index(fuzzy_old_text)
        match_end = match_start + len(fuzzy_old_text)
        base_content = normalized_content
        old_text_to_replace = normalized_content[
            fuzzy_spans[match_start][0] : fuzzy_spans[match_end - 1][1]
        ]
        new_text_to_insert = normalized_new_text

    updated = base_content.replace(old_text_to_replace, new_text_to_insert, 1)
    if updated == base_content:
        raise ValueError(f"Edit would not change file contents: {path}")

    final_content = bom + restore_line_endings(updated, original_line_ending)
    path.write_bytes(final_content.encode("utf-8"))
    return f"Edited {path}"


def create_edit_tool(*, workspace_root: Path | str) -> Tool:
    root = normalize_workspace_root(workspace_root)

    def edit(path: str, old_text: str, new_text: str) -> str | dict[str, bool | str]:
        """Edit a UTF-8 text file by replacing one exact or normalized text match.

        Args:
            path: Path to the file to edit, relative to the workspace root or absolute.
            old_text: Existing text to replace. Exact matching is tried first;
                a normalized fallback handles BOM, line endings, and minor
                Unicode formatting differences.
            new_text: Replacement text to insert in place of old_text.
        """

        try:
            return execute_edit(
                tool_input=EditToolInput(
                    path=path,
                    old_text=old_text,
                    new_text=new_text,
                ),
                workspace_root=root,
            )
        except (OSError, UnicodeError, ValueError) as error:
            return make_tool_error_result(error)

    return Tool(
        edit,
        name="edit",
        description=(
            "Edit a UTF-8 text file by replacing exactly one occurrence of "
            "old_text with new_text. Exact matching is tried first; if that "
            "fails, the tool falls back to normalized matching that tolerates "
            "BOM differences, LF versus CRLF, trailing whitespace, and common "
            "Unicode quote, dash, and space variants while preserving "
            "surrounding file content outside the replaced region. Zero or "
            "multiple matches return an error result. new_text may be empty "
            "to delete the matched text. Use this for precise surgical changes."
        ),
        docstring_format="google",
        require_parameter_descriptions=True,
        strict=True,
    )

__all__ = ["create_edit_tool", "execute_edit"]
