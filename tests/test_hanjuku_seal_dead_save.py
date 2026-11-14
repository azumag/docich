"""Operator seal of a dead Hanjuku runtime's finished manual save."""
from pathlib import Path
import json
import time

import pytest

from docich import config, hanjuku_resume
from docich.adapters.base import AdapterError
from docich.game_switch import atomic_write_json

RUNTIME_ID = "g1163-42bfb85e"
LEASE = "8634592c-56f9-4991-a169-a16f5499584c"


def _rastate(payload=b"synthetic core memory"):
    pad = lambda n: b"\x00" * (-n % 8)
    mem = b"MEM " + len(payload).to_bytes(4, "little") + payload + pad(len(payload))
    return b"RASTATE\x01" + mem + b"END " + (0).to_bytes(4, "little")


class FakeAdapter:
    def __init__(self, dead=True):
        self.dead = dead

    def runtime_provably_dead(self, deadline, cancel):
        return self.dead


@pytest.fixture
def sealed(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    game_dir = repo / "config" / "games"
    game_dir.mkdir(parents=True)
    rom = repo / "games" / "roms" / "hanjuku.sfc"
    rom.parent.mkdir(parents=True)
    rom.write_bytes(b"rom")
    core = repo / "libretro" / "snes9x_libretro.so"
    core.parent.mkdir(parents=True)
    core.write_bytes(b"core")
    (game_dir / "hanjuku-hero.toml").write_text(
        '[game]\nname = "hanjuku-hero"\nadapter = "retroarch"\ntitle = "半休"\n\n'
        f'[retroarch]\nrom = "{rom}"\ncore = "{core}"\n', encoding="utf-8")
    g = config.load_global(repo)
    runtime = repo / "run" / "runtimes" / RUNTIME_ID
    (runtime / "states").mkdir(parents=True)
    atomic_write_json(runtime / "hanjuku_run.json", {
        "schema": 1, "game": "hanjuku-hero", "runtime_id": RUNTIME_ID,
        "generation": 1163, "lease_id": LEASE, "terminal_reason": None,
        "frame_sha256": "a" * 64,
    })
    atomic_write_json(runtime / "retroarch_boundary.json", {
        "schema": 1, "game": "hanjuku-hero", "runtime_id": RUNTIME_ID,
        "generation": 1163, "lease_id": LEASE, "status": "cancelled",
        "request_id": "4a0e85cf-b631-46f7-993c-467582e40407", "requested_ns": 1,
    })
    time.sleep(0.01)
    atomic_write_json(runtime / "hanjuku_manual_save.json", {
        "schema": 1, "game": "hanjuku-hero", "runtime_id": RUNTIME_ID,
        "generation": 1163, "lease_id": LEASE, "allow_unsaved_stop": False,
        "request_id": "7a239ee9-8f0f-4850-9ebd-a353f19084c7",
    })
    time.sleep(0.01)
    (runtime / "states" / "hanjuku.state").write_bytes(_rastate())
    return g, runtime


def test_seals_a_dead_runtime_manual_save(sealed):
    g, runtime = sealed
    out = hanjuku_resume.seal_dead_manual_save(
        g, RUNTIME_ID, adapter=FakeAdapter(dead=True))
    record = json.loads((runtime / "retroarch_boundary.json").read_text())
    assert record["status"] == "reached"
    assert record["outcome"] == "suspended"
    assert record["request_id"] == "7a239ee9-8f0f-4850-9ebd-a353f19084c7"
    assert record["checkpoint"] == "hanjuku.state"
    assert record["checkpoint_bytes"] == (runtime / "states" / "hanjuku.state").stat().st_size
    assert out["outcome"] == "suspended" and out["bytes"] == record["checkpoint_bytes"]


@pytest.mark.parametrize("case", ["alive", "terminal", "unsaved_stop", "older_save", "reached"])
def test_refuses_anything_but_an_interrupted_save(sealed, case):
    g, runtime = sealed
    if case == "terminal":
        run = json.loads((runtime / "hanjuku_run.json").read_text())
        run["terminal_reason"] = "screen_stalled"
        atomic_write_json(runtime / "hanjuku_run.json", run)
    elif case == "unsaved_stop":
        manual = json.loads((runtime / "hanjuku_manual_save.json").read_text())
        manual["allow_unsaved_stop"] = True
        atomic_write_json(runtime / "hanjuku_manual_save.json", manual)
    elif case == "older_save":
        import os
        saved = runtime / "states" / "hanjuku.state"
        saved.write_bytes(_rastate())
        older = (runtime / "hanjuku_manual_save.json").stat().st_mtime_ns - 10_000_000
        os.utime(saved, ns=(older, older))
    elif case == "reached":
        record = json.loads((runtime / "retroarch_boundary.json").read_text())
        record["status"] = "reached"
        atomic_write_json(runtime / "retroarch_boundary.json", record)
    adapter = FakeAdapter(dead=case != "alive")
    before = json.loads((runtime / "retroarch_boundary.json").read_text())
    with pytest.raises(AdapterError):
        hanjuku_resume.seal_dead_manual_save(g, RUNTIME_ID, adapter=adapter)
    assert json.loads((runtime / "retroarch_boundary.json").read_text()) == before


def test_refuses_without_a_liveness_probe(sealed):
    g, _ = sealed
    with pytest.raises(AdapterError):
        hanjuku_resume.seal_dead_manual_save(g, RUNTIME_ID, adapter=object())
