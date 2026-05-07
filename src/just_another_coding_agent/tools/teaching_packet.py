from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated
from uuid import uuid4

from pydantic import Field
from pydantic_ai import RunContext, Tool

from just_another_coding_agent.contracts.run_events import (
    TeachingPacketActivityDetails,
)
from just_another_coding_agent.contracts.teaching import (
    TeachingSnippet,
    TeachingSnippetRef,
)
from just_another_coding_agent.tools._activity import make_tool_return
from just_another_coding_agent.tools._permissions import (
    approved_read_only_filesystem_policy,
)
from just_another_coding_agent.tools.deps import WorkspaceDeps
from just_another_coding_agent.tools.errors import ToolOperationalError
from just_another_coding_agent.tools.read_only_worker.protocol import (
    ReadCallResult,
    ReadWorkerRequest,
)

_DOCUMENTATION_SUFFIXES = frozenset({".md", ".mdx", ".rst", ".txt", ".adoc"})
_DOCUMENTATION_BASENAMES = frozenset(
    {
        "agents.md",
        "claude.md",
        "readme",
        "readme.md",
        "changelog.md",
        "contributing.md",
    }
)


def _is_documentation_path(path: str) -> bool:
    normalized = PurePosixPath(path.replace("\\", "/"))
    if any(part.lower() == "docs" for part in normalized.parts):
        return True
    if normalized.name.lower() in _DOCUMENTATION_BASENAMES:
        return True
    return normalized.suffix.lower() in _DOCUMENTATION_SUFFIXES


async def _read_exact_snippet(
    *,
    ctx: RunContext[WorkspaceDeps],
    snippet_ref: TeachingSnippetRef,
) -> TeachingSnippet:
    filesystem_policy = await approved_read_only_filesystem_policy(
        ctx=ctx,
        tool_path=snippet_ref.path,
        action="read teaching snippet",
    )
    line_count = snippet_ref.end_line - snippet_ref.start_line + 1
    response = await ctx.deps.read_only_worker.send(
        ReadWorkerRequest(
            request_id=uuid4().hex,
            workspace_root=str(ctx.deps.workspace_root),
            filesystem_policy=filesystem_policy,
            path=snippet_ref.path,
            offset=snippet_ref.start_line,
            limit=line_count,
            max_lines=line_count,
            max_bytes=256 * 1024,
        )
    )
    if not isinstance(response, ReadCallResult):
        raise RuntimeError(
            "Read-only worker returned the wrong response type for teaching "
            f"snippet: {type(response).__name__}"
        )
    if response.first_line_exceeds_max_bytes:
        raise ToolOperationalError(
            "Teaching snippet line exceeds the worker byte limit"
        )
    if response.truncated:
        raise ToolOperationalError(
            "Teaching snippet read truncated unexpectedly"
        )
    if response.start_line != snippet_ref.start_line:
        raise ToolOperationalError(
            "Teaching snippet start line did not match the requested span"
        )
    if response.end_line != snippet_ref.end_line:
        raise ToolOperationalError(
            "Teaching snippet end line did not match the requested span"
        )
    return TeachingSnippet(
        path=snippet_ref.path,
        start_line=snippet_ref.start_line,
        end_line=snippet_ref.end_line,
        text=response.window_text.rstrip("\n"),
    )


async def publish_teaching_packet(
    ctx: RunContext[WorkspaceDeps],
    title: Annotated[str, Field(min_length=1)],
    snippets: Annotated[list[TeachingSnippetRef], Field(min_length=1, max_length=5)],
) -> dict[str, object]:
    """Publish one curated code-teaching packet into the transcript.

    Args:
        title: Short title for the teaching packet.
        snippets: One to five snippet references with path, start_line, and end_line.
    """

    for snippet_ref in snippets:
        if _is_documentation_path(snippet_ref.path):
            raise ToolOperationalError(
                "publish_teaching_packet accepts code files only; "
                "documentation paths are not allowed"
            )
    materialized = [
        await _read_exact_snippet(ctx=ctx, snippet_ref=snippet_ref)
        for snippet_ref in snippets
    ]
    run_id = ctx.deps.session_scope.run_id
    if run_id is None:
        raise ToolOperationalError(
            "publish_teaching_packet requires an active root session and run"
        )
    packet_id = uuid4().hex
    ctx.deps.teaching_packet_registry.remember(
        packet_id=packet_id,
        run_id=run_id,
        title=title,
        snippets=tuple(materialized),
    )
    snippet_count = len(materialized)
    snippet_noun = "snippet" if snippet_count == 1 else "snippets"
    return make_tool_return(
        return_value={
            "packet_id": packet_id,
            "title": title,
            "snippet_count": snippet_count,
            "snippets": [
                snippet.model_dump(mode="json")
                for snippet in materialized
            ],
        },
        title=title,
        summary=f"showing {snippet_count} {snippet_noun}",
        details=TeachingPacketActivityDetails(snippets=materialized),
        display_label="Teach",
    )


PUBLISH_TEACHING_PACKET_TOOL = Tool(
    publish_teaching_packet,
    takes_ctx=True,
    name="publish_teaching_packet",
    description=(
        "Publish one curated teaching packet into the transcript by resolving "
        "1 to 5 code-file snippet references into canonical file text. "
        "Documentation paths are not allowed. Supply a short title and "
        "snippet references with path, start_line, and end_line."
    ),
    docstring_format="google",
    require_parameter_descriptions=True,
    strict=True,
    sequential=True,
)


__all__ = ["PUBLISH_TEACHING_PACKET_TOOL", "publish_teaching_packet"]
