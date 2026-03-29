from __future__ import annotations

import os


def env_flag(name: str) -> bool:
    value = os.environ.get(name)
    if value is None:
        return False
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


def trace_mode() -> str:
    value = os.environ.get("JACA_TRACE_MODE", "").strip().lower()
    if value in {"", "off"}:
        return "off"
    if value in {"local", "logfire"}:
        return value
    raise RuntimeError(
        "JACA_TRACE_MODE must be one of: off, local, logfire"
    )


__all__ = ["env_flag", "trace_mode"]
