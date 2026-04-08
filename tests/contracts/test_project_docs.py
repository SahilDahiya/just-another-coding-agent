import pytest

from just_another_coding_agent.runtime.project_docs import (
    PROJECT_DOC_MESSAGE_HEADER,
    build_project_doc_prefix_messages,
    load_workspace_project_docs,
)


def test_load_workspace_project_docs_prefers_stable_filename_order(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "CLAUDE.md").write_text("claude instructions\n", encoding="utf-8")
    (workspace_root / "AGENTS.md").write_text("agent instructions\n", encoding="utf-8")

    docs = load_workspace_project_docs(workspace_root)

    assert [doc.filename for doc in docs] == ["AGENTS.md", "CLAUDE.md"]
    assert [doc.short_path for doc in docs] == ["AGENTS.md", "CLAUDE.md"]


def test_build_project_doc_prefix_messages_marks_truncation(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "AGENTS.md").write_text("A" * 200, encoding="utf-8")

    docs, messages = build_project_doc_prefix_messages(
        workspace_root,
        total_byte_budget=64,
    )

    assert len(docs) == 1
    assert docs[0].truncated is True
    assert len(messages) == 1
    text = messages[0].parts[0].content
    assert text.startswith(f"{PROJECT_DOC_MESSAGE_HEADER} from AGENTS.md:")
    assert "<INSTRUCTIONS>" in text
    assert "truncated to fit the current runtime context budget" in text


def test_load_workspace_project_docs_skips_later_docs_when_budget_is_exhausted(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "AGENTS.md").write_text("A" * 64, encoding="utf-8")
    (workspace_root / "CLAUDE.md").write_text("claude instructions\n", encoding="utf-8")

    docs = load_workspace_project_docs(
        workspace_root,
        total_byte_budget=64,
    )

    assert len(docs) == 1
    assert docs[0].filename == "AGENTS.md"
    assert docs[0].truncated is False


def test_load_workspace_project_docs_rejects_directory_targets(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "AGENTS.md").mkdir()

    with pytest.raises(IsADirectoryError, match="Project doc path is a directory"):
        load_workspace_project_docs(workspace_root)
