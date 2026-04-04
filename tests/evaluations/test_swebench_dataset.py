from evaluations.swebench.dataset import (
    SWEBenchTask,
    load_swebench_lite,
    load_task_ids_from_file,
)


def _sample_rows() -> list[dict]:
    return [
        {
            "instance_id": "django__django-11099",
            "repo": "django/django",
            "base_commit": "abc123",
            "problem_statement": "Fix the login bug",
            "hints_text": "Check the auth module",
        },
        {
            "instance_id": "flask__flask-4045",
            "repo": "pallets/flask",
            "base_commit": "def456",
            "problem_statement": "Handle empty config",
            "hints_text": "",
        },
    ]


def _fake_loader(name, split):
    return _sample_rows()


def test_load_swebench_lite_returns_all_tasks() -> None:
    tasks = load_swebench_lite(_loader=_fake_loader)

    assert len(tasks) == 2
    assert tasks[0] == SWEBenchTask(
        instance_id="django__django-11099",
        repo="django/django",
        base_commit="abc123",
        problem_statement="Fix the login bug",
        hints_text="Check the auth module",
    )
    assert tasks[1].instance_id == "flask__flask-4045"


def test_load_swebench_lite_filters_by_instance_id() -> None:
    tasks = load_swebench_lite(
        filter_ids=["flask__flask-4045"],
        _loader=_fake_loader,
    )

    assert len(tasks) == 1
    assert tasks[0].instance_id == "flask__flask-4045"


def test_load_swebench_lite_empty_filter_returns_nothing() -> None:
    tasks = load_swebench_lite(
        filter_ids=["nonexistent__task-999"],
        _loader=_fake_loader,
    )

    assert tasks == []


def test_load_task_ids_from_file(tmp_path) -> None:
    task_file = tmp_path / "tasks.txt"
    task_file.write_text(
        "# comment\ndjango__django-11099\n\nflask__flask-4045\n",
        encoding="utf-8",
    )

    ids = load_task_ids_from_file(task_file)

    assert ids == ["django__django-11099", "flask__flask-4045"]
