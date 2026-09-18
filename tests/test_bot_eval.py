"""bot_eval presets and match loop against pane text captured from the real games.

nInvaders has no "Game Over" screen: a finished match goes straight back to the
title ("Press SPACE to start").  The preset used to wait for "Game Over", so on
the real game every evaluated match ran to the turn cap (`maxed`), `played`
stayed 0, and the promotion gate could never pass.
"""
import inspect
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.resolver import bot_eval  # noqa: E402

NINVADERS_TITLE = "\n".join([
    "                             ____                 __",
    "                       ___  /  _/__ _  _____  ___/ /__ _______",
    "                                    <o o> = 500",
    "                               Press SPACE to start",
])


def ninvaders_play(score):
    return "\n".join([
        "                           _O-_O-_O-_O-_O-_O-_O-_O-_O-_O-",
        "                              -o--o--o--o--o--o--o--o--o-",
        "          /-^-\\",
        f"                 Level: 01 Score: {score:07d} Lives: /-\\",
    ])


# nsnake 3.0: the dialog that does exist on the real game.
NSNAKE_GAME_OVER = "\n".join([
    "xa                             lGame Overqqqqqqqk                             ax",
    "xa                             xRetry?     <Yes>x                             ax",
    "xHi-Score 0                Score 0                   Speed 1                   x",
])


def _over(game, text):
    kw = bot_eval._bot_presets()[game]["run_kwargs"]
    return any(re.search(p, text) for p in kw["game_over_res"])


def test_ninvaders_match_ends_on_the_title_not_on_game_over():
    assert _over("ninvaders", NINVADERS_TITLE)
    assert not _over("ninvaders", ninvaders_play(800))
    assert not _over("ninvaders", ninvaders_play(0))


def test_nsnake_still_ends_on_its_game_over_dialog():
    assert _over("nsnake", NSNAKE_GAME_OVER)


def test_ninvaders_evaluation_cadence_is_close_to_the_live_brain():
    kw = bot_eval._bot_presets()["ninvaders"]["run_kwargs"]
    # live: [agent] interval_ms = 250; the evaluator adds the brain's own runtime.
    assert kw["interval_s"] <= 0.3
    assert 0 < kw["max_turns"] < 3000


def test_cli_passes_the_game_name_and_uses_the_preset_cadence(monkeypatch, tmp_path):
    real_run = bot_eval.run_bot_matches
    seen = {}

    def fake_run(**kwargs):
        seen.update(kwargs)
        return {"game": kwargs["label"], "matches": [], "mean_score": None}

    monkeypatch.setattr(bot_eval, "run_bot_matches", fake_run)
    monkeypatch.setenv("DOCICH_BOTEVAL_NINVADERS_BIN", "/fake/ninvaders")
    cfg = str(tmp_path / "missing.toml")

    assert bot_eval.main(["ninvaders", "--config", cfg, "--matches", "2"]) == 0
    assert seen["binary"] == ["/fake/ninvaders"]
    assert seen["interval_s"] == 0.2 and seen["max_turns"] == 1500  # preset values

    assert bot_eval.main(["ninvaders", "--config", cfg, "--interval-ms", "500", "--max-turns", "9"]) == 0
    assert seen["interval_s"] == 0.5 and seen["max_turns"] == 9  # explicit flags win

    seen.clear()
    monkeypatch.setenv("DOCICH_BOTEVAL_NSNAKE_BIN", "/fake/nsnake")
    assert bot_eval.main(["nsnake", "--config", cfg]) == 0
    # nsnake sets no cadence: nothing is passed, so run_bot_matches' defaults apply.
    assert "interval_s" not in seen and "max_turns" not in seen
    params = inspect.signature(real_run).parameters
    assert params["interval_s"].default == 0.7 and params["max_turns"].default == 3000


def _fake_tmux_env(tmp_path, frames):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "frames.json").write_text(json.dumps(frames))
    tmux = bin_dir / "tmux"
    tmux.write_text(f"#!{sys.executable}\n" + '''import json, os, pathlib, sys
root = pathlib.Path(os.environ["FAKE_ROOT"])
if sys.argv[1] == "capture-pane":
    frames = json.loads((root / "frames.json").read_text())
    state = root / "n"
    n = int(state.read_text()) if state.exists() else 0
    state.write_text(str(n + 1))
    print(frames[min(n, len(frames) - 1)])
elif sys.argv[1] == "send-keys":
    with (root / "keys").open("a") as stream:
        stream.write(json.dumps(sys.argv[2:]) + "\\n")
''')
    tmux.chmod(0o700)
    bot = tmp_path / "bot.py"
    bot.write_text('import json, sys\nsys.stdin.read()\nprint(json.dumps({"actions": []}))\n')
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_ROOT": str(tmp_path)}, bot


def test_run_bot_matches_finishes_a_ninvaders_match_when_the_title_returns(tmp_path, monkeypatch):
    frames = [
        ninvaders_play(100),   # turn 1
        ninvaders_play(200),   # turn 2
        NINVADERS_TITLE,       # match over: straight back to the title
        ninvaders_play(0),     # retry started a new match
        ninvaders_play(50),    # match 2, turn 1
        NINVADERS_TITLE,       # match 2 over
    ]
    env, bot = _fake_tmux_env(tmp_path, frames)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    # A small cap makes the old behaviour (never seeing the end) fail fast.
    kwargs = {**bot_eval._bot_presets()["ninvaders"]["run_kwargs"], "boot_sleep_s": 0,
              "interval_s": 0, "max_turns": 30}
    summary = bot_eval.run_bot_matches(
        label="ninvaders", binary=["true"], bot_cmd=[sys.executable, str(bot)],
        cwd=str(ROOT), cols=80, rows=24, matches=2, **kwargs,
    )
    assert [(m["score"], m["maxed"]) for m in summary["matches"]] == [(200, False), (50, False)]
    # start key once, then the retry key between the matches
    keys = [json.loads(line) for line in (tmp_path / "keys").read_text().splitlines()]
    assert keys.count(["-t", keys[0][1], "Space"]) == 2


@pytest.mark.parametrize("game_over_res", [["Game Over"]])
def test_old_game_over_pattern_would_run_to_the_cap(tmp_path, monkeypatch, game_over_res):
    # Documents the failure this fixes: with "Game Over" the same frames never end
    # the match and it is reported as maxed with no completed score.
    frames = [ninvaders_play(100), ninvaders_play(200), NINVADERS_TITLE]
    env, bot = _fake_tmux_env(tmp_path, frames)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    kwargs = {**bot_eval._bot_presets()["ninvaders"]["run_kwargs"], "boot_sleep_s": 0,
              "interval_s": 0, "max_turns": 20, "game_over_res": game_over_res}
    summary = bot_eval.run_bot_matches(
        label="ninvaders", binary=["true"], bot_cmd=[sys.executable, str(bot)],
        cwd=str(ROOT), cols=80, rows=24, matches=1, **kwargs,
    )
    assert summary["matches"][0]["maxed"] is True
