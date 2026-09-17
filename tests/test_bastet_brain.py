import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]
BRAIN = ROOT / "brains/bastet/brain.py"
PLAY = "┌────────────────────┐\n│                    │ Next block:\n│                    │ Score:     0\n│                    │ Lines:     0\n└────────────────────┘ Level:     0"


def run(raw, weights):
    proc = subprocess.run(
        [sys.executable, str(BRAIN)], input=raw, capture_output=True,
        text=True, timeout=15,
        env={**os.environ, "DOCICH_BRAIN_WEIGHTS": str(weights)},
    )
    assert proc.stderr == ""
    return proc.returncode, json.loads(proc.stdout)


def decide(text, tmp_path):
    code, out = run(json.dumps({"game": "bastet", "text": text}), tmp_path / "weights.json")
    assert code == 0
    return out["actions"]


def test_places_piece(tmp_path):
    assert decide(PLAY, tmp_path) == [{"type": "key", "keys": ["Enter"]}]
    assert decide(PLAY, tmp_path) == decide(PLAY, tmp_path)


@pytest.mark.parametrize("text", [
    "", "broken pane", "Bastet", "Play! (normal version)", "difficulty",
    "Try again!", "Game Over", "Press any key...", "Main Menu",
    PLAY + "\nTry again!", PLAY + "\nPlease enter your name",
    PLAY + "\n**Normal difficulty**", PLAY + "\nPress SPACE or ENTER to resume the game",
    PLAY + "\nStarting level = 0 <SPACE> to start",
])
def test_non_play_is_silent(text, tmp_path):
    assert decide(text, tmp_path) == []


@pytest.mark.parametrize("raw", ["not json", "", "[]", "null", "{}", '{"text":42}', '{"text":null}'])
def test_bad_input(raw, tmp_path):
    assert run(raw, tmp_path / "missing") == (2, {"actions": []})


def test_weight_hot_swap(tmp_path):
    weights = tmp_path / "weights.json"
    weights.write_text('{"hard_drop":0}')
    assert decide(PLAY, tmp_path)[0]["keys"] == ["Down"]
    weights.write_text('{"hard_drop":1}')
    assert decide(PLAY, tmp_path)[0]["keys"] == ["Enter"]


@pytest.mark.parametrize("raw", ['bad', '[]', '{"hard_drop":"q"}', '{"hard_drop":null}', '{"hard_drop":true}', '{"hard_drop":NaN}', '{"hard_drop":Infinity}'])
def test_invalid_weights_fall_back(raw, tmp_path):
    (tmp_path / "weights.json").write_text(raw)
    assert decide(PLAY, tmp_path)[0]["keys"] == ["Enter"]


def test_config_contract():
    cfg = tomllib.loads((ROOT / "config/games/bastet.toml").read_text())
    assert cfg["cli"]["command"] == "/bin/sh games/cli-wrappers/bastet_docich.sh"
    assert cfg["agent"]["enabled"] is True
    assert cfg["agent"]["brain"] == "command"
    assert cfg["agent"]["command"] == ["python3", "brains/bastet/brain.py"]
    assert cfg["corner"]["self_play"] and cfg["corner"]["intro"]
    assert cfg["retro_corner"]["unattended"]
    assert cfg["lifecycle"]["require_round_boundary"] is False
