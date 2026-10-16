"""IOWAP MCP stdio server.

Runs as an MCP stdio subprocess spawned by the MCP client (Hermes, OpenCode,
any MCP-speaking agent). The node CLI (`node-cli --json`) remains the source of
truth for relay auth and request execution — this server never touches tokens
or speaks HTTP to the relay itself.

Skeleton (T-001): the server starts and exposes zero tools. The curated v1
toolset is added in T-002/T-003/T-004 (see the project board).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from importlib import import_module, metadata
from typing import Any

PACKAGE = "iowap-client-mcp"
SERVER_NAME = "iowap"


def build_server() -> Any | None:
    """Build the MCP server instance, or return None if the SDK is missing."""
    try:
        module = import_module("mcp.server.mcpserver")
    except ImportError:
        return None

    server = module.MCPServer(
        name=SERVER_NAME,
        instructions=(
            "Curated IOWAP toolkit over the IOWAP node CLI. Fleet visibility, "
            "capability discovery, task submit/result tracking, artifact "
            "download, and live relay docs."
        ),
    )
    from iowap_client_mcp import tools

    tools.register_read_tools(server)
    tools.register_write_tools(server)
    return server


async def list_tool_names() -> list[str]:
    """Contract helper: names of currently registered tools (empty in skeleton)."""
    server = build_server()
    if server is None:
        return []
    return sorted(tool.name for tool in await server.list_tools())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="iowap-mcp",
        description=(
            "IOWAP MCP stdio toolkit — exposes a curated toolset over the "
            "IOWAP node CLI to any MCP client. Run by the MCP client; do not "
            "start manually for tool use."
        ),
    )
    parser.add_argument("--version", action="store_true", help="print version and exit")
    args = parser.parse_args(argv)

    if args.version:
        print(metadata.version(PACKAGE))
        return 0

    server = build_server()
    if server is None:
        print(
            "error: the 'mcp' package is not installed — "
            "install with: pip install 'iowap-client-mcp[mcp]'",
            file=sys.stderr,
        )
        return 1

    asyncio.run(server.run_stdio_async())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())