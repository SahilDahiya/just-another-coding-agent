import pytest
from pydantic import ValidationError

from pi_code_agent.contracts.tools import ReadToolInput
from pi_code_agent.tools.read import execute_read


def test_read_tool_reads_utf8_text_file(tmp_path) -> None:
    path = tmp_path / "note.txt"
    path.write_text("hello\nworld\n", encoding="utf-8")

    result = execute_read(ReadToolInput(path=str(path)))

    assert result == "hello\nworld\n"


def test_read_tool_rejects_non_string_input() -> None:
    with pytest.raises(ValidationError):
        ReadToolInput(path=123)


def test_read_tool_fails_for_missing_file(tmp_path) -> None:
    path = tmp_path / "missing.txt"

    with pytest.raises(FileNotFoundError):
        execute_read(ReadToolInput(path=str(path)))


def test_read_tool_fails_for_directory(tmp_path) -> None:
    with pytest.raises(IsADirectoryError):
        execute_read(ReadToolInput(path=str(tmp_path)))


def test_read_tool_fails_for_invalid_utf8(tmp_path) -> None:
    path = tmp_path / "binary.bin"
    path.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(UnicodeDecodeError):
        execute_read(ReadToolInput(path=str(path)))
