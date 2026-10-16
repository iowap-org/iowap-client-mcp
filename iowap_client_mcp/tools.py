"""Curated IOWAP tools over the node-cli runner.

v1 read set (T-003): relay_status, node_list, capabilities_list, docs.
The write set (task_submit/task_result/artifact_download) lands in T-004.
Destructive surface is deliberately absent — see the project DECISIONS.md.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from iowap_client_mcp.nodecli import run_node_cli, run_node_cli_text

__all__ = [
    "READ_TOOLS",
    "capabilities_list",
    "docs",
    "node_list",
    "register_read_tools",
    "relay_status",
]

READ_TOOLS = ("relay_status", "node_list", "capabilities_list", "docs")


async def relay_status() -> dict[str, Any]:
    """Relay health: version, mode, node counts, queue depth, task counters.

    Unauthenticated probe; works while the local node daemon is down.
    An unreachable relay returns ``{"ok": false, "error": ...}`` (the CLI's
    exit-1-with-JSON semantics), which is data, not a tool failure.
    """
    return await run_node_cli(["server", "health"], timeout=20.0)


async def node_list() -> dict[str, Any]:
    """Fleet overview: node IDs, names, status, capabilities, load."""
    nodes = await run_node_cli(["node", "list"], timeout=20.0)
    return {"count": len(nodes), "nodes": nodes}


def _invert_capability_nodes(nodes: list[Any]) -> dict[str, list[str]]:
    """Map capability name → list of node IDs offering it (any node, any role).

    This mirrors the node-capability inventory recipe of the operations
    skill — here it lives in code, not agent knowledge.
    """
    owners: dict[str, list[str]] = {}
    for node in nodes or []:
        node_id = node.get("node_id")
        if not node_id:
            continue
        for capability in node.get("capabilities") or []:
            name = capability.get("name")
            if name:
                owners.setdefault(name, []).append(node_id)
    return owners


async def capabilities_list() -> dict[str, Any]:
    """What the relay can do: input schemas **and** owning node IDs.

    Merges ``capabilities server`` (which already carries ``input_schema``)
    with ``node list`` (which carries per-node capabilities) — one call
    answers "what exists, who executes it, how do I call it".
    """
    caps = await run_node_cli(["capabilities", "server"], timeout=30.0)
    nodes = await run_node_cli(["node", "list"], timeout=20.0)
    owners = _invert_capability_nodes(nodes)
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cap in caps or []:
        name = cap.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        merged.append(
            {
                "name": name,
                "type": cap.get("type"),
                "description": cap.get("description"),
                "version": cap.get("version"),
                "available": cap.get("available"),
                "input_schema": cap.get("input_schema"),
                "node_ids": owners.get(name, []),
            }
        )
    return {"count": len(merged), "capabilities": merged}


async def docs(page: str | None = None) -> dict[str, Any]:
    """Live relay documentation.

    Without ``page``: the available doc pages (name, title, url, available).
    With ``page``: the page content (markdown — node-cli returns text, not
    JSON, for named pages).
    """
    if page is None:
        return {"pages": await run_node_cli(["docs"], timeout=20.0)}
    content = await run_node_cli_text(["docs", page], timeout=30.0)
    return {"page": page, "content": content.rstrip("\n")}


def register_read_tools(server: Any) -> None:
    """Attach the v1 read toolset to a MCPServer instance."""
    _t: Callable[..., Any] = server.tool
    _t(
        name="relay_status",
        description=(
            "IOWAP relay health: version, mode, database/scheduler state, "
            "nodes online/total, queue depth, task/stage counters. Unauth "
            "probe — data even when the relay is unreachable ({ok:false, ...})."
        ),
    )(relay_status)
    _t(
        name="node_list",
        description=(
            "List all IOWAP nodes: node_id (needed for --owner targets), "
            "name, status online/offline, capabilities, load, queue depth."
        ),
    )(node_list)
    _t(
        name="capabilities_list",
        description=(
            "List relay capabilities with full input_schema AND the node_ids "
            "that own each capability. Submit tasks against a capability from "
            "here: payload must match input_schema; pass one of node_ids as "
            "owner_node_id to direct the task."
        ),
    )(capabilities_list)
    _t(
        name="docs",
        description=(
            "Read the live relay documentation served by the relay. Without "
            "page: available pages. With page: that page's markdown content."
        ),
    )(docs)