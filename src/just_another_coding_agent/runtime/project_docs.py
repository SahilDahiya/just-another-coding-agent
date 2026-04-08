from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart

from just_another_coding_agent.tools._workspace import normalize_workspace_root

PROJECT_DOC_FILENAMES = ("AGENTS.md", "CLAUDE.md")
PROJECT_DOC_TOTAL_BYTE_BUDGET = 24 * 1024
PROJECT_DOC_MESSAGE_HEADER = "Project instructions for this workspace"


@dataclass(frozen=True)
class LoadedProjectDoc:
    path: Path
    workspace_root: Path
    contents: str
    truncated: bool = False

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def short_path(self) -> str:
        try:
            return str(self.path.relative_to(self.workspace_root))
        except ValueError:
            return str(self.path)


def _truncate_utf8_text_to_bytes(text: str, limit: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text

    truncated = encoded[:limit]
    while truncated:
        try:
            return truncated.decode("utf-8")
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return ""


def load_workspace_project_docs(
    workspace_root: Path | str,
    *,
    total_byte_budget: int = PROJECT_DOC_TOTAL_BYTE_BUDGET,
) -> tuple[LoadedProjectDoc, ...]:
    normalized_workspace_root = normalize_workspace_root(workspace_root)
    remaining_budget = total_byte_budget
    loaded_docs: list[LoadedProjectDoc] = []

    for filename in PROJECT_DOC_FILENAMES:
        if remaining_budget <= 0:
            break
        path = normalized_workspace_root / filename
        if not path.exists():
            continue
        if path.is_dir():
            raise IsADirectoryError(f"Project doc path is a directory: {path}")

        contents = path.read_text(encoding="utf-8")
        if not contents.strip():
            continue

        encoded_length = len(contents.encode("utf-8"))
        doc_contents = contents
        truncated = False
        if encoded_length > remaining_budget:
            doc_contents = _truncate_utf8_text_to_bytes(
                contents,
                remaining_budget,
            ).rstrip()
            truncated = True
        if not doc_contents.strip():
            break

        loaded_docs.append(
            LoadedProjectDoc(
                path=path,
                workspace_root=normalized_workspace_root,
                contents=doc_contents,
                truncated=truncated,
            )
        )
        remaining_budget -= len(doc_contents.encode("utf-8"))

    return tuple(loaded_docs)


def build_project_doc_prefix_messages(
    workspace_root: Path | str,
    *,
    total_byte_budget: int = PROJECT_DOC_TOTAL_BYTE_BUDGET,
) -> tuple[tuple[LoadedProjectDoc, ...], tuple[ModelMessage, ...]]:
    loaded_docs = load_workspace_project_docs(
        workspace_root,
        total_byte_budget=total_byte_budget,
    )
    messages: list[ModelMessage] = []
    for doc in loaded_docs:
        note = ""
        if doc.truncated:
            note = (
                "\n\n[Note: this project doc was truncated to fit the current "
                "runtime context budget.]"
            )
        messages.append(
            ModelResponse(
                parts=[
                    TextPart(
                        content=(
                            f"{PROJECT_DOC_MESSAGE_HEADER} from {doc.short_path}:\n\n"
                            f"<INSTRUCTIONS>\n{doc.contents}\n</INSTRUCTIONS>{note}"
                        )
                    )
                ],
                model_name="jaca-project-docs",
            )
        )
    return loaded_docs, tuple(messages)


def build_project_doc_notice_line(
    docs: Sequence[tuple[str, bool]] | tuple[LoadedProjectDoc, ...],
) -> str | None:
    if not docs:
        return None
    labels: list[str] = []
    for doc in docs:
        if isinstance(doc, tuple):
            short_path, truncated = doc
        else:
            short_path, truncated = doc.short_path, doc.truncated
        label = short_path
        if truncated:
            label += " (truncated)"
        labels.append(label)
    return f"loaded project instructions: {', '.join(labels)}"


__all__ = [
    "LoadedProjectDoc",
    "PROJECT_DOC_FILENAMES",
    "PROJECT_DOC_MESSAGE_HEADER",
    "PROJECT_DOC_TOTAL_BYTE_BUDGET",
    "build_project_doc_notice_line",
    "build_project_doc_prefix_messages",
    "load_workspace_project_docs",
]
