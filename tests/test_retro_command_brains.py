"""Offline live-config/CommandBrain wiring; never starts tmux or a real game."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.actions import parse_actions
from docich.adapters.cli_game import cli_command_list
from docich.agent.brains import CommandBrain
from docich.config import load_game, load_global
from docich.corner_improve import run_corner_improve
from docich.retro_corner import RetroCornerManager, load_retro_corner_config

GAMES = ("bastet", "moon-buggy", "pacman4console")
# Every live retro game is now played by a command brain (ninvaders included: its
# wrapper runs with the "brain" argument and only starts matches / records scores).
BRAIN_GAMES = ("ninvaders", "nsnake", *GAMES)


def test_live_daily_games():
    g = load_global(ROOT, ROOT / "config/docich.soren-live.toml")
    cfg = load_retro_corner_config(g)
    assert cfg.games == ["ninvaders", "nsnake", *GAMES]
    assert cfg.daily_each_game and cfg.randomize_start  # mode="daily" へ戻すとき用に残す
    assert cfg.target_matches == 3
    # 固定枠を持たず毎時抽選。次の正時 (固定枠のコーナーの開始) までに必ず終わる。
    assert cfg.mode == "lottery" and 0 < cfg.lottery_probability <= 1
    assert cfg.lottery_minute + cfg.lottery_wait_minutes + cfg.duration_minutes <= 55
    for name in cfg.games:
        required = RetroCornerManager._required_executables(load_game(g, name))
        assert required and all(path.startswith("/usr/games/") for path in required), name
    for game in BRAIN_GAMES:
        loaded = load_game(g, game)
        assert loaded.agent.enabled and loaded.agent.brain == "command"
        assert loaded.agent.command == ["python3", f"brains/{game}/brain.py"]
        assert 0 < loaded.agent.interval_ms <= 500
    # The baseline wrapper sweeps every 0.35s; the brain must not be slower.
    assert load_game(g, "ninvaders").agent.interval_ms <= 350
    assert cli_command_list(load_game(g, "ninvaders")) == [
        "/bin/sh", "games/cli-wrappers/ninvaders_docich.sh", "brain",
    ]


@pytest.mark.parametrize("game", GAMES)
def test_improvement_skips_without_ai(game, tmp_path):
    def forbidden(_):
        pytest.fail("unsupported game must not call AI")
    result = run_corner_improve(
        SimpleNamespace(state_dir=tmp_path), game=game,
        date_str="2026-09-18", agents="test-only", llm=forbidden,
    )
    assert result == {"status": "skipped", "reason": f"unsupported-game:{game}"}


@pytest.mark.parametrize("game,text", [
    ("bastet", "Score: 0\nLines: 0\nLevel: 0"),
    ("moon-buggy", "score: 0\nlevel: 1"),
    ("pacman4console", "\n" + " C.\n" + "\n" * 28 + " C C C\nLevel: 1 Score: 0"),
    ("ninvaders", "  _O-_O-\n\n\n /-^-\\\n Level: 01 Score: 0000000 Lives: /-\\"),
])
def test_real_command_brain_contract(game, text, tmp_path, monkeypatch):
    monkeypatch.setenv("DOCICH_BRAIN_WEIGHTS", str(tmp_path / "missing"))
    monkeypatch.setenv("DOCICH_PACMAN_LEVELS_DIR", str(tmp_path / "missing"))
    g = load_global(ROOT, ROOT / "config/docich.soren-live.toml")
    loaded = load_game(g, game)
    # Match test interpreter instead of relying on whichever python3 is on PATH.
    loaded.agent.command[0] = sys.executable
    obs = SimpleNamespace(to_json=lambda: json.dumps({"text": text, "game": game}))
    actions = CommandBrain(g, loaded).decide(obs)
    assert len(actions) == 1
    assert actions[0].type == "key"
    assert actions[0].keys[0] in {"Enter", "Down", "Space", "Up", "Left", "Right", "l"}
    assert parse_actions({"actions": [{"type": "key", "keys": actions[0].keys}]})


@pytest.mark.parametrize("game", ("ninvaders", *GAMES))
def test_default_weight_path_and_numeric_keys(game, monkeypatch):
    monkeypatch.delenv("DOCICH_BRAIN_WEIGHTS", raising=False)
    spec = importlib.util.spec_from_file_location("brain_under_test", ROOT / "brains" / game / "brain.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.WEIGHTS_PATH == ROOT / "run/brain" / game / "weights.json"
    assert module.DEFAULT_WEIGHTS
    assert all(type(v) in (float, int) for v in module.DEFAULT_WEIGHTS.values())
