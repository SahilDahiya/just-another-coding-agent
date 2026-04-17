from pathlib import Path


def test_tb2_starter_task_files_exist_and_are_non_empty() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    for name in ("a", "b", "c"):
        path = repo_root / "tasks" / f"{name}.txt"
        assert path.is_file(), path

        lines = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert lines, path
        assert len(lines) == len(set(lines)), path
