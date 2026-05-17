from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from just_another_coding_agent.contracts.session import (
    SessionMcpInventoryEntry,
    SessionMcpInventorySnapshot,
    SessionMcpInventoryTool,
)


@dataclass(frozen=True)
class McpToolInventoryItem:
    name: str
    server_id: str
    tool_name: str
    raw_tool_name: str
    title: str
    description: str
    deferred: bool

    def as_result(self) -> dict[str, str | bool]:
        return {
            "name": self.name,
            "server_id": self.server_id,
            "tool_name": self.tool_name,
            "raw_tool_name": self.raw_tool_name,
            "title": self.title,
            "description": self.description,
            "deferred": self.deferred,
        }


@dataclass
class McpToolInventory:
    items_by_name: dict[str, McpToolInventoryItem] = field(default_factory=dict)
    direct_tool_names: tuple[str, ...] = ()
    deferred_tool_names: tuple[str, ...] = ()
    activated_deferred_tool_names: set[str] = field(default_factory=set)

    @classmethod
    def from_manager(
        cls,
        manager: Any,
        *,
        direct_tool_names: tuple[str, ...],
        deferred_tool_names: tuple[str, ...],
    ) -> "McpToolInventory":
        deferred = set(deferred_tool_names)
        items: dict[str, McpToolInventoryItem] = {}
        for tool in manager.discover_tools():
            model_tool_name = tool.model_tool_name
            if (
                model_tool_name not in direct_tool_names
                and model_tool_name not in deferred
            ):
                continue
            items[model_tool_name] = McpToolInventoryItem(
                name=model_tool_name,
                server_id=tool.identity.server_id,
                tool_name=tool.identity.tool_name,
                raw_tool_name=(
                    tool.mounted_identity.raw_tool_name
                    if tool.mounted_identity is not None
                    else tool.identity.tool_name
                ),
                title=tool.title,
                description=tool.description,
                deferred=model_tool_name in deferred,
            )
        return cls(
            items_by_name=items,
            direct_tool_names=direct_tool_names,
            deferred_tool_names=deferred_tool_names,
        )

    def visible_deferred_tool_names(self) -> tuple[str, ...]:
        return tuple(
            tool_name
            for tool_name in self.deferred_tool_names
            if tool_name in self.activated_deferred_tool_names
        )

    def activate_tool(self, tool_name: str) -> None:
        if tool_name not in self.items_by_name:
            raise KeyError(tool_name)
        if tool_name in self.deferred_tool_names:
            self.activated_deferred_tool_names.add(tool_name)

    def to_session_snapshot(self) -> SessionMcpInventorySnapshot:
        return SessionMcpInventorySnapshot(
            tools=tuple(
                SessionMcpInventoryTool(
                    name=item.name,
                    server_id=item.server_id,
                    tool_name=item.tool_name,
                    raw_tool_name=item.raw_tool_name,
                    title=item.title,
                    description=item.description,
                    exposure="deferred" if item.deferred else "direct",
                    activated=item.name in self.activated_deferred_tool_names,
                )
                for item in self.items_by_name.values()
            )
        )

    def to_session_entry(self, *, run_id: str) -> SessionMcpInventoryEntry:
        snapshot = self.to_session_snapshot()
        return SessionMcpInventoryEntry(run_id=run_id, tools=snapshot.tools)

    def search(self, *, query: str, limit: int) -> tuple[McpToolInventoryItem, ...]:
        normalized_query = query.strip().lower()
        if not normalized_query:
            return ()

        scored: list[tuple[int, int, McpToolInventoryItem]] = []
        for index, item in enumerate(self.items_by_name.values()):
            haystacks = (
                item.name.lower(),
                item.server_id.lower(),
                item.tool_name.lower(),
                item.title.lower(),
                item.description.lower(),
            )
            if not any(normalized_query in haystack for haystack in haystacks):
                continue
            score = 0
            if normalized_query in item.name.lower():
                score += 4
            if normalized_query in item.title.lower():
                score += 3
            if normalized_query in item.description.lower():
                score += 1
            scored.append((-score, index, item))

        scored.sort()
        return tuple(item for _score, _index, item in scored[:limit])


__all__ = ["McpToolInventory", "McpToolInventoryItem"]
