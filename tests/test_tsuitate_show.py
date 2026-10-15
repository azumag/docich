"""The 衝立将棋 感想戦 show: kifu payload, pacing and the view projection."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich import shogi_board as sb  # noqa: E402
from docich import tsuitate_show as show  # noqa: E402
from docich import tsuitate_view as view  # noqa: E402

SEQUENCE = [
    {"usi": "7g7f", "by": "sente", "ms": 120},
    {"usi": "3c3d", "by": "gote", "ms": 300},
    {"usi": "8h2b+", "by": "sente", "ms": 900},
    {"usi": "3a2b", "by": "gote", "ms": 1500},
    {"usi": "B*5b", "by": "sente", "ms": 4200},
]


def _kifu():
    annotated = sb.annotate(SEQUENCE)
    assert len(annotated) == 5
    return annotated


def _payload(**overrides):
    payload = show.build_show(
        _kifu(), color="sente", opponent="kakuren_bot",
        result={"outcome": "win", "reason": "checkmate", "moveNumber": 5},
        request_id="req-1", runtime_id="g4-a1b2c3d4", generation=4)
    payload.update(overrides)
    return payload


def test_chunk_ranges_cover_every_move_with_a_bounded_count():
    assert show.chunk_ranges(0) == []
    assert show.chunk_ranges(3) == [(1, 3)]
    assert show.chunk_ranges(12) == [(1, 6), (7, 12)]
    ranges = show.chunk_ranges(94)
    assert ranges[0][0] == 1 and ranges[-1][1] == 94
    assert len(ranges) <= show.MAX_CHUNKS + 1  # bounded, and no gaps
    for (_, end), (start, _) in zip(ranges, ranges[1:]):
        assert start == end + 1
    with pytest.raises(show.ShowError):
        show.chunk_ranges(-1)


def test_fallback_chunk_text_reads_only_reviewed_facts():
    text = show.fallback_chunk_text(_kifu(), 1, 5)

    assert text.startswith("1手目から5手目です。")
    assert "▲7六歩" in text and "△3四歩" in text
    assert "角を取ります" in text or "角成" in text
    assert "4秒考えました" in text  # 4200ms is the longest think in the stretch
    assert len(text) <= show.MAX_CHUNK_SPEECH
    assert show.fallback_chunk_text(_kifu(), 9, 12) == ""


def test_chunk_facts_are_the_models_only_input():
    facts = show.chunk_facts(_kifu(), 1, 5)

    assert "対象: 1手目から5手目まで" in facts
    assert "指し手: ▲7六歩、△3四歩、▲2二角成、△同銀、▲5二角打" in facts
    assert "長考: 5手目4秒" in facts
    assert "board" not in facts and "finalSfen" not in facts


def test_build_show_is_bounded_and_carries_the_board_per_move():
    payload = _payload()

    assert payload["move_count"] == 5 and payload["index"] == 0
    assert payload["done"] is False
    assert payload["moves"][2]["notation"] == "▲2二角成"
    assert all(len(move["board"]) == 162 for move in payload["moves"])
    assert payload["runtime_id"] == "g4-a1b2c3d4" and payload["generation"] == 4
    assert payload["result"]["outcome"] == "win"
    with pytest.raises(show.ShowError):
        show.build_show([], color="sente")
    with pytest.raises(show.ShowError):
        show.build_show(_kifu() * (show.MAX_SHOW_MOVES + 1), color="sente")


def test_write_and_read_show_are_bounded_and_runtime_pinned(tmp_path):
    payload = _payload()
    show.write_show(tmp_path, payload, index=3)

    read = show.read_show(tmp_path, runtime_id="g4-a1b2c3d4", generation=4)
    assert read is not None and read["index"] == 3
    # Another generation, another request or a missing file is never rendered.
    assert show.read_show(tmp_path, runtime_id="g4-other", generation=4) is None
    assert show.read_show(tmp_path, runtime_id="g4-a1b2c3d4", generation=5) is None
    assert show.read_show(tmp_path, runtime_id="g4-a1b2c3d4", generation=4,
                          request_id="req-2") is None
    assert show.read_show(tmp_path / "missing") is None

    # Corrupt, oversized and drifted payloads fail closed.
    path = tmp_path / show.SHOW_FILENAME
    path.write_bytes(b"{not json")
    assert show.read_show(tmp_path) is None
    path.write_bytes(b"x" * (show.MAX_SHOW_BYTES + 1))
    assert show.read_show(tmp_path) is None
    path.write_text(json.dumps({"schema": show.SHOW_SCHEMA, "kind": "other"}), encoding="utf-8")
    assert show.read_show(tmp_path) is None


def test_write_show_is_atomic_and_never_leaves_a_temp_file(tmp_path):
    payload = _payload()
    show.write_show(tmp_path, payload, index=1)
    show.write_show(tmp_path, payload, index=2)

    assert [path.name for path in tmp_path.iterdir()] == [show.SHOW_FILENAME]
    assert show.read_show(tmp_path)["index"] == 2
    with pytest.raises(show.ShowError):
        show.write_show(tmp_path / "missing", payload)


def test_show_view_projects_only_bounded_fields():
    payload = _payload(commentary={"1": "立ち上がりです。", "7": "x" * 500})
    payload["index"] = 4
    projection = show.show_view(payload)

    assert projection["active"] is True and projection["index"] == 4
    assert projection["move_count"] == 5
    assert projection["opponent"] == "kakuren_bot"
    assert projection["result"]["outcome"] == "win"
    assert "moves" not in projection  # the kifu itself is served separately
    assert show.show_view(None) is None
    assert show.show_view({"index": "x"})["index"] == 0


def test_step_plan_paces_each_chunk_and_falls_back_for_missing_text():
    payload = _payload(commentary={"1": "立ち上がりです。"})
    plan = show.step_plan(payload, step_seconds=2.0)

    assert plan[0]["start"] == 1 and plan[0]["end"] == 5
    assert plan[0]["text"] == "立ち上がりです。"
    assert plan[0]["at"] == 0.0
    assert show.step_plan({"moves": []}) == []


# --- view projection -------------------------------------------------------

def test_status_projection_serves_the_review_for_this_runtime(tmp_path, monkeypatch):
    payload = _payload()
    show.write_show(tmp_path, payload, index=2)
    monkeypatch.setattr(view, "call_beta_control", lambda *_: {
        "state": "finished", "runId": "req-1", "gameId": "fixtureGame01",
        "brainVersion": "v", "completedGames": 1, "reservedGames": 1,
        "stopRequested": False, "readyForNextRun": True,
        "playerView": None, "gameResult": None,
    })

    projected = view.status_projection("g4-a1b2c3d4", 4, "lease", state_dir=tmp_path)
    assert projected["review"]["active"] is True
    assert projected["review"]["index"] == 2

    # Another runtime never sees this show, and no state dir means no review.
    other = view.status_projection("g9-other", 4, "lease", state_dir=tmp_path)
    assert other["review"] is None
    assert view.status_projection("g4-a1b2c3d4", 4, "lease")["review"] is None


def test_view_page_renders_the_board_and_the_kifu():
    html = view.HTML

    assert 'id="kifu"' in html
    assert 'fetch("/api/tsuitate/kifu"' in html
    assert "class=\"board\"" in html and "class=\"moves\"" in html
    assert "renderKifu" in html and "currentComment" in html
    # The kifu only appears when the corner published one for this runtime.
    assert 'show&&show.active' in html
    # The live board is still never sent to the browser outside the show.
    assert "yourPieces" not in html and "yourHand" not in html


def test_view_script_is_valid_javascript(tmp_path):
    """Regression: the served page must parse, or it never calls refresh().

    A quoting mistake in the view's script left the broadcast stuck on
    「接続確認中…」 because the whole script failed to parse.  The check runs the
    real interpreter when node is available.
    """
    import re
    import shutil
    import subprocess

    script = re.search(r"<script>(.*?)</script>", view.HTML, re.S)
    assert script is not None
    body = script.group(1)
    assert "refresh()" in body and "/api/tsuitate" in body
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    path = tmp_path / "view.js"
    path.write_text(body, encoding="utf-8")
    check = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
