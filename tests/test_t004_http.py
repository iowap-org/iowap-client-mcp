"""T-004: Streamable-HTTP transport — Bearer auth (fail closed), same toolset.

stdio stays the default transport; --http adds the Streamable-HTTP endpoint
at /mcp with a static operator Bearer token from IOWAP_MCP_BEARER. The
allowlist semantics (IOWAP_ALLOW_CAPS) apply identically over HTTP because
the toolset is the very same server object.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager

from iowap_client_mcp.mcp_server import (
    BEARER_ENV,
    build_http_app,
    build_server,
    main,
    resolve_port,
)

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t004-test", "version": "0"},
    },
}

INITIALIZED_NOTIFICATION = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def _post(
    url: str,
    body: dict,
    *,
    token: str | None = None,
    session: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """POST a JSON-RPC message to the MCP endpoint, never raising on 4xx/5xx."""
    data = json.dumps(body).encode()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if session is not None:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()


@contextmanager
def _http_server(app) -> Iterator[str]:
    """Serve a Starlette app via uvicorn on a random free port; yield base URL."""
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started:
            if time.monotonic() > deadline:
                raise TimeoutError("uvicorn did not start")
            time.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _session_and_url(base_url: str, token: str) -> tuple[str, str]:
    """Initialize a session; return (session_id, url) for follow-up requests."""
    status, headers, body = _post(f"{base_url}/mcp", INITIALIZE, token=token)
    assert status == 200, (status, body)
    session = headers.get("mcp-session-id")
    assert session, f"no session header in {headers}"
    status, _, body = _post(
        f"{base_url}/mcp", INITIALIZED_NOTIFICATION, token=token, session=session
    )
    assert status == 202, (status, body)
    return session, f"{base_url}/mcp"


# --- fail closed -------------------------------------------------------------


def test_http_fail_closed_without_env(capsys, monkeypatch) -> None:
    monkeypatch.delenv(BEARER_ENV, raising=False)
    assert main(["--http"]) == 1
    err = capsys.readouterr().err
    assert BEARER_ENV in err
    assert "fail" in err.lower() or "refusing" in err.lower()


def test_http_fail_closed_with_blank_env(capsys, monkeypatch) -> None:
    monkeypatch.setenv(BEARER_ENV, "   ")
    assert main(["--http"]) == 1
    assert BEARER_ENV in capsys.readouterr().err


def test_http_fail_closed_before_server_build(monkeypatch) -> None:
    """No SDK import / server build happens when the token env is missing."""
    import iowap_client_mcp.mcp_server as mod

    monkeypatch.delenv(BEARER_ENV, raising=False)

    def _boom(*args, **kwargs):  # pragma: no cover
        raise AssertionError("server must not be built without a Bearer token")

    monkeypatch.setattr(mod, "build_http_app", _boom)
    assert main(["--http"]) == 1


def test_resolve_port_cli_wins_over_env(monkeypatch) -> None:
    monkeypatch.setenv("IOWAP_MCP_PORT", "9999")
    assert resolve_port(1234) == 1234
    assert resolve_port(None) == 9999
    monkeypatch.delenv("IOWAP_MCP_PORT")
    assert resolve_port(None) == 7781


def test_resolve_port_rejects_junk_env(monkeypatch) -> None:
    monkeypatch.setenv("IOWAP_MCP_PORT", "not-a-port")
    try:
        resolve_port(None)
    except ValueError as exc:
        assert "IOWAP_MCP_PORT" in str(exc)
    else:
        raise AssertionError("expected ValueError for non-numeric port env")


# --- auth ---------------------------------------------------------------------


def test_http_401_without_bearer() -> None:
    app = build_http_app("sekrit")
    with _http_server(app) as base_url:
        status, headers, _ = _post(f"{base_url}/mcp", INITIALIZE)
        assert status == 401
        assert headers.get("www-authenticate", "").startswith("Bearer")


def test_http_401_with_wrong_bearer() -> None:
    app = build_http_app("sekrit")
    with _http_server(app) as base_url:
        status, _, _ = _post(f"{base_url}/mcp", INITIALIZE, token="wrong-token")
        assert status == 401


def test_http_200_initialize_with_bearer() -> None:
    app = build_http_app("sekrit")
    with _http_server(app) as base_url:
        status, _, body = _post(f"{base_url}/mcp", INITIALIZE, token="sekrit")
        assert status == 200, body
        payload = json.loads(body)
        assert payload["result"]["serverInfo"]["name"] == "iowap"


# --- same curated toolset over HTTP -------------------------------------------


def test_http_tools_discoverable() -> None:
    from iowap_client_mcp.tools import ALL_TOOLS

    app = build_http_app("sekrit")
    with _http_server(app) as base_url:
        session, url = _session_and_url(base_url, "sekrit")
        status, _, body = _post(
            url,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            token="sekrit",
            session=session,
        )
        assert status == 200, body
        names = {tool["name"] for tool in json.loads(body)["result"]["tools"]}
        assert names == set(ALL_TOOLS)
        assert len(names) == 8


def test_http_allowlist_greps(monkeypatch, caplog) -> None:
    """IOWAP_ALLOW_CAPS applies over HTTP (same tool object, per-call env)."""
    monkeypatch.setenv("IOWAP_ALLOW_CAPS", "only-this-cap")
    app = build_http_app("sekrit")
    with _http_server(app) as base_url:
        session, url = _session_and_url(base_url, "sekrit")
        status, _, body = _post(
            url,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "task_submit",
                    "arguments": {"capability": "forbidden-cap", "payload": {}},
                },
            },
            token="sekrit",
            session=session,
        )
        assert status == 200, body
        result = json.loads(body)["result"]
        assert result["isError"] is True
        # The MCP host sees a generic error text (SDK wrapping); the allowlist
        # detail lands in the server-side log — assert it there.
        assert "IOWAP_ALLOW_CAPS" in caplog.text


# --- stdio path unchanged ------------------------------------------------------


def test_stdio_default_server_has_no_auth() -> None:
    """build_server() default (stdio) carries no auth/verifier — untouched."""
    server = build_server()
    assert server is not None
    assert server.settings.auth is None
    assert server._token_verifier is None


def test_stdio_still_default_transport(capsys, monkeypatch) -> None:
    """Without --http, main() never looks at the Bearer env (stdio path)."""
    import iowap_client_mcp.mcp_server as mod

    monkeypatch.delenv(BEARER_ENV, raising=False)

    class _FakeServer:
        async def run_stdio_async(self):  # pragma: no cover
            return None

    monkeypatch.setattr(mod, "build_server", lambda *a, **kw: _FakeServer())
    monkeypatch.setattr(
        mod.asyncio, "run", lambda coro_fn, *a, **kw: coro_fn
    )
    assert main([]) == 0
    assert os.environ.get(BEARER_ENV) is None
