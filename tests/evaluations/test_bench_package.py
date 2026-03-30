import subprocess
import sys


def test_exec_prompt_module_help_does_not_emit_runpy_warning() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "evaluations.bench.exec_prompt", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "found in sys.modules after import of package 'evaluations.bench'" not in (
        result.stderr
    )
