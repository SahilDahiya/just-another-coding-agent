from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import sys
import sysconfig
import threading
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from just_another_coding_agent.install_repair import (
    PACKAGE_NAME,
    find_repo_root,
    repair_install_command,
)
from just_another_coding_agent.install_repair import (
    explicit_update_command as resolve_explicit_update_command,
)

GO_TUI_BINARY = "jaca-go.exe" if os.name == "nt" else "jaca-go"
GO_TUI_BUILD_ENV = "JACA_BUILD_TUI"
GO_TUI_GO_RUN_ENV = "JACA_GO_RUN"
UPDATE_CHECK_URL = "https://pypi.org/pypi/just-another-coding-agent/json"
UPDATE_CHECK_TIMEOUT = 5.0
UPDATE_CACHE_FILENAME = "version.json"
UPDATE_REFRESH_INTERVAL = timedelta(hours=20)


@dataclass(frozen=True)
class AvailableUpdate:
    current_version: str
    latest_version: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class CachedReleaseVersion:
    latest_version: str
    last_checked_at: datetime | None


def default_backend_command(python_executable: str | None = None) -> list[str]:
    executable = sys.executable if python_executable is None else python_executable
    return [executable, "-m", "just_another_coding_agent"]


def go_tui_build_requested(env: Mapping[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    raw = values.get(GO_TUI_BUILD_ENV, "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def go_tui_go_run_requested(env: Mapping[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    raw = values.get(GO_TUI_GO_RUN_ENV, "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def go_tui_install_command(*, repo_root: Path | None = None) -> str:
    return repair_install_command(repo_root=repo_root, build_tui=True)


def package_version() -> str:
    return importlib.metadata.version(PACKAGE_NAME)


def explicit_update_command(*, repo_root: Path | None = None) -> list[str] | None:
    return resolve_explicit_update_command(repo_root=repo_root)


def available_installed_update(
    *,
    current_version: str,
    repo_root: Path | None = None,
) -> AvailableUpdate | None:
    command = explicit_update_command(repo_root=repo_root)
    if not current_version or command is None:
        return None

    latest = resolve_release_version_for_launch()
    if latest is None:
        return None
    latest_version = latest.latest_version

    newer, ok = is_newer_release_version(current_version, latest_version)
    if not ok or not newer:
        return None

    return AvailableUpdate(
        current_version=current_version,
        latest_version=latest_version,
        command=tuple(command),
    )


def resolve_release_version_for_launch() -> CachedReleaseVersion | None:
    cached = load_cached_release_version()
    if not should_refresh_cached_release_version(cached):
        return cached

    latest_version = fetch_latest_release_version()
    if latest_version is None:
        return cached

    try:
        write_cached_release_version(latest_version)
        refreshed = load_cached_release_version()
        if refreshed is not None:
            return refreshed
    except OSError:
        pass
    return CachedReleaseVersion(
        latest_version=latest_version,
        last_checked_at=datetime.now(UTC),
    )


def update_cache_path() -> Path:
    return Path.home() / ".jaca" / UPDATE_CACHE_FILENAME


def load_cached_release_version() -> CachedReleaseVersion | None:
    path = update_cache_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    latest = payload.get("latest_version", "")
    if not isinstance(latest, str) or not latest.strip():
        return None
    raw_checked = payload.get("last_checked_at", "")
    checked_at = parse_cached_release_timestamp(raw_checked)
    return CachedReleaseVersion(
        latest_version=latest.strip(),
        last_checked_at=checked_at,
    )


def parse_cached_release_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def format_cached_release_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def should_refresh_cached_release_version(
    cached: CachedReleaseVersion | None,
    *,
    now: datetime | None = None,
) -> bool:
    current = datetime.now(UTC) if now is None else now.astimezone(UTC)
    if cached is None or cached.last_checked_at is None:
        return True
    return cached.last_checked_at < current - UPDATE_REFRESH_INTERVAL


def write_cached_release_version(
    latest_version: str,
    *,
    checked_at: datetime | None = None,
) -> None:
    path = update_cache_path()
    timestamp = datetime.now(UTC) if checked_at is None else checked_at.astimezone(UTC)
    payload = {
        "latest_version": latest_version,
        "last_checked_at": format_cached_release_timestamp(timestamp),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{json.dumps(payload)}\n", encoding="utf-8")


def refresh_cached_release_version() -> None:
    latest_version = fetch_latest_release_version()
    if latest_version is None:
        return
    try:
        write_cached_release_version(latest_version)
    except OSError:
        # Read-only home or other filesystem failure: the foreground
        # launcher already has a fallback for this case, so silently
        # skip the cache write rather than crashing the daemon thread.
        return


def _refresh_cached_release_version_thread_entry() -> None:
    try:
        refresh_cached_release_version()
    except Exception:
        # Daemon threads with unhandled exceptions print to stderr and
        # look like crashes to the user. Suppress anything unexpected;
        # the worst case is a stale cache on the next launch.
        return


def refresh_cached_release_version_in_background(
    *,
    repo_root: Path | None = None,
) -> None:
    if explicit_update_command(repo_root=repo_root) is None:
        return
    cached = load_cached_release_version()
    if not should_refresh_cached_release_version(cached):
        return
    threading.Thread(
        target=_refresh_cached_release_version_thread_entry,
        name="jaca-update-check",
        daemon=True,
    ).start()


def fetch_latest_release_version() -> str | None:
    request = urllib.request.Request(UPDATE_CHECK_URL, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=UPDATE_CHECK_TIMEOUT) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None

    version = payload.get("info", {}).get("version", "")
    if not isinstance(version, str):
        return None
    return version.strip() or None


def is_newer_release_version(current: str, latest: str) -> tuple[bool, bool]:
    current_parts = parse_release_version(current)
    if current_parts is None:
        return False, False
    latest_parts = parse_release_version(latest)
    if latest_parts is None:
        return False, False
    if latest_parts > current_parts:
        return True, True
    return False, True


def parse_release_version(raw: str) -> tuple[int, int, int] | None:
    clean = raw.strip().removeprefix("v")
    if not clean or "-" in clean or "+" in clean:
        return None
    chunks = clean.split(".")
    if len(chunks) != 3:
        return None
    try:
        major, minor, patch = (int(chunk) for chunk in chunks)
    except ValueError:
        return None
    if major < 0 or minor < 0 or patch < 0:
        return None
    return major, minor, patch


def resolve_go_tui_binary() -> Path:
    scripts_dir = sysconfig.get_path("scripts")
    if not scripts_dir:
        raise RuntimeError("Python scripts directory is unavailable")
    binary = Path(scripts_dir) / GO_TUI_BINARY
    if not binary.is_file():
        repo_root = find_go_tui_repo_root()
        raise RuntimeError(
            "Installed Go TUI binary is missing. Restore it explicitly with "
            f"`{go_tui_install_command(repo_root=repo_root)}`: {binary}"
        )
    return binary


def find_go_tui_repo_root(start: Path | None = None) -> Path | None:
    return find_repo_root(Path(__file__).resolve() if start is None else start)


def resolve_go_tui_launch() -> tuple[list[str], Path | None]:
    repo_root = find_go_tui_repo_root()
    go_executable = shutil.which("go")
    if repo_root is not None and go_tui_go_run_requested() and go_executable:
        return ["go", "run", "./cmd/jaca"], repo_root
    try:
        binary = resolve_go_tui_binary()
    except RuntimeError:
        if repo_root is not None and go_executable:
            return ["go", "run", "./cmd/jaca"], repo_root
        raise
    return [str(binary)], None
