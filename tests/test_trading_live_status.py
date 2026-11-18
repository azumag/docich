"""The LIVE worker status file (L4b-6a): allow-list, 0600, atomic replace, no secrets.

Every test writes inside the workspace's state dir (the shared LIVE ``Workspace`` helper),
uses a synthetic clock and asserts the exact bytes/keys/mode it asked for.  No network.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from docich.trading.live_worker import status as status_module
from docich.trading.live_worker.status import (ALLOWED_KEYS, MAX_INTERVAL_MS,
                                               MIN_INTERVAL_MS, PHASES, STATUS_FILENAME,
                                               status_path, write_status)

from test_trading_live_support import Workspace, block_network

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


@pytest.fixture
def ws():
    workspace = Workspace()
    workspace.layout.root.mkdir(mode=0o700)
    yield workspace
    workspace.cleanup()


def read(ws) -> dict:
    return json.loads((ws.layout.root / STATUS_FILENAME).read_text(encoding="utf-8"))


def test_the_path_is_the_layout_root_plus_the_filename(ws):
    assert status_path(ws.state) == ws.layout.root / STATUS_FILENAME


# ---- s1: the allow-list drops unknown keys and non str/int/list[str]/None values ---------
def test_the_allow_list_drops_unknown_keys_and_wrong_types(ws):
    ok = write_status(ws.state, {
        "phase": "running",
        "cycle": 4,
        "mode": None,
        "blocks": ["rules_unavailable", "balance_unavailable"],
        "api_key": "SENTINEL-API-KEY",
        "balance": "12345",
        "unknown": "dropped",
        "reason": {"nested": "dict"},            # valid key, wrong type -> dropped
        "fresh_symbols": ["OAS/JPY", 3],          # list with a non-str -> dropped
        "exit_code": True,                        # bool is not an int here -> dropped
        "pid": 4242,
    }, now=NOW)

    assert ok is True
    data = read(ws)
    assert set(data) == {"schema_version", "written_at", "phase", "cycle", "mode",
                         "blocks", "pid"}
    assert data["schema_version"] == 1
    assert data["written_at"] == int(NOW)
    assert data["phase"] == "running"
    assert data["cycle"] == 4
    assert data["mode"] is None
    assert data["blocks"] == ["rules_unavailable", "balance_unavailable"]
    assert data["pid"] == 4242
    assert "SENTINEL-API-KEY" not in (ws.layout.root / STATUS_FILENAME).read_text(encoding="utf-8")
    assert set(data) <= ALLOWED_KEYS


def test_generated_fields_win_and_digest_is_truncated_to_12_hex(ws):
    ok = write_status(ws.state, {
        "schema_version": 99, "written_at": 1.0, "phase": "exited",
        "control_state": "on", "control_mode": "trade",
        "policy_set_digest": "0123456789abcdef" + "0" * 48,
        "reason": "done",
    }, now=NOW)
    assert ok is True
    data = read(ws)
    assert data["schema_version"] == 1 and data["written_at"] == int(NOW)
    assert data["control_state"] == "on" and data["control_mode"] == "trade"
    assert data["policy_set_digest"] == "0123456789ab"
    # A non-hex digest is not a digest: dropped rather than truncated.
    assert write_status(ws.state, {"policy_set_digest": "not-hex", "phase": "running"},
                        now=NOW) is True
    assert "policy_set_digest" not in read(ws)
    # The removed revision key is not allow-listed at all.
    assert "code_revision" not in ALLOWED_KEYS
    assert write_status(ws.state, {"code_revision": "a" * 40, "phase": "running"},
                        now=NOW) is True
    assert "code_revision" not in read(ws)
    # Only the two known control states/modes (and None) survive.
    assert write_status(ws.state, {"control_state": "bogus", "control_mode": "monitor"},
                        now=NOW) is True
    assert "control_state" not in read(ws) and "control_mode" not in read(ws)


def test_only_the_known_phases_are_written(ws):
    for phase in PHASES:
        assert write_status(ws.state, {"phase": phase}, now=NOW) is True
        assert read(ws)["phase"] == phase
    assert write_status(ws.state, {"phase": "mystery"}, now=NOW) is True
    assert "phase" not in read(ws)


# ---- s2: mode 0600 from the moment the file exists ---------------------------------------
def test_the_temp_file_is_created_0600_with_excl_and_nofollow(ws, monkeypatch):
    real_open = os.open
    calls = []

    def open_spy(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is None:
            calls.append((str(path), flags, mode))
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(status_module.os, "open", open_spy)
    assert write_status(ws.state, {"phase": "running"}, now=NOW) is True

    temp = [call for call in calls if STATUS_FILENAME in Path(call[0]).name]
    assert len(temp) == 1
    name, flags, mode = temp[0]
    assert Path(name).parent == ws.layout.root
    assert mode == 0o600
    assert flags & os.O_WRONLY and flags & os.O_CREAT
    assert flags & os.O_EXCL and flags & os.O_NOFOLLOW
    assert stat.S_IMODE(os.stat(ws.layout.root / STATUS_FILENAME).st_mode) == 0o600
    assert [p.name for p in ws.layout.root.iterdir() if p.name.startswith(".")] == []


# ---- s3: a failed replace keeps the old file and leaves no temp --------------------------
def test_a_failed_replace_keeps_the_old_file_and_leaves_no_temp(ws, monkeypatch):
    assert write_status(ws.state, {"phase": "running", "cycle": 1}, now=NOW) is True
    target = ws.layout.root / STATUS_FILENAME
    before = target.read_bytes()

    def boom(*args, **kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr(status_module.os, "replace", boom)
    assert write_status(ws.state, {"phase": "exited", "cycle": 2}, now=NOW) is False
    assert target.read_bytes() == before
    assert read(ws)["cycle"] == 1
    assert [p.name for p in ws.layout.root.iterdir() if p.name.startswith(".")] == []


# ---- s4: a symlink at the destination is refused ----------------------------------------
def test_a_symlinked_destination_is_refused(ws):
    target = ws.state / "elsewhere.json"
    target.write_text("untouched", encoding="utf-8")
    link = ws.layout.root / STATUS_FILENAME
    os.symlink(target, link)

    assert write_status(ws.state, {"phase": "running"}, now=NOW) is False
    assert target.read_text(encoding="utf-8") == "untouched"
    assert link.is_symlink()
    assert [p.name for p in ws.layout.root.iterdir() if p.name.startswith(".")] == []


# ---- s5: an unwritable/missing directory returns False, never raises ---------------------
def test_a_missing_root_returns_false_without_raising():
    workspace = Workspace()
    try:
        assert not workspace.layout.root.exists()
        assert write_status(workspace.state, {"phase": "running"}, now=NOW) is False
        assert not workspace.layout.root.exists()
    finally:
        workspace.cleanup()


def test_a_bad_value_or_bad_now_returns_false_without_raising(ws):
    class Bad:
        def __str__(self):
            raise RuntimeError("no")

    assert write_status(ws.state, {"cycle": Bad()}, now=NOW) is True  # dropped, not fatal
    assert "cycle" not in read(ws)
    assert write_status(ws.state, {"phase": "running"}, now=Bad()) is False
    assert write_status(ws.state, None, now=NOW) is True             # not a dict: empty record
    assert read(ws)["schema_version"] == 1


# ---- s6: secrets and balances are never present -----------------------------------------
def test_balances_and_secrets_never_appear(ws):
    ok = write_status(ws.state, {
        "phase": "running",
        "api_key": "SENTINEL-API-KEY",
        "api_secret": "SENTINEL-SECRET",
        "balance": "123456.78",
        "amount": "99",
        "prices": ["1", "2"],
        "note": "SENTINEL-NOTE",
    }, now=NOW)
    assert ok is True
    text = (ws.layout.root / STATUS_FILENAME).read_text(encoding="utf-8")
    data = read(ws)
    assert set(data) == {"schema_version", "written_at", "phase"}
    for secret in ("SENTINEL-API-KEY", "SENTINEL-SECRET", "SENTINEL-NOTE",
                   "123456.78"):
        assert secret not in text


# ---- s7: durability and a failed fsync --------------------------------------------------
def test_status_write_fsyncs_the_file_and_the_directory(ws, monkeypatch):
    target = ws.layout.root / STATUS_FILENAME
    real_fsync = os.fsync
    seen = []

    def fsync_spy(fd):
        mode = os.fstat(fd).st_mode
        seen.append("dir" if stat.S_ISDIR(mode) else "file" if stat.S_ISREG(mode) else "other")
        return real_fsync(fd)

    monkeypatch.setattr(status_module.os, "fsync", fsync_spy)
    assert write_status(ws.state, {"phase": "running", "cycle": 1}, now=NOW) is True

    # One fsync on the temp file (before the atomic replace), one on the directory (after it).
    assert len(seen) >= 2
    assert seen.count("file") >= 1
    assert seen.count("dir") >= 1
    assert read(ws)["phase"] == "running"

    before = target.read_bytes()

    def fsync_fails(fd):
        raise OSError("fsync failed")

    monkeypatch.setattr(status_module.os, "fsync", fsync_fails)
    assert write_status(ws.state, {"phase": "exited", "cycle": 2}, now=NOW) is False
    assert target.read_bytes() == before
    assert read(ws)["cycle"] == 1
    assert [p.name for p in ws.layout.root.iterdir() if p.name.startswith(".")] == []


def test_balance_warnings_is_allow_listed_and_a_non_negative_int(ws):
    assert write_status(ws.state, {"phase": "running", "balance_warnings": 2},
                        now=NOW) is True
    assert read(ws)["balance_warnings"] == 2

    # Negative values are dropped (the cycle never produces one).
    assert write_status(ws.state, {"phase": "running", "balance_warnings": -1},
                        now=NOW) is True
    assert "balance_warnings" not in read(ws)

    # bool is not an int here, exactly like the other integer keys.
    assert write_status(ws.state, {"phase": "running", "balance_warnings": True},
                        now=NOW) is True
    assert "balance_warnings" not in read(ws)
    assert "balance_warnings" in ALLOWED_KEYS


def test_interval_ms_is_allow_listed_only_inside_the_supported_cli_range(ws):
    # Whole milliseconds preserve a fractional interval (600.5 s) exactly, without floats.
    assert write_status(ws.state, {"phase": "running", "interval_ms": 600_500},
                        now=NOW) is True
    assert read(ws)["interval_ms"] == 600_500

    # The CLI range is 30..3600 s; both boundaries are valid.
    for value in (MIN_INTERVAL_MS, MAX_INTERVAL_MS):
        assert write_status(ws.state, {"phase": "running", "interval_ms": value},
                            now=NOW) is True
        assert read(ws)["interval_ms"] == value

    # Out-of-range, non-int and bool values are dropped; the reader then falls back.
    for value in (MIN_INTERVAL_MS - 1, MAX_INTERVAL_MS + 1, 0, -1, 600_000.0, True):
        assert write_status(ws.state, {"phase": "running", "interval_ms": value},
                            now=NOW) is True
        assert "interval_ms" not in read(ws)
    assert "interval_ms" in ALLOWED_KEYS
