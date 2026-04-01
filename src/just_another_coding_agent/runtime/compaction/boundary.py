from __future__ import annotations

from just_another_coding_agent.contracts.session import LoadedSession, SessionRunRecord


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
    latest_compaction = loaded_session.latest_compaction
    if latest_compaction is None:
        return list(loaded_session.runs)

    summary_run_index = run_index_for_id(
        loaded_session,
        latest_compaction.summarized_through_run_id,
    )
    return list(loaded_session.runs[summary_run_index + 1 :])


__all__ = [
    "run_index_for_id",
    "runs_since_latest_compaction",
    "runs_since_latest_compaction_boundary",
]
