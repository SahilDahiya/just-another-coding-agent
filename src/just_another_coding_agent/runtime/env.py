from __future__ import annotations

import os


def trace_mode() -> str:
    value = os.environ.get("JACA_TRACE_MODE", "").strip().lower()
    if value == "off":
        return "off"
    if value == "":
        return "local"
    if value in {"local", "logfire"}:
        return value
    raise RuntimeError("JACA_TRACE_MODE must be one of: off, local, logfire")


__all__ = ["trace_mode"]
