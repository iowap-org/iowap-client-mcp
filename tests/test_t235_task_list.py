"""Unit tests for the task_list read tool (T-235) — logic, not live relay.

Contract (plan 2026-11-12_t235-task-list-tool.md):
- ``node-cli task list --json`` passthrough mapping: --submitted-by/--owner/
  --status/--since/--until, only set flags, csv verbatim.
- Compact rows (task_id/task_name/status/created_at/updated_at/submitted_by/
  owner_node_id), no stage payloads.
- Client-side newest-first sort, then limit slice (relay has no LIMIT).
- Relay 4xx rejections (400 foreign submitted_by, 422 unknown status) land as
  understandable tool errors, not raw runner noise.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from iowap_client_mcp import tools
from iowap_client_mcp.nodecli import NodeCliError
from iowap_client_mcp.tools import task_list

RUN_PATCH = "iowap_client_mcp.tools.run_node_cli"

ROW_KEYS = [
    "task_id",
    "task_name",
    "status",
    "created_at",
    "updated_at",
    "submitted_by",
    "owner_node_id",
]


def _ok(payload: Any):
    async def _fake(args, **kwargs):
        _fake.calls.append(list(args))
        return payload

    _fake.calls = []  # type: ignore[attr-defined]
    return _fake


def _raising(message: str):
    def _fake(args, **kwargs):
        raise NodeCliError(message)

    return _fake


# ---------------------------------------------------------------------------
# Passthrough arg serialization
# ---------------------------------------------------------------------------


def test_task_list_no_flags_spawns_bare_task_list(monkeypatch) -> None:
    """Default call: only the subcommand, no filter flags, no limit flag."""
    row = {
        "task_id": "task_Zrl4_iOgqIfvY8YB",
        "task_name": "simple-flow.run",
        "status": "completed",
        "priority": 10,
        "owner_node_id": "MWCJKYHE",
        "submitted_by": "E4W3CBWQ",
        "timeout_seconds": 300,
        "created_at": "2026-10-21T15:49:14.612055+00:00",
        "updated_at": "2026-10-21T15:52:58.690661+00:00",
        "completed_at": "2026-10-21T15:52:58.690661+00:00",
    }
    fake = _ok({"tasks": [row]})
    monkeypatch.setattr(tools, "run_node_cli", fake)
    result = asyncio.run(task_list())
    assert fake.calls == [["task", "list"]]
    assert result["count"] == 1
    assert result["tasks"][0] == {
        "task_id": "task_Zrl4_iOgqIfvY8YB",
        "task_name": "simple-flow.run",
        "status": "completed",
        "created_at": "2026-10-21T15:49:14.612055+00:00",
        "updated_at": "2026-10-21T15:52:58.690661+00:00",
        "submitted_by": "E4W3CBWQ",
        "owner_node_id": "MWCJKYHE",
    }


def test_task_list_serializes_all_flags_with_exact_mapping(monkeypatch) -> None:
    """Param mapping --since→created_since/--until→created_until, csv verbatim."""
    fake = _ok({"tasks": []})
    monkeypatch.setattr(tools, "run_node_cli", fake)
    asyncio.run(
        task_list(
            submitted_by="E4W3CBWQ",
            owner_node_id="NODE23A4",
            status="failed,cancelled",  # csv must pass through unvalidated
            created_since="2026-11-01T00:00:00+00:00",
            created_until="2026-11-12T23:59:59Z",
        )
    )
    assert fake.calls == [
        [
            "task",
            "list",
            "--submitted-by",
            "E4W3CBWQ",
            "--owner",
            "NODE23A4",
            "--status",
            "failed,cancelled",
            "--since",
            "2026-11-01T00:00:00+00:00",
            "--until",
            "2026-11-12T23:59:59Z",
        ]
    ]


def test_task_list_partial_flags_only_set(monkeypatch) -> None:
    """Only set flags serialize; empty strings are not flags."""
    fake = _ok({"tasks": []})
    monkeypatch.setattr(tools, "run_node_cli", fake)
    asyncio.run(task_list(status="pending"))
    assert fake.calls == [["task", "list", "--status", "pending"]]
    fake = _ok({"tasks": []})
    monkeypatch.setattr(tools, "run_node_cli", fake)
    asyncio.run(
        task_list(submitted_by="", owner_node_id="", status="", created_since="")
    )
    assert fake.calls == [["task", "list"]]


# ---------------------------------------------------------------------------
# Compact rows + client-side newest-first limit
# ---------------------------------------------------------------------------


def _rows() -> list[dict]:
    """Three rows, relay order (server may return oldest-first), extra keys."""
    return [
        {
            "task_id": "task_OLD",
            "task_name": "a.old",
            "status": "completed",
            "priority": 10,
            "timeout_seconds": 300,
            "completed_at": "2026-10-21T16:00:00+00:00",
            "stages": [{"status": "completed"}],
            "created_at": "2026-10-21T15:49:14.612055+00:00",
            "updated_at": "2026-10-21T16:00:00+00:00",
            "submitted_by": "E4W3CBWQ",
            "owner_node_id": "MWCJKYHE",
        },
        {
            "task_id": "task_NEW",
            "task_name": "c.newest",
            "status": "failed",
            "created_at": "2026-11-02T22:57:49.728726+00:00",
            "updated_at": "2026-11-02T22:57:53.208101+00:00",
            "submitted_by": "E4W3CBWQ",
            "owner_node_id": "RYBFNMET",
        },
        {
            "task_id": "task_MID",
            "task_name": "b.middle",
            "status": "cancelled",
            "priority": 5,
            "created_at": "2026-11-01T10:00:00+00:00",
            "updated_at": "2026-11-01T10:05:00+00:00",
            "submitted_by": "E4W3CBWQ",
            "owner_node_id": None,
        },
    ]


def test_task_list_strips_to_compact_and_sorts_newest_first(monkeypatch) -> None:
    """Rows carry ONLY the 7 contract keys, newest-first regardless of relay order."""
    fake = _ok({"tasks": _rows()})
    monkeypatch.setattr(tools, "run_node_cli", fake)
    result = asyncio.run(task_list())
    assert result["count"] == 3
    assert [t["task_id"] for t in result["tasks"]] == [
        "task_NEW",
        "task_MID",
        "task_OLD",
    ]
    for row in result["tasks"]:
        assert list(row) == ROW_KEYS  # contract order, exactly these 7 keys
    assert "stages" not in result["tasks"][1]
    assert "priority" not in result["tasks"][1]


def test_task_list_limit_applies_after_newest_first_sort(monkeypatch) -> None:
    """limit slices AFTER the client-side sort — the two newest rows survive."""
    fake = _ok({"tasks": _rows()})
    monkeypatch.setattr(tools, "run_node_cli", fake)
    result = asyncio.run(task_list(submitted_by="E4W3CBWQ", limit=2))
    assert result["count"] == 2
    assert [t["task_id"] for t in result["tasks"]] == ["task_NEW", "task_MID"]


def test_task_list_unparseable_created_at_sinks_to_end(monkeypatch) -> None:
    """Defensive: rows without parseable created_at never break the sort."""
    rows = [
        {"task_id": "task_NO_TS", "created_at": None},
        {"task_id": "task_OK", "created_at": "2026-11-02T10:00:00+00:00"},
        {"task_id": "task_GARBAGE", "created_at": "not-a-timestamp"},
    ]
    fake = _ok({"tasks": rows})
    monkeypatch.setattr(tools, "run_node_cli", fake)
    result = asyncio.run(task_list())
    assert next(t["task_id"] for t in result["tasks"]) == "task_OK"
    assert {t["task_id"] for t in result["tasks"][1:]} == {
        "task_NO_TS",
        "task_GARBAGE",
    }


# ---------------------------------------------------------------------------
# Input guards
# ---------------------------------------------------------------------------


def test_task_list_rejects_node_name_as_owner(monkeypatch) -> None:
    """Same guard as task_submit: a node NAME breaks the relay — no spawn."""
    def _boom(args, **kwargs):
        raise AssertionError("no node-cli call may happen on validation failure")

    monkeypatch.setattr(tools, "run_node_cli", _boom)
    with pytest.raises(ValueError, match="node ID"):
        asyncio.run(task_list(owner_node_id="felix-m4-v4"))


def test_task_list_rejects_bad_limit(monkeypatch) -> None:
    """Non-int limit → TypeError; limit < 1 → ValueError; no spawn either way."""
    def _boom(args, **kwargs):
        raise AssertionError("no node-cli call may happen on validation failure")

    monkeypatch.setattr(tools, "run_node_cli", _boom)
    with pytest.raises(TypeError, match="limit"):
        asyncio.run(task_list(limit="5"))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="limit"):
        asyncio.run(task_list(limit=True))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="limit"):
        asyncio.run(task_list(limit=0))


# ---------------------------------------------------------------------------
# Relay 4xx → understandable tool errors (the _parse_relay_error pendant)
# ---------------------------------------------------------------------------


def _cli_4xx_error(stderr_line: str) -> str:
    """The exact runner message shape for a relay 4xx (stderr tail attached)."""
    return f"node-cli produced no JSON output (exit 1): {stderr_line}"


def test_task_list_422_unknown_status_is_understandable(monkeypatch) -> None:
    """422: clean tool error with the relay's message — no runner noise."""
    monkeypatch.setattr(
        tools,
        "run_node_cli",
        _raising(
            _cli_4xx_error(
                "task list failed: HTTP 422: Unknown task status: fail. "
                "Valid statuses: pending, running, completed, failed, cancelled"
            )
        ),
    )
    with pytest.raises(NodeCliError) as excinfo:
        asyncio.run(task_list(status="fail"))
    message = str(excinfo.value)
    assert "HTTP 422" in message
    assert "Unknown task status: fail" in message
    assert "Valid statuses: pending, running, completed, failed, cancelled" in message
    assert "no JSON output" not in message  # runner noise stripped


def test_task_list_400_foreign_submitted_by_is_understandable(monkeypatch) -> None:
    """400 non-admin foreign ID: clean tool error + self-help hint."""
    monkeypatch.setattr(
        tools,
        "run_node_cli",
        _raising(
            _cli_4xx_error(
                "task list failed: HTTP 400: Cannot list tasks submitted by "
                "another node"
            )
        ),
    )
    with pytest.raises(NodeCliError) as excinfo:
        asyncio.run(task_list(submitted_by="OTHERID1"))
    message = str(excinfo.value)
    assert "HTTP 400" in message
    assert "Cannot list tasks submitted by another node" in message
    assert "no JSON output" not in message
    assert "submitted_by" in message


def test_task_list_non_http_cli_errors_propagate_untouched(monkeypatch) -> None:
    """Transport failures keep the original NodeCliError verbatim."""
    original = "node-cli not found on this host (tried pin → PATH → candidates)"
    monkeypatch.setattr(tools, "run_node_cli", _raising(original))
    with pytest.raises(NodeCliError) as excinfo:
        asyncio.run(task_list())
    assert str(excinfo.value) == original
