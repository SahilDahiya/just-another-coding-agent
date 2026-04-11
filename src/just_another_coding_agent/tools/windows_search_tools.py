from __future__ import annotations

import json
import os
import platform
import shutil
import tempfile
import urllib.request
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

NETWORK_TIMEOUT_SECONDS = 10
DOWNLOAD_TIMEOUT_SECONDS = 120
GITHUB_API_BASE = "https://api.github.com/repos"
GITHUB_RELEASES_BASE = "https://github.com"
JACA_MANAGED_BIN_DIR = Path.home() / ".jaca" / "bin"


@dataclass(frozen=True)
class WindowsSearchToolSpec:
    tool_name: str
    display_name: str
    repo: str
    binary_name: str
    tag_prefix: str


WINDOWS_SEARCH_TOOLS: dict[str, WindowsSearchToolSpec] = {
    "fd": WindowsSearchToolSpec(
        tool_name="fd",
        display_name="fd",
        repo="sharkdp/fd",
        binary_name="fd.exe",
        tag_prefix="v",
    ),
    "rg": WindowsSearchToolSpec(
        tool_name="rg",
        display_name="ripgrep",
        repo="BurntSushi/ripgrep",
        binary_name="rg.exe",
        tag_prefix="",
    ),
}


def jaca_managed_bin_dir() -> Path:
    return JACA_MANAGED_BIN_DIR


def build_tool_process_env(
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if base_env is None else base_env
    env = dict(source)
    if os.name != "nt":
        return env
    bin_dir = str(jaca_managed_bin_dir())
    current_path = env.get("PATH", "")
    parts = [part for part in current_path.split(os.pathsep) if part]
    normalized = {os.path.normcase(part) for part in parts}
    if os.path.normcase(bin_dir) not in normalized:
        env["PATH"] = (
            bin_dir
            if not current_path
            else os.pathsep.join([bin_dir, current_path])
        )
    return env


def apply_managed_tool_path() -> None:
    if os.name != "nt":
        return
    os.environ["PATH"] = build_tool_process_env().get("PATH", "")


def bootstrap_windows_search_tools(*, writer: TextIO | None = None) -> None:
    apply_managed_tool_path()
    if os.name != "nt":
        return
    ensure_windows_search_tool("fd", writer=writer)
    ensure_windows_search_tool("rg", writer=writer)


def ensure_windows_search_tool(
    tool: str,
    *,
    writer: TextIO | None = None,
    silent: bool = False,
) -> str:
    spec = WINDOWS_SEARCH_TOOLS.get(tool)
    if spec is None:
        raise RuntimeError(f"Unsupported Windows search tool: {tool}")
    apply_managed_tool_path()
    existing = _resolve_existing_tool_path(spec)
    if existing is not None:
        return existing
    if os.name != "nt":
        raise RuntimeError(
            f"{spec.display_name} ({spec.tool_name}) is not installed"
        )
    if not silent:
        _emit(writer, f"{spec.display_name} not found. Downloading...")
    try:
        installed = _download_and_install_windows_tool(spec)
    except Exception as error:
        raise RuntimeError(
            f"Failed to install {spec.display_name} ({spec.tool_name}): {error}"
        ) from error
    if not silent:
        _emit(writer, f"{spec.display_name} installed to {installed}")
    return installed


def _emit(writer: TextIO | None, message: str) -> None:
    if writer is None:
        return
    writer.write(f"{message}\n")
    writer.flush()


def _resolve_existing_tool_path(spec: WindowsSearchToolSpec) -> str | None:
    local_path = jaca_managed_bin_dir() / spec.binary_name
    if local_path.is_file():
        return str(local_path)
    env = build_tool_process_env()
    found = shutil.which(spec.binary_name, path=env.get("PATH"))
    if found:
        return found
    return None


def _download_and_install_windows_tool(spec: WindowsSearchToolSpec) -> str:
    version = _fetch_latest_release_version(spec.repo)
    asset_name = _windows_asset_name(spec, version)
    release_tag = f"{spec.tag_prefix}{version}"
    archive_url = (
        f"{GITHUB_RELEASES_BASE}/{spec.repo}/releases/download/"
        f"{release_tag}/{asset_name}"
    )

    bin_dir = jaca_managed_bin_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)
    final_binary = bin_dir / spec.binary_name

    with tempfile.TemporaryDirectory(
        prefix=f"jaca-{spec.tool_name}-",
        dir=bin_dir,
    ) as temp_dir:
        temp_root = Path(temp_dir)
        archive_path = temp_root / asset_name
        _download_file(archive_url, archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(temp_root)
        extracted_binary = _find_binary(temp_root, spec.binary_name)
        if extracted_binary is None:
            raise RuntimeError(
                f"Downloaded archive did not contain {spec.binary_name}"
            )
        staged_binary = temp_root / spec.binary_name
        shutil.copy2(extracted_binary, staged_binary)
        staged_binary.replace(final_binary)
    return str(final_binary)


def _fetch_latest_release_version(repo: str) -> str:
    request = urllib.request.Request(
        f"{GITHUB_API_BASE}/{repo}/releases/latest",
        headers={"User-Agent": "just-another-coding-agent"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise RuntimeError(f"GitHub API returned status {response.status}")
        payload = json.loads(response.read().decode("utf-8"))
    tag_name = payload.get("tag_name", "")
    if not isinstance(tag_name, str) or not tag_name.strip():
        raise RuntimeError("GitHub release payload did not include tag_name")
    return tag_name.strip().removeprefix("v")


def _windows_asset_name(spec: WindowsSearchToolSpec, version: str) -> str:
    arch = _windows_asset_architecture()
    if spec.tool_name == "fd":
        return f"fd-v{version}-{arch}-pc-windows-msvc.zip"
    if spec.tool_name == "rg":
        return f"ripgrep-{version}-{arch}-pc-windows-msvc.zip"
    raise RuntimeError(f"Unsupported Windows search tool: {spec.tool_name}")


def _windows_asset_architecture() -> str:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "x86_64"
    if machine in {"arm64", "aarch64"}:
        return "aarch64"
    raise RuntimeError(
        f"Unsupported Windows architecture for tool bootstrap: {machine}"
    )


def _download_file(url: str, destination: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "just-another-coding-agent"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise RuntimeError(f"download returned status {response.status}")
        destination.write_bytes(response.read())


def _find_binary(root: Path, binary_name: str) -> Path | None:
    for candidate in root.rglob(binary_name):
        if candidate.is_file():
            return candidate
    return None


__all__ = [
    "WINDOWS_SEARCH_TOOLS",
    "apply_managed_tool_path",
    "bootstrap_windows_search_tools",
    "build_tool_process_env",
    "ensure_windows_search_tool",
    "jaca_managed_bin_dir",
]
