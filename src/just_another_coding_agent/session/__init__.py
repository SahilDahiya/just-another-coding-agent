"""Session persistence package."""

from .jsonl import (
    SessionFormatError,
    SessionNameValidationError,
    append_compaction_to_session,
    append_project_docs_to_session,
    append_run_to_session,
    append_session_name_to_session,
    fork_session,
    initialize_session,
    load_session,
    normalize_session_name,
    read_session_metadata,
    update_session_auto_compaction_failures,
)
from .preview import build_session_preview

__all__ = [
    "SessionFormatError",
    "SessionNameValidationError",
    "append_compaction_to_session",
    "append_project_docs_to_session",
    "append_session_name_to_session",
    "append_run_to_session",
    "fork_session",
    "initialize_session",
    "load_session",
    "normalize_session_name",
    "read_session_metadata",
    "update_session_auto_compaction_failures",
    "build_session_preview",
]
