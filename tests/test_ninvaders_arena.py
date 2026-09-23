"""評価器 (arena): 実フレームを台本にして、試合終了の検出・censored・異常系を検証する。

試合終了はタイトル画面の再出現で検出する (ゲームは "Game Over" という文字を描かず、
'#' のブロック文字を描く)。実ゲームでの通し実走は scripts / handoff に記録。
"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.ninvaders import arena  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "ninvaders"
TITLE = (FIX / "title.txt").read_text(encoding="utf-8")
PLAY = (FIX / "play_start.txt").read_text(encoding="utf-8")
GAMEOVER = (FIX / "gameover.txt").read_text(encoding="utf-8")
NO_CANNON = "\n".join("" if i == 22 else line for i, line in enumerate(PLAY.splitlines()))


def scored(score):
    return PLAY.replace("Score: 0000000", f"Score: {score:07d}")


class FakeTmux:
    def __init__(self, frames, repeat_last=False):
        self.frames, self.repeat_last, self.i = list(frames), repeat_last, 0
        self.sent, self.killed, self.created, self.kill_target = [], False, False, None

    def __call__(self, *args):
        cmd = args[0]
        if cmd == "new-session":
            self.created = True
            return subprocess.CompletedProcess(args, 0, "", "")
        if cmd == "capture-pane":
            if self.i < len(self.frames):
                frame = self.frames[self.i]
                self.i += 1
                return subprocess.CompletedProcess(args, 0, frame, "")
            if self.repeat_last:
                return subprocess.CompletedProcess(args, 0, self.frames[-1], "")
            return subprocess.CompletedProcess(args, 1, "", "no session")
        if cmd == "send-keys":
            self.sent.append(tuple(args[3:]))
        if cmd == "kill-session":
            self.killed = True
            self.kill_target = args[2]
        return subprocess.CompletedProcess(args, 0, "", "")


@pytest.fixture
def policy(tmp_path):
    def make(body="return ['Space']"):
        p = tmp_path / "p.py"
        p.write_text(f"def decide(obs, state):\n    {body}\n", encoding="utf-8")
        return p
    return make


@pytest.fixture
def fake(monkeypatch):
    def install(frames, **kw):
        tmux = FakeTmux(frames, **kw)
        monkeypatch.setattr(arena, "_tmux", tmux)
        monkeypatch.setattr(arena.shutil, "which", lambda name: "/usr/bin/tmux")
        return tmux
    return install


def play(policy_path, **kw):
    return arena.play_match(policy_path, binary=["fake-game"], tick_s=0.0, boot_s=0.0,
                            start_timeout_s=1.0, **kw)


def test_match_ends_when_the_title_returns_and_score_is_the_best_seen(policy, fake):
    tmux = fake([TITLE, scored(0), scored(150), scored(400), scored(250), GAMEOVER, GAMEOVER, TITLE])
    result = play(policy())
    assert result["end"] == "title" and result["score"] == 500  # the game-over frame shows 500
    assert result["ticks"] >= 2 and result["policy"]["errors"] == 0
    assert tmux.killed and tmux.created
    assert tmux.kill_target.startswith("=nva-")  # exact match, never a prefix match on a parallel match


def test_block_letter_game_over_does_not_end_the_match_only_the_title_does(policy, fake):
    fake([TITLE, scored(100), GAMEOVER, GAMEOVER, GAMEOVER, TITLE])
    result = play(policy())
    assert result["end"] == "title" and result["score"] == 500


def test_policy_keys_are_sent_as_named_keys(policy, fake):
    tmux = fake([TITLE, scored(0), scored(10), scored(20), TITLE])
    play(policy("return ['Left', 'Space']"))
    assert ("Left", "Space") in tmux.sent


def test_survivor_at_the_time_cap_is_a_valid_censored_result(policy, fake):
    fake([TITLE, scored(300)], repeat_last=True)
    result = play(policy(), max_seconds=0.0)
    assert result["end"] == "timeout"
    summary = arena.summarize([result])
    assert summary["played"] == 1 and summary["censored"] == 1 and summary["incomplete"] == 0


def test_a_game_that_never_starts_is_reported_not_hung(policy, fake, monkeypatch):
    monkeypatch.setattr(arena.time, "sleep", lambda s: None)
    fake([TITLE], repeat_last=True)
    result = arena.play_match(policy(), binary=["g"], tick_s=0.0, boot_s=0.0, start_timeout_s=0.0)
    assert result["end"] == "no-start"


def test_lost_session_is_reported(policy, fake):
    fake([TITLE, scored(0), scored(10)])  # then capture-pane fails
    assert play(policy())["end"] == "session-lost"


def test_missing_cannon_is_repainted_by_the_shared_nudge(policy, fake):
    tmux = fake([TITLE, scored(0)] + [NO_CANNON] * 12 + [TITLE])
    result = play(policy("return []"))
    assert ("Space",) in tmux.sent
    # Live player advances policy state on every frame, even when Nudge's
    # trusted repaint key overrides the policy's keys.
    assert result["policy"]["ticks"] == result["ticks"]


def test_session_is_killed_even_when_the_policy_is_rejected(tmp_path, fake):
    bad = tmp_path / "bad.py"
    bad.write_text("import os\ndef decide(o, s):\n    return []\n", encoding="utf-8")
    tmux = fake([TITLE, scored(0), scored(1), TITLE])
    with pytest.raises(Exception):
        play(bad)
    assert tmux.killed


def test_missing_tmux_is_an_infrastructure_error(policy, monkeypatch):
    monkeypatch.setattr(arena.shutil, "which", lambda name: None)
    with pytest.raises(arena.ArenaError):
        arena.play_match(policy(), binary=["g"])


def test_summarize_separates_valid_incomplete_and_policy_faults():
    matches = [
        {"end": "title", "score": 5000, "ticks": 900, "policy": {"timeouts": 0, "errors": 0}},
        {"end": "title", "score": 6000, "ticks": 900, "policy": {"timeouts": 9, "errors": 9}},
        {"end": "timeout", "score": 7000, "ticks": 2400, "policy": {}},
        {"end": "no-start", "score": None, "ticks": 0, "policy": {}},
        {"end": "error", "score": None, "ticks": 0, "policy": {}},
    ]
    s = arena.summarize(matches)
    assert (s["n"], s["played"], s["incomplete"], s["censored"]) == (5, 3, 2, 1)
    assert s["mean"] == 6000 and s["median"] == 6000 and s["min"] == 5000 and s["max"] == 7000
    assert s["policy_faults"] == 18 and s["policy_fault_rate"] == pytest.approx(18 / 4200)


def test_summarize_of_nothing_is_all_zero_not_a_crash():
    s = arena.summarize([])
    assert s["played"] == 0 and s["mean"] == 0.0 and s["scores"] == []


def test_evaluate_runs_every_match_and_isolates_a_failing_one(monkeypatch, policy):
    calls = []

    def fake_match(path, kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 2:
            return {"end": "error", "score": None, "ticks": 0, "policy": {}}
        return {"end": "title", "score": 1000 * n, "ticks": 100, "policy": {}}

    monkeypatch.setattr(arena, "_safe_match", fake_match)
    result = arena.evaluate(policy(), 4, parallel=1, max_seconds=5.0)
    assert result["summary"]["n"] == 4 and result["summary"]["played"] == 3
    assert all(c["max_seconds"] == 5.0 for c in calls)


def test_resolve_binary_honours_the_env_override(monkeypatch):
    monkeypatch.setenv("DOCICH_NINVADERS_BIN", "/opt/x/ninvaders --flag")
    assert arena.resolve_binary() == ["/opt/x/ninvaders", "--flag"]
    monkeypatch.delenv("DOCICH_NINVADERS_BIN")
    monkeypatch.setattr(arena.shutil, "which", lambda n: None)
    monkeypatch.setattr(arena.Path, "exists", lambda self: False)
    with pytest.raises(arena.ArenaError):
        arena.resolve_binary()
