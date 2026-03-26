import pytest
from pydantic import ValidationError

from pi_code_agent.contracts.tools import WriteToolInput
from pi_code_agent.tools.write import execute_write


def test_write_tool_writes_utf8_text_file(tmp_path) -> None:
    path = tmp_path / "note.txt"

    result = execute_write(
        WriteToolInput(path=str(path), content="hello\nworld\n")
    )

    assert path.read_text(encoding="utf-8") == "hello\nworld\n"
    assert result == f"Wrote {path}"


def test_write_tool_overwrites_existing_file(tmp_path) -> None:
    path = tmp_path / "note.txt"
    path.write_text("old\n", encoding="utf-8")

    execute_write(WriteToolInput(path=str(path), content="new\n"))

    assert path.read_text(encoding="utf-8") == "new\n"


def test_write_tool_creates_parent_directories(tmp_path) -> None:
    path = tmp_path / "nested" / "dir" / "note.txt"

    execute_write(WriteToolInput(path=str(path), content="hello"))

    assert path.read_text(encoding="utf-8") == "hello"


def test_write_tool_allows_empty_content(tmp_path) -> None:
    path = tmp_path / "empty.txt"

    execute_write(WriteToolInput(path=str(path), content=""))

    assert path.read_text(encoding="utf-8") == ""


def test_write_tool_rejects_non_string_path() -> None:
    with pytest.raises(ValidationError):
        WriteToolInput(path=123, content="hello")


def test_write_tool_rejects_non_string_content() -> None:
    with pytest.raises(ValidationError):
        WriteToolInput(path="note.txt", content=123)


def test_write_tool_fails_for_directory_target(tmp_path) -> None:
    with pytest.raises(IsADirectoryError):
        execute_write(WriteToolInput(path=str(tmp_path), content="hello"))
