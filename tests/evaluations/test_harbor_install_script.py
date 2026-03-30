from pathlib import Path


def test_install_script_uses_virtualenv_for_local_package_install() -> None:
    script = (
        Path(__file__)
        .resolve()
        .parents[2]
        .joinpath("evaluations/harbor/install-just-another-coding-agent.sh.j2")
        .read_text()
    )

    assert 'PACKAGE_ROOT=/installed-agent/just-another-coding-agent' in script
    expected_prebuilt = (
        'PREBUILT_READ_ONLY_WORKER="$PACKAGE_ROOT/prebuilt/jaca-read-only-worker"'
    )
    assert 'VENV_PATH="$PACKAGE_ROOT/.venv"' in script
    assert 'BOOTSTRAP_PYTHON=python3' in script
    assert '"$BOOTSTRAP_PYTHON" -m venv "$VENV_PATH"' in script
    assert 'VENV_PYTHON="$VENV_PATH/bin/python"' in script
    expected_export = (
        'export JACA_PREBUILT_READ_ONLY_WORKER="$PREBUILT_READ_ONLY_WORKER"'
    )
    assert expected_prebuilt in script
    assert expected_export in script
    assert '"$VENV_PYTHON" -m pip install "$PACKAGE_ROOT"' in script
    assert '"$VENV_PYTHON" -m just_another_coding_agent --help' in script
    assert "go build" not in script
    assert "JACA_BUILD_TUI" not in script


def test_install_script_retries_venv_creation_after_installing_python3_venv() -> None:
    script = (
        Path(__file__)
        .resolve()
        .parents[2]
        .joinpath("evaluations/harbor/install-just-another-coding-agent.sh.j2")
        .read_text()
    )

    assert 'if ! "$BOOTSTRAP_PYTHON" -m venv "$VENV_PATH"; then' in script
    assert 'apt-get install -y python3-venv' in script
    assert 'rm -rf "$VENV_PATH"' in script


def test_install_script_bootstraps_python_312_with_uv_when_system_python_is_too_old(
) -> None:
    script = (
        Path(__file__)
        .resolve()
        .parents[2]
        .joinpath("evaluations/harbor/install-just-another-coding-agent.sh.j2")
        .read_text()
    )

    assert "sys.version_info >= (3, 12)" in script
    assert "curl -LsSf https://astral.sh/uv/install.sh | sh" in script
    assert 'uv python install 3.12' in script
    assert 'BOOTSTRAP_PYTHON="$(uv python find 3.12)"' in script
