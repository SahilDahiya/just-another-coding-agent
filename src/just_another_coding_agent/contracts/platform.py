from __future__ import annotations

import os
from typing import Literal, TypeAlias

ShellFamily: TypeAlias = Literal["posix", "powershell"]


def detect_default_shell_family() -> ShellFamily:
    if os.name == "nt":
        return "powershell"
    return "posix"


__all__ = ["ShellFamily", "detect_default_shell_family"]
