"""Curated IOWAP tools over the node-cli runner.

v1 read set (T-003): relay_status, node_list, capabilities_list, docs.
The write set (task_submit/task_result/artifact_download) lands in T-004.
T-235 adds task_list to the read set — filtered task queries on the
node-cli task-list filters of the T-268 server backend.
Destructive surface is deliberately absent — see the project DECISIONS.md.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from iowap_client_mcp import tracking
from iowap_client_mcp.envelope import find_first_completed_stage_result
from iowap_client_mcp.nodecli import (
    NodeCliError,
    parse_relay_error,
    run_node_cli,
    run_node_cli_text,
)

__all__ = [
    "ALL_TOOLS",
    "READ_TOOLS",
    "WRITE_TOOLS",
    "artifact_download",
    "capabilities_list",
    "docs",
    "node_list",
    "register_read_tools",
    "register_write_tools",
    "relay_status",
    "task_list",
    "task_result",
    "task_submit",
]

READ_TOOLS = ("relay_status", "node_list", "capabilities_list", "docs", "task_list")
WRITE_TOOLS = ("task_submit", "task_result", "artifact_download")
ALL_TOOLS = READ_TOOLS + WRITE_TOOLS

# Node IDs are uppercase alphanumeric tokens (e.g. NODE23A4, AMKJA9AE — NOT
# strict base32: digits 0-9 all occur). A node NAME passed as --owner crashes
# the relay with a 500 — validate before spawning.
_NODE_ID_RE = re.compile(r"^[A-Z0-9]{3,16}$")
_CAPABILITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_WAIT_CAP = 90
_POLL_INTERVAL = 2.0


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


def _created_sort_key(row: dict[str, Any]) -> tuple[int, str]:
    """Newest-first key on ``created_at`` (ISO-8601); unparseable sinks last.

    Bucket 1 = parseable timestamp, compared as a UTC-normalized ISO string
    (lexicographic == chronological there); bucket 0 = missing/unparseable
    — under ``reverse=True`` those rows sink to the end without raising.
    """
    raw = row.get("created_at")
    value = raw if isinstance(raw, str) and raw.strip() else ""
    if value:
        parsed: datetime | None = None
        try:
            parsed = datetime.fromisoformat(value)  # py311+ parses 'Z' suffix
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return (1, parsed.astimezone(UTC).isoformat())
    return (0, value)


async def task_list(
    submitted_by: str | None = None,
    owner_node_id: str | None = None,
    status: str | None = None,
    created_since: str | None = None,
    created_until: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Query tasks with optional filters (AND-combined relay-side).

    Passthrough params: ``status`` is a comma-separated list (e.g.
    ``"failed,cancelled"``) the relay validates (unknown → 422);
    ``submitted_by`` is a node ID; ``created_since``/``created_until`` are
    ISO-8601 timestamps. A non-admin node listing tasks submitted by a
    FOREIGN node ID is rejected with HTTP 400 — both rejections surface as
    clean tool errors (no runner noise).

    ``owner_node_id`` must be a node ID from ``node_list`` — a node NAME
    breaks the relay, so it is rejected before spawning (same guard as
    ``task_submit``).

    Returns compact rows ``{"task_id", "task_name", "status", "created_at",
    "updated_at", "submitted_by", "owner_node_id"}`` — deliberately NO
    stage payloads (fetch those per task via ``task_result``). Rows are
    sorted newest-first and sliced to the first ``limit`` (default 50)
    CLIENT-side: the relay has no LIMIT parameter (T-268).
    """
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise TypeError(f"limit must be an integer, got {type(limit).__name__}")
    if limit < 1:
        raise ValueError("limit must be >= 1 — the client slices after sorting")
    if owner_node_id and not _NODE_ID_RE.match(owner_node_id):
        raise ValueError(
            f"owner_node_id {owner_node_id!r} is not a node ID — use node_list "
            "(IDs are uppercase alphanumeric tokens like NODE23A4; a node NAME breaks the relay)"
        )
    args = ["task", "list"]
    if submitted_by:
        args += ["--submitted-by", submitted_by]
    if owner_node_id:
        args += ["--owner", owner_node_id]
    if status:
        args += ["--status", status]
    if created_since:
        args += ["--since", created_since]
    if created_until:
        args += ["--until", created_until]
    try:
        payload = await run_node_cli(args, timeout=30.0)
    except NodeCliError as exc:
        parsed_error = parse_relay_error(str(exc))
        if parsed_error is None:  # transport/setup failure — propagate verbatim
            raise
        code, message = parsed_error
        if code == 400:
            message += (
                " — non-admin nodes may only list tasks they submitted "
                "themselves (pass the host's own submitted_by, or ask an admin)"
            )
        raise NodeCliError(f"relay rejected task list (HTTP {code}): {message}") from exc
    rows = payload.get("tasks") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        rows = []
    compact = [
        {
            "task_id": row.get("task_id"),
            "task_name": row.get("task_name"),
            "status": row.get("status"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "submitted_by": row.get("submitted_by"),
            "owner_node_id": row.get("owner_node_id"),
        }
        for row in rows
        if isinstance(row, dict)
    ]
    compact.sort(key=_created_sort_key, reverse=True)
    limited = compact[:limit]
    return {"count": len(limited), "tasks": limited}


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
    _t(
        name="task_list",
        description=(
            "Query tasks with AND-combined filters (all optional): "
            "submitted_by, owner_node_id (node ID, not name), status as csv "
            "(relay-validated, e.g. failed,cancelled), created_since/"
            "created_until (ISO-8601). Returns compact task rows (no stages "
            "— fetch those per task via task_result), sorted newest-first "
            "client-side, first limit (default 50). Relay 4xx rejections "
            "(400 non-admin foreign submitted_by, 422 unknown status) come "
            "back as clear errors."
        ),
    )(task_list)


async def _track_task(
    task_id: str | None,
    *,
    capability: str | None,
    name: str | None,
    owner: str | None,
    delegated: bool,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Best-effort merge into the shared track store (T-007: in-process).

    Same flock/store semantics as the session bridge and the desktop
    plugin backend — no external binary required. Tracking must never
    break a submit; a missing store dir degrades to ``tracked: false``.
    """
    if not task_id:
        return {"tracked": False, "reason": "submit returned no task_id"}
    return tracking.merge(  # type: ignore[return-value]
        task_id,
        capability=capability or "unknown",
        name=name,
        owner=owner,
        delegated=delegated,
        session_id=session_id,
    )


def _stage_arg(capability: str, payload: dict[str, Any]) -> str:
    if not _CAPABILITY_RE.match(capability):
        raise ValueError(
            f"invalid capability name {capability!r} (pattern: "
            "letters/digits with . _ - separators)"
        )
    return f"{capability}:{json.dumps(payload, separators=(',', ':'))}"


def _allowed_capabilities() -> set[str] | None:
    """Operator allowlist from ``IOWAP_ALLOW_CAPS`` (csv), or None = open.

    Read per call so MCP-host configuration (``--env IOWAP_ALLOW_CAPS=…``)
    applies live; unset/empty means no restriction (local-first trust model).
    """
    raw = os.environ.get("IOWAP_ALLOW_CAPS", "")
    allowed = {entry.strip() for entry in raw.split(",") if entry.strip()}
    return allowed or None


async def task_submit(
    capability: str,
    payload: dict[str, Any],
    owner_node_id: str | None = None,
    wait_seconds: int = 0,
    name: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Submit a task against a capability; optionally wait for completion.

    ``payload`` must match the capability's ``input_schema`` (see
    ``capabilities_list``). ``owner_node_id`` directs the task to one node —
    it must be a node ID from ``node_list``/``capabilities_list`` (a node
    NAME would crash the relay with a 500). Owner-directed tasks are
    tracked as ``delegated``; since T-227 the relay lets the admin, the
    owner node, and the sender (submitted_by) read the task — this node
    can fetch its own owner-directed results.

    ``session_id`` records the submitting agent session id in the track
    store — the desktop plugin's task watcher uses it as the composer
    target and posts stage/final updates back into exactly that session
    (fail-closed; falls back to a notification when the session is not
    mounted). Omit it to track without a session target.

    ``wait_seconds`` (capped at 90) polls for a short while; long-running
    tasks should be polled via ``task_result`` instead. ``name`` labels
    the row on the desktop Tasks page (optional).
    """
    if not isinstance(payload, dict):
        raise TypeError("payload must be a JSON object (dict)")
    if owner_node_id and not _NODE_ID_RE.match(owner_node_id):
        raise ValueError(
            f"owner_node_id {owner_node_id!r} is not a node ID — use node_list "
            "(IDs are uppercase alphanumeric tokens like NODE23A4; a node NAME breaks the relay)"
        )
    allowed = _allowed_capabilities()
    if allowed and capability not in allowed:
        raise ValueError(
            f"capability {capability!r} not in IOWAP_ALLOW_CAPS allowlist — "
            f"operator-set allowed capabilities: {sorted(allowed)}"
        )
    wait = max(0, min(int(wait_seconds), _WAIT_CAP))

    args = ["task", "submit", "--stage", _stage_arg(capability, payload)]
    if name:
        args += ["--name", name[:120]]
    if owner_node_id:
        args += ["--owner", owner_node_id]
    submit_payload = await run_node_cli(args, timeout=30.0)
    task_id = submit_payload.get("task_id")

    result: dict[str, Any] = {
        "task_id": task_id,
        "status": submit_payload.get("status"),
        "owner_node_id": owner_node_id,
        "name": name,
    }
    result["tracking"] = await _track_task(
        task_id,
        capability=capability,
        name=name,
        owner=owner_node_id,
        delegated=bool(owner_node_id),
        session_id=(session_id or "").strip()[:64] or None,
    )

    if not wait or not task_id:
        return result

    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)
        status = await task_result(task_id)
        if status.get("status") in ("completed", "failed", "timed_out", "cancelled"):
            return {
                **result,
                "status": status.get("status"),
                "result": status.get("result"),
                "waited_seconds": round(wait - (deadline - time.monotonic()), 1),
            }
    return {
        **result,
        "status": "pending",
        "result": None,
        "waited_seconds": wait,
        "hint": "still running — poll task_result(task_id)",
    }


async def task_result(task_id: str) -> dict[str, Any]:
    """Fetch a task's state and result (first completed stage, envelope-unwrapped).

    Read scope since T-227: admin, the owner node, or the sender
    (``submitted_by``) — this node sees its own owner-directed results.
    If the CLI still reports ``not found`` (different identity, pruned
    task), that is surfaced as ``status: "not_found"`` with a hint, not
    as a tool failure.
    """
    try:
        payload = await run_node_cli(["task", "result", task_id], timeout=30.0)
    except NodeCliError as exc:
        if "not found" in str(exc):
            return {
                "task_id": task_id,
                "status": "not_found",
                "hint": (
                    "task not readable from this node's identity — since T-227 "
                    "admin, the owner node, and the sender (submitted_by) "
                    "can read it; submit from the owning/sending node's token"
                ),
            }
        raise
    task = payload.get("task") or {}
    stages = payload.get("stages") or []
    return {
        "task_id": task_id,
        "status": task.get("status"),
        "claimed_by": task.get("claimed_by"),
        "owner_node_id": task.get("owner_node_id"),
        "result": find_first_completed_stage_result(payload),
        "stages_count": len(stages),
        "stages": stages,
    }


async def artifact_download(
    artifact_id: str,
    output_path: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Download a task artifact to an explicit local path and verify it.

    ``output_path`` must be absolute (or is resolved against the home
    directory). This is a FILE WRITE: an existing file is refused unless
    ``overwrite: true`` is passed (the caller states the destruction
    explicitly). The node-cli writes the bytes; this tool checks the file
    afterwards so the caller does not trust the CLI message alone.
    """
    target = Path(output_path).expanduser()
    if not target.is_absolute():
        target = Path.home() / target
    if target.is_file() and not overwrite:
        raise FileExistsError(
            f"{target} exists — pass overwrite=true to replace the file explicitly"
        )
    payload = await run_node_cli(
        ["artifact", "download", artifact_id, "--output", str(target)],
        timeout=300.0,
    )
    exists = target.is_file()
    return {
        "artifact_id": artifact_id,
        "path": str(target),
        "exists": exists,
        "bytes": target.stat().st_size if exists else None,
        "cli_output": payload,
    }


def register_write_tools(server: Any) -> None:
    """Attach the v1 write toolset to a MCPServer instance."""
    _t: Callable[..., Any] = server.tool
    _t(
        name="task_submit",
        description=(
            "Submit an IOWAP task: payload must match the capability's "
            "input_schema (capabilities_list); owner_node_id must be a node "
            "ID (not a name); name labels the row on the desktop Tasks page. "
            "wait_seconds<=90 polls briefly; long tasks: poll task_result "
            "afterwards. Auto-merges into the task tracking store "
            "(in-process). Operators can restrict submitable capabilities "
            "via IOWAP_ALLOW_CAPS (csv env; unset = all)."
        ),
    )(task_submit)
    _t(
        name="task_result",
        description=(
            "Fetch task state + result (first completed stage, envelope "
            "unwrapped). Read scope: admin, owner node, or the sender "
            "(submitted_by, T-227) — this node can fetch its own "
            "owner-directed results."
        ),
    )(task_result)
    _t(
        name="artifact_download",
        description=(
            "Download a task artifact to an absolute local path; refuses to "
            "overwrite an existing file unless overwrite=true. Verifies the "
            "file exists afterwards and reports its size."
        ),
    )(artifact_download)