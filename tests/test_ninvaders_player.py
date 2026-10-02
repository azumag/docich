"""ライブのプレイヤー: 試合中だけ入力し、試合境界で昇格版へ切替え、動かない政策ではスイープへ縮退する。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.ninvaders import player  # noqa: E402
from docich.ninvaders.store import PolicyStore  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "ninvaders"
TITLE = (FIX / "title.txt").read_text(encoding="utf-8")
PLAY = (FIX / "play_start.txt").read_text(encoding="utf-8")
GAMEOVER = (FIX / "gameover.txt").read_text(encoding="utf-8")
NO_CANNON = "\n".join("" if i == 22 else line for i, line in enumerate(PLAY.splitlines()))

SPACE = "def decide(obs, state):\n    return ['Space']\n"
LEFT = "def decide(obs, state):\n    return ['Left']\n"
SILENT = "def decide(obs, state):\n    return []\n"


class Script:
    """Feeds scripted pane captures to player.run and records what it sends."""

    def __init__(self, seq, at=None):
        self.seq, self.at, self.i, self.sent = list(seq), at or {}, 0, []

    def capture(self, pane):
        if self.i in self.at:
            self.at[self.i]()
        frame = self.seq[self.i] if self.i < len(self.seq) else None
        self.i += 1
        return frame

    def send(self, pane, keys):
        self.sent.append((self.i - 1, list(keys)))

    def stopped(self):
        return self.i >= len(self.seq)


def run_script(monkeypatch, store, script):
    monkeypatch.setattr(player, "_capture", script.capture)
    monkeypatch.setattr(player, "_send", script.send)
    opened = []
    real_open = player.open_policy

    def spy(s):
        runner, entry = real_open(s)
        opened.append((runner, entry))
        return runner, entry

    monkeypatch.setattr(player, "open_policy", spy)
    player.run("%1", store, tick_s=0.0, stop=script.stopped)
    return opened


def make_store(tmp_path, source=SPACE):
    base = tmp_path / "baseline.py"
    base.write_text(source, encoding="utf-8")
    return PolicyStore(tmp_path / "pd", base)


def test_keys_are_sent_only_during_live_play(tmp_path, monkeypatch):
    seq = [TITLE, PLAY, PLAY, PLAY, GAMEOVER, GAMEOVER, TITLE, TITLE]
    script = Script(seq)
    run_script(monkeypatch, make_store(tmp_path), script)
    assert [i for i, _ in script.sent] == [1, 2, 3]
    assert all(keys == ["Space"] for _, keys in script.sent)


def test_runner_is_closed_when_the_title_returns(tmp_path, monkeypatch):
    script = Script([TITLE, PLAY, PLAY, GAMEOVER, TITLE, TITLE])
    opened = run_script(monkeypatch, make_store(tmp_path), script)
    assert len(opened) == 1 and opened[0][0]._proc is None  # worker stopped


def test_promoted_policy_takes_over_at_the_next_match_not_mid_match(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    promote_mid_match = lambda: store.promote(LEFT, {"origin": "llm"})
    seq = [TITLE, PLAY, PLAY, PLAY, PLAY, GAMEOVER, TITLE, PLAY, PLAY, PLAY, TITLE]
    script = Script(seq, at={3: promote_mid_match})
    opened = run_script(monkeypatch, store, script)
    by_frame = dict(script.sent)
    assert [by_frame[i] for i in (1, 2, 3, 4)] == [["Space"]] * 4  # match 1 keeps its policy
    assert [by_frame[i] for i in (7, 8, 9)] == [["Left"]] * 3      # match 2 uses the promotion
    assert [e["origin"] for _, e in opened] == ["baseline", "promoted"]


def test_unusable_policy_falls_back_to_the_sweep_instead_of_idling(tmp_path, monkeypatch):
    store = make_store(tmp_path, source="import os\ndef decide(obs, state):\n    return []\n")
    script = Script([TITLE] + [PLAY] * 10 + [TITLE])
    opened = run_script(monkeypatch, store, script)
    assert opened[0][0] is None and opened[0][1]["origin"] == "fallback"
    keys = [k for _, ks in script.sent for k in ks]
    assert "Right" in keys and "Space" in keys


def test_policy_that_dies_mid_match_is_replaced_by_the_sweep(tmp_path, monkeypatch):
    store = make_store(tmp_path, source="x = 1 // 0\ndef decide(obs, state):\n    return ['Space']\n")
    script = Script([TITLE] + [PLAY] * 12 + [TITLE])
    run_script(monkeypatch, store, script)
    late = [keys for i, keys in script.sent if i >= 9]
    assert late and all("Space" in keys and ("Right" in keys or "Left" in keys) for keys in late)


def test_missing_cannon_is_repainted_with_space_even_if_the_policy_is_silent(tmp_path, monkeypatch):
    script = Script([TITLE] + [NO_CANNON] * 12 + [TITLE])
    run_script(monkeypatch, make_store(tmp_path, source=SILENT), script)
    fired = [i for i, keys in script.sent if keys == ["Space"]]
    assert fired and fired[0] == 8  # the 8th blind tick, not during a short explosion


def test_capture_failures_are_skipped_without_input(tmp_path, monkeypatch):
    monkeypatch.setattr(player.time, "sleep", lambda s: None)
    script = Script([None, TITLE, None, PLAY, TITLE])
    run_script(monkeypatch, make_store(tmp_path), script)
    assert [i for i, _ in script.sent] == [3]
