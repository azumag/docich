"""Offline narration contract for the ぷよぷよクローン corner (Issue #698).

No VM, no Twitch; sinks are replaced by in-memory fakes like the doushi/yoso
narration tests.
"""
from __future__ import annotations

from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich import puyo_narration as pn  # noqa: E402


def entry(**kw):
    base = {
        "ruleset_id": "x", "started_at": "2026-10-18T01:00:00+00:00",
        "ended_at": "2026-10-18T01:02:03+00:00", "outcome": "user_win",
        "phase": "END", "round": 1, "frames": 12, "actions": 34,
        "pairs_spawned": 6, "user_score": 1200, "ai_score": 900,
        "winner": "user", "failure_reason": None, "participants": ["u1", "u2"],
    }
    base.update(kw)
    return base


def test_start_invitation_is_fixed_and_bounded():
    text = pn.start_speech()
    assert "!puyo join" in text
    assert len(text) <= pn.MAX_SPEECH
    assert pn.result_text(entry(winner="draw"))[:10] != text[:10]


def test_result_text_covers_winners_and_timeout_only():
    assert "プレイヤーの勝ち" in pn.result_text(entry(winner="user"))
    assert "CPUの勝ち" in pn.result_text(entry(winner="ai"))
    assert "引き分け" in pn.result_text(entry(winner="draw"))
    assert "表示時間" in pn.result_text(entry(winner=None, outcome="timeout"))
    for nothing in (
        entry(winner=None, outcome="completed"),
        entry(winner=None),
        None,
        "not-a-dict",
    ):
        assert pn.result_text(nothing) is None
    long_scores = entry(user_score=-5, ai_score=10 ** 12)
    assert pn.result_text(long_scores) == pn.result_text(long_scores)[:pn.MAX_SPEECH]
    assert "プレイヤー0点" in pn.result_text(long_scores)


def test_announce_publishes_the_game_event_and_gates_speech(tmp_path, monkeypatch):
    sent = []
    speeches = []

    def fake_root(_g):
        return tmp_path

    monkeypatch.setattr(pn, "resolve_soren_root", fake_root)

    def batch(g, text, **kwargs):
        sent.append(kwargs)
        return "sent"

    g = SimpleNamespace(state_dir="/nonexistent-soren-root")
    result = pn.announce(
        g, "ぷよぷよ対戦開始", "参加は !puyo join", request_id="req-1",
        audio_enabled=True, overlay=lambda root, ev: sent.append(ev) or "sent",
        speech=lambda *a, **kw: speeches.append((a, kw)) or "sent",
        now=lambda: 1730000000.0, speaker="announcer",
    )
    assert result == {"overlay": "sent", "speech": "sent"}
    event = sent[0]
    assert event["category"] == "game" and event["source_id"] == "puyo:req-1"
    assert event["ts"] == 1730000000
    assert "!puyo join" in speeches[0][0][1]

    # audio off → speech stays skipped, overlay goes out anyway
    result = pn.announce(
        g, "t", "b", request_id="req-2",
        overlay=lambda root, ev: "sent",
        speech=lambda *a, **kw: pytest.fail("no speech without the opt-in"),
    )
    assert result == {"overlay": "sent", "speech": "skipped"}

    # sink failures are fixed statuses, never exceptions
    result = pn.announce(
        g, "t", "b", audio_enabled=True,
        overlay=lambda root, ev: (_ for _ in ()).throw(RuntimeError("private")),
        speech=lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("private")),
    )
    assert result == {"overlay": "failed", "speech": "failed"}


def test_load_result_returns_only_entries_from_the_current_slot(tmp_path):
    import json

    state_dir = tmp_path / "s"
    state_dir.mkdir()
    ledger = {"schema": 1, "entries": [
        entry(ended_at="2026-01-01T00:00:00+00:00", winner="ai"),
        entry(ended_at="2026-10-18T01:02:03+00:00", winner="user"),
        entry(ended_at="garbage"),
    ]}
    (state_dir / pn.LEDGER_FILE).write_text(json.dumps(ledger), encoding="utf-8")
    g = SimpleNamespace(state_dir=state_dir)

    now = time.time()
    started = now - 200  # slot opened 200 s ago
    found = pn.load_result(g, started)
    # the 2026-01-01 entry is too old; the garbage one never parses; the slot
    # entry timestamp is decades behind "now" → nothing belongs to this slot
    assert found is None

    fresh = {"schema": 1, "entries": [
        entry(ended_at=_iso(now - 60), winner="user"),
        entry(ended_at=_iso(now - 300), winner="ai"),
    ]}
    (state_dir / pn.LEDGER_FILE).write_text(json.dumps(fresh), encoding="utf-8")
    found = pn.load_result(g, started)
    assert isinstance(found, dict) and found["winner"] == "user"
    assert pn.result_text(found).startswith(pn.RESULT_HEAD)

    # missing/corrupt ledger → None, never a guess
    (state_dir / pn.LEDGER_FILE).unlink()
    assert pn.load_result(g, started) is None
    (state_dir / pn.LEDGER_FILE).write_text("{broken", encoding="utf-8")
    assert pn.load_result(g, started) is None
    assert pn.load_result(g, "not-a-clock") is None


def _iso(ts: float) -> str:
    import datetime as dt

    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds")


def test_announce_result_requires_a_verifiable_entry():
    sent = []
    pn.announce_result(
        SimpleNamespace(state_dir="/nonexistent"), None,
        request_id="r", overlay=lambda *a: sent.append(a) or "sent",
    )
    assert sent == []  # nothing verifiable → nothing announced
