"""Task-track store merge — self-contained (T-007).

Maintains the SAME flock-protected store the desktop plugin backend
(``plugin_api._track_store_merge``) and the session bridge
(iowap-hermes-integration ``tools/iowap-task``) write:

    {"ids": [newest-first …max 50], "meta": {task_id: {...}}}

with row metadata ``{capability, name, owner, delegated, submitted_at}``
(submitted_at = wall-clock epoch float; the backend prunes
delegated-stale rows after 24h on it).

Do not diverge from those writers — the backend reader is
schema-sensitive. Tracking is best-effort by contract: a merge failure
returns ``{"tracked": false, "reason": …}`` and never breaks a submit.

POSIX-only feature: locking uses ``fcntl`` (Linux/macOS). On platforms
without it the module still imports (lazy import inside ``merge``) and
tracking degrades to ``{"tracked": false}`` — submits are unaffected.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

TRACK_IDS_MAX = 50


def hermes_home() -> Path:
    """Hermes home dir — HERMES_HOME env wins, otherwise ~/.hermes."""
    env_home = os.environ.get("HERMES_HOME", "").strip()
    return Path(env_home) if env_home else Path.home() / ".hermes"


def track_file() -> Path:
    return hermes_home() / "plugin-data" / "iowap" / "task-track-iowap.json"


def merge(
    task_id: str,
    *,
    capability: str,
    name: str | None = None,
    owner: str | None = None,
    delegated: bool,
) -> dict[str, Any]:
    """Add/refresh one tracked id + metadata under an exclusive flock.

    Identical locking and write shape as the bridge and the backend.
    Returns ``{"tracked": bool}`` (bool = id newly added) plus ``reason``
    on failure — best-effort, never raises.
    """
    meta: dict[str, Any] = {
        "capability": capability,
        "name": name,
        "owner": owner,
        "delegated": delegated,
        "submitted_at": time.time(),
    }
    try:
        import fcntl  # lazy: POSIX-only (degrades to tracked:false without it)

        ff = track_file()
        ff.parent.mkdir(parents=True, exist_ok=True)
        with ff.open("a+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                fh.seek(0)
                try:
                    raw = json.load(fh)
                except Exception:  # noqa: BLE001 — unreadable store = empty (backend parity)
                    raw = {}
                ids = [t for t in (raw.get("ids") or []) if isinstance(t, str)]
                meta_map = (
                    raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
                )
                added = task_id not in ids
                if added:
                    ids.insert(0, task_id)
                    ids = ids[:TRACK_IDS_MAX]
                merged_meta = {**meta_map, task_id: {**meta_map.get(task_id, {}), **meta}}
                live = set(ids)
                meta_map = {k: v for k, v in merged_meta.items() if k in live}
                fh.seek(0)
                fh.truncate()
                json.dump({"ids": ids, "meta": meta_map}, fh)
                # second truncate is ESSENTIAL: a shorter dump must not leave
                # trailing garbage from the previous content (proved by the
                # concurrency test — the next reader would crash and reset).
                fh.truncate()
                return {"tracked": added}
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception as exc:  # noqa: BLE001 — best-effort contract: tracking never breaks a submit
        return {"tracked": False, "reason": f"{type(exc).__name__}: {exc}"}