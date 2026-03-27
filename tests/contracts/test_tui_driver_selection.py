from pathlib import Path

from textual.drivers.linux_driver import LinuxDriver

from just_another_coding_agent.tui.app import CodingAgentApp
from just_another_coding_agent.tui.drivers import VscodeLinuxDriver


def test_tui_uses_vscode_driver_in_vscode_terminal(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TERM_PROGRAM", "vscode")

    app = CodingAgentApp(
        model="ollama:test",
        workspace_root=tmp_path,
        sessions_root=tmp_path / "sessions",
        thinking=None,
    )

    assert app.driver_class is VscodeLinuxDriver


def test_tui_uses_default_driver_outside_vscode(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TERM_PROGRAM", raising=False)

    app = CodingAgentApp(
        model="ollama:test",
        workspace_root=tmp_path,
        sessions_root=tmp_path / "sessions",
        thinking=None,
    )

    assert app.driver_class is LinuxDriver
