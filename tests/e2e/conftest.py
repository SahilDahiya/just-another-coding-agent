from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_jaca_home(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
