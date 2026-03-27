"""Textual driver overrides for known terminal compatibility issues."""

from __future__ import annotations

import os

from textual.drivers.linux_driver import LinuxDriver
from textual.drivers.linux_inline_driver import LinuxInlineDriver

KITTY_KEYBOARD_ENABLE = "\x1b[>1u"


class VscodeLinuxDriver(LinuxDriver):
    """Linux driver that skips Kitty keyboard mode in VS Code terminals.

    Temporary compatibility workaround for Textual + VS Code integrated
    terminal spacebar regression. Delete once the upstream Textual / VS Code
    interaction is fixed and validated in this repo.
    """

    def write(self, data: str) -> None:
        if data == KITTY_KEYBOARD_ENABLE:
            return
        super().write(data)


class VscodeLinuxInlineDriver(LinuxInlineDriver):
    """Inline driver variant of the VS Code Kitty keyboard workaround."""

    def write(self, data: str) -> None:
        if data == KITTY_KEYBOARD_ENABLE:
            return
        super().write(data)


def running_in_vscode_terminal() -> bool:
    """Return whether the current terminal is the VS Code integrated terminal."""
    return os.environ.get("TERM_PROGRAM", "").lower() == "vscode"


__all__ = [
    "VscodeLinuxDriver",
    "VscodeLinuxInlineDriver",
    "running_in_vscode_terminal",
]
