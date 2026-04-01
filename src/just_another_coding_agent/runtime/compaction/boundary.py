from __future__ import annotations

from dataclasses import dataclass

from just_another_coding_agent.contracts.session import (
    LoadedSession,
    SessionCompactionSummary,
    SessionRunRecord,
)


@dataclass(frozen=True)
class PostCompactionContinuityBoundary:
    summary: SessionCompactionSummary | None
    retained_runs: list[SessionRunRecord]


def run_index_for_id(loaded_session: LoadedSession, run_id: str) -> int:
    for index, run in enumerate(loaded_session.runs):
        if run.run_id == run_id:
            return index

    raise RuntimeError(f"Compaction references unknown run_id: {run_id}")


def runs_since_latest_compaction(loaded_session: LoadedSession) -> int:
    latest_compaction = loaded_session.latest_compaction
    if latest_compaction is None:
        return len(loaded_session.runs)

    summary_run_index = run_index_for_id(
        loaded_session,
        latest_compaction.summarized_through_run_id,
    )
    return len(loaded_session.runs[summary_run_index + 1 :])


def runs_since_latest_compaction_boundary(
    loaded_session: LoadedSession,
) -> list[SessionRunRecord]:
    return build_post_compaction_continuity_boundary(loaded_session).retained_runs


def build_post_compaction_continuity_boundary(
    loaded_session: LoadedSession,
) -> PostCompactionContinuityBoundary:
    latest_compaction = loaded_session.latest_compaction
    if latest_compaction is None:
        return PostCompactionContinuityBoundary(
            summary=None,
            retained_runs=list(loaded_session.runs),
        )

    if latest_compaction.first_kept_run_id is not None:
        retained_start_index = run_index_for_id(
            loaded_session,
            latest_compaction.first_kept_run_id,
        )
    else:
        retained_start_index = (
            run_index_for_id(
                loaded_session,
                latest_compaction.summarized_through_run_id,
            )
            + 1
        )
    return PostCompactionContinuityBoundary(
        summary=latest_compaction.summary,
        retained_runs=list(loaded_session.runs[retained_start_index:]),
    )


__all__ = [
    "PostCompactionContinuityBoundary",
    "build_post_compaction_continuity_boundary",
    "run_index_for_id",
    "runs_since_latest_compaction",
    "runs_since_latest_compaction_boundary",
]
