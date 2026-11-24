"""Periodic savestates keep a long run recoverable (g1163 2026-11-10)."""
from types import SimpleNamespace

from docich.agent import loop


def _game(interval):
    return SimpleNamespace(lifecycle=SimpleNamespace(periodic_savestate_s=interval))


def test_no_lifecycle_key_never_saves(monkeypatch):
    calls = []
    monkeypatch.setattr('docich.netcmd.send_ra_cmd', lambda *a, **k: calls.append(a))
    clock = {'last': 0.0}
    adapter = SimpleNamespace(_network_port=lambda: 55518)
    loop._maybe_periodic_savestate(_game(None), adapter, clock)
    assert calls == [] and clock['last'] == 0.0


def test_the_interval_bounds_the_saves(monkeypatch):
    calls = []
    monkeypatch.setattr('docich.netcmd.send_ra_cmd', lambda *a, **k: calls.append(a))
    now = [1000.0]
    monkeypatch.setattr(loop.time, 'monotonic', lambda: now[0])
    clock = {'last': 0.0}
    adapter = SimpleNamespace(_network_port=lambda: 55518)
    loop._maybe_periodic_savestate(_game(900), adapter, clock)
    assert calls == [('SAVE_STATE',)] or calls[0][0] == 'SAVE_STATE'
    loop._maybe_periodic_savestate(_game(900), adapter, clock)
    assert len(calls) == 1
    now[0] += 901
    loop._maybe_periodic_savestate(_game(900), adapter, clock)
    assert len(calls) == 2
