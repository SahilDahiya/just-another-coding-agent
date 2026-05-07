from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from just_another_coding_agent.contracts.onboarding import (
    OnboardingAnswerResult,
    OnboardingQuestionRequest,
)
from just_another_coding_agent.provider_readiness import ProviderReadinessError
from just_another_coding_agent.runtime.dspy_bridge import (
    build_dspy_lm,
    import_dspy,
)

GENERATOR_VERSION = "dspy-mcq-v1"
TOOL_GENERATOR_VERSION = "agent-authored-mcq-v1"
QUESTION_TYPE_MCQ = "mcq"
STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"
STATUS_ABANDONED = "abandoned"
_CODE_EXTENSIONS = (
    ".py",
    ".go",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".rs",
    ".java",
)
_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "node_modules",
        "dist",
        "build",
        "__pycache__",
    }
)
_MAX_SNIPPET_LINES = 24
_MAX_FILE_BYTES = 64_000


class OnboardingError(RuntimeError):
    """Base onboarding domain error."""


class OnboardingGenerationError(OnboardingError):
    """Raised when onboarding question generation cannot produce a valid MCQ."""


class OnboardingValidationError(ValueError):
    """Raised when onboarding state would violate canonical invariants."""


class OnboardingAttemptNotFoundError(ValueError):
    """Raised when a requested onboarding attempt does not exist."""


@dataclass(frozen=True)
class SnippetSelection:
    path: str
    start_line: int
    end_line: int
    text: str


@dataclass(frozen=True)
class GeneratedMcqQuestion:
    question_type: Literal["mcq"]
    snippet: SnippetSelection
    prompt: str
    options: tuple[str, str, str, str]
    correct_index: int
    explanation: str
    generator_version: str = GENERATOR_VERSION


@dataclass(frozen=True)
class PublishedMcqQuestion:
    question_type: Literal["mcq"]
    packet_ids: tuple[str, ...]
    prompt: str
    options: tuple[str, str, str, str]
    correct_index: int
    explanation: str
    generator_version: str = TOOL_GENERATOR_VERSION


@dataclass(frozen=True)
class OnboardingStartResult:
    session_id: str
    created_session: bool
    attempt_id: str
    question_type: Literal["mcq"]
    snippet: SnippetSelection
    prompt: str
    options: tuple[str, str, str, str]
    explanation: str
    generator_version: str


OnboardingSubmitResult = OnboardingAnswerResult


def onboarding_db_path(
    *,
    sessions_root: Path | str,
    workspace_root: Path | str,
) -> Path:
    from just_another_coding_agent.rpc.session_store import workspace_sessions_dir

    return workspace_sessions_dir(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
    ) / "onboarding-v2.sqlite3"


def publish_onboarding_mcq(
    *,
    sessions_root: Path | str,
    workspace_root: Path | str,
    session_id: str,
    run_id: str,
    question: PublishedMcqQuestion,
) -> OnboardingQuestionRequest:
    _validate_published_question(question)
    db_path = onboarding_db_path(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
    )
    attempt_id = uuid4().hex
    with _connect(db_path) as conn:
        _ensure_schema(conn)
        _abandon_pending_attempts(conn, session_id=session_id)
        conn.execute(
            """
            INSERT INTO onboarding_attempts (
                id,
                session_id,
                run_id,
                created_at,
                status,
                question_type,
                snippet_path,
                snippet_start_line,
                snippet_end_line,
                snippet_text,
                prompt,
                question_payload_json,
                answer_payload_json,
                result_payload_json,
                explanation,
                completed_at,
                generator_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt_id,
                session_id,
                run_id,
                _utc_now(),
                STATUS_PENDING,
                question.question_type,
                None,
                None,
                None,
                None,
                question.prompt,
                json.dumps(_published_question_payload(question), sort_keys=True),
                None,
                None,
                question.explanation,
                None,
                question.generator_version,
            ),
        )
    return OnboardingQuestionRequest(
        attempt_id=attempt_id,
        question_type=question.question_type,
        prompt=question.prompt,
        options=list(question.options),
    )


def start_onboarding_mcq(
    *,
    sessions_root: Path | str,
    workspace_root: Path | str,
    session_id: str,
    model: Any,
    created_session: bool,
    generated_question: GeneratedMcqQuestion | None = None,
) -> OnboardingStartResult:
    db_path = onboarding_db_path(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
    )
    if db_path.exists():
        with _connect(db_path) as conn:
            _ensure_schema(conn)
            pending_row = _fetch_pending_attempt(conn, session_id=session_id)
            if pending_row is not None:
                if not _row_is_start_compatible(pending_row):
                    raise OnboardingValidationError(
                        "Session has a pending live onboarding tool question "
                        "that cannot be reopened through onboarding.start"
                    )
                return _row_to_start_result(
                    row=pending_row,
                    session_id=session_id,
                    created_session=created_session,
                )

    question = generated_question
    if question is None:
        question = generate_onboarding_mcq(
            workspace_root=workspace_root,
            model=model,
        )
    _validate_generated_question(question)
    return _persist_generated_onboarding_mcq(
        db_path=db_path,
        session_id=session_id,
        created_session=created_session,
        question=question,
    )


def submit_onboarding_mcq(
    *,
    sessions_root: Path | str,
    workspace_root: Path | str,
    session_id: str,
    attempt_id: str,
    selected_index: int,
) -> OnboardingSubmitResult:
    _validate_selected_index(selected_index)
    db_path = onboarding_db_path(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
    )
    with _connect(db_path) as conn:
        _ensure_schema(conn)
        row = _fetch_attempt_row(conn, attempt_id=attempt_id, session_id=session_id)
        if row is None:
            raise OnboardingAttemptNotFoundError(
                f"Unknown onboarding attempt: {attempt_id}"
            )
        if row["status"] != STATUS_PENDING:
            raise OnboardingValidationError(
                f"Onboarding attempt is not pending: {attempt_id}"
            )

        payload = _parse_question_payload(row["question_payload_json"])
        correct_index = int(payload["correct_index"])
        options = tuple(str(item).strip() for item in payload["options"])
        _validate_options(options)
        _validate_selected_index(correct_index)
        is_correct = selected_index == correct_index

        conn.execute(
            """
            UPDATE onboarding_attempts
            SET
                status = ?,
                answer_payload_json = ?,
                result_payload_json = ?,
                completed_at = ?
            WHERE id = ? AND session_id = ? AND status = ?
            """,
            (
                STATUS_COMPLETED,
                json.dumps(
                    {"selected_index": selected_index},
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "is_correct": is_correct,
                        "correct_index": correct_index,
                    },
                    sort_keys=True,
                ),
                _utc_now(),
                attempt_id,
                session_id,
                STATUS_PENDING,
            ),
        )
        updated = _fetch_attempt_row(conn, attempt_id=attempt_id, session_id=session_id)
        if updated is None:
            raise OnboardingError("completed onboarding attempt disappeared")
        if updated["status"] != STATUS_COMPLETED:
            raise OnboardingError("completed onboarding attempt was not persisted")
        return OnboardingSubmitResult(
            session_id=session_id,
            attempt_id=attempt_id,
            question_type=QUESTION_TYPE_MCQ,
            selected_index=selected_index,
            correct_index=correct_index,
            correct_option=options[correct_index],
            is_correct=is_correct,
            explanation=str(updated["explanation"]),
        )


def abandon_pending_onboarding_attempt(
    *,
    sessions_root: Path | str,
    workspace_root: Path | str,
    session_id: str,
    attempt_id: str,
) -> None:
    db_path = onboarding_db_path(
        sessions_root=sessions_root,
        workspace_root=workspace_root,
    )
    if not db_path.exists():
        return
    with _connect(db_path) as conn:
        _ensure_schema(conn)
        conn.execute(
            """
            UPDATE onboarding_attempts
            SET
                status = ?,
                completed_at = ?
            WHERE id = ? AND session_id = ? AND status = ?
            """,
            (
                STATUS_ABANDONED,
                _utc_now(),
                attempt_id,
                session_id,
                STATUS_PENDING,
            ),
        )


def generate_onboarding_mcq(
    *,
    workspace_root: Path | str,
    model: Any,
) -> GeneratedMcqQuestion:
    try:
        snippet = _select_snippet(workspace_root)
        dspy = import_dspy()
        lm = build_dspy_lm(dspy=dspy, model=model)
    except ProviderReadinessError:
        raise
    except RuntimeError as error:
        raise OnboardingGenerationError(str(error)) from error

    class GenerateOnboardingMcqSignature(dspy.Signature):
        """Generate one objective repository onboarding MCQ from the provided code.

        The question must be answerable from the snippet alone.
        Return exactly four concise options with exactly one correct answer.
        """

        repo_name: str = dspy.InputField()
        snippet_path: str = dspy.InputField()
        snippet_start_line: int = dspy.InputField()
        snippet_end_line: int = dspy.InputField()
        snippet_text: str = dspy.InputField()
        prompt: str = dspy.OutputField(
            desc="One concise objective question about the snippet."
        )
        option_a: str = dspy.OutputField(desc="Option 1.")
        option_b: str = dspy.OutputField(desc="Option 2.")
        option_c: str = dspy.OutputField(desc="Option 3.")
        option_d: str = dspy.OutputField(desc="Option 4.")
        correct_index: Literal[0, 1, 2, 3] = dspy.OutputField(
            desc="0 for option_a, 1 for option_b, 2 for option_c, 3 for option_d."
        )
        explanation: str = dspy.OutputField(
            desc="One short explanation of why the correct option is right."
        )

    predictor = dspy.Predict(GenerateOnboardingMcqSignature)
    predictor.set_lm(lm)
    try:
        prediction = predictor(
            repo_name=Path(workspace_root).name,
            snippet_path=snippet.path,
            snippet_start_line=snippet.start_line,
            snippet_end_line=snippet.end_line,
            snippet_text=snippet.text,
        )
    except ProviderReadinessError:
        raise
    except RuntimeError as error:
        raise OnboardingGenerationError(str(error)) from error
    try:
        question = GeneratedMcqQuestion(
            question_type=QUESTION_TYPE_MCQ,
            snippet=snippet,
            prompt=str(prediction.prompt).strip(),
            options=(
                str(prediction.option_a).strip(),
                str(prediction.option_b).strip(),
                str(prediction.option_c).strip(),
                str(prediction.option_d).strip(),
            ),
            correct_index=int(prediction.correct_index),
            explanation=str(prediction.explanation).strip(),
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise OnboardingGenerationError(str(error)) from error
    _validate_generated_question(question)
    return question


def _select_snippet(workspace_root: Path | str) -> SnippetSelection:
    root = Path(workspace_root)
    if not root.exists():
        raise RuntimeError(f"Workspace root does not exist: {root}")
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if _should_skip_path(root, path):
            continue
        if path.suffix not in _CODE_EXTENSIONS:
            continue
        selection = _extract_snippet_from_file(root, path)
        if selection is not None:
            return selection
    raise RuntimeError("No supported code snippet was found for onboarding")


def _should_skip_path(root: Path, path: Path) -> bool:
    relative_parts = path.relative_to(root).parts
    if any(part in _SKIP_DIRS for part in relative_parts):
        return True
    name = path.name.lower()
    if name.endswith("_test.go") or name.endswith("_test.py"):
        return True
    if (
        name.startswith("test_")
        or name.endswith(".spec.ts")
        or name.endswith(".test.ts")
    ):
        return True
    return False


def _extract_snippet_from_file(
    workspace_root: Path,
    path: Path,
) -> SnippetSelection | None:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if len(text.encode("utf-8")) > _MAX_FILE_BYTES:
        text = text[:_MAX_FILE_BYTES]
    lines = text.splitlines()
    if not lines:
        return None

    pattern = _definition_pattern_for_suffix(path.suffix)
    if pattern is None:
        return None
    matches = list(pattern.finditer(text))
    if not matches:
        return None

    line_starts = _line_start_offsets(lines)
    for index, match in enumerate(matches):
        start_offset = match.start()
        start_line = _offset_to_line_number(line_starts, start_offset)
        end_line = len(lines)
        if index + 1 < len(matches):
            next_offset = matches[index + 1].start()
            end_line = _offset_to_line_number(line_starts, next_offset) - 1
        end_line = min(end_line, start_line + _MAX_SNIPPET_LINES - 1)
        if end_line < start_line:
            continue
        snippet_lines = lines[start_line - 1 : end_line]
        if not any(line.strip() for line in snippet_lines):
            continue
        return SnippetSelection(
            path=str(path.relative_to(workspace_root)),
            start_line=start_line,
            end_line=end_line,
            text="\n".join(snippet_lines).strip(),
        )
    return None


def _definition_pattern_for_suffix(suffix: str) -> re.Pattern[str] | None:
    if suffix == ".py":
        return re.compile(r"(?m)^(?:async\s+def|def|class)\s+[A-Za-z_][A-Za-z0-9_]*")
    if suffix == ".go":
        return re.compile(r"(?m)^func\s+(?:\([^)]*\)\s*)?[A-Za-z_][A-Za-z0-9_]*")
    if suffix in {".ts", ".tsx", ".js", ".jsx"}:
        return re.compile(
            r"(?m)^(?:export\s+)?(?:async\s+)?function\s+[A-Za-z_][A-Za-z0-9_]*"
        )
    if suffix == ".rs":
        return re.compile(r"(?m)^fn\s+[A-Za-z_][A-Za-z0-9_]*")
    if suffix == ".java":
        return re.compile(
            r"(?m)^(?:public|private|protected)?\s*(?:static\s+)?(?:final\s+)?"
            r"[A-Za-z_<>\[\]]+\s+[A-Za-z_][A-Za-z0-9_]*\s*\("
        )
    return None


def _line_start_offsets(lines: list[str]) -> list[int]:
    offsets: list[int] = []
    current = 0
    for line in lines:
        offsets.append(current)
        current += len(line) + 1
    return offsets


def _offset_to_line_number(line_starts: list[int], offset: int) -> int:
    line_number = 1
    for index, start in enumerate(line_starts, start=1):
        if start > offset:
            break
        line_number = index
    return line_number


@contextmanager
def _connect(path: Path) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS onboarding_attempts (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            run_id TEXT,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('pending', 'completed', 'abandoned')
            ),
            question_type TEXT NOT NULL,
            snippet_path TEXT,
            snippet_start_line INTEGER,
            snippet_end_line INTEGER,
            snippet_text TEXT,
            prompt TEXT NOT NULL,
            question_payload_json TEXT NOT NULL,
            answer_payload_json TEXT,
            result_payload_json TEXT,
            explanation TEXT NOT NULL,
            completed_at TEXT,
            generator_version TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS onboarding_attempts_session_idx
        ON onboarding_attempts (session_id, created_at DESC);

        CREATE UNIQUE INDEX IF NOT EXISTS
        onboarding_attempts_one_pending_per_session_idx
        ON onboarding_attempts (session_id)
        WHERE status = 'pending';
        """
    )


def _fetch_pending_attempt(
    conn: sqlite3.Connection,
    session_id: str,
) -> sqlite3.Row | None:
    cursor = conn.execute(
        """
        SELECT *
        FROM onboarding_attempts
        WHERE session_id = ? AND status = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (session_id, STATUS_PENDING),
    )
    return cursor.fetchone()


def _abandon_pending_attempts(
    conn: sqlite3.Connection,
    *,
    session_id: str,
) -> None:
    conn.execute(
        """
        UPDATE onboarding_attempts
        SET
            status = ?,
            completed_at = ?
        WHERE session_id = ? AND status = ?
        """,
        (
            STATUS_ABANDONED,
            _utc_now(),
            session_id,
            STATUS_PENDING,
        ),
    )


def _fetch_attempt_row(
    conn: sqlite3.Connection,
    *,
    attempt_id: str,
    session_id: str,
) -> sqlite3.Row | None:
    cursor = conn.execute(
        """
        SELECT *
        FROM onboarding_attempts
        WHERE id = ? AND session_id = ?
        LIMIT 1
        """,
        (attempt_id, session_id),
    )
    return cursor.fetchone()


def _row_to_start_result(
    *,
    row: sqlite3.Row,
    session_id: str,
    created_session: bool,
) -> OnboardingStartResult:
    payload = _parse_question_payload(row["question_payload_json"])
    options = tuple(str(item).strip() for item in payload["options"])
    _validate_options(options)
    if not _row_is_start_compatible(row):
        raise OnboardingValidationError(
            "Persisted onboarding start question is missing snippet metadata"
        )
    return OnboardingStartResult(
        session_id=session_id,
        created_session=created_session,
        attempt_id=str(row["id"]),
        question_type=QUESTION_TYPE_MCQ,
        snippet=SnippetSelection(
            path=str(row["snippet_path"]),
            start_line=int(row["snippet_start_line"]),
            end_line=int(row["snippet_end_line"]),
            text=str(row["snippet_text"]),
        ),
        prompt=str(row["prompt"]),
        options=options,
        explanation=str(row["explanation"]),
        generator_version=str(row["generator_version"]),
    )


def _persist_generated_onboarding_mcq(
    *,
    db_path: Path,
    session_id: str,
    created_session: bool,
    question: GeneratedMcqQuestion,
) -> OnboardingStartResult:
    with _connect(db_path) as conn:
        _ensure_schema(conn)
        pending_row = _fetch_pending_attempt(conn, session_id=session_id)
        if pending_row is not None:
            if not _row_is_start_compatible(pending_row):
                raise OnboardingValidationError(
                    "Session has a pending live onboarding tool question "
                    "that cannot be reopened through onboarding.start"
                )
            return _row_to_start_result(
                row=pending_row,
                session_id=session_id,
                created_session=created_session,
            )

        attempt_id = uuid4().hex
        question_payload = _question_payload(question)
        conn.execute(
            """
            INSERT INTO onboarding_attempts (
                id,
                session_id,
                run_id,
                created_at,
                status,
                question_type,
                snippet_path,
                snippet_start_line,
                snippet_end_line,
                snippet_text,
                prompt,
                question_payload_json,
                answer_payload_json,
                result_payload_json,
                explanation,
                completed_at,
                generator_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt_id,
                session_id,
                None,
                _utc_now(),
                STATUS_PENDING,
                question.question_type,
                question.snippet.path,
                question.snippet.start_line,
                question.snippet.end_line,
                question.snippet.text,
                question.prompt,
                json.dumps(question_payload, sort_keys=True),
                None,
                None,
                question.explanation,
                None,
                question.generator_version,
            ),
        )
        row = _fetch_attempt_row(conn, attempt_id=attempt_id, session_id=session_id)
        if row is None:
            raise OnboardingError("onboarding attempt insert did not persist")
        return _row_to_start_result(
            row=row,
            session_id=session_id,
            created_session=created_session,
        )


def _row_is_start_compatible(row: sqlite3.Row) -> bool:
    return (
        row["snippet_path"] is not None
        and row["snippet_start_line"] is not None
        and row["snippet_end_line"] is not None
        and row["snippet_text"] is not None
    )


def _parse_question_payload(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise OnboardingValidationError(
            f"Invalid persisted onboarding question payload: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise OnboardingValidationError(
            "Persisted onboarding question payload is not an object"
        )
    options = payload.get("options")
    correct_index = payload.get("correct_index")
    packet_ids = payload.get("packet_ids", [])
    if not isinstance(options, list):
        raise OnboardingValidationError("Persisted onboarding options must be a list")
    if not isinstance(correct_index, int):
        raise OnboardingValidationError(
            "Persisted onboarding correct_index must be an integer"
        )
    if not isinstance(packet_ids, list):
        raise OnboardingValidationError(
            "Persisted onboarding packet_ids must be a list"
        )
    return {
        "packet_ids": packet_ids,
        "options": options,
        "correct_index": correct_index,
    }


def _question_payload(question: GeneratedMcqQuestion) -> dict[str, Any]:
    return {
        "options": list(question.options),
        "correct_index": question.correct_index,
    }


def _published_question_payload(question: PublishedMcqQuestion) -> dict[str, Any]:
    return {
        "packet_ids": list(question.packet_ids),
        "options": list(question.options),
        "correct_index": question.correct_index,
    }


def _validate_generated_question(question: GeneratedMcqQuestion) -> None:
    if question.question_type != QUESTION_TYPE_MCQ:
        raise OnboardingValidationError(
            f"Unsupported onboarding question type: {question.question_type}"
        )
    if question.snippet.path.strip() == "":
        raise OnboardingValidationError("Onboarding snippet path must not be blank")
    if (
        question.snippet.start_line <= 0
        or question.snippet.end_line < question.snippet.start_line
    ):
        raise OnboardingValidationError("Onboarding snippet line span is invalid")
    if question.snippet.text.strip() == "":
        raise OnboardingValidationError("Onboarding snippet text must not be blank")
    if question.prompt.strip() == "":
        raise OnboardingValidationError("Onboarding prompt must not be blank")
    _validate_options(question.options)
    _validate_selected_index(question.correct_index)
    if question.explanation.strip() == "":
        raise OnboardingValidationError("Onboarding explanation must not be blank")
    if question.generator_version.strip() == "":
        raise OnboardingValidationError(
            "Onboarding generator version must not be blank"
        )


def _validate_published_question(question: PublishedMcqQuestion) -> None:
    if question.question_type != QUESTION_TYPE_MCQ:
        raise OnboardingValidationError(
            f"Unsupported onboarding question type: {question.question_type}"
        )
    if not question.packet_ids:
        raise OnboardingValidationError(
            "Published onboarding MCQ must link at least one teaching packet"
        )
    normalized_packet_ids: list[str] = []
    for packet_id in question.packet_ids:
        if packet_id.strip() == "":
            raise OnboardingValidationError(
                "Published onboarding packet id must not be blank"
            )
        normalized_packet_ids.append(packet_id)
    if len(set(normalized_packet_ids)) != len(normalized_packet_ids):
        raise OnboardingValidationError(
            "Published onboarding packet ids must be unique"
        )
    if question.prompt.strip() == "":
        raise OnboardingValidationError("Onboarding prompt must not be blank")
    _validate_options(question.options)
    _validate_selected_index(question.correct_index)
    if question.explanation.strip() == "":
        raise OnboardingValidationError("Onboarding explanation must not be blank")
    if question.generator_version.strip() == "":
        raise OnboardingValidationError(
            "Onboarding generator version must not be blank"
        )


def _validate_options(options: tuple[str, ...]) -> None:
    if len(options) != 4:
        raise OnboardingValidationError(
            f"Onboarding MCQ must contain exactly 4 options, got {len(options)}"
        )
    normalized = []
    for option in options:
        if option.strip() == "":
            raise OnboardingValidationError("Onboarding option must not be blank")
        normalized.append(option.strip().lower())
    if len(set(normalized)) != len(normalized):
        raise OnboardingValidationError("Onboarding options must be unique")


def _validate_selected_index(index: int) -> None:
    if index not in {0, 1, 2, 3}:
        raise OnboardingValidationError(
            f"Onboarding selected index must be 0, 1, 2, or 3; got {index}"
        )


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


__all__ = [
    "GENERATOR_VERSION",
    "GeneratedMcqQuestion",
    "OnboardingAttemptNotFoundError",
    "PublishedMcqQuestion",
    "OnboardingError",
    "OnboardingStartResult",
    "OnboardingSubmitResult",
    "OnboardingValidationError",
    "QUESTION_TYPE_MCQ",
    "SnippetSelection",
    "generate_onboarding_mcq",
    "onboarding_db_path",
    "start_onboarding_mcq",
    "submit_onboarding_mcq",
]
