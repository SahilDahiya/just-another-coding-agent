from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic_evals.otel.span_tree import SpanNode, SpanTree

from just_another_coding_agent.runtime.tracing import (
    RUN_SPAN_NAME,
    TOOL_NAME_ATTRIBUTE,
    TOOL_SPAN_NAME,
)
from just_another_coding_agent.watchdog.evaluators import (
    BashHeavyWithoutEditsEvaluator,
    has_long_tool_span,
)


@dataclass
class _FakeEvaluatorContext:
    span_tree: SpanTree


def _node(
    *,
    name: str,
    span_id: int,
    parent_span_id: int | None,
    duration_seconds: float,
    attributes: dict[str, object] | None = None,
) -> SpanNode:
    start = datetime(2026, 3, 28, 8, 0, 0, tzinfo=UTC)
    end = start + timedelta(seconds=duration_seconds)
    return SpanNode(
        name=name,
        trace_id=1,
        span_id=span_id,
        parent_span_id=parent_span_id,
        start_timestamp=start,
        end_timestamp=end,
        attributes=dict(attributes or {}),
    )


def _span_tree(*children: SpanNode) -> SpanTree:
    root = _node(
        name=RUN_SPAN_NAME,
        span_id=1,
        parent_span_id=None,
        duration_seconds=30,
    )
    nodes = {root.node_key: root}
    for child in children:
        root.add_child(child)
        nodes[child.node_key] = child
    return SpanTree(roots=[root], nodes_by_id=nodes)


def test_has_long_tool_span_matches_long_bash_tool_span() -> None:
    tree = _span_tree(
        _node(
            name=TOOL_SPAN_NAME,
            span_id=2,
            parent_span_id=1,
            duration_seconds=12,
            attributes={TOOL_NAME_ATTRIBUTE: "bash"},
        )
    )

    evaluator = has_long_tool_span(tool_name="bash", min_duration_seconds=10)

    assert evaluator.evaluate(_FakeEvaluatorContext(span_tree=tree)) is True


def test_bash_heavy_without_edits_evaluator_detects_probe_loop() -> None:
    tree = _span_tree(
        _node(
            name=TOOL_SPAN_NAME,
            span_id=2,
            parent_span_id=1,
            duration_seconds=1,
            attributes={TOOL_NAME_ATTRIBUTE: "bash"},
        ),
        _node(
            name=TOOL_SPAN_NAME,
            span_id=3,
            parent_span_id=1,
            duration_seconds=1,
            attributes={TOOL_NAME_ATTRIBUTE: "bash"},
        ),
        _node(
            name=TOOL_SPAN_NAME,
            span_id=4,
            parent_span_id=1,
            duration_seconds=1,
            attributes={TOOL_NAME_ATTRIBUTE: "bash"},
        ),
    )

    evaluator = BashHeavyWithoutEditsEvaluator(min_bash_spans=3)

    assert evaluator.evaluate(_FakeEvaluatorContext(span_tree=tree)) is True
