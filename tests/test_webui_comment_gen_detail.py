"""Comment-generation indicator detail display (#1182).

The soviet_now comment worker writes tmp/state/.comment_gen_state.json
(model/preview/count/attempt/max_retry/batch_hash/mode/owner_pid/ts);
the WebUI indicator surfaces those fields and keeps a longer stale
window while the owner PID is alive. Synthetic soren roots only.
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import overlay_queue  # noqa: E402
from docich import webui  # noqa: E402


@pytest.fixture
def soren_root(tmp_path, monkeypatch):
    root = tmp_path / "soren"
    (root / "tmp/state").mkdir(parents=True)
    monkeypatch.delenv("COMMENT_GEN_DETAIL_FILE", raising=False)
    monkeypatch.delenv("EVENT_OVERLAY_COMMENT_GEN_DETAIL_STATE", raising=False)
    monkeypatch.delenv("COMMENT_GEN_STATE_FILE", raising=False)
    monkeypatch.delenv("EVENT_OVERLAY_COMMENT_GEN_ALIVE_STALE_SEC", raising=False)
    monkeypatch.delenv("EVENT_OVERLAY_COMMENT_GEN_DEAD_STALE_SEC", raising=False)
    monkeypatch.delenv("EVENT_OVERLAY_COMMENT_GEN_STALE_SEC", raising=False)
    return root


def _write_state(root: Path, ts: int) -> None:
    (root / "tmp/state/.comment_gen_state").write_text(
        f"generating:comment:{ts}", encoding="utf-8"
    )


def _write_detail(root: Path, pid: int, ts: int) -> None:
    (root / "tmp/state/.comment_gen_state.json").write_text(
        json.dumps(
            {
                "ts": ts,
                "owner_pid": pid,
                "model": "amd:DeepSeek-V4-Flash",
                "preview": "こんにちは世界",
                "count": 3,
                "attempt": 1,
                "max_retry": 3,
                "batch_hash": "abc123def456",
                "mode": "main",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _comment_indicators(root: Path, now: int):
    return [
        g
        for g in webui._get_gen_indicators(root, now)
        if g["key"] == "comment"
    ]


def test_detail_fields_surfaced_and_live_pid_extends_window(soren_root):
    now = int(time.time())
    _write_state(soren_root, now - 200)
    _write_detail(soren_root, os.getpid(), now - 200)
    found = _comment_indicators(soren_root, now)
    assert len(found) == 1
    gen = found[0]
    # 200s exceeds the dead window (90s): only a live owner keeps it fresh.
    assert gen["owner_alive"] is True
    assert gen["stale_sec"] == 300
    assert gen["fresh"] is True
    assert gen["model"] == "amd:DeepSeek-V4-Flash"
    assert gen["preview"] == "こんにちは世界"
    assert gen["count"] == 3
    assert gen["attempt"] == 1
    assert gen["max_retry"] == 3
    assert gen["batch_hash"] == "abc123def456"
    assert gen["mode"] == "main"


def test_dead_pid_falls_back_to_short_window(soren_root):
    now = int(time.time())
    _write_state(soren_root, now - 200)
    _write_detail(soren_root, 2**30, now - 200)  # no such PID: dead
    found = _comment_indicators(soren_root, now)
    assert len(found) == 1
    gen = found[0]
    assert gen["owner_alive"] is False
    assert gen["stale_sec"] == 90
    assert gen["fresh"] is False


def test_no_detail_file_keeps_legacy_behavior(soren_root):
    now = int(time.time())
    _write_state(soren_root, now - 10)
    found = _comment_indicators(soren_root, now)
    assert len(found) == 1
    gen = found[0]
    assert gen["model"] == "" and gen["preview"] == ""
    assert gen["count"] == 0 and gen["attempt"] == 0
    assert gen["owner_pid"] == 0 and gen["owner_alive"] is False
    assert gen["stale_sec"] == 90
    assert gen["fresh"] is True


def test_malformed_detail_json_degrades_gracefully(soren_root):
    now = int(time.time())
    _write_state(soren_root, now - 10)
    (soren_root / "tmp/state/.comment_gen_state.json").write_text(
        "{not json", encoding="utf-8"
    )
    found = _comment_indicators(soren_root, now)
    assert len(found) == 1
    assert found[0]["fresh"] is True
    assert found[0]["model"] == ""


def test_detail_ts_overrides_state_line_ts(soren_root):
    now = int(time.time())
    _write_state(soren_root, now - 500)
    _write_detail(soren_root, os.getpid(), now - 5)
    found = _comment_indicators(soren_root, now)
    assert len(found) == 1
    assert found[0]["ts"] == now - 5
    assert found[0]["fresh"] is True


def test_detail_path_env_override(soren_root, monkeypatch, tmp_path):
    custom = tmp_path / "custom-detail.json"
    custom.write_text(
        json.dumps({"ts": int(time.time()), "owner_pid": os.getpid(),
                    "model": "m", "preview": "p", "count": 1,
                    "attempt": 1, "max_retry": 1,
                    "batch_hash": "h", "mode": "main"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("COMMENT_GEN_DETAIL_FILE", str(custom))
    assert overlay_queue.comment_gen_detail_path(soren_root) == custom
    now = int(time.time())
    _write_state(soren_root, now - 5)
    found = _comment_indicators(soren_root, now)
    assert len(found) == 1
    assert found[0]["model"] == "m"


def test_detail_path_defaults_next_to_state_file(soren_root):
    assert overlay_queue.comment_gen_detail_path(soren_root) == (
        soren_root / "tmp/state/.comment_gen_state.json"
    )
