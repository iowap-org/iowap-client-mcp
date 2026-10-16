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


def find_first_completed_stage_result(data: dict[str, Any]) -> dict[str, Any] | Any:
    """Return the first completed stage's result value.

    Unwrap conformance: a result carrying the envelope triple
    (``status`` + ``result`` keys) is unwrapped exactly one layer; envelope
    ``status: "error"`` or a set ``error`` yields None. Legacy flat results
    pass through unchanged.

    Refinement over ``cli_bridge._find_result`` (live finding 2026-10-16,
    ``mc.list.players``): a FLAT payload that merely carries the ``_handler``
    debug dict is not an envelope — it used to collapse to ``{}``. It is
    returned as the payload (debug key stripped). If ``_handler`` is present
    together with a ``result`` key (no ``status``), the ``result`` value is
    returned as the inner value.
    """
    for stage in data.get("stages", []):
        if stage.get("status") == "completed" and stage.get("result"):
            result = stage["result"]
            if not isinstance(result, dict):
                return result
            if {"status", "result"} <= set(result):
                if result.get("status") == "error" or result.get("error"):
                    return None
                return result.get("result")
            if "_handler" in result:
                if "result" in result:
                    return result["result"]
                return {k: v for k, v in result.items() if k != "_handler"}
            return result
    return None