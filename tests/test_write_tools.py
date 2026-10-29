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


def test_task_result_propagates_other_cli_errors(monkeypatch) -> None:
    def _fake(args, **kwargs):
        raise NodeCliError("node-cli exit 7: relay unreachable")

    monkeypatch.setattr(tools, "run_node_cli", _fake)
    try:
        asyncio.run(task_result("task_T2"))
    except NodeCliError:
        pass
    else:
        raise AssertionError("non-notfound NodeCliError must propagate")


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