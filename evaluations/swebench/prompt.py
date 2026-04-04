"""Format SWE-bench tasks into agent prompts."""

from __future__ import annotations

from evaluations.swebench.dataset import SWEBenchTask

SWEBENCH_WORKFLOW_PROMPT = "\n".join(
    [
        "# SWE-bench Workflow",
        "",
        "You are working on a Python repository with a reported bug or feature request.",
        "Your goal is to produce a minimal patch that resolves the issue.",
        "",
        "- Read the problem statement carefully before exploring the codebase.",
        "- Use grep and find to locate the relevant source files.",
        "- Understand the existing test structure so you know what will be validated.",
        "- Make minimal, targeted changes to fix the issue.",
        "- Do NOT modify test files unless the problem statement explicitly requires it.",
        "- After making changes, run relevant tests to verify your fix.",
        "- Ensure your changes do not break existing tests.",
        "- When you are confident the fix is correct, conclude.",
    ]
)


def format_swebench_prompt(
    task: SWEBenchTask,
    *,
    include_hints: bool = False,
) -> str:
    sections = [
        SWEBENCH_WORKFLOW_PROMPT,
        "",
        f"# Repository: {task.repo}",
        "",
        "# Problem Statement",
        "",
        task.problem_statement.strip(),
    ]

    if include_hints and task.hints_text.strip():
        sections.extend(
            [
                "",
                "# Hints",
                "",
                task.hints_text.strip(),
            ]
        )

    return "\n".join(sections)


__all__ = [
    "SWEBENCH_WORKFLOW_PROMPT",
    "format_swebench_prompt",
]
