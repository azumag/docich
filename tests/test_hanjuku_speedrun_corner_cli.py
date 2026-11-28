"""Fixed CLI entry point for the speedrun copy of the Hanjuku corner."""
from types import SimpleNamespace

import pytest

from docich import hanjuku_corner
from docich.hanjuku_family import ORIGINAL, SPEEDRUN


def _patch_queue(monkeypatch):
    """Record coordinator calls without touching the real state directory."""
    config = SimpleNamespace(state_dir="/tmp/unused")
    calls = {"recovery": 0, "queue": []}
    monkeypatch.setattr(hanjuku_corner, "load_global", lambda *args, **kwargs: config)

    import docich.corner_rotation as rotation_module
    import docich.hanjuku_resume_recovery as recovery_module

    class Manager:
        def __init__(self, loaded):
            assert loaded is config

        def queue_manual(self, game, **kwargs):
            calls["queue"].append((game, kwargs))
            return {"status": "queued", "corner": game, "request_id": "opaque"}

    def recover(loaded):
        assert loaded is config
        calls["recovery"] += 1
        return None

    monkeypatch.setattr(rotation_module, "CornerRotationManager", Manager)
    monkeypatch.setattr(recovery_module, "queue_failed_start", recover)
    return calls


def test_the_default_invocation_keeps_the_original_start_call(monkeypatch):
    calls = []
    monkeypatch.setattr(
        hanjuku_corner, "start",
        lambda **kwargs: calls.append(kwargs) or {"status": "queued"},
    )
    assert hanjuku_corner.main([]) == 0
    assert calls == [{}]


def test_explicit_original_game_keeps_the_original_start_call(monkeypatch):
    calls = []
    monkeypatch.setattr(
        hanjuku_corner, "start",
        lambda **kwargs: calls.append(kwargs) or {"status": "queued"},
    )
    assert hanjuku_corner.main(["--game", ORIGINAL]) == 0
    assert calls == [{}]


def test_speedrun_game_is_queued_as_the_speedrun_corner(monkeypatch):
    calls = _patch_queue(monkeypatch)
    assert hanjuku_corner.start(game=SPEEDRUN) == {
        "status": "queued", "corner": SPEEDRUN, "request_id": "opaque"}
    assert calls == {"recovery": 0, "queue": [(SPEEDRUN, {})]}


def test_cli_game_option_selects_the_speedrun_start(monkeypatch):
    calls = []
    monkeypatch.setattr(
        hanjuku_corner, "start",
        lambda **kwargs: calls.append(kwargs) or {"status": "queued"},
    )
    assert hanjuku_corner.main(["--game", SPEEDRUN]) == 0
    assert calls == [{"game": SPEEDRUN}]


def test_speedrun_rejects_resume_before_original_checkpoint_recovery(monkeypatch, capsys):
    # A refused speedrun resume must not read or rewrite the original corner's
    # prepared checkpoint, so even config loading is unreachable.
    monkeypatch.setattr(
        hanjuku_corner, "load_global",
        lambda *args, **kwargs: pytest.fail("speedrun resume loaded the original config"),
    )
    assert hanjuku_corner.main(["--game", SPEEDRUN, "--resume-latest"]) == 85
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "hanjuku_queue_failure=invalid_value\n"


def test_clear_clock_quarantine_carries_the_selected_game(monkeypatch):
    calls = []
    monkeypatch.setattr(
        hanjuku_corner, "clear_clock_quarantine",
        lambda *args: calls.append(args) or {"status": "cleared"},
    )
    assert hanjuku_corner.main(["--game", SPEEDRUN, "--clear-clock-quarantine"]) == 0
    assert calls == [(SPEEDRUN,)]

    calls.clear()
    assert hanjuku_corner.main(["--clear-clock-quarantine"]) == 0
    assert calls == [()]


def test_unknown_game_is_still_rejected_before_start(monkeypatch):
    called = []
    monkeypatch.setattr(hanjuku_corner, "start", lambda **kwargs: called.append(kwargs))
    with pytest.raises(SystemExit):
        hanjuku_corner.main(["--game", "another-game"])
    assert called == []
