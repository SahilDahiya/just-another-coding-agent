"""Benchmark-oriented adapters around the canonical stdio backend."""

from .exec_prompt import ExecPromptError, main, read_prompt, run_exec_prompt

__all__ = ["ExecPromptError", "main", "read_prompt", "run_exec_prompt"]
