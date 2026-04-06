import subprocess

from evaluations.swebench.dataset import SWEBenchTask
from evaluations.swebench.runner import (
    SWEBenchPrediction,
    extract_patch,
    run_swebench_task,
)


def _init_test_repo(path, *, commit_message="initial"):
    subprocess.run(
        ["git", "init", "--quiet", str(path)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path, check=True, capture_output=True,
    )
    (path / "README.md").write_text("# Test\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", commit_message, "--quiet"],
        cwd=path, check=True, capture_output=True,
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path, check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def test_extract_patch_captures_changes(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    base_commit = _init_test_repo(repo)

    (repo / "README.md").write_text("# Updated\n", encoding="utf-8")
    (repo / "new_file.py").write_text("print('hello')\n", encoding="utf-8")

    patch = extract_patch(repo, base_commit)

    assert "README.md" in patch
    assert "new_file.py" in patch
    assert "+# Updated" in patch
    assert "+print('hello')" in patch


def test_extract_patch_returns_empty_when_no_changes(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    base_commit = _init_test_repo(repo)

    patch = extract_patch(repo, base_commit)

    assert patch == ""


def test_extract_patch_compares_against_base_commit_not_head(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    base_commit = _init_test_repo(repo)

    (repo / "change.txt").write_text("change\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "agent commit", "--quiet"],
        cwd=repo, check=True, capture_output=True,
    )

    patch = extract_patch(repo, base_commit)

    assert "change.txt" in patch


def test_run_swebench_task_with_mock_exec_prompt(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "upstream"
    repo.mkdir()
    base_commit = _init_test_repo(repo)

    workspace = tmp_path / "workspace"
    subprocess.run(
        ["git", "clone", "--quiet", str(repo), str(workspace)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "--quiet", base_commit],
        cwd=workspace, check=True, capture_output=True,
    )

    (workspace / "fix.py").write_text("# fix\n", encoding="utf-8")

    monkeypatch.setattr(
        "evaluations.swebench.runner.run_exec_prompt",
        lambda **kwargs: "done",
    )

    task = SWEBenchTask(
        instance_id="test__repo-1",
        repo="test/repo",
        base_commit=base_commit,
        problem_statement="Fix the thing",
        hints_text="",
    )

    prediction = run_swebench_task(
        task,
        model="test:model",
        workspace_dir=workspace,
        sessions_dir=tmp_path / "sessions",
    )

    assert isinstance(prediction, SWEBenchPrediction)
    assert prediction.instance_id == "test__repo-1"
    assert prediction.model_name_or_path == "test:model"
    assert "fix.py" in prediction.model_patch


def test_run_swebench_task_produces_empty_patch_on_exec_failure(
    tmp_path, monkeypatch
) -> None:
    repo = tmp_path / "upstream"
    repo.mkdir()
    base_commit = _init_test_repo(repo)

    workspace = tmp_path / "workspace"
    subprocess.run(
        ["git", "clone", "--quiet", str(repo), str(workspace)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "--quiet", base_commit],
        cwd=workspace, check=True, capture_output=True,
    )

    from evaluations.bench.exec_prompt import ExecPromptError

    monkeypatch.setattr(
        "evaluations.swebench.runner.run_exec_prompt",
        lambda **kwargs: (_ for _ in ()).throw(ExecPromptError("run failed")),
    )

    task = SWEBenchTask(
        instance_id="test__repo-2",
        repo="test/repo",
        base_commit=base_commit,
        problem_statement="Broken task",
        hints_text="",
    )

    prediction = run_swebench_task(
        task,
        model="test:model",
        workspace_dir=workspace,
        sessions_dir=tmp_path / "sessions",
    )

    assert prediction.instance_id == "test__repo-2"
    assert prediction.model_patch == ""
