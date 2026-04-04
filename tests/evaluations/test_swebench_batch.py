import json

from evaluations.swebench.batch import (
    COMPLETED_TASKS_FILENAME,
    PREDICTIONS_FILENAME,
    run_swebench_batch,
)
from evaluations.swebench.dataset import SWEBenchTask


def _make_tasks(count: int = 3) -> list[SWEBenchTask]:
    return [
        SWEBenchTask(
            instance_id=f"test__repo-{i}",
            repo="test/repo",
            base_commit=f"commit{i}",
            problem_statement=f"Fix issue {i}",
            hints_text="",
        )
        for i in range(1, count + 1)
    ]


def test_run_swebench_batch_skips_completed_tasks(tmp_path, monkeypatch) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    completed_path = output_dir / COMPLETED_TASKS_FILENAME
    completed_path.write_text("test__repo-1\n", encoding="utf-8")

    executed: list[str] = []

    def fake_run_one_task(*, task, **kwargs):
        executed.append(task.instance_id)
        from evaluations.swebench.batch import _append_prediction, _record_completed
        from evaluations.swebench.runner import SWEBenchPrediction

        _append_prediction(
            SWEBenchPrediction(
                instance_id=task.instance_id,
                model_name_or_path="test",
                model_patch="",
            ),
            kwargs["predictions_path"],
        )
        _record_completed(task.instance_id, kwargs["completed_path"])

    monkeypatch.setattr(
        "evaluations.swebench.batch._run_one_task",
        fake_run_one_task,
    )

    tasks = _make_tasks(3)
    predictions_path = run_swebench_batch(
        tasks=tasks,
        model="test:model",
        output_dir=output_dir,
    )

    assert "test__repo-1" not in executed
    assert "test__repo-2" in executed
    assert "test__repo-3" in executed
    assert predictions_path == output_dir / PREDICTIONS_FILENAME


def test_run_swebench_batch_creates_output_structure(tmp_path, monkeypatch) -> None:
    output_dir = tmp_path / "output"

    def fake_run_one_task(*, task, **kwargs):
        from evaluations.swebench.batch import _append_prediction, _record_completed
        from evaluations.swebench.runner import SWEBenchPrediction

        _append_prediction(
            SWEBenchPrediction(
                instance_id=task.instance_id,
                model_name_or_path="test",
                model_patch=f"patch-{task.instance_id}",
            ),
            kwargs["predictions_path"],
        )
        _record_completed(task.instance_id, kwargs["completed_path"])

    monkeypatch.setattr(
        "evaluations.swebench.batch._run_one_task",
        fake_run_one_task,
    )

    run_swebench_batch(
        tasks=_make_tasks(2),
        model="test:model",
        output_dir=output_dir,
    )

    predictions_path = output_dir / PREDICTIONS_FILENAME
    assert predictions_path.exists()
    lines = predictions_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["instance_id"] == "test__repo-1"
    assert first["model_name_or_path"] == "test"
    assert first["model_patch"] == "patch-test__repo-1"

    completed = (
        (output_dir / COMPLETED_TASKS_FILENAME)
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()
    )
    assert set(completed) == {"test__repo-1", "test__repo-2"}


def test_run_swebench_batch_returns_early_when_all_completed(tmp_path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    completed_path = output_dir / COMPLETED_TASKS_FILENAME
    completed_path.write_text("test__repo-1\n", encoding="utf-8")

    predictions_path = run_swebench_batch(
        tasks=_make_tasks(1),
        model="test:model",
        output_dir=output_dir,
    )

    assert predictions_path == output_dir / PREDICTIONS_FILENAME
