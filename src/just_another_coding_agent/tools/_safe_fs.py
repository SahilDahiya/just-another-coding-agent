from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO


def _required_nofollow_flag() -> int:
    try:
        return os.O_NOFOLLOW
    except AttributeError as error:  # pragma: no cover - Windows remains deferred.
        raise RuntimeError(
            "Symlink-safe filesystem operations require os.O_NOFOLLOW"
        ) from error


def _dir_open_flags() -> int:
    flags = os.O_RDONLY | _required_nofollow_flag()
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if directory_flag:
        flags |= directory_flag
    cloexec_flag = getattr(os, "O_CLOEXEC", 0)
    if cloexec_flag:
        flags |= cloexec_flag
    return flags


def _file_open_flags(base_flags: int) -> int:
    cloexec_flag = getattr(os, "O_CLOEXEC", 0)
    return base_flags | _required_nofollow_flag() | cloexec_flag


def _open_file_descriptor_no_symlink(
    path: Path,
    *,
    flags: int,
    mode: int = 0o666,
) -> int:
    absolute_path = Path(path)
    if not absolute_path.is_absolute():
        raise RuntimeError(
            f"Symlink-safe file open requires an absolute path: {absolute_path}"
        )

    parent_fd = os.open(str(absolute_path.parent), _dir_open_flags())
    try:
        return os.open(
            absolute_path.name,
            _file_open_flags(flags),
            mode,
            dir_fd=parent_fd,
        )
    finally:
        os.close(parent_fd)


def read_bytes_no_symlink(path: Path) -> bytes:
    file_fd = _open_file_descriptor_no_symlink(path, flags=os.O_RDONLY)
    with os.fdopen(file_fd, "rb", closefd=True) as handle:
        return handle.read()


def write_bytes_no_symlink(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_fd = _open_file_descriptor_no_symlink(
        path,
        flags=os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
    )
    with os.fdopen(file_fd, "wb", closefd=True) as handle:
        handle.write(data)


@contextlib.contextmanager
def open_binary_update_no_symlink(path: Path) -> Iterator[BinaryIO]:
    file_fd = _open_file_descriptor_no_symlink(path, flags=os.O_RDWR)
    with os.fdopen(file_fd, "r+b", closefd=True) as handle:
        yield handle
