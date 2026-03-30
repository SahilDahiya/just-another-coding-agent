"""Benchmark-oriented adapters around the canonical stdio backend."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["ExecPromptError", "main", "read_prompt", "run_exec_prompt"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(".exec_prompt", __name__)
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(list(globals()) + __all__)
