"""node-cli subprocess runner — the ONLY relay access path of this toolkit.

Mirrors the contract of the existing node-cli consumers
(`iowap-hermes-integration/dashboard/plugin_api.py`, node-cli resolution
contract test suite) plus the T-005b envelope handling of the node side:

- Binary resolution: ``~/.relay/node.yaml`` top-level ``node_cli_path`` pin
  (node-cli self-stamps it, iowap-node 2.3.20 / T-226) → ``PATH`` → known
  install locations. Column-0 keys only — an indented ``node_cli_path``
  inside a capability block must never win.
- Invocation: ``<cli> --json <args>`` with ``--json`` as global flag BEFORE
  the subcommand, cwd = HOME (never an iowap checkout — a local ``nodes/``
  directory shadows the installed package).
- Exit semantics: exit codes 0 and 1 both count as "ran" when stdout is
  parseable JSON (e.g. ``server health`` exits 1 with ``{"ok": false, ...}``
  on an unreachable relay — the payload decides, not the exit code).

This module never touches tokens or speaks HTTP to the relay; node-cli owns
auth, refresh and error handling.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Any

__all__ = [
    "NodeCliError",
    "NodeCliTimeoutError",
    "find_node_cli",
    "resolve_node_cli",
    "run_node_cli",
]

_PIN_RE = re.compile(r"^node_cli_path:(.*)$", re.MULTILINE)


class NodeCliError(RuntimeError):
    """node-cli could not be run or produced unusable output."""


class NodeCliTimeoutError(NodeCliError):
    """node-cli did not finish within the timeout; the process was killed."""


def _candidates(home: Path) -> tuple[str, ...]:
    """Known install locations probed at call time (mirrors plugin_api)."""
    return (
        str(home / ".hermes/hermes-agent/venv/bin/node-cli"),
        str(home / ".local/bin/node-cli"),
        "/usr/local/bin/node-cli",
    )


def _node_yaml_cli_pin(home: Path) -> str | None:
    """Top-level ``node_cli_path`` from ``<home>/.relay/node.yaml`` (read-only).

    Only a column-0 key counts (indented keys inside capability blocks never
    match); a missing/stale pin returns None and the chain falls through.
    """
    try:
        raw = (home / ".relay" / "node.yaml").read_text(encoding="utf-8")
    except OSError:
        return None
    match = _PIN_RE.search(raw)
    if not match:
        return None
    value = match.group(1).strip().strip("'\"")
    return value or None


def find_node_cli(home: Path | None = None) -> str | None:
    """Locate node-cli: node.yaml pin → PATH → known install locations."""
    home = Path.home() if home is None else Path(home)
    pin = _node_yaml_cli_pin(home)
    if pin:
        candidate = Path(pin).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    found = shutil.which("node-cli")
    if found:
        return found
    for candidate in _candidates(home):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def resolve_node_cli(home: Path | None = None) -> str:
    found = find_node_cli(home)
    if found:
        return found
    return "node-cli"  # let the spawn fail with a clear FileNotFoundError


async def run_node_cli(
    args: list[str],
    timeout: float = 90.0,
    ok: tuple[int, ...] = (0, 1),
    home: Path | None = None,
) -> dict[str, Any]:
    """Run ``node-cli --json <args>`` and return the parsed JSON payload.

    Raises:
        NodeCliError: binary missing, exit code outside ``ok`` without
            parseable JSON, or non-JSON stdout (stderr tail is attached).
        NodeCliTimeoutError: the CLI did not finish within ``timeout``.
    """
    home_dir = Path.home() if home is None else Path(home)
    cmd = [resolve_node_cli(home_dir), "--json", *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(home_dir),  # never an iowap checkout (nodes/ shadow pitfall)
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise NodeCliError(
            f"node-cli not found on this host (tried pin → PATH → candidates): {exc}"
        ) from exc

    try:
        body, errb = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:  # py313+: asyncio.TimeoutError is an alias of builtin
        proc.kill()
        await proc.wait()
        raise NodeCliTimeoutError(
            f"node-cli {' '.join(args)} timed out after {timeout}s"
        ) from None

    stdout = body.decode("utf-8", "replace")
    stderr = errb.decode("utf-8", "replace")
    detail = (stderr or stdout).strip()

    if proc.returncode not in ok:
        raise NodeCliError(
            f"node-cli exit {proc.returncode} ({' '.join(args)}): {detail[:300]}"
        )
    if not stdout.strip():
        raise NodeCliError(
            f"node-cli produced no JSON output (exit {proc.returncode}): {detail[:300]}"
        )
    try:
        return _parse(stdout)
    except ValueError as exc:
        raise NodeCliError(
            f"node-cli produced non-JSON output (exit {proc.returncode}): "
            f"{stderr.strip()[:300] or stdout.strip()[:300]}"
        ) from exc


def _parse(raw: str) -> Any:
    import json

    return json.loads(raw.strip())