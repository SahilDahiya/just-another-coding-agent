# ruff: noqa: E402
from __future__ import annotations

import glob
import re
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from just_another_coding_agent.contracts.sandbox import FileSystemSandboxPolicy
from just_another_coding_agent.tools._workspace import (
    canonicalize_path_target,
    normalize_workspace_root,
    resolve_workspace_path,
)
from just_another_coding_agent.tools.read_only_worker.protocol import (
    CancelWorkerRequest,
    FindCallResult,
    FindWorkerRequest,
    GrepCallResult,
    GrepMatch,
    GrepWorkerRequest,
    HelloWorkerRequest,
    HelloWorkerResponse,
    LsCallResult,
    LsEntry,
    LsWorkerRequest,
    ReadCallResult,
    ReadOnlyWorkerErrorResponse,
    ReadWorkerRequest,
    ShutdownWorkerRequest,
    WorkerRequest,
    encode_worker_message,
    parse_worker_request_line,
)


def _emit(message) -> None:
    sys.stdout.write(f"{encode_worker_message(message)}\n")
    sys.stdout.flush()


def _allowed_roots(
    workspace_root: str,
    filesystem_policy: FileSystemSandboxPolicy,
) -> tuple[Path, ...]:
    roots = [normalize_workspace_root(workspace_root)]
    roots.extend(
        canonicalize_path_target(root) for root in filesystem_policy.extra_read_roots
    )
    return tuple(roots)


def _resolve_readable_path(
    *,
    workspace_root: str,
    filesystem_policy: FileSystemSandboxPolicy,
    tool_path: str | None,
) -> Path:
    target_path = tool_path or "."
    resolved = resolve_workspace_path(
        workspace_root=workspace_root,
        tool_path=target_path,
    )
    if filesystem_policy.access == "full_access":
        return resolved
    for allowed_root in _allowed_roots(workspace_root, filesystem_policy):
        if resolved.is_relative_to(allowed_root):
            return resolved
    raise PermissionError(
        f"path is outside allowed read roots: {canonicalize_path_target(resolved)}"
    )


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _handle_read(request: ReadWorkerRequest) -> ReadCallResult:
    target = _resolve_readable_path(
        workspace_root=request.workspace_root,
        filesystem_policy=request.filesystem_policy,
        tool_path=request.path,
    )
    if not target.exists():
        raise FileNotFoundError(str(target))
    if target.is_dir():
        raise IsADirectoryError(str(target))
    raw = target.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise UnicodeDecodeError(
            error.encoding,
            error.object,
            error.start,
            error.end,
            f"{target} is not valid UTF-8 text",
        ) from error

    lines = text.splitlines(keepends=True)
    total_lines = len(lines)
    if total_lines == 0:
        return ReadCallResult(
            request_id=request.request_id,
            window_text="",
            total_lines=0,
            start_line=1,
            end_line=1,
            truncated=False,
            next_offset=None,
            first_line_exceeds_max_bytes=False,
        )

    start_line = request.offset or 1
    if start_line > total_lines:
        raise ValueError(
            f"offset {start_line} is beyond end of file ({total_lines} lines total)"
        )

    requested_limit = request.limit or (total_lines - start_line + 1)
    hard_limit = min(requested_limit, request.max_lines)
    window: list[str] = []
    consumed_bytes = 0
    next_offset: int | None = None
    truncated = False
    first_line_exceeds_max_bytes = False
    end_line = start_line

    remaining = lines[start_line - 1 :]
    for index, line in enumerate(remaining, start=start_line):
        if len(window) >= hard_limit:
            truncated = request.limit is None or requested_limit > request.max_lines
            next_offset = index
            break
        line_bytes = _utf8_len(line)
        if not window and line_bytes > request.max_bytes:
            first_line_exceeds_max_bytes = True
            next_offset = index
            break
        if consumed_bytes + line_bytes > request.max_bytes:
            truncated = True
            next_offset = index
            break
        window.append(line)
        consumed_bytes += line_bytes
        end_line = index

    if (
        not truncated
        and not first_line_exceeds_max_bytes
        and request.limit is not None
        and end_line < total_lines
        and len(window) >= request.limit
    ):
        next_offset = end_line + 1

    return ReadCallResult(
        request_id=request.request_id,
        window_text="".join(window),
        total_lines=total_lines,
        start_line=start_line,
        end_line=end_line,
        truncated=truncated,
        next_offset=next_offset,
        first_line_exceeds_max_bytes=first_line_exceeds_max_bytes,
    )


def _handle_ls(request: LsWorkerRequest) -> LsCallResult:
    target = _resolve_readable_path(
        workspace_root=request.workspace_root,
        filesystem_policy=request.filesystem_policy,
        tool_path=request.path,
    )
    if not target.exists():
        raise FileNotFoundError(str(target))
    if not target.is_dir():
        raise NotADirectoryError(str(target))

    children = sorted(target.iterdir(), key=lambda child: child.name)
    rendered_entries: list[LsEntry] = []
    consumed_bytes = 0
    byte_limit_hit = False
    for child in children[: request.limit]:
        display_name = f"{child.name}/" if child.is_dir() else child.name
        line_bytes = _utf8_len(f"{display_name}\n")
        if consumed_bytes + line_bytes > request.max_bytes:
            byte_limit_hit = True
            break
        rendered_entries.append(LsEntry(name=child.name, is_dir=child.is_dir()))
        consumed_bytes += line_bytes

    return LsCallResult(
        request_id=request.request_id,
        entries=rendered_entries,
        total_entries=len(children),
        limit_hit=len(children) > len(rendered_entries) and not byte_limit_hit,
        byte_limit_hit=byte_limit_hit,
    )


def _handle_find(request: FindWorkerRequest) -> FindCallResult:
    target = _resolve_readable_path(
        workspace_root=request.workspace_root,
        filesystem_policy=request.filesystem_policy,
        tool_path=request.path,
    )
    if not target.exists():
        raise FileNotFoundError(str(target))
    if not target.is_dir():
        raise NotADirectoryError(str(target))

    matched_paths = sorted(
        {
            Path(match).resolve().relative_to(target.resolve()).as_posix()
            for match in glob.glob(str(target / request.pattern), recursive=True)
            if Path(match).is_file()
        }
    )

    rendered_matches: list[str] = []
    consumed_bytes = 0
    byte_limit_hit = False
    for match in matched_paths[: request.limit]:
        line_bytes = _utf8_len(f"{match}\n")
        if consumed_bytes + line_bytes > request.max_bytes:
            byte_limit_hit = True
            break
        rendered_matches.append(match)
        consumed_bytes += line_bytes

    return FindCallResult(
        request_id=request.request_id,
        matches=rendered_matches,
        total_matches=len(matched_paths),
        limit_hit=len(matched_paths) > len(rendered_matches) and not byte_limit_hit,
        byte_limit_hit=byte_limit_hit,
    )


def _iter_grep_candidates(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(path for path in target.rglob("*") if path.is_file())


def _handle_grep(request: GrepWorkerRequest) -> GrepCallResult:
    target = _resolve_readable_path(
        workspace_root=request.workspace_root,
        filesystem_policy=request.filesystem_policy,
        tool_path=request.path,
    )
    if not target.exists():
        raise FileNotFoundError(str(target))

    base_root = target if target.is_dir() else target.parent
    pattern = re.escape(request.pattern) if request.literal else request.pattern
    flags = re.IGNORECASE if request.ignore_case else 0
    matcher = re.compile(pattern, flags)
    matches: list[GrepMatch] = []
    consumed_bytes = 0
    limit_hit = False
    byte_limit_hit = False
    truncated_lines = False

    for candidate in _iter_grep_candidates(target):
        relative_path = candidate.relative_to(base_root).as_posix()
        if request.glob is not None and not PurePosixPath(relative_path).match(
            request.glob
        ):
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if matcher.search(line) is None:
                continue
            if len(matches) >= request.limit:
                limit_hit = True
                break
            rendered_line = line
            line_truncated = False
            if len(rendered_line) > request.max_line_chars:
                rendered_line = rendered_line[: request.max_line_chars]
                line_truncated = True
                truncated_lines = True
            display_line = f"{relative_path}:{line_number}:{rendered_line}"
            line_bytes = _utf8_len(f"{display_line}\n")
            if consumed_bytes + line_bytes > request.max_bytes:
                byte_limit_hit = True
                break
            matches.append(
                GrepMatch(
                    path=relative_path,
                    line_number=line_number,
                    text=rendered_line,
                    text_truncated=line_truncated,
                )
            )
            consumed_bytes += line_bytes
        if limit_hit or byte_limit_hit:
            break

    return GrepCallResult(
        request_id=request.request_id,
        matches=matches,
        limit_hit=limit_hit,
        byte_limit_hit=byte_limit_hit,
        truncated_lines=truncated_lines,
    )


def _dispatch(request: WorkerRequest):
    if isinstance(request, HelloWorkerRequest):
        return HelloWorkerResponse(request_id=request.request_id)
    if isinstance(request, ReadWorkerRequest):
        return _handle_read(request)
    if isinstance(request, LsWorkerRequest):
        return _handle_ls(request)
    if isinstance(request, FindWorkerRequest):
        return _handle_find(request)
    if isinstance(request, GrepWorkerRequest):
        return _handle_grep(request)
    if isinstance(request, CancelWorkerRequest):
        return ReadOnlyWorkerErrorResponse(
            request_id=request.request_id,
            error_code="cancelled",
            message=f"request cancelled: {request.target_request_id}",
        )
    if isinstance(request, ShutdownWorkerRequest):
        return None
    raise RuntimeError(f"unsupported request type: {type(request).__name__}")


def main() -> int:
    for line in sys.stdin:
        stripped = line.strip()
        if not stripped:
            continue
        request: WorkerRequest | None = None
        try:
            request = parse_worker_request_line(stripped)
            response = _dispatch(request)
            if response is None:
                break
        except PermissionError as error:
            _emit(
                ReadOnlyWorkerErrorResponse(
                    request_id=request.request_id,
                    error_code="path_error",
                    message=str(error),
                )
            )
            continue
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError) as error:
            _emit(
                ReadOnlyWorkerErrorResponse(
                    request_id=request.request_id,
                    error_code="path_error",
                    message=str(error),
                )
            )
            continue
        except UnicodeDecodeError as error:
            detail = error.reason if error.reason else str(error)
            _emit(
                ReadOnlyWorkerErrorResponse(
                    request_id=request.request_id,
                    error_code="encoding_error",
                    message=detail,
                )
            )
            continue
        except re.error as error:
            _emit(
                ReadOnlyWorkerErrorResponse(
                    request_id=request.request_id,
                    error_code="operational_error",
                    message=str(error),
                )
            )
            continue
        except ValueError as error:
            _emit(
                ReadOnlyWorkerErrorResponse(
                    request_id=request.request_id,
                    error_code="operational_error",
                    message=str(error),
                )
            )
            continue
        except Exception as error:
            request_id = (
                request.request_id if request is not None else "unknown"
            )
            _emit(
                ReadOnlyWorkerErrorResponse(
                    request_id=request_id,
                    error_code="operational_error",
                    message=str(error),
                )
            )
            continue

        _emit(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
