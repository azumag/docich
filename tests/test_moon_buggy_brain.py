import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]
BRAIN = ROOT / "brains/moon-buggy/brain.py"
PLAY = "score: 0  level: 1\n    __\n __/  \\__\n O      O\n###########    ###########"


def run(raw, weights):
    proc = subprocess.run(
        [sys.executable, str(BRAIN)], input=raw, capture_output=True,
        text=True, timeout=15,
        env={**os.environ, "DOCICH_BRAIN_WEIGHTS": str(weights)},
    )
    assert proc.stderr == ""
    return proc.returncode, json.loads(proc.stdout)


def decide(text, tmp_path):
    code, out = run(json.dumps({"game": "moon-buggy", "text": text}), tmp_path / "weights.json")
    assert code == 0
    return out["actions"]


def test_jumps_to_continue_running(tmp_path):
    assert decide(PLAY, tmp_path) == [{"type": "key", "keys": ["Space"]}]
    assert decide(PLAY, tmp_path) == decide(PLAY, tmp_path)


def test_periodic_laser(tmp_path):
    assert decide(PLAY.replace("score: 0", "score: 6"), tmp_path) == [{"type": "key", "keys": ["l"]}]


@pytest.mark.parametrize("text", [
    "", "broken pane", "moon-buggy", "y:start game", "y,RET:new game",
    "Game Over", "Main Menu", "difficulty", "Press any key...",
    PLAY + "\ny,RET:new game", PLAY + "\ny:start game",
    PLAY + "\nEnter your name", "score: 2", "level: 1",
])
def test_non_play_is_silent(text, tmp_path):
    assert decide(text, tmp_path) == []


@pytest.mark.parametrize("raw", ["not json", "", "[]", "null", "{}", '{"text":42}', '{"text":null}'])
def test_bad_input(raw, tmp_path):
    assert run(raw, tmp_path / "missing") == (2, {"actions": []})


def test_weight_hot_swap(tmp_path):
    pane = PLAY.replace("score: 0", "score: 6")
    weights = tmp_path / "weights.json"
    weights.write_text('{"laser_period":8}')
    assert decide(pane, tmp_path)[0]["keys"] == ["Space"]
    weights.write_text('{"laser_period":7}')
    assert decide(pane, tmp_path)[0]["keys"] == ["l"]


@pytest.mark.parametrize("raw", ['bad', '[]', '{"laser_period":"q"}', '{"laser_period":null}', '{"laser_period":true}', '{"laser_period":NaN}', '{"laser_period":Infinity}'])
def test_invalid_weights_fall_back(raw, tmp_path):
    (tmp_path / "weights.json").write_text(raw)
    assert decide(PLAY, tmp_path)[0]["keys"] == ["Space"]


def test_config_contract():
    cfg = tomllib.loads((ROOT / "config/games/moon-buggy.toml").read_text())
    assert cfg["cli"]["command"] == "/bin/sh games/cli-wrappers/moon-buggy_docich.sh"
    assert cfg["agent"]["enabled"] is True
    assert cfg["agent"]["brain"] == "command"
    assert cfg["agent"]["command"] == ["python3", "brains/moon-buggy/brain.py"]
    assert cfg["corner"]["self_play"] and cfg["corner"]["intro"]
    assert cfg["retro_corner"]["unattended"]
    assert cfg["lifecycle"]["require_round_boundary"] is False
