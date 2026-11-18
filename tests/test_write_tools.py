"""Unit tests for the v1 write tools (T-004) — logic, not live relay."""

from __future__ import annotations

import asyncio
import stat
from pathlib import Path

import pytest

from iowap_client_mcp import tools
from iowap_client_mcp.nodecli import NodeCliError
from iowap_client_mcp.tools import (
    _stage_arg,
    artifact_download,
    task_result,
    task_submit,
)

RUN_PATCH = "iowap_client_mcp.tools.run_node_cli"


def _fake_exe(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


# ---------------------------------------------------------------------------
# Validation guards (whitelist negatives — no subprocess may fire)
# ---------------------------------------------------------------------------


def test_submit_rejects_node_name_as_owner(monkeypatch) -> None:
    def _boom(args, **kwargs):
        raise AssertionError("no node-cli call may happen on validation failure")

    monkeypatch.setattr(tools, "run_node_cli", _boom)
    with pytest.raises(ValueError, match="node ID"):
        asyncio.run(
            task_submit("chat.ai", {"prompt": "x"}, owner_node_id="felix-m4-v4")
        )


def test_submit_allowlist_denies(monkeypatch) -> None:
    """Operator allowlist (IOWAP_ALLOW_CAPS) gates the execution surface."""
    monkeypatch.setenv("IOWAP_ALLOW_CAPS", "chat.ai, mc.list.players")
    monkeypatch.setattr(
        tools, "run_node_cli", lambda *a, **k: (_ for _ in ()).throw(AssertionError())
    )
    with pytest.raises(ValueError, match="IOWAP_ALLOW_CAPS"):
        asyncio.run(task_submit("desktop.ctl", {}))


def test_submit_allowlist_allows_listed_and_unset(monkeypatch) -> None:
    async def _fake(args, **kwargs):
        return {"task_id": "task_OK", "status": "pending"}

    monkeypatch.setattr(tools, "run_node_cli", _fake)

    async def _track(task_id, **kwargs):
        return {"tracked": False}

    monkeypatch.setattr(tools, "_track_task", _track)
    monkeypatch.setenv("IOWAP_ALLOW_CAPS", "chat.ai")
    r1 = asyncio.run(task_submit("chat.ai", {}))
    monkeypatch.delenv("IOWAP_ALLOW_CAPS", raising=False)
    r2 = asyncio.run(task_submit("desktop.ctl", {}))  # unset = open
    monkeypatch.setenv("IOWAP_ALLOW_CAPS", "   ")
    r3 = asyncio.run(task_submit("desktop.ctl", {}))  # whitespace-only = open
    assert (r1["task_id"], r2["task_id"], r3["task_id"]) == ("task_OK", "task_OK", "task_OK")


def test_artifact_download_refuses_overwrite(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "keep.png"
    target.write_bytes(b"original")

    monkeypatch.setattr(
        tools, "run_node_cli",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no download allowed"))
    )
    with pytest.raises(FileExistsError, match="overwrite"):
        asyncio.run(artifact_download("art_x", str(target)))
    assert target.read_bytes() == b"original"  # untouched


def test_artifact_download_overwrite_true_proceeds(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "keep.png"
    target.write_bytes(b"original")

    async def _fake(args, **kwargs):
        target.write_bytes(b"REPLACED")
        return {"artifact_id": "art_x", "ok": True}

    monkeypatch.setattr(tools, "run_node_cli", _fake)
    result = asyncio.run(artifact_download("art_x", str(target), overwrite=True))
    assert result["exists"] is True
    assert target.read_bytes() == b"REPLACED"


def test_tracking_degrades_without_fcntl(monkeypatch, tmp_path: Path) -> None:
    """POSIX-only locking degrades to tracked:false — server start survives."""

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setitem(__import__("sys").modules, "fcntl", None)
    result = asyncio.run(tools._track_task("task_LZ", capability="c", name=None, owner=None, delegated=False))
    # ModuleNotFoundError (subclass of ImportError) is what the halted halt yields
    assert result == {
        "tracked": False,
        "reason": "ModuleNotFoundError: import of fcntl halted; None in sys.modules",
    }


def test_submit_rejects_non_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        tools, "run_node_cli", lambda *a, **k: (_ for _ in ()).throw(AssertionError())
    )
    with pytest.raises(TypeError, match="payload"):
        asyncio.run(task_submit("chat.ai", ["not", "a", "dict"]))  # type: ignore[arg-type]


def test_stage_arg_rejects_separator_injection() -> None:
    assert _stage_arg("chat.ai", {"prompt": "hi"}) == 'chat.ai:{"prompt":"hi"}'
    try:
        _stage_arg("chat:evil", {"x": "y"})
    except ValueError as exc:
        assert "invalid capability" in str(exc)
    else:
        raise AssertionError("capability name with ':' must be rejected")


def test_invalid_capability_rejected_before_spawning(monkeypatch) -> None:
    monkeypatch.setattr(
        tools, "run_node_cli", lambda *a, **k: (_ for _ in ()).throw(AssertionError())
    )
    with pytest.raises(ValueError, match="invalid capability"):
        asyncio.run(task_submit(":evil", {}))


# ---------------------------------------------------------------------------
# Submit flow incl. tracking
# ---------------------------------------------------------------------------


def test_submit_without_wait_returns_task_and_tracks(monkeypatch) -> None:
    submit_payload = {"task_id": "task_XYZ", "stage_id": "s1", "status": "pending"}

    async def _fake(args, **kwargs):
        assert args[0] == "task" and args[1] == "submit"
        assert "--json" not in args  # runner prepends the global flag itself
        assert args[2] == "--stage"
        return submit_payload

    monkeypatch.setattr(tools, "run_node_cli", _fake)
    seen: list[list] = []

    async def _track(task_id: str, **meta) -> dict:
        seen.append([task_id, meta])
        return {"tracked": True}

    monkeypatch.setattr(tools, "_track_task", _track)
    result = asyncio.run(task_submit("chat.ai", {"prompt": "hi"}))
    assert result["task_id"] == "task_XYZ"
    assert result["tracking"] == {"tracked": True}
    assert seen == [
        [
            "task_XYZ",
            {
                "capability": "chat.ai",
                "name": None,
                "owner": None,
                "delegated": False,
                "session_id": None,
            },
        ]
    ]


def test_submit_owner_directed_tracks_delegated(monkeypatch) -> None:
    async def _fake(args, **kwargs):
        return {"task_id": "task_ABC", "status": "pending"}

    monkeypatch.setattr(tools, "run_node_cli", _fake)

    async def _track(task_id: str, **meta) -> dict:
        return {"tracked": bool(meta.get("delegated"))}

    monkeypatch.setattr(tools, "_track_task", _track)
    result = asyncio.run(
        task_submit("mc.list.players", {}, owner_node_id="NODE23A4")
    )
    assert result["owner_node_id"] == "NODE23A4"
    assert result["tracking"] == {"tracked": True}


def test_submit_wait_completes_quickly(monkeypatch) -> None:
    calls: list[list[str]] = []

    async def _fake(args, **kwargs):
        calls.append(list(args))
        if args[1] == "submit":
            return {"task_id": "task_W", "status": "pending"}
        return {
            "task": {"status": "completed", "claimed_by": "NODE23A4"},
            "stages": [
                {"status": "completed", "result": {"answer": 42}},
            ],
        }

    monkeypatch.setattr(tools, "run_node_cli", _fake)
    monkeypatch.setattr(tools, "_POLL_INTERVAL", 0.01)

    async def _track(task_id: str, **meta) -> dict:
        return {"tracked": False}

    monkeypatch.setattr(tools, "_track_task", _track)
    result = asyncio.run(task_submit("chat.ai", {}, wait_seconds=10))
    assert result["status"] == "completed"
    assert result["result"] == {"answer": 42}
    assert result["waited_seconds"] <= 3
    assert calls[0][1] == "submit" and calls[1][1] == "result"


def test_submit_wait_capped_at_ninety(monkeypatch) -> None:
    async def _fake(args, **kwargs):
        if args[1] == "submit":
            return {"task_id": "task_CAP", "status": "pending"}
        return {"task": {"status": "pending"}, "stages": []}

    monkeypatch.setattr(tools, "run_node_cli", _fake)
    monkeypatch.setattr(tools, "_POLL_INTERVAL", 0.05)
    monkeypatch.setattr(tools, "_WAIT_CAP", 0.12)  # cap itself under test

    async def _track(task_id: str, **meta) -> dict:
        return {"tracked": False}

    monkeypatch.setattr(tools, "_track_task", _track)
    result = asyncio.run(task_submit("chat.ai", {}, wait_seconds=999))
    assert result["status"] == "pending"  # never completed → pending after cap
    assert result["waited_seconds"] <= 1


# ---------------------------------------------------------------------------
# task_result
# ---------------------------------------------------------------------------


def test_task_result_unwraps_envelope(monkeypatch) -> None:
    payload = {
        "task": {"status": "completed", "claimed_by": "NODE23A4"},
        "stages": [
            {
                "status": "completed",
                "result": {
                    "status": "success",
                    "result": {"text": "hi"},
                    "error": None,
                },
            }
        ],
    }

    async def _fake(args, **kwargs):
        assert args[:2] == ["task", "result"] and args[2] == "task_T1"
        return payload

    monkeypatch.setattr(tools, "run_node_cli", _fake)
    result = asyncio.run(task_result("task_T1"))
    assert result["status"] == "completed"
    assert result["result"] == {"text": "hi"}
    assert result["stages_count"] == 1


def test_task_result_not_found_is_structured(monkeypatch) -> None:
    def _fake(args, **kwargs):
        raise NodeCliError("node-cli produced non-JSON output (exit 0): Task t: not found")

    monkeypatch.setattr(tools, "run_node_cli", _fake)
    result = asyncio.run(task_result("task_GONE"))
    assert result["status"] == "not_found"
    assert "owner" in result["hint"]


def test_task_result_transport_error_becomes_structured_result(monkeypatch) -> None:
    """T-274: non-'not found' NodeCliError paths return the error dict.

    Supersedes the old propagate-verbatim contract ('Error executing tool'
    on the MCP host loses the relay detail): relay rejections AND transport
    failures become {'error': {http_status, detail, raw}} results.
    """
    def _fake(args, **kwargs):
        raise NodeCliError(
            "node-cli exit 7 (task result task_T2): relay unreachable"
        )

    monkeypatch.setattr(tools, "run_node_cli", _fake)
    result = asyncio.run(task_result("task_T2"))
    assert result == {
        "error": {"http_status": None, "detail": "relay unreachable", "raw": "relay unreachable"}
    }


# ---------------------------------------------------------------------------
# artifact_download
# ---------------------------------------------------------------------------


def test_artifact_download_verifies_file(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "out" / "shot.png"
    target.parent.mkdir(parents=True)

    async def _fake(args, **kwargs):
        assert args[:2] == ["artifact", "download"]
        assert "--output" in args and str(target) in args
        target.write_bytes(b"PNGbytes")
        return {"artifact_id": "art_1", "ok": True}

    monkeypatch.setattr(tools, "run_node_cli", _fake)
    result = asyncio.run(artifact_download("art_1", str(target)))
    assert result["exists"] is True
    assert result["bytes"] == 8
    assert result["path"] == str(target)


def test_artifact_download_relative_resolves_home(monkeypatch, tmp_path: Path) -> None:

    monkeypatch.setattr("pathlib.Path.home", staticmethod(lambda: tmp_path))
    captured: dict[str, str] = {}

    async def _fake(args, **kwargs):
        captured["target"] = args[args.index("--output") + 1]
        return {"ok": False, "error": "no such artifact"}

    monkeypatch.setattr(tools, "run_node_cli", _fake)
    try:
        asyncio.run(artifact_download("art_2", "rel/file.bin"))
    except NodeCliError:
        pass
    assert captured["target"] == str(tmp_path / "rel/file.bin")


# ---------------------------------------------------------------------------
# iowap-task bridge (track store reuse)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# iowap-task bridge (track store reuse) — REMOVED by T-007: tracking is now
# in-process (`iowap_client_mcp.tracking`); flock/concurrency/schema
# contracts live in tests/test_t007_tracking.py. The bridge binary itself
# stays a session-side CLI in the iowap-hermes-integration repo.
# ---------------------------------------------------------------------------


def test_submit_accepts_digits_in_node_ids(monkeypatch) -> None:
    """Live-found IDs carry 8/9 (4W8EATRW, AMKJA9AE) — not strict base32."""
    async def _fake(args, **kwargs):
        return {"task_id": "task_D9", "status": "pending"}

    monkeypatch.setattr(tools, "run_node_cli", _fake)

    async def _track(task_id: str, **meta) -> dict:
        return {"tracked": False}

    monkeypatch.setattr(tools, "_track_task", _track)
    result = asyncio.run(
        task_submit("mc.list.players", {}, owner_node_id="AMKJA9AE")
    )
    assert result["task_id"] == "task_D9"


# ---------------------------------------------------------------------------
# T-274: structured relay-error results — NodeCliError never bubbles raw
# to the MCP host ('Error executing tool' loses the relay detail); the
# three write tools convert it into {'error': {http_status, detail, raw}}.
# Fixture messages replicate the runner's own NodeCliError formats
# (nodecli.run_node_cli / _run) so parse_relay_error() sees the same
# stderr tail it sees in production.
# ---------------------------------------------------------------------------


def test_submit_404_relay_body_returns_error_result(monkeypatch) -> None:
    """Submit rejected with HTTP 404 + relay body → parsed error dict, no raise."""
    stderr_tail = (
        'submit failed: HTTP 404: '
        '{"detail": "capability missing.cap unknown to the relay"}'
    )
    calls: list[str] = []

    def _fake(args, **kwargs):
        calls.append(" ".join(args))
        raise NodeCliError(f"node-cli produced no JSON output (exit 1): {stderr_tail}")

    monkeypatch.setattr(tools, "run_node_cli", _fake)

    async def _track(task_id: str, **meta) -> dict:
        raise AssertionError("failed submit must not touch the track store")

    monkeypatch.setattr(tools, "_track_task", _track)
    result = asyncio.run(task_submit("missing.cap", {"prompt": "x"}))
    assert result == {
        "error": {
            "http_status": 404,
            "detail": '{"detail": "capability missing.cap unknown to the relay"}',
            "raw": stderr_tail,
        }
    }
    assert calls[0].startswith("task submit")  # the submit path raised, nothing else ran


def test_submit_500_without_body_stays_unclassified(monkeypatch) -> None:
    """Relay 500 with no body: no parseable status → http_status None, detail = raw.

    Pins parse_relay_error's boundary — a bare ``HTTP 500:`` line carries no
    message body, so the tool falls back to the raw stderr tail for both
    detail and raw.
    """
    def _fake(args, **kwargs):
        raise NodeCliError(
            "node-cli produced no JSON output (exit 1): submit failed: HTTP 500:"
        )

    monkeypatch.setattr(tools, "run_node_cli", _fake)
    result = asyncio.run(task_submit("chat.ai", {"prompt": "x"}))
    assert result == {
        "error": {
            "http_status": None,
            "detail": "submit failed: HTTP 500:",
            "raw": "submit failed: HTTP 500:",
        }
    }


def test_submit_binary_missing_returns_transport_error_result(monkeypatch) -> None:
    """node-cli binary missing (spawn FileNotFoundError) → unclassified error dict."""
    def _fake(args, **kwargs):
        raise NodeCliError(
            "node-cli not found on this host (tried pin → PATH → candidates): "
            "[Errno 2] No such file or directory: 'node-cli'"
        )

    monkeypatch.setattr(tools, "run_node_cli", _fake)
    result = asyncio.run(task_submit("chat.ai", {"prompt": "x"}))
    assert result == {
        "error": {
            "http_status": None,
            "detail": "[Errno 2] No such file or directory: 'node-cli'",
            "raw": "[Errno 2] No such file or directory: 'node-cli'",
        }
    }


def test_task_result_404_relay_body_returns_error_result(monkeypatch) -> None:
    """A 404 whose body avoids the literal 'not found' is NOT the legacy branch."""
    stderr_tail = (
        'task result failed: HTTP 404: '
        '{"detail": "task deleted; retention window passed"}'
    )

    def _fake(args, **kwargs):
        raise NodeCliError(f"node-cli produced no JSON output (exit 1): {stderr_tail}")

    monkeypatch.setattr(tools, "run_node_cli", _fake)
    result = asyncio.run(task_result("task_GONE1"))
    assert result == {
        "error": {
            "http_status": 404,
            "detail": '{"detail": "task deleted; retention window passed"}',
            "raw": stderr_tail,
        }
    }


def test_task_result_500_without_body_stays_unclassified(monkeypatch) -> None:
    """task_result HTTP 500 without body → http_status None, detail = raw."""
    def _fake(args, **kwargs):
        raise NodeCliError(
            "node-cli produced no JSON output (exit 1): task result failed: HTTP 500:"
        )

    monkeypatch.setattr(tools, "run_node_cli", _fake)
    result = asyncio.run(task_result("task_R5"))
    assert result == {
        "error": {
            "http_status": None,
            "detail": "task result failed: HTTP 500:",
            "raw": "task result failed: HTTP 500:",
        }
    }


def test_task_result_binary_missing_returns_transport_error_result(monkeypatch) -> None:
    """Transport failure surface mirrors task_submit's (same error dict)."""
    def _fake(args, **kwargs):
        raise NodeCliError(
            "node-cli not found on this host (tried pin → PATH → candidates): "
            "[Errno 2] No such file or directory: 'node-cli'"
        )

    monkeypatch.setattr(tools, "run_node_cli", _fake)
    result = asyncio.run(task_result("task_R6"))
    assert result == {
        "error": {
            "http_status": None,
            "detail": "[Errno 2] No such file or directory: 'node-cli'",
            "raw": "[Errno 2] No such file or directory: 'node-cli'",
        }
    }


def test_task_result_notfound_wording_wins_over_http_parsing(monkeypatch) -> None:
    """The legacy 'not found' branch stays exactly as-is — checked FIRST.

    Even when the error carries a parseable HTTP status (404 + 'task not
    found'), the pre-T-274 result (task_id + status: not_found + hint, no
    'error' key) must keep winning.
    """
    def _fake(args, **kwargs):
        raise NodeCliError(
            "node-cli produced no JSON output (exit 1): "
            "task result failed: HTTP 404: task not found"
        )

    monkeypatch.setattr(tools, "run_node_cli", _fake)
    result = asyncio.run(task_result("task_GONE2"))
    assert result["status"] == "not_found"
    assert result["task_id"] == "task_GONE2"
    assert "hint" in result
    assert "error" not in result


def test_artifact_download_404_relay_body_returns_error_result(monkeypatch, tmp_path) -> None:
    """Artifact gone: relay 404 with 'Artifact not found' body — no legacy
    special case exists here, so this lands in the error dict. The tool also
    guarantees the local target stays absent (no CLI progress → no file)."""
    target = tmp_path / "gone.png"
    stderr_tail = (
        'artifact download failed: HTTP 404: {"detail": "Artifact not found"}'
    )

    def _fake(args, **kwargs):
        raise NodeCliError(f"node-cli produced no JSON output (exit 1): {stderr_tail}")

    monkeypatch.setattr(tools, "run_node_cli", _fake)
    result = asyncio.run(artifact_download("art_GONE", str(target)))
    assert result == {
        "error": {
            "http_status": 404,
            "detail": '{"detail": "Artifact not found"}',
            "raw": stderr_tail,
        }
    }
    assert not target.exists()  # no partial write on a rejected download


def test_artifact_download_500_without_body_stays_unclassified(monkeypatch, tmp_path) -> None:
    """artifact_download HTTP 500 without body → http_status None, detail = raw."""
    def _fake(args, **kwargs):
        raise NodeCliError(
            "node-cli produced no JSON output (exit 1): "
            "artifact download failed: HTTP 500:"
        )

    monkeypatch.setattr(tools, "run_node_cli", _fake)
    result = asyncio.run(artifact_download("art_X", str(tmp_path / "x.bin")))
    assert result == {
        "error": {
            "http_status": None,
            "detail": "artifact download failed: HTTP 500:",
            "raw": "artifact download failed: HTTP 500:",
        }
    }


def test_artifact_download_binary_missing_returns_transport_error_result(
    monkeypatch, tmp_path
) -> None:
    """Transport failure surface mirrors the other write tools."""
    def _fake(args, **kwargs):
        raise NodeCliError(
            "node-cli not found on this host (tried pin → PATH → candidates): "
            "[Errno 2] No such file or directory: 'node-cli'"
        )

    monkeypatch.setattr(tools, "run_node_cli", _fake)
    result = asyncio.run(artifact_download("art_Y", str(tmp_path / "y.bin")))
    assert result == {
        "error": {
            "http_status": None,
            "detail": "[Errno 2] No such file or directory: 'node-cli'",
            "raw": "[Errno 2] No such file or directory: 'node-cli'",
        }
    }