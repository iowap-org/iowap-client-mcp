"""Unit tests for the v1 read tools (T-003) — logic, not live relay."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from iowap_client_mcp import tools
from iowap_client_mcp.tools import (
    _invert_capability_nodes,
    capabilities_list,
    docs,
    node_list,
    relay_status,
)

RUN_PATCH = "iowap_client_mcp.tools.run_node_cli"
TEXT_PATCH = "iowap_client_mcp.tools.run_node_cli_text"


def _ok(payload: Any):
    async def _fake(args, **kwargs):
        _fake.calls.append(list(args))
        return payload

    _fake.calls = []  # type: ignore[attr-defined]
    return _fake


def test_relay_status_passes_health_payload(monkeypatch) -> None:
    fake = _ok({"ok": True, "version": "2.0.0", "nodes_online": 9.0})
    monkeypatch.setattr(tools, "run_node_cli", fake)
    result = asyncio.run(relay_status())
    assert result["ok"] is True
    assert fake.calls == [["server", "health"]]


def test_node_list_wraps_with_count(monkeypatch) -> None:
    nodes = [{"node_id": "NODE23A4", "status": "online"}]
    fake = _ok(nodes)
    monkeypatch.setattr(tools, "run_node_cli", fake)
    result = asyncio.run(node_list())
    assert result == {"count": 1, "nodes": nodes}
    assert fake.calls == [["node", "list"]]


def test_invert_capability_nodes_groupes_providers() -> None:
    nodes = [
        {
            "node_id": "NODE23A4",
            "capabilities": [{"name": "chat.ai"}, {"name": "tts.ai"}],
        },
        {"node_id": "NODE23B5", "capabilities": [{"name": "chat.ai"}]},
        {"node_id": "SERVICE1", "capabilities": []},
    ]
    owners = _invert_capability_nodes(nodes)
    assert owners["chat.ai"] == ["NODE23A4", "NODE23B5"]
    assert owners["tts.ai"] == ["NODE23A4"]
    assert len(owners) == 2


def test_capabilities_list_merges_schema_and_owners(monkeypatch) -> None:
    caps = [
        {
            "name": "chat.ai",
            "type": "ai",
            "description": "chat",
            "version": "1.0.0",
            "available": True,
            "input_schema": {"fields": {}},
        },
        {"name": "ghost.cap", "available": False},
    ]
    nodes = [
        {"node_id": "NODE23A4", "capabilities": [{"name": "chat.ai"}]},
    ]

    def _fake(args, **kwargs):
        return caps if args == ["capabilities", "server"] else nodes

    async def _arun(args, **kwargs):
        return _fake(args)

    monkeypatch.setattr(tools, "run_node_cli", _arun)
    result = asyncio.run(capabilities_list())

    by_name = {c["name"]: c for c in result["capabilities"]}
    assert result["count"] == 2
    assert by_name["chat.ai"]["node_ids"] == ["NODE23A4"]
    assert by_name["chat.ai"]["input_schema"] == {"fields": {}}
    assert by_name["ghost.cap"]["node_ids"] == []


def test_docs_without_page_returns_page_list(monkeypatch) -> None:
    fake = _ok([{"name": "readme", "available": True}])
    monkeypatch.setattr(tools, "run_node_cli", fake)
    result = asyncio.run(docs())
    assert result == {"pages": [{"name": "readme", "available": True}]}
    assert fake.calls == [["docs"]]


def test_docs_named_page_uses_text_mode(monkeypatch) -> None:
    _calls: list[list[str]] = []

    async def _text_fake(args, **kwargs):
        _calls.append(list(args))
        return "# Relay\n\nBody text.\n"

    monkeypatch.setattr(tools, "run_node_cli_text", _text_fake)
    result = asyncio.run(docs("concepts"))
    assert result["page"] == "concepts"
    assert result["content"].endswith("Body text.")  # trailing newline stripped
    assert _calls == [["docs", "concepts"]]


def test_read_tools_are_json_safe_serializable(monkeypatch) -> None:
    """The merged structure must survive a JSON round-trip (MCP payloads)."""
    async def _arun(args, **kwargs):
        caps = [
            {"name": "chat.ai", "input_schema": {"fields": {"prompt": {}}}},
        ]
        nodes = [{"node_id": "NODE23A4", "capabilities": [{"name": "chat.ai"}]}]
        return caps if args == ["capabilities", "server"] else nodes

    monkeypatch.setattr(tools, "run_node_cli", _arun)
    result = asyncio.run(capabilities_list())
    assert json.loads(json.dumps(result)) == result