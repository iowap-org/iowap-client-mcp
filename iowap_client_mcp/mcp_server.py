"""IOWAP MCP stdio server.

Runs as an MCP stdio subprocess spawned by the MCP client (Hermes, OpenCode,
Codex, any MCP-speaking agent). The node CLI (`node-cli --json`) remains the
source of truth for relay auth and request execution — this server never
touches tokens or speaks HTTP to the relay itself.

v1 (T-003/T-004/T-235) registers the curated 8-tool set (see tools.py):
read — relay_status, node_list, capabilities_list, docs, task_list
write — task_submit, task_result, artifact_download
HTTP-Transport (T-004): `--http` serves the same MCPServer instance via
Streamable HTTP at ``/mcp`` (SDK-native ``streamable_http_app``, no
hand-rolled ASGI). Auth is a static operator Bearer token from
``IOWAP_MCP_BEARER`` — the process refuses to start HTTP without it
(fail closed). stdio remains the default transport and never touches
that env, so existing MCP-client configs keep working.

Scope: binds the local node to local MCP clients; destructive node
operations are deliberately not exposed. See the project
board (DECISIONS.md) for every boundary decision.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import os
import sys
from importlib import import_module, metadata
from typing import Any

PACKAGE = "iowap-client-mcp"
SERVER_NAME = "iowap"
BEARER_ENV = "IOWAP_MCP_BEARER"
PORT_ENV = "IOWAP_MCP_PORT"
DEFAULT_HTTP_PORT = 7781
DEFAULT_HTTP_HOST = "127.0.0.1"


def build_server(token_verifier: Any | None = None, auth: Any | None = None) -> Any | None:
    """Build the MCP server instance, or return None if the SDK is missing.

    ``token_verifier``/``auth`` are only set for the HTTP transport (T-004);
    the stdio default stays unauthenticated-by-transport (bound to the
    spawning client's stdio pipes, no shared secret involved).
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
            "result tracking, artifact download, and live relay docs."
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


class StaticBearerVerifier:
    """Exact-match verifier for the operator-set shared Bearer token.

    Implements the SDK ``TokenVerifier`` protocol (duck-typed — the mcp SDK
    only requires ``async verify_token``). Uses constant-time comparison and
    grants no scopes/resources; the token is a shared secret, not an identity.
    """

    def __init__(self, token: str) -> None:
        self._token = token.encode()

    async def verify_token(self, token: str) -> Any | None:
        from mcp.server.auth.provider import AccessToken

        if hmac.compare_digest(token.encode(), self._token):
            return AccessToken(
                token=token,
                client_id="iowap-mcp-bearer",
                scopes=[],
                expires_at=None,
                subject="operator-bearer",
            )
        return None


def build_http_app(
    bearer_token: str, *, host: str = DEFAULT_HTTP_HOST, port: int = DEFAULT_HTTP_PORT
) -> Any:
    """Return the SDK-native Streamable-HTTP Starlette app (endpoint /mcp).

    Wires the static Bearer verifier through the SDK's own auth middleware
    (``AuthSettings`` + ``TokenVerifier``) — no hand-built ASGI scaffold, so
    401 handling, WWW-Authenticate and session management stay SDK-owned.
    ``validate_token_resource=False`` because the exact-match verifier itself
    is the full audience check for a shared secret.
    """
    from mcp.server.auth.settings import AuthSettings
    from pydantic import AnyHttpUrl

    base_url = AnyHttpUrl(f"http://{host}:{port}")
    auth = AuthSettings(
        issuer_url=base_url,
        resource_server_url=base_url,
        validate_token_resource=False,
    )
    server = build_server(token_verifier=StaticBearerVerifier(bearer_token), auth=auth)
    if server is None:
        raise RuntimeError(
            "the 'mcp' package is not installed — "
            "install with: pip install 'iowap-client-mcp[mcp]'"
        )
    return server.streamable_http_app(host=host, json_response=True)


def resolve_port(port: int | None) -> int:
    """CLI ``--port`` wins over ``IOWAP_MCP_PORT``; default 7781."""
    if port is not None:
        return port
    raw = os.environ.get(PORT_ENV, "").strip()
    if not raw:
        return DEFAULT_HTTP_PORT
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{PORT_ENV}={raw!r} is not an integer port number"
        ) from exc


def _run_http(host: str, port: int) -> int:
    """Start the HTTP transport — fail closed without a Bearer token env."""
    bearer = os.environ.get(BEARER_ENV, "").strip()
    if not bearer:
        print(
            f"error: {BEARER_ENV} is not set — refusing to start the HTTP "
            "transport (fail closed: never serve an unauthenticated MCP "
            "endpoint). Set a strong token in the environment, or use the "
            "default stdio transport.",
            file=sys.stderr,
        )
        return 1
    try:
        port = resolve_port(port)
        app = build_http_app(bearer, host=host, port=port)
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="iowap-mcp",
        description=(
            "IOWAP MCP toolkit — exposes a curated toolset over the "
            "IOWAP node CLI to any MCP client. Default transport is stdio "
            "(spawned by the MCP client); --http serves Streamable HTTP "
            "with Bearer auth from the IOWAP_MCP_BEARER env (fail closed)."
        ),
    )
    parser.add_argument("--version", action="store_true", help="print version and exit")
    parser.add_argument(
        "--http",
        action="store_true",
        help=(
            "serve Streamable HTTP at /mcp instead of stdio; requires "
            f"{BEARER_ENV} (exits without it) and binds {DEFAULT_HTTP_HOST}"
        ),
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HTTP_HOST,
        help=f"HTTP bind host (default: {DEFAULT_HTTP_HOST}; container-internal loopback)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=(
            f"HTTP port (default: {PORT_ENV} env or {DEFAULT_HTTP_PORT}); "
            "only used with --http"
        ),
    )
    args = parser.parse_args(argv)

    if args.version:
        print(metadata.version(PACKAGE))
        return 0

    if args.http:
        return _run_http(args.host, args.port)

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