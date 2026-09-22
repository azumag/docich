"""Offline evidence for rotation waits; no VM operations or recovery."""
import fcntl
import json
import os
from types import SimpleNamespace

import pytest

from test_collect_diagnostics import load_collector


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_existing_wait_can_come_from_failed_improvement_after_corner_completion(tmp_path):
    from docich.corner_adapters import GameCornerAdapter
    from docich.corner_catalog import Corner

    module = load_collector()
    state_path = tmp_path / "retro_corner.json"
    write(state_path, {"game": "nsnake", "status": "completed", "completed_at": 100,
                       "improve_job": {"spawned": True}})
    write(tmp_path / "corner_improve_nsnake.json",
          {"status": "failed", "started_at": 101, "completed_at": 102})
    adapter = GameCornerAdapter.__new__(GameCornerAdapter)
    adapter.g = SimpleNamespace(state_dir=tmp_path)
    adapter.corner = Corner("nsnake", "game", "nsnake")
    adapter.manager = SimpleNamespace(state_path=state_path)
    # This is a reproduction of an existing gate, NOT proof of the VM cause
    # or permission to ignore failure/unknown child ownership.
    assert not adapter.resources_released()
    output = module._collect_rotation_evidence(tmp_path)
    assert output["corners"]["retro_corner"]["status"] == "completed"
    assert output["improvements"]["nsnake"] == {
        "present": True, "readable": True, "lock": "absent", "status": "failed",
        "started_at": 101, "completed_at": 102,
    }


@pytest.mark.parametrize("status", ["running", "failed", "kept", "skipped"])
def test_per_game_improvement_evidence_is_fixed_and_read_only(tmp_path, status):
    module = load_collector()
    write(tmp_path / "corner_improve_nsnake.json", {
        "status": status, "started_at": 99, "completed_at": 101,
        "prompt": "DO-NOT-EMIT", "save": "DO-NOT-EMIT", "pid": 999999,
    })
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file()}
    output = module._collect_rotation_evidence(tmp_path)
    row = output["improvements"]["nsnake"]
    assert row["status"] == status
    assert row["started_at"] == 99
    assert "DO-NOT-EMIT" not in json.dumps(output)
    assert "999999" not in json.dumps(output)
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file()}
    assert not (tmp_path / "locks").exists()


def test_held_lock_overrides_no_assumptions_about_terminal_status(tmp_path):
    module = load_collector()
    path = tmp_path / "locks/corner-improve-nsnake.lock"
    path.parent.mkdir()
    path.touch()
    with path.open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert module._collect_rotation_evidence(tmp_path)["improvements"]["nsnake"]["lock"] == "held"
    assert module._collect_rotation_evidence(tmp_path)["improvements"]["nsnake"]["lock"] == "free"


def test_missing_corrupt_and_legacy_sources_are_distinct(tmp_path):
    module = load_collector()
    (tmp_path / "corner_improve_nsnake.json").write_text("broken")
    write(tmp_path / "soren91_corner_manual.json", {"status": "failed", "completed_at": 100})
    write(tmp_path / "retro_corner_manual.json", {"game": "bastet", "status": "restoring"})
    output = module._collect_rotation_evidence(tmp_path)
    assert output["improvements"]["nsnake"]["present"] is True
    assert output["improvements"]["nsnake"]["readable"] is False
    assert output["improvements"]["bastet"]["present"] is False
    assert output["corners"]["soren91_corner_manual"]["status"] == "failed"
    assert output["corners"]["retro_corner_manual"]["status"] == "restoring"


@pytest.mark.parametrize("value", ["DO-NOT-EMIT", [], {}, True, -1, float("inf")])
def test_malformed_values_cannot_become_free_text_or_crash(tmp_path, value):
    module = load_collector()
    write(tmp_path / "retro_corner.json", {
        "status": value, "game": value, "completed_at": value,
        "recovery_required": value, "improve_job": {"spawned": value},
    })
    write(tmp_path / "corner_improve_nsnake.json", {
        "status": value, "started_at": value, "completed_at": value,
    })
    output = module._collect_rotation_evidence(tmp_path)
    assert output["improvements"]["nsnake"]["status"] == "unknown"
    assert output["improvements"]["nsnake"]["started_at"] is None
    assert "DO-NOT-EMIT" not in json.dumps(output)


def test_fixed_paths_reject_links_nonregular_and_oversized_files(tmp_path):
    module = load_collector()
    outside = tmp_path / "unrelated.json"
    write(outside, {"status": "kept", "started_at": 99})
    (tmp_path / "corner_improve_nsnake.json").symlink_to(outside)
    os.mkfifo(tmp_path / "corner_improve_bastet.json")
    (tmp_path / "corner_improve_ninvaders.json").write_text(" " * 65537)
    (tmp_path / "locks").symlink_to(tmp_path)
    output = module._collect_rotation_evidence(tmp_path)
    for game in ("nsnake", "bastet", "ninvaders"):
        assert output["improvements"][game]["readable"] is False
        assert output["improvements"][game]["lock"] == "unknown"


def test_full_corner_projection_includes_new_evidence(tmp_path):
    module = load_collector()
    output = {}
    module._collect_corner_files(tmp_path, output, 100)
    assert set(output["rotation_evidence"]) == {"corners", "improvements"}
    assert len(json.dumps(output["rotation_evidence"])) < 8192
