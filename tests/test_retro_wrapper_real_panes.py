"""Drive the tracked wrappers with pane text captured from the real games.

The panes below were captured with ``tmux capture-pane -p`` from bastet 0.43,
moon-buggy 1.0.51, pacman4console 1.3 and nInvaders 0.1.1 (Ubuntu 24.04).
Synthetic panes had hidden defects: bastet's right-aligned "Score:      0" (and
the ACS border letter glued to it), and moon-buggy's name-entry prompt that sits
between game over and "new game".  The "lost screen" tests cover a key the brain
already had in flight dismissing a game-over screen before the 2s/0.35s driver
poll saw it (pacman4console and nInvaders restart on any/SPACE key).
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
WRAPPERS = ROOT / "games/cli-wrappers"

BASTET_BOARD = [
    "                            lqqqqqqqqqqqqqqqqqqqqk lqqqqqqqqqqqqqqk",
    "                            x                    x x Next block:  x",
    "                            x                    x mqqqqqqqqqqqqqqj",
    "                            x                    x lqqqqqqqqqqqqqqk",
    "                            x                    x xScore:{score} x",
    "                            x                    x xLines:      0 x",
    "                            mqqqqqqqqqqqqqqqqqqqqj",
]
BASTET_TRY_AGAIN = [
    "                            lqqqqqqqqqqqqqqqqqqqqqk lqqqqqqqqqqqqqqk",
    "                            xYou did not get into x x              x",
    "                            xthe high score list! x xScore:{score} x",
    "                            x     Try again!      x xLines:      0 x",
    "                            mqqqqqqqqqqqqqqqqqqqqqj xLevel:      0 x",
]
BASTET_MENU = [
    "                         lqqqqqqqqqqqqqqqqqqqqqqqqqqqkqqqqqqqqqqqqqk",
    "                         x -> Play! (normal version) x             x",
    "                         x    Play! (harder version) xcore:      0 x",
    "                         x    View highscores        x             x",
    "                         mqqqqqqqqqqqqqqqqqqqqqqqqqqqjevel:      0 x",
]

MOONBUGGY_PLAY = [
    "SPC,j:jump  a,l:fire  q,n:abort game  r,C-l:redraw",
    "                                    level: 1    lives: 3     score: {score}",
]
MOONBUGGY_NAME = [
    "      rank   score lvl     date  expires  name",
    "       98       30 1    2026-09-18  17d  Nori",
    "                  your score: {score}",
    '  please enter your name (default: "root.."):',
    "                                    level: 1    lives: 0     score: {score}",
]
MOONBUGGY_NEW_GAME = [
    "      rank   score lvl     date  expires  name",
    "       98       33 1    2026-09-18  17d  root",
    "                  your score: {score}",
    "                  your rank: 98",
    "y,RET:new game  q,n:quit  UP:up DOWN:down b:pg up NEXT:pg down s:reload r:redraw",
    "                                    level: 1    lives: 0     score: {score}",
]


# pacman4console 1.3 status row / Game Over dialog (Level 2, Score 523 captured).
PACMAN_PLAY = [
    "              &",
    "              .            &",
    "                 .*         ",
    "   Level: {level}     Score: {score}",
]
PACMAN_GAME_OVER = [
    "             Game Over",
    "    Press q to quit ...",
    "    ... or any other key",
    "        to play again",
    "    Level: {level}     Score: {score}",
]


# nInvaders 0.1.1: title screen and a play frame (status row is the last row).
NINVADERS_TITLE = [
    "                             ____                 __",
    "                       ___  /  _/__ _  _____  ___/ /__ _______",
    "                                    <o o> = 500",
    "                               Press SPACE to start",
]
NINVADERS_PLAY = [
    "                           _O-_O-_O-_O-_O-_O-_O-_O-_O-_O-",
    "                              -o--o--o--o--o--o--o--o--o-",
    "          /-^-\\",
    "                 Level: 01 Score: {score} Lives: /-\\",
]


def pane(lines, score="", level=""):
    return "\n".join(lines).replace("{score}", score).replace("{level}", level)


def run_wrapper(tmp_path, wrapper, bin_var, log_var, panes, args=()):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "panes.json").write_text(json.dumps(panes))
    fake_tmux = bin_dir / "tmux"
    fake_tmux.write_text(f"#!{sys.executable}\n" + '''import json, os, pathlib, sys
root = pathlib.Path(os.environ["FAKE_ROOT"])
if sys.argv[1] == "capture-pane":
    panes = json.loads((root / "panes.json").read_text())
    state = root / "captures"
    n = int(state.read_text()) if state.exists() else 0
    state.write_text(str(n + 1))
    print(panes[min(n, len(panes) - 1)])
elif sys.argv[1] == "send-keys":
    with (root / "keys").open("a") as stream:
        stream.write(json.dumps(sys.argv[2:]) + "\\n")
''')
    fake_tmux.chmod(0o700)
    fake_sleep = bin_dir / "sleep"
    fake_sleep.write_text("#!/bin/sh\nexit 0\n")
    fake_sleep.chmod(0o700)
    fake_game = bin_dir / "game"
    fake_game.write_text(f"#!{sys.executable}\n" + f'''import os, pathlib, time
root = pathlib.Path(os.environ["FAKE_ROOT"])
deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    try:
        if int((root / "captures").read_text()) >= {len(panes) + 6}:
            raise SystemExit(0)
    except (OSError, ValueError):
        pass
    time.sleep(0.005)
raise SystemExit(1)
''')
    fake_game.chmod(0o700)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "TMUX_PANE": "%9",
           "FAKE_ROOT": str(tmp_path), bin_var: str(fake_game),
           log_var: str(tmp_path / "scores.jsonl")}
    result = subprocess.run(["/bin/sh", str(WRAPPERS / wrapper), *args], env=env,
                            capture_output=True, text=True, timeout=8)
    assert result.returncode == 0, result.stderr
    key_log = tmp_path / "keys"
    keys = [json.loads(line) for line in key_log.read_text().splitlines()] if key_log.exists() else []
    log = tmp_path / "scores.jsonl"
    scores = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return keys, scores


@pytest.mark.parametrize("score", ["   1200", "      0", "     42"])
def test_bastet_records_right_aligned_score_and_retries(tmp_path, score):
    panes = [
        pane(BASTET_BOARD, score), pane(BASTET_TRY_AGAIN, score),
        pane(BASTET_MENU), pane(BASTET_BOARD, "      0"),
    ]
    keys, scores = run_wrapper(tmp_path, "bastet_docich.sh", "BASTET_BIN", "BASTET_SCORELOG", panes)
    # One Enter dismisses "Try again!", one starts the next game from the menu.
    assert keys == [["-t", "%9", "Enter"], ["-t", "%9", "Enter"]]
    assert [(s["game"], s["score"]) for s in scores] == [("bastet", int(score))]


def test_moon_buggy_enters_name_then_records_and_restarts(tmp_path):
    panes = [
        pane(MOONBUGGY_PLAY, "33"), pane(MOONBUGGY_NAME, "33"),
        pane(MOONBUGGY_NEW_GAME, "33"), pane(MOONBUGGY_PLAY, "0"),
    ]
    keys, scores = run_wrapper(tmp_path, "moon-buggy_docich.sh", "MOONBUGGY_BIN", "MOONBUGGY_SCORELOG", panes)
    # Enter accepts the default name; the literal "y" then starts the next game.
    assert keys == [["-t", "%9", "Enter"], ["-t", "%9", "-l", "y"]]
    assert [(s["game"], s["score"]) for s in scores] == [("moon-buggy", 33)]


def test_moon_buggy_without_high_score_skips_name_entry(tmp_path):
    # A score that does not rank shows the table directly with "new game".
    panes = [pane(MOONBUGGY_PLAY, "8"), pane(MOONBUGGY_NEW_GAME, "8"), pane(MOONBUGGY_PLAY, "0")]
    keys, scores = run_wrapper(tmp_path, "moon-buggy_docich.sh", "MOONBUGGY_BIN", "MOONBUGGY_SCORELOG", panes)
    assert keys == [["-t", "%9", "-l", "y"]]
    assert [s["score"] for s in scores] == [8]


def test_bastet_flushes_match_when_try_again_dialog_was_missed(tmp_path):
    # An Enter already in flight from the brain dismisses "Try again!" before
    # the 2s driver poll sees it; the next menu must still record the match.
    panes = [pane(BASTET_BOARD, "   1200"), pane(BASTET_MENU), pane(BASTET_BOARD, "      0")]
    keys, scores = run_wrapper(tmp_path, "bastet_docich.sh", "BASTET_BIN", "BASTET_SCORELOG", panes)
    assert keys == [["-t", "%9", "Enter"]]
    assert [s["score"] for s in scores] == [1200]


def test_bastet_first_menu_records_nothing(tmp_path):
    panes = [pane(BASTET_MENU), pane(BASTET_BOARD, "      0")]
    keys, scores = run_wrapper(tmp_path, "bastet_docich.sh", "BASTET_BIN", "BASTET_SCORELOG", panes)
    assert keys == [["-t", "%9", "Enter"]]
    assert scores == []


def test_pacman_records_and_restarts_on_game_over_screen(tmp_path):
    panes = [
        pane(PACMAN_PLAY, "523", "2"), pane(PACMAN_GAME_OVER, "523", "2"),
        pane(PACMAN_PLAY, "0", "1"), pane(PACMAN_PLAY, "40", "1"),
    ]
    keys, scores = run_wrapper(tmp_path, "pacman4console_docich.sh", "PACMAN_BIN", "PACMAN_SCORELOG", panes)
    assert keys == [["-t", "%9", "-l", " "]]
    assert [s["score"] for s in scores] == [523]  # once, not again on the score reset


def test_pacman_flushes_match_when_game_over_screen_was_skipped(tmp_path):
    # Any key restarts pacman4console, so a key the brain already had in flight
    # can skip "Game Over" entirely; the score falling back marks the new match.
    panes = [pane(PACMAN_PLAY, "523", "2"), pane(PACMAN_PLAY, "12", "1"), pane(PACMAN_PLAY, "60", "1")]
    keys, scores = run_wrapper(tmp_path, "pacman4console_docich.sh", "PACMAN_BIN", "PACMAN_SCORELOG", panes)
    assert keys == []
    assert [s["score"] for s in scores] == [523]


NIN = ("ninvaders_docich.sh", "NINVADERS_BIN", "NINVADERS_SCORELOG")


def test_ninvaders_baseline_wrapper_plays_while_match_runs(tmp_path):
    panes = [pane(NINVADERS_TITLE), pane(NINVADERS_PLAY, "0000800"),
             pane(NINVADERS_PLAY, "0000900"), pane(NINVADERS_TITLE)]
    keys, scores = run_wrapper(tmp_path, *NIN, panes)
    # The parked title screen is repeated by the fake pane, so SPACE may repeat.
    assert keys[0] == ["-t", "%9", "Space"]
    assert keys.count(["-t", "%9", "Right", "Space"]) == 2  # one per running frame
    assert all(k in (["-t", "%9", "Space"], ["-t", "%9", "Right", "Space"]) for k in keys)
    assert [s["score"] for s in scores] == [900]


def test_ninvaders_brain_mode_only_starts_matches_and_records(tmp_path):
    # With the command brain playing, the wrapper must never send competing
    # movement/fire keys; it still presses SPACE on the title screen.
    panes = [pane(NINVADERS_TITLE), pane(NINVADERS_PLAY, "0000800"),
             pane(NINVADERS_PLAY, "0000900"), pane(NINVADERS_TITLE)]
    keys, scores = run_wrapper(tmp_path, *NIN, panes, args=("brain",))
    assert len(keys) >= 2  # start, then restart after the match
    assert all(k == ["-t", "%9", "Space"] for k in keys)  # no movement/fire keys
    assert [s["score"] for s in scores] == [900]


def test_ninvaders_flushes_match_when_title_screen_was_skipped(tmp_path):
    # A SPACE the brain already had in flight starts the next match the moment
    # the title appears; the score falling back is the only trace of the old one.
    panes = [pane(NINVADERS_PLAY, "0001200"), pane(NINVADERS_PLAY, "0000030"),
             pane(NINVADERS_PLAY, "0000090")]
    keys, scores = run_wrapper(tmp_path, *NIN, panes, args=("brain",))
    assert keys == []
    assert [s["score"] for s in scores] == [1200]
