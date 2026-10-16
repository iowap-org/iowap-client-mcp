"""Tests for the node-cli runner (T-002) — resolution chain + subprocess contract."""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

import pytest

from iowap_client_mcp import nodecli
from iowap_client_mcp.nodecli import (
    NodeCliError,
    NodeCliTimeoutError,
    find_node_cli,
    resolve_node_cli,
    run_node_cli,
)


def _fake_exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


# ---------------------------------------------------------------------------
# Resolution chain: pin → PATH → candidates
# ---------------------------------------------------------------------------


def test_pin_wins_over_path(fake_home: Path, tmp_path: Path, monkeypatch) -> None:
    pin = _fake_exe(fake_home / "iowap" / "bin" / "node-cli")
    path_cli = _fake_exe(tmp_path / "pathbin" / "node-cli")
    monkeypatch.setenv("PATH", str(path_cli.parent))
    (fake_home / ".relay").mkdir()
    (fake_home / ".relay" / "node.yaml").write_text(
        f'node_cli_path: "{pin}"\n', encoding="utf-8"
    )
    assert find_node_cli(home=fake_home) == str(pin)


def test_stale_pin_falls_through_to_path(
    fake_home: Path, tmp_path: Path, monkeypatch
) -> None:
    (fake_home / ".relay").mkdir()
    (fake_home / ".relay" / "node.yaml").write_text(
        'node_cli_path: "/gone/away/node-cli"\n', encoding="utf-8"
    )
    path_cli = _fake_exe(tmp_path / "pathbin" / "node-cli")
    monkeypatch.setenv("PATH", str(path_cli.parent))
    assert find_node_cli(home=fake_home) == str(path_cli)


def test_indented_pin_never_counts(fake_home: Path, tmp_path: Path, monkeypatch) -> None:
    """An indented node_cli_path inside a capability block must never win."""
    (fake_home / ".relay").mkdir()
    (fake_home / ".relay" / "node.yaml").write_text(
        "capabilities:\n"
        "  - name: x\n"
        "    handler: y\n"
        "    node_cli_path: /evil/inside/cap\n",
        encoding="utf-8",
    )
    path_cli = _fake_exe(tmp_path / "pathbin" / "node-cli")
    monkeypatch.setenv("PATH", str(path_cli.parent))
    assert find_node_cli(home=fake_home) == str(path_cli)


def test_pin_quotes_and_spaces_are_stripped(fake_home: Path, tmp_path: Path) -> None:
    pin = _fake_exe(tmp_path / "iowap" / "bin" / "node-cli")
    (fake_home / ".relay").mkdir()
    (fake_home / ".relay" / "node.yaml").write_text(
        f"node_cli_path:  '{pin}'  \n", encoding="utf-8"
    )
    assert find_node_cli(home=fake_home) == str(pin)


def test_no_pin_no_path_returns_none(fake_home: Path, monkeypatch) -> None:
    monkeypatch.setattr(nodecli.shutil, "which", lambda *_: None)
    assert find_node_cli(home=fake_home) is None


def test_candidate_probed_as_fallback(fake_home: Path, tmp_path: Path, monkeypatch) -> None:
    cand = _fake_exe(tmp_path / "cand" / "node-cli")
    monkeypatch.setattr(nodecli.shutil, "which", lambda *_: None)
    monkeypatch.setattr(
        nodecli, "_candidates", lambda _h: (cand,)
    )
    assert find_node_cli(home=fake_home) == cand


def test_resolve_falls_back_to_bare_name_when_nothing_found(
    fake_home: Path, monkeypatch
) -> None:
    monkeypatch.setattr(nodecli.shutil, "which", lambda *_: None)
    assert resolve_node_cli(home=fake_home) == "node-cli"


# ---------------------------------------------------------------------------
# Runner contract
# ---------------------------------------------------------------------------


def _script(exe_path: Path, body: str) -> Path:
    exe_path.parent.mkdir(parents=True, exist_ok=True)
    exe_path.write_text(body, encoding="utf-8")
    exe_path.chmod(exe_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return exe_path


def test_run_injects_global_json_flag_before_subcommand(
    tmp_path: Path, monkeypatch
) -> None:
    """--json must precede the subcommand (global flags live on the root level)."""
    exe = _script(
        tmp_path / "bin" / "node-cli",
        '#!/bin/sh\nprintf \'{"argv": "%s"}\' "$*"\n',
    )
    monkeypatch.setattr(nodecli, "resolve_node_cli", lambda home=None: str(exe))
    payload = asyncio.run(run_node_cli(["node", "list"], home=tmp_path))
    assert payload["argv"] == "--json node list"


def test_exit_1_with_json_is_valid(tmp_path: Path, monkeypatch) -> None:
    """e.g. `server health` on an unreachable relay: exit 1 + {ok: false, ...}."""
    exe = _script(
        tmp_path / "bin" / "node-cli",
        '#!/bin/sh\necho \'{"ok": false, "error": "Connection refused"}\'\nexit 1\n',
    )
    monkeypatch.setattr(nodecli, "resolve_node_cli", lambda home=None: str(exe))
    payload = asyncio.run(run_node_cli(["server", "health"], ok=(0, 1), home=tmp_path))
    assert payload["ok"] is False


def test_exit_code_outside_ok_raises(tmp_path: Path, monkeypatch) -> None:
    exe = _script(
        tmp_path / "bin" / "node-cli",
        '#!/bin/sh\necho "boom" >&2\nexit 2\n',
    )
    monkeypatch.setattr(nodecli, "resolve_node_cli", lambda home=None: str(exe))
    with pytest.raises(NodeCliError, match="exit 2"):
        asyncio.run(run_node_cli(["node", "list"], home=tmp_path))


def test_non_json_output_raises_with_detail(tmp_path: Path, monkeypatch) -> None:
    exe = _script(tmp_path / "bin" / "node-cli", '#!/bin/sh\necho "not json"\n')
    monkeypatch.setattr(nodecli, "resolve_node_cli", lambda home=None: str(exe))
    with pytest.raises(NodeCliError, match="non-JSON"):
        asyncio.run(run_node_cli(["node", "list"], home=tmp_path))


def test_timeout_kills_and_raises(tmp_path: Path, monkeypatch) -> None:
    exe = _script(tmp_path / "bin" / "node-cli", "#!/bin/sh\nsleep 30\n")
    monkeypatch.setattr(nodecli, "resolve_node_cli", lambda home=None: str(exe))
    with pytest.raises(NodeCliTimeoutError):
        asyncio.run(run_node_cli(["task", "wait"], timeout=0.2, home=tmp_path))
    assert exe.exists()  # script gone with the tmp dir; kill happened via proc.kill()


def test_runner_cwd_is_home_not_repo(tmp_path: Path, monkeypatch) -> None:
    exe = _script(
        tmp_path / "bin" / "node-cli",
        '#!/bin/sh\nprintf \'{"cwd": "%s"}\' "$(pwd)"\n',
    )
    monkeypatch.setattr(nodecli, "resolve_node_cli", lambda home=None: str(exe))
    home = tmp_path / "fakehome"
    home.mkdir()
    payload = asyncio.run(run_node_cli(["docs"], home=home))
    # Fake home must be a subdir of tmp_path; runner must NOT spawn inside the
    # test cwd (the repo) — the nodes/-shadow rule generalized.
    assert payload["cwd"] == str(home)
    assert payload["cwd"] != os.getcwd()


def test_spawn_failure_maps_to_nodecli_error(monkeypatch) -> None:
    """A non-spawnable resolved binary maps FileNotFoundError → NodeCliError.

    Deterministic: resolve to an absolute, guaranteed-missing path (a bare
    name would let subprocess itself resolve it via the ambient PATH).
    """
    monkeypatch.setattr(
        nodecli, "resolve_node_cli", lambda home=None: "/definitely/not/here/node-cli"
    )
    with pytest.raises(NodeCliError, match="not found"):
        asyncio.run(run_node_cli(["node", "list"]))