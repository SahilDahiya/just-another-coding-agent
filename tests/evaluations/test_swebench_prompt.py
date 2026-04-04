from evaluations.swebench.dataset import SWEBenchTask
from evaluations.swebench.prompt import (
    SWEBENCH_WORKFLOW_PROMPT,
    format_swebench_prompt,
)

_TASK = SWEBenchTask(
    instance_id="django__django-11099",
    repo="django/django",
    base_commit="abc123",
    problem_statement="Fix the login bug in contrib.auth",
    hints_text="Check SessionMiddleware",
)


def test_format_swebench_prompt_includes_workflow_and_issue() -> None:
    prompt = format_swebench_prompt(_TASK)

    assert SWEBENCH_WORKFLOW_PROMPT in prompt
    assert "django/django" in prompt
    assert "Fix the login bug in contrib.auth" in prompt
    assert "Hints" not in prompt
    assert "SessionMiddleware" not in prompt


def test_format_swebench_prompt_includes_hints_when_requested() -> None:
    prompt = format_swebench_prompt(_TASK, include_hints=True)

    assert "# Hints" in prompt
    assert "SessionMiddleware" in prompt


def test_format_swebench_prompt_skips_empty_hints() -> None:
    task = SWEBenchTask(
        instance_id="flask__flask-4045",
        repo="pallets/flask",
        base_commit="def456",
        problem_statement="Handle empty config",
        hints_text="   ",
    )

    prompt = format_swebench_prompt(task, include_hints=True)

    assert "# Hints" not in prompt
