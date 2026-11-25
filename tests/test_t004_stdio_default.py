"""T-004 residual (post T-037): the stdio default transport is untouched.

T-037 moved the Streamable-HTTP endpoint (build_app/main --http machinery)
to the iowap-node-mcp package. What stays here — and is now the package's
whole transport surface — is stdio: unauthenticated-by-transport, never
reading IOWAP_MCP_BEARER, spawn contract unchanged.
"""

from __future__ import annotations

import os

from iowap_client_mcp.mcp_server import build_server, main

BEARER_ENV = "IOWAP_MCP_BEARER"


def test_stdio_default_server_has_no_auth() -> None:
    """build_server() default (stdio) carries no auth/verifier — untouched."""
    server = build_server()
    assert server is not None
    assert server.settings.auth is None
    assert server._token_verifier is None


def test_stdio_never_reads_bearer_env(capsys, monkeypatch) -> None:
    """main() on the stdio path never looks at the Bearer env."""
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


def test_stdio_only_http_flag_is_gone() -> None:
    """T-037: --http is no longer a recognized flag on the local package.

    Callers still passing the old `--http` argv (e.g. a stale spawn builder)
    must fail loudly at parse time — silently serving the stdio transport
    (or pretending to serve HTTP) would mask the split.
    """
    import pytest

    with pytest.raises(SystemExit):
        main(["--http"])
