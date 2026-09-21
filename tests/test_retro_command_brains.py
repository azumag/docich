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
from docich.resolver.bot_eval import bot_games, bot_preset

GAMES = ("bastet", "moon-buggy", "pacman4console")
# The five enabled short CLI games are played by command brains (ninvaders included: its
# wrapper runs with the "brain" argument and only starts matches / records scores).
BRAIN_GAMES = ("ninvaders", "nsnake", *GAMES)


def test_live_rolling_games():
    g = load_global(ROOT, ROOT / "config/docich.soren-live.toml")
    cfg = load_retro_corner_config(g)
    assert cfg.games == ["ninvaders", "nsnake", *GAMES, "nethack", "hanjuku-hero"]
    assert cfg.daily_each_game and cfg.randomize_start  # mode="daily" へ戻すとき用に残す
    assert cfg.target_matches == 3
    # 24時間を登録ゲーム数で割った間隔。毎時の確率抽選ではない。
    assert cfg.mode == "rotation"
    assert cfg.rotation_period_hours == 24.0
    assert cfg.rotation_period_hours * 3600 / len(cfg.games) == 24 * 3600 / 7
    assert cfg.rotation_wait_minutes == 10
    for name in [*BRAIN_GAMES, "nethack"]:
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


def test_live_nethack_is_a_save_safe_bounded_candidate():
    g = load_global(ROOT, ROOT / "config/docich.soren-live.toml")
    loaded = load_game(g, "nethack")
    assert loaded.agent.enabled and loaded.agent.brain == "nethack"
    assert loaded.agent.interval_ms == 1500
    assert loaded.raw["nethack"]["persistent_run"] is True
    assert RetroCornerManager._required_executables(loaded) == ["/usr/games/nethack"]


@pytest.mark.parametrize("game", BRAIN_GAMES)
def test_each_live_brain_has_an_improvement_preset(game):
    assert game in bot_games()
    preset = bot_preset(None, game)
    assert preset["bot_cmd"][-1] == f"brains/{game}/brain.py"
    assert preset["cols"] == 80
    assert preset["rows"] == (32 if game == "pacman4console" else 24)


@pytest.mark.parametrize("game", BRAIN_GAMES)
def test_improvement_dry_run_accepts_without_live_matches(game, tmp_path):
    state_dir = tmp_path / "run"
    state_dir.mkdir()
    (state_dir / "retro_corner.json").write_text(json.dumps({
        "schema_version": 1,
        "status": "completed",
        "date": "2026-09-18",
        "game": game,
        "previous_game": "sorengame",
        "started_at": "2026-09-18T19:00:00+09:00",
        "ends_at": "2026-09-18T19:20:00+09:00",
        "completed_at": "2026-09-18T19:20:00+09:00",
    }), encoding="utf-8")
    result = run_corner_improve(
        SimpleNamespace(state_dir=state_dir), game=game,
        date_str="2026-09-18", agents="test-only", dry_run=True,
    )
    assert result["status"] == "dry-run"
    assert result["stats"]["n"] == 0
    assert "bounded headless evaluation" in result["stats"]["basis"]


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
