"""Watchdog helpers for diagnosing long-running agent behavior."""

from .evaluators import BashHeavyWithoutEditsEvaluator, has_long_tool_span

__all__ = ["BashHeavyWithoutEditsEvaluator", "has_long_tool_span"]
