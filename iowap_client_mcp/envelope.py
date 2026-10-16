"""T-005b Response-Envelope contraction for stage results.

Mirror of ``iowap-node nodes/common/cli/cli_bridge.py::_find_result``
(introduced 2026-10-03, tests ``tests/test_t166_find_result_envelope.py``).

handler_runner passes conforming handler stdout (``{status, result, error}``
plus the optional ``_handler`` debug dict) through VERBATIM as
``stages[].result``, so CLI consumers must unwrap exactly one envelope layer.
Legacy flat results pass through unchanged.
"""

from __future__ import annotations

from typing import Any

__all__ = ["find_first_completed_stage_result"]


def find_first_completed_stage_result(data: dict[str, Any]) -> dict[str, Any] | None:
    """Return the first completed stage's result from a task fetch.

    Unwrap exactly one envelope layer; ``status: "error"`` or a set
    ``error`` yields None (failed work). Legacy flat results pass through
    unchanged. No completed stage → None.
    """
    for stage in data.get("stages", []):
        if stage.get("status") == "completed" and stage.get("result"):
            result = stage["result"]
            is_envelope = isinstance(result, dict) and (
                "_handler" in result or {"status", "result", "error"} <= set(result)
            )
            if is_envelope:
                if result.get("status") == "error" or result.get("error"):
                    return None
                inner = result.get("result")
                return inner if isinstance(inner, dict) else {}
            return result
    return None