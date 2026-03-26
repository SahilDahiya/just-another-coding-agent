from pathlib import Path


def test_install_script_uses_virtualenv_for_local_package_install() -> None:
    script = (
        Path(__file__)
        .resolve()
        .parents[2]
        .joinpath("src/pi_code_agent_adapters/harbor/install-pi-code-agent.sh.j2")
        .read_text()
    )

    assert 'PACKAGE_ROOT=/installed-agent/pi-code-agent' in script
    assert 'VENV_PATH="$PACKAGE_ROOT/.venv"' in script
    assert 'python3 -m venv "$VENV_PATH"' in script
    assert 'VENV_PYTHON="$VENV_PATH/bin/python"' in script
    assert '"$VENV_PYTHON" -m pip install "$PACKAGE_ROOT"' in script
    assert '"$VENV_PYTHON" -m pi_code_agent --help' in script
