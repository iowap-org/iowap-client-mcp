"""Envelope contract tests (T-005b mirror of cli_bridge._find_result)."""

from __future__ import annotations

from iowap_client_mcp.envelope import find_first_completed_stage_result


def test_conformant_envelope_is_unwrapped() -> None:
    data = {
        "stages": [
            {
                "status": "completed",
                "result": {
                    "status": "success",
                    "result": {"answer": 42},
                    "error": None,
                },
            }
        ]
    }
    assert find_first_completed_stage_result(data) == {"answer": 42}


def test_handler_key_with_result_value_yields_inner() -> None:
    data = {
        "stages": [
            {
                "status": "completed",
                "result": {"_handler": {"tool": "x"}, "result": {"a": 1}},
            }
        ]
    }
    assert find_first_completed_stage_result(data) == {"a": 1}


def test_flat_payload_with_handler_debug_is_not_an_envelope() -> None:
    """Live finding (T-004, mc.list.players 2026-10-16): flat payload +
    `_handler` debug dict used to collapse to {} — payload must survive."""
    data = {
        "stages": [
            {
                "status": "completed",
                "result": {
                    "ok": True,
                    "servers": {"lobby": {"online": 0, "max": 20, "players": []}},
                    "_handler": {"stderr": "", "stdout_length": 182, "exit_code": 0},
                },
            }
        ]
    }
    result = find_first_completed_stage_result(data)
    assert result["ok"] is True
    assert result["servers"]["lobby"]["max"] == 20
    assert "_handler" not in result


def test_non_dict_result_passes_through() -> None:
    data = {"stages": [{"status": "completed", "result": ["a", "b"]}]}
    assert find_first_completed_stage_result(data) == ["a", "b"]


def test_error_envelope_yields_none() -> None:
    data = {
        "stages": [
            {
                "status": "completed",
                "result": {
                    "status": "error",
                    "result": None,
                    "error": "handler exited with code 28",
                },
            }
        ]
    }
    assert find_first_completed_stage_result(data) is None


def test_error_key_set_yields_none() -> None:
    data = {
        "stages": [
            {
                "status": "completed",
                "result": {"status": "success", "result": {}, "error": "partial"},
            }
        ]
    }
    assert find_first_completed_stage_result(data) is None


def test_legacy_flat_result_passes_through() -> None:
    data = {"stages": [{"status": "completed", "result": {"rows": [1, 2]}}, ""]}
    assert find_first_completed_stage_result(data) == {"rows": [1, 2]}


def test_inner_non_dict_becomes_empty_dict() -> None:
    """Legacy-conformant envelope with non-dict inner: value passes through.

    (Refinement 2026-10-16: the cli_bridge collapse-to-{} loses data for a
    tool consumer; non-dict inner values are returned as they are.)"""
    data = {
        "stages": [
            {
                "status": "completed",
                "result": {"status": "success", "result": ["flat", "list"], "error": None},
            }
        ]
    }
    assert find_first_completed_stage_result(data) == ["flat", "list"]


def test_no_completed_stage_yields_none() -> None:
    data = {"stages": [{"status": "pending", "result": None}]}
    assert find_first_completed_stage_result(data) is None


def test_first_completed_stage_wins() -> None:
    data = {
        "stages": [
            {"status": "pending", "result": None},
            {"status": "completed", "result": {"v": 1}},
            {"status": "completed", "result": {"v": 2}},
        ]
    }
    assert find_first_completed_stage_result(data) == {"v": 1}


def test_empty_stages_yields_none() -> None:
    assert find_first_completed_stage_result({}) is None
    assert find_first_completed_stage_result({"stages": []}) is None