"""IOWAP MCP stdio server.

Runs as an MCP stdio subprocess spawned by the MCP client (Hermes, OpenCode,
Codex, any MCP-speaking agent). The node CLI (`iowap-cli --json`) remains the
source of truth for relay auth and request execution — this server never
touches tokens or speaks HTTP to the relay itself.

Scope (T-037 split): this package is the local-agent path — stdio is the
default and only transport here. The container-HTTP surface (Streamable
HTTP ``/mcp`` + Bearer auth, formerly T-004 ``--http`` in this file) now
lives in the sibling package ``iowap-node-mcp``, which imports this
package as a library instead of duplicating it.

v1 (T-003/T-004/T-235) registers the curated 9-tool set (see tools.py):
read — relay_status, node_list, capabilities_list, docs, task_list, artifact_link
write — task_submit, task_result, artifact_download

The stdio transport binds to the spawning client's stdio pipes
(unauthenticated-by-transport, no shared secret involved) and never reads
``IOWAP_MCP_BEARER``, so existing MCP-client configs keep working. See the
project board (DECISIONS.md) for every boundary decision.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from importlib import import_module, metadata
from typing import Any

PACKAGE = "iowap-client-mcp"
SERVER_NAME = "iowap"


def build_server(token_verifier: Any | None = None, auth: Any | None = None) -> Any | None:
    """Build the MCP server instance, or return None if the SDK is missing.

    ``token_verifier``/``auth`` are only used by the iowap-node-mcp HTTP
    server (T-037), which imports this builder as a library; the stdio
    default stays unauthenticated-by-transport (bound to the spawning
    client's stdio pipes, no shared secret involved).
    """
    try:
        module = import_module("mcp.server.mcpserver")
    except ImportError:
        return None

    server = module.MCPServer(
        name=SERVER_NAME,
        instructions=(
            "Curated IOWAP toolkit over the IOWAP node CLI. Fleet visibility, "
            "capability discovery, filtered task list queries, task submit/"
            "result tracking, artifact download and short-lived download "
            "links, and live relay docs."
        ),
        token_verifier=token_verifier,
        auth=auth,
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
            "IOWAP MCP toolkit — exposes a curated toolset over the IOWAP "
            "node CLI to any MCP client over stdio (spawned by the MCP "
            "client; the only transport). The container-HTTP surface "
            "(Streamable HTTP /mcp + Bearer) lives in the iowap-node-mcp "
            "package (T-037)."
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
