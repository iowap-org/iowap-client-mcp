"""T-033: MCP read tool ``artifact_link`` — happy path + T-274 error shape.

Contract (brief):
* ``run_node_cli(["artifact", "link", artifact_id, ...])``.
* Happy response: ``{"artifact_id", "url", "expires_at", "max_downloads",
  "cli_output"}`` — CLI-Output geparst.
* ``NodeCliError`` → ``{'error': {'http_status', 'detail', 'raw'}}``,
  NO raise (T-274 error shape, same as artifact_download).
* Tool registered in the read toolset (ALL_TOOLS complete).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from iowap_client_mcp import tools

RUN_PATCH = "iowap_client_mcp.tools.run_node_cli"

_LINK_RESULT = {
    "artifact_id": "artifact_x",
    "url": "http://relay.test:8788/relay/v2/storage/files/artifact_x/dl/tok",
    "expires_at": "2026-11-25T20:00:00+00:00",
    "max_downloads": 1,
    "ttl_seconds": 900,
    "max_downloads_cap": 10,
}


def _run(coro) -> Any:  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_artifact_link_happy_path_parses_cli_output(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    async def fake_run(argv: list[str], **_kw: Any) -> dict[str, Any]:
        seen.append(argv)
        return dict(_LINK_RESULT)

    monkeypatch.setattr(tools, "run_node_cli", fake_run)

    result = _run(tools.artifact_link("artifact_x"))

    assert seen == [["artifact", "link", "artifact_x"]]
    assert result["artifact_id"] == "artifact_x"
    assert result["url"] == _LINK_RESULT["url"]
    assert result["expires_at"] == _LINK_RESULT["expires_at"]
    assert result["max_downloads"] == 1
    assert result["cli_output"] == _LINK_RESULT


def test_artifact_link_forwards_optional_clamps_as_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []

    async def fake_run(argv: list[str], **_kw: Any) -> dict[str, Any]:
        seen.append(argv)
        return dict(_LINK_RESULT)

    monkeypatch.setattr(tools, "run_node_cli", fake_run)

    result = _run(tools.artifact_link("artifact_x", ttl_seconds=120, max_downloads=3))

    assert seen == [
        ["artifact", "link", "artifact_x", "--ttl-seconds", "120", "--max-downloads", "3"]
    ]
    assert result["url"] == _LINK_RESULT["url"]


def test_artifact_link_omits_unset_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    async def fake_run(argv: list[str], **_kw: Any) -> dict[str, Any]:
        seen.append(argv)
        return dict(_LINK_RESULT)

    monkeypatch.setattr(tools, "run_node_cli", fake_run)

    _run(tools.artifact_link("artifact_x", ttl_seconds=None, max_downloads=None))

    assert seen == [["artifact", "link", "artifact_x"]]


def test_artifact_link_http_error_returns_t274_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Relay 404 via node-cli → error dict, no raise; stderr tail is embedded
    in raw, http_status 404 and detail parsed from the HTTP status line."""

    async def fake_run(argv: list[str], **_kw: Any) -> dict[str, Any]:
        raise tools.NodeCliError(
            f"node-cli exit 1 ({' '.join(argv)}): "
            "artifact link failed: HTTP 404: Artifact not found"
        )

    monkeypatch.setattr(tools, "run_node_cli", fake_run)

    result = _run(tools.artifact_link("artifact_missing"))

    assert result["error"]["http_status"] == 404
    assert result["error"]["detail"] == "Artifact not found"
    assert "HTTP 404" in result["error"]["raw"]


def test_artifact_link_transport_error_returns_t274_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run(argv: list[str], **_kw: Any) -> dict[str, Any]:
        raise tools.NodeCliError("node-cli not found on this host (tried pin → PATH → candidates)")

    monkeypatch.setattr(tools, "run_node_cli", fake_run)

    result = _run(tools.artifact_link("artifact_x"))

    assert result["error"]["http_status"] is None
    assert result["error"]["raw"] == (
        "node-cli not found on this host (tried pin → PATH → candidates)"
    )


def test_artifact_link_registered_in_read_toolset() -> None:
    tools.register_read_tools(_RecordingServer())


class _RecordingServer:
    """Minimal server.tool recorder mirroring the contract tests."""

    def __init__(self) -> None:
        self.registered: dict[str, Any] = {}

    def tool(self, name: str, description: str = ""):  # noqa: ANN001
        def deco(fn):  # noqa: ANN001
            self.registered[name] = (fn, description)
            return fn

        return deco


def test_artifact_link_brief_description_string() -> None:
    """The curated description carries the brief's 'URL itself is the bearer' line."""
    server = _RecordingServer()
    tools.register_read_tools(server)
    _fn, description = server.registered["artifact_link"]
    assert "the URL itself is the bearer" in description
