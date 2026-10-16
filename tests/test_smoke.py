"""Smoke tests for the iowap-client-mcp skeleton (T-001)."""

from __future__ import annotations

import asyncio
import importlib
import tomllib
from importlib import metadata
from pathlib import Path

import iowap_client_mcp
from iowap_client_mcp.mcp_server import PACKAGE, build_server, list_tool_names, main

REPO_ROOT = Path(__file__).resolve().parents[1]


def _mcp_unavailable(module_name: str, *args, **kwargs):
    if module_name == "mcp" or module_name.startswith("mcp."):
        raise ImportError(f"No module named '{module_name}'", name=module_name)
    return importlib.import_module(module_name)


def test_version_consistency() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    declared = pyproject["project"]["version"]
    assert iowap_client_mcp.__version__ == declared
    assert metadata.version("iowap-client-mcp") == declared


def test_main_version_flag_exits_zero(capsys) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == iowap_client_mcp.__version__


def test_entry_point_constant_matches_package() -> None:
    assert PACKAGE == "iowap-client-mcp"


def test_build_server_with_sdk() -> None:
    """With the mcp SDK importable, build_server returns a named MCPServer."""
    from mcp.server.mcpserver import MCPServer

    server = build_server()
    assert isinstance(server, MCPServer)
    assert server.name == "iowap"


def test_v1_read_toolset_is_registered_and_whitelisted() -> None:
    """Full v1 set (read T-003 + write T-004) registered; nothing beyond."""
    from iowap_client_mcp.tools import ALL_TOOLS

    names = asyncio.run(list_tool_names())
    assert set(names) == set(ALL_TOOLS)
    assert len(names) == len(set(names)) == 7


def test_build_server_without_sdk(monkeypatch) -> None:
    """Without the mcp SDK, build_server returns None (graceful degradation)."""
    monkeypatch.setattr(
        "iowap_client_mcp.mcp_server.import_module", _mcp_unavailable
    )
    # NOTE: build_server must import the SDK via a module-level import_module
    # so this patch can simulate an SDK-free host without import gymnastics.
    assert build_server() is None


def test_list_tool_names_without_sdk(monkeypatch) -> None:
    monkeypatch.setattr(
        "iowap_client_mcp.mcp_server.import_module", _mcp_unavailable
    )
    assert asyncio.run(list_tool_names()) == []


def test_main_without_sdk_exits_one(monkeypatch, capsys) -> None:
    monkeypatch.setattr("iowap_client_mcp.mcp_server.build_server", lambda: None)
    assert main([]) == 1
    err = capsys.readouterr().err
    assert "pip install 'iowap-client-mcp[mcp]'" in err