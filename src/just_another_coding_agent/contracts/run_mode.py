from __future__ import annotations

from typing import Literal

DEFAULT_RUN_MODE = "coding"
ONBOARDING_RUN_MODE = "onboarding"

RunMode = Literal["coding", "onboarding"]

__all__ = [
    "DEFAULT_RUN_MODE",
    "ONBOARDING_RUN_MODE",
    "RunMode",
]
