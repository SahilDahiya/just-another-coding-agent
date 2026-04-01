from __future__ import annotations

import difflib
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import Field
from pydantic_ai import RunContext, Tool

from just_another_coding_agent.contracts.run_events import EditActivityDetails
from just_another_coding_agent.tools._activity import (
    make_tool_return,
    truncate_activity_label,
)
from just_another_coding_agent.tools._workspace import resolve_workspace_path
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.errors import (
    ToolEncodingError,
    ToolMatchError,
    reraise_path_error,
)


@dataclass(frozen=True)
class EditResult:
    path: str
    diff: str
    added_lines: int
    removed_lines: int


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
        .replace("\u201a", "'")
        .replace("\u201b", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u201e", '"')
        .replace("\u201f", '"')
        .replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2015", "-")
        .replace("\u2212", "-")
        .replace("\u00a0", " ")
        .replace("\u2002", " ")
        .replace("\u2003", " ")
        .replace("\u2004", " ")
        .replace("\u2005", " ")
        .replace("\u2006", " ")
        .replace("\u2007", " ")
        .replace("\u2008", " ")
        .replace("\u2009", " ")
        .replace("\u200a", " ")
        .replace("\u202f", " ")
        .replace("\u205f", " ")
        .replace("\u3000", " ")
    )


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


def execute_edit(
    *,
    workspace_root: Path | str,
    path: str,
    old_text: str,
    new_text: str,
) -> EditResult:
    try:
        resolved_path = resolve_workspace_path(
            workspace_root=workspace_root,
            tool_path=path,
        )
        raw_content = resolved_path.read_bytes().decode("utf-8")
    except UnicodeError as error:
        raise ToolEncodingError(f"{path} is not valid UTF-8 text") from error
    except OSError as error:
        reraise_path_error(error)

    bom, content = strip_bom(raw_content)
    original_line_ending = detect_line_ending(content)
    normalized_content = normalize_to_lf(content)
    normalized_old_text = normalize_to_lf(old_text)
    normalized_new_text = normalize_to_lf(new_text)

    exact_occurrences = normalized_content.count(normalized_old_text)
    if exact_occurrences > 1:
        raise ToolMatchError(
            "old_text must match exactly once in "
            f"{resolved_path}; found {exact_occurrences} occurrences"
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
            raise ToolMatchError(
                "old_text must match exactly once in "
                f"{resolved_path}; found {fuzzy_occurrences} occurrences"
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
        raise ToolMatchError(f"Edit would not change file contents: {resolved_path}")

    diff_text = _generate_unified_diff(
        old_content=base_content,
        new_content=updated,
        file_path=str(resolved_path),
    )
    added_lines, removed_lines = _count_changed_lines(diff_text)

    final_content = bom + restore_line_endings(updated, original_line_ending)
    try:
        resolved_path.write_bytes(final_content.encode("utf-8"))
    except OSError as error:
        reraise_path_error(error)
    return EditResult(
        path=str(resolved_path),
        diff=diff_text,
        added_lines=added_lines,
        removed_lines=removed_lines,
    )


async def edit(
    ctx: RunContext[WorkspaceDeps],
    path: Annotated[str, Field(min_length=1)],
    old_text: Annotated[str, Field(min_length=1)],
    new_text: str,
) -> str:
    """Edit a UTF-8 text file by replacing one exact or normalized text match.

    Args:
        path: Path to the file to edit, relative to the workspace root or absolute.
        old_text: Existing text to replace. Exact matching is tried first;
            a normalized fallback handles BOM, line endings, and minor
            Unicode formatting differences.
        new_text: Replacement text to insert in place of old_text.
    """

    result = execute_edit(
        workspace_root=ctx.deps.workspace_root,
        path=path,
        old_text=old_text,
        new_text=new_text,
    )
    return make_tool_return(
        return_value=f"Edited {result.path}",
        title=f"edit {truncate_activity_label(path)}",
        summary="edit applied",
        details=EditActivityDetails(
            path=path,
            diff=result.diff,
            added_lines=result.added_lines,
            removed_lines=result.removed_lines,
        ),
    )


EDIT_TOOL = Tool(
    edit,
    takes_ctx=True,
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
    sequential=True,
)


def _generate_unified_diff(
    *,
    old_content: str,
    new_content: str,
    file_path: str,
    context_lines: int = 3,
) -> str:
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff_lines = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=file_path,
        tofile=file_path,
        n=context_lines,
    )
    return "".join(diff_lines)


def _count_changed_lines(diff_text: str) -> tuple[int, int]:
    added_lines = 0
    removed_lines = 0

    for line in diff_text.splitlines():
        if line.startswith("--- ") or line.startswith("+++ ") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            added_lines += 1
            continue
        if line.startswith("-"):
            removed_lines += 1

    return added_lines, removed_lines


__all__ = ["EDIT_TOOL", "EditResult", "edit", "execute_edit"]
