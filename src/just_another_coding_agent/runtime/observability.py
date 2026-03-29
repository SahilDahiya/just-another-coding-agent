from __future__ import annotations

import os
import tomllib
from pathlib import Path

from just_another_coding_agent.runtime.env import env_flag

_configured = False
_DEFAULT_SERVICE_NAME = "jaca"


def configure_observability() -> None:
    global _configured

    if not env_flag("JACA_TRACE"):
        return
    if _configured:
        return
    if not _has_logfire_credentials():
        raise RuntimeError(
            "JACA_TRACE=1 requires Logfire project credentials. Run "
            "`uv run logfire auth` and `uv run logfire projects use <project>` "
            "or set `LOGFIRE_TOKEN`."
        )

    import logfire

    logfire.configure(
        send_to_logfire=True,
        console=False,
        service_name=os.environ.get(
            "LOGFIRE_SERVICE_NAME",
            _DEFAULT_SERVICE_NAME,
        ),
    )
    _configured = True


def _has_logfire_credentials() -> bool:
    if os.environ.get("LOGFIRE_TOKEN", "").strip():
        return True

    config_path = Path.home() / ".logfire" / "default.toml"
    if not config_path.exists():
        return False

    try:
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise RuntimeError(
            f"Invalid Logfire credentials file: {config_path}"
        ) from error

    tokens = config.get("tokens")
    if not isinstance(tokens, dict):
        return False

    for value in tokens.values():
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, dict):
            token = value.get("token")
            if isinstance(token, str) and token.strip():
                return True

    return False


__all__ = ["configure_observability"]
