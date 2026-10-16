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


def test_handler_key_triggers_envelope_unwrap() -> None:
    data = {
        "stages": [
            {
                "status": "completed",
                "result": {"_handler": {"tool": "x"}, "result": {"a": 1}},
            }
        ]
    }
    assert find_first_completed_stage_result(data) == {"a": 1}


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
    data = {
        "stages": [
            {
                "status": "completed",
                "result": {"status": "success", "result": ["flat", "list"], "error": None},
            }
        ]
    }
    assert find_first_completed_stage_result(data) == {}


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