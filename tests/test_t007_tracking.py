"""T-007: self-contained task tracking + T-227 semantics in tool texts."""

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from iowap_client_mcp import tools, tracking
from iowap_client_mcp.nodecli import NodeCliError


@pytest.fixture(autouse=True)
def hermes_home(monkeypatch, tmp_path):
    """Send tracking to an isolated home instead of the real ~/.hermes."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def test_merge_writes_session_origin_meta():
    # T-016: session_id rides in the meta — the desktop watcher uses it as
    # composer target (posts back into the submitting session)
    assert tracking.merge("s1", capability="code.ai", delegated=False, session_id="20261025_165600_6cb063") == {"tracked": True}
    meta = _read_store()["meta"]["s1"]
    assert meta["session_id"] == "20261025_165600_6cb063"
    # omit → explicit None (schema parity with bridge/backend writers)
    assert tracking.merge("s2", capability="code.ai", delegated=False) == {"tracked": True}
    assert _read_store()["meta"]["s2"]["session_id"] is None


def _read_store() -> dict:
    ff = Path(os.environ["HERMES_HOME"]) / "plugin-data" / "iowap" / "task-track-iowap.json"
    with ff.open() as fh:
        return json.load(fh)


# --- schema contract (identical to bridge/plugin_api store writers) ----------

def test_merge_write_schema_matches_bridge():
    ret = tracking.merge(
        "task_a", capability="agent.ai", name="job", owner="NODE23A4", delegated=True
    )
    assert ret["tracked"] is True
    row = _read_store()["meta"]["task_a"]
    assert set(row) == {
        "capability", "name", "owner", "delegated", "submitted_at", "session_id"
    }
    assert row == {
        "capability": "agent.ai",
        "name": "job",
        "owner": "NODE23A4",
        "delegated": True,
        "submitted_at": row["submitted_at"],
        "session_id": None,
    }
    assert isinstance(row["submitted_at"], float)


def test_newest_first_and_cap_50():
    for i in range(55):
        tracking.merge(f"task_{i:02}", capability="x.ai", delegated=False)
    store = _read_store()
    assert len(store["ids"]) == 50
    assert store["ids"][0] == "task_54"
    assert "task_00" not in store["ids"]
    assert set(store["meta"]) == set(store["ids"])  # pruned with the ids


def test_remerge_updates_meta_without_rotating_order():
    tracking.merge("task_a", capability="x.ai", delegated=False)
    tracking.merge("task_b", capability="x.ai", delegated=False)
    ret = tracking.merge("task_a", capability="y.ai", delegated=True)
    store = _read_store()
    assert store["ids"] == ["task_b", "task_a"]
    assert store["meta"]["task_a"]["capability"] == "y.ai"
    assert store["meta"]["task_a"]["delegated"] is True
    assert ret == {"tracked": False}


def test_merge_is_flock_safe_against_concurrent_writers():
    def hammer(prefix: str) -> None:
        for i in range(20):
            tracking.merge(f"{prefix}_{i}", capability="x.ai", delegated=False)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(hammer, ["a", "b", "c"]))

    store = _read_store()
    assert len(store["ids"]) == 50  # 60 written, capped, no torn writes / losses
    assert set(store["meta"]) == set(store["ids"])


def test_merge_is_best_effort(monkeypatch):
    monkeypatch.setenv("HERMES_HOME", "/dev/null")  # mkdir/store write must fail
    ret = tracking.merge("task_x", capability="x.ai", delegated=False)
    assert ret["tracked"] is False
    assert "reason" in ret


# --- submit wiring -----------------------------------------------------------

def test_task_submit_tracks_full_metadata(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_submit(args, timeout):
        assert args[:3] == ["task", "submit", "--stage"]
        return {"task_id": "task_z", "status": "pending"}

    async def fake_track(task_id, **meta):
        captured.update(meta)
        captured["task_id"] = task_id
        return {"tracked": True}

    monkeypatch.setattr(tools, "run_node_cli", fake_submit)
    monkeypatch.setattr(tools, "_track_task", fake_track)
    result = asyncio.run(
        tools.task_submit(
            "agent.ai", {"task": "hi"}, owner_node_id="NODE23A4", name="demo"
        )
    )
    assert captured == {
        "task_id": "task_z",
        "capability": "agent.ai",
        "name": "demo",
        "owner": "NODE23A4",
        "delegated": True,
        "session_id": None,
    }
    assert result["task_id"] == "task_z"
    assert result["tracking"] == {"tracked": True}


def test_submit_works_without_external_bridge(monkeypatch):
    """The point of T-007: no iowap-task binary involved anywhere."""
    import inspect

    assert "iowap-task" not in inspect.getsource(tools._track_task)

    async def fake_submit(args, timeout):
        return {"task_id": "task_q", "status": "pending"}

    monkeypatch.setattr(tools, "run_node_cli", fake_submit)
    result = asyncio.run(tools.task_submit("x.ai", {"task": "{}"}, name=None))
    store = _read_store()
    assert "task_q" in store["ids"]
    row = store["meta"]["task_q"]
    assert row["capability"] == "x.ai"
    assert row["delegated"] is False
    assert row["name"] is None and row["owner"] is None
    assert result["tracking"] == {"tracked": True}


def test_track_task_signature_covers_metadata():
    import inspect

    params = inspect.signature(tools._track_task).parameters
    assert {"task_id", "capability", "name", "owner", "delegated"} <= set(params)


def _listed_tools() -> list[Any]:
    from iowap_client_mcp.mcp_server import build_server

    return asyncio.run(build_server().list_tools())


def test_tool_schema_has_name_param():
    schema = {t.name: t.input_schema for t in _listed_tools()}
    assert set(schema["task_submit"]["properties"]) >= {
        "capability", "payload", "name", "owner_node_id", "wait_seconds",
    }


# --- T-227 semantics in tool texts -------------------------------------------

def test_submit_docstring_post_t227():
    doc = tools.task_submit.__doc__
    assert "only by the owner node" not in doc
    assert "sender" in doc and "admin" in doc


def test_result_docstring_post_t227():
    doc = tools.task_result.__doc__
    assert "only by the owner node" not in doc
    assert "sender" in doc


def test_not_found_hint_mentions_sender(monkeypatch):
    async def boom(args, timeout):
        raise NodeCliError("task not found: task_x")

    monkeypatch.setattr(tools, "run_node_cli", boom)
    result = asyncio.run(tools.task_result("task_x"))
    assert result["status"] == "not_found"
    assert "sender" in result["hint"]
    assert "owner node only" not in result["hint"]


def test_tool_descriptions_post_t227():
    descs = {t.name: t.description for t in _listed_tools()}
    assert "owner node only" not in descs["task_result"]
    assert "sender" in (descs["task_submit"] + descs["task_result"])