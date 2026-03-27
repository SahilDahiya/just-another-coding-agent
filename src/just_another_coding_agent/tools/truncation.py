from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


def append_tool_note(text: str, note: str) -> str:
    if not text:
        return note
    return f"{text.rstrip('\n')}\n\n{note}"


@dataclass(frozen=True)
class HeadLineWindow:
    text: str
    line_count: int
    truncated: bool
    first_line_exceeds_limit: bool


def truncate_head_line_window(
    lines: list[str],
    *,
    max_lines: int,
    max_bytes: int,
) -> HeadLineWindow:
    output_lines: list[str] = []
    output_bytes = 0

    for line in lines:
        if len(output_lines) >= max_lines:
            return HeadLineWindow(
                text="".join(output_lines),
                line_count=len(output_lines),
                truncated=True,
                first_line_exceeds_limit=False,
            )

        line_bytes = len(line.encode("utf-8"))
        if not output_lines and line_bytes > max_bytes:
            return HeadLineWindow(
                text="",
                line_count=0,
                truncated=True,
                first_line_exceeds_limit=True,
            )
        if output_bytes + line_bytes > max_bytes:
            return HeadLineWindow(
                text="".join(output_lines),
                line_count=len(output_lines),
                truncated=True,
                first_line_exceeds_limit=False,
            )

        output_lines.append(line)
        output_bytes += line_bytes

    return HeadLineWindow(
        text="".join(output_lines),
        line_count=len(output_lines),
        truncated=False,
        first_line_exceeds_limit=False,
    )


@dataclass(frozen=True)
class BoundedItems:
    items: list[str]
    limit_hit: bool
    byte_limit_hit: bool


def collect_bounded_items(
    items: list[str],
    *,
    item_limit: int,
    max_bytes: int,
) -> BoundedItems:
    displayed_items: list[str] = []
    output_bytes = 0
    limit_hit = False
    byte_limit_hit = False

    for item in items:
        if len(displayed_items) >= item_limit:
            limit_hit = True
            break

        item_bytes = len(item.encode("utf-8"))
        if output_bytes + item_bytes + 1 > max_bytes:
            byte_limit_hit = True
            break

        displayed_items.append(item)
        output_bytes += item_bytes + 1

    return BoundedItems(
        items=displayed_items,
        limit_hit=limit_hit,
        byte_limit_hit=byte_limit_hit,
    )


def truncate_last_bytes(text: str, max_bytes: int) -> str:
    chars: list[str] = []
    bytes_used = 0

    for char in reversed(text):
        char_bytes = len(char.encode("utf-8"))
        if bytes_used + char_bytes > max_bytes:
            break
        chars.append(char)
        bytes_used += char_bytes

    return "".join(reversed(chars))


@dataclass(frozen=True)
class TailTextWindow:
    text: str
    start_line: int
    end_line: int
    total_lines: int
    truncated_by: Literal["lines", "bytes", "line_bytes"] | None
    last_line_partial: bool


def truncate_tail_text(
    text: str,
    *,
    max_lines: int,
    max_bytes: int,
) -> TailTextWindow:
    output_lines = text.splitlines(keepends=True)
    output_bytes = len(text.encode("utf-8"))
    if len(output_lines) <= max_lines and output_bytes <= max_bytes:
        return TailTextWindow(
            text=text,
            start_line=1 if output_lines else 0,
            end_line=len(output_lines),
            total_lines=len(output_lines),
            truncated_by=None,
            last_line_partial=False,
        )

    tail_lines: list[str] = []
    tail_bytes = 0
    last_line_partial = False
    truncated_by: Literal["lines", "bytes", "line_bytes"] = "lines"

    for line in reversed(output_lines):
        if len(tail_lines) >= max_lines:
            truncated_by = "lines"
            break

        line_bytes = len(line.encode("utf-8"))
        if not tail_lines and line_bytes > max_bytes:
            tail_lines.append(truncate_last_bytes(line, max_bytes))
            last_line_partial = True
            truncated_by = "line_bytes"
            break

        if tail_bytes + line_bytes > max_bytes:
            truncated_by = "bytes"
            break

        tail_lines.append(line)
        tail_bytes += line_bytes

    displayed_lines = list(reversed(tail_lines))
    displayed_text = "".join(displayed_lines)
    end_line = len(output_lines)
    start_line = end_line - len(displayed_lines) + 1 if displayed_lines else 0

    return TailTextWindow(
        text=displayed_text,
        start_line=start_line,
        end_line=end_line,
        total_lines=len(output_lines),
        truncated_by=truncated_by,
        last_line_partial=last_line_partial,
    )


__all__ = [
    "BoundedItems",
    "HeadLineWindow",
    "TailTextWindow",
    "append_tool_note",
    "collect_bounded_items",
    "truncate_head_line_window",
    "truncate_last_bytes",
    "truncate_tail_text",
]
