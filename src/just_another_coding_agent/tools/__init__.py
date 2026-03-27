"""Canonical coding tools package."""

from .bash import BASH_TOOL, bash, execute_bash
from .deps import WorkspaceDeps
from .edit import EDIT_TOOL, edit, execute_edit
from .errors import (
    ErrorWrappingToolset,
    ToolCommandError,
    ToolEncodingError,
    ToolMatchError,
    ToolOperationalError,
    ToolPathError,
)
from .find import FIND_TOOL, execute_find, find
from .grep import GREP_TOOL, execute_grep, grep
from .ls import LS_TOOL, execute_ls, ls
from .read import READ_TOOL, execute_read, read
from .registry import (
    UnknownToolError,
    build_canonical_toolset,
    list_canonical_tool_names,
)
from .truncation import (
    BoundedItems,
    HeadLineWindow,
    TailTextWindow,
    append_tool_note,
    collect_bounded_items,
    truncate_head_line_window,
    truncate_last_bytes,
    truncate_tail_text,
)
from .write import WRITE_TOOL, execute_write, write

__all__ = [
    "BASH_TOOL",
    "BoundedItems",
    "EDIT_TOOL",
    "ErrorWrappingToolset",
    "FIND_TOOL",
    "GREP_TOOL",
    "HeadLineWindow",
    "LS_TOOL",
    "READ_TOOL",
    "TailTextWindow",
    "ToolCommandError",
    "ToolEncodingError",
    "ToolMatchError",
    "UnknownToolError",
    "ToolOperationalError",
    "ToolPathError",
    "WorkspaceDeps",
    "WRITE_TOOL",
    "append_tool_note",
    "bash",
    "build_canonical_toolset",
    "collect_bounded_items",
    "edit",
    "execute_bash",
    "execute_edit",
    "execute_find",
    "execute_grep",
    "execute_ls",
    "execute_read",
    "execute_write",
    "find",
    "grep",
    "list_canonical_tool_names",
    "ls",
    "read",
    "truncate_head_line_window",
    "truncate_last_bytes",
    "truncate_tail_text",
    "write",
]
