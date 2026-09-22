"""Offline registration gates; VM-only ROM and live state stay out of tests."""
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.config import load_game, load_global
from docich.retro_corner import RetroCornerError, RetroCornerManager, load_retro_corner_config

EXISTING = ["ninvaders", "nsnake", "bastet", "moon-buggy", "pacman4console"]


@pytest.fixture
def manager(tmp_path):
    g = load_global(ROOT, ROOT / "config/docich.soren-live.toml")
    g = replace(g, state_dir=tmp_path / "run")
    return RetroCornerManager(
        g, config=load_retro_corner_config(g), coordinator=Mock(),
        ensure_runtime=Mock(), active_game_reader=lambda: None,
        chat=Mock(), stream_game=Mock(),
    )


def test_live_registration_is_enabled_but_keeps_vm_prerequisites_separate(manager):
    assert manager.config.games == [*EXISTING, "hanjuku-hero"]
    game = load_game(manager.g, "hanjuku-hero")
    assert game.adapter == "retroarch"
    assert game.agent.enabled
    assert game.agent.brain == "command"
    assert game.agent.command == ["python3", "brains/hanjuku/bot.py"]
    assert (ROOT / game.agent.command[1]).is_file()
    assert game.raw["retro_corner"]["enabled"] is True
    assert game.raw["retro_corner"]["unattended"] is True
    assert manager._required_executables(game) == [
        "retroarch", "dbus-run-session", "python3",
    ]
    assert game.raw["retroarch"] == {
        "rom": "games/roms/hanjuku-hero.sfc", "core": "auto",
        "audio_enabled": True, "audio_sink": "soren_null",
    }
    assert manager._rotation_interval_seconds() == 86400 / 6


def test_vm_only_rom_gate_keeps_local_checkout_out_of_candidates(manager, monkeypatch):
    monkeypatch.setattr(manager, "_executable_exists", lambda _: True)
    # Configuration is valid even when the checkout intentionally has no ROM.
    manager._validate_games(["hanjuku-hero"])
    assert manager._playable_games() == EXISTING

    # A VM with its private ROM/core and binaries passes the same gate.
    monkeypatch.setattr("docich.adapters.retroarch.resolve_rom", lambda *_: ROOT / "vm-only.sfc")
    monkeypatch.setattr("docich.adapters.retroarch.resolve_core", lambda *_: "/vm-only/core.so")
    assert manager._playable_games() == [*EXISTING, "hanjuku-hero"]
    now = datetime(2026, 9, 21, 12, tzinfo=ZoneInfo("Asia/Tokyo"))
    history = []
    selected = []
    for _ in [*EXISTING, "hanjuku-hero"]:
        game = manager._rotation_pick({"selection_history": history}, now)
        assert game in [*EXISTING, "hanjuku-hero"] and game not in selected
        selected.append(game)
        history.append({"game": game, "selected_at": now.isoformat()})
    assert manager._rotation_pick({"selection_history": history}, now) is None
    manager.coordinator.start.assert_not_called()
    manager.coordinator.switch.assert_not_called()


def test_missing_executables_still_exclude_existing_games(manager, monkeypatch):
    monkeypatch.setattr(manager, "_executable_exists", lambda _: False)
    assert manager._playable_games() == []


@pytest.mark.parametrize("mode", ["rotation", "lottery"])
def test_immediate_start_uses_only_playable_candidates(manager, monkeypatch, mode):
    manager.config = replace(manager.config, mode=mode)
    monkeypatch.setattr(manager, "_executable_exists", lambda _: True)
    monkeypatch.setattr(manager, "_wait_and_finish", manager._state_result)
    manager.coordinator.start.return_value = SimpleNamespace(status="succeeded")
    # Exercise the adapter's legacy immediate-start eligibility; unified manual
    # entry/locking/cooldown is covered by test_corner_rotation.
    result = manager._start_direct()
    assert result.status == "active"
    assert result.game in EXISTING
    assert manager.coordinator.start.call_args.args == (result.game,)
    manager._ensure_runtime.assert_called_once()


def test_immediate_start_without_candidates_does_not_start_runtime(manager, monkeypatch):
    monkeypatch.setattr(manager, "_executable_exists", lambda _: False)
    result = manager._start_direct()
    assert (result.status, result.detail) == ("noop", "no-eligible-game")
    manager._ensure_runtime.assert_not_called()
    manager.coordinator.start.assert_not_called()


@pytest.mark.parametrize("gate,eligible", [(None, True), (True, True), (False, False), ("true", False), (1, False)])
def test_gate_is_strict_and_absent_preserves_cli_behavior(manager, monkeypatch, gate, eligible):
    game = load_game(manager.g, "nsnake")
    if gate is None:
        game.raw["retro_corner"].pop("enabled", None)
    else:
        game.raw["retro_corner"]["enabled"] = gate
    monkeypatch.setattr("docich.retro_corner.load_game", lambda *_: game)
    if eligible:
        manager._validate_games([game.name])
    else:
        with pytest.raises(RetroCornerError):
            manager._validate_games([game.name])


def test_retroarch_requires_safe_round_boundary(manager, monkeypatch):
    game = load_game(manager.g, "hanjuku-hero")
    game.lifecycle = replace(game.lifecycle, require_round_boundary=False)
    monkeypatch.setattr("docich.retro_corner.load_game", lambda *_: game)
    with pytest.raises(RetroCornerError, match="require_round_boundary=true"):
        manager._validate_games([game.name])
