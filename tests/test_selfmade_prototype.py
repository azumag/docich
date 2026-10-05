"""Synthetic #380 fixtures only: never run the fixture's game.mjs."""
import copy
import hashlib
import json
import struct
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from docich import selfmade_prototype as p

FIXTURE = Path(__file__).parent / "fixtures" / "selfmade_key_switch"


def encode(value):
    return json.dumps(value).encode()


@pytest.fixture
def bundle(tmp_path):
    for source in FIXTURE.iterdir():
        (tmp_path / source.name).write_bytes(source.read_bytes())
    return tmp_path


def rewrite(root, name, value):
    (root / name).write_bytes(encode(value))
    manifest = json.loads((root / "manifest.json").read_bytes())
    manifest["files"][name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    (root / "manifest.json").write_bytes(encode(manifest))


def session(root, **kwargs):
    return p.FixtureSession(root, p.Artifact.load(root), **kwargs)


def request(s, button=None, ticks=4):
    return {"frame_id": s.observation()["frame_id"], "seq": s.seq + 1,
            "buttons": [button] if button else [], "ticks": ticks}


def win(s):
    # Test-only answer: neither observation nor bundle contains this input list.
    assert s.submit(encode(request(s, "RIGHT", 16)))
    assert s.state.candidate_win and s.state.tick == 16
    return s


def test_keys_candidate_replay_and_restore(bundle):
    s = session(bundle)
    o = s.observation()
    assert set(o) == {"png", "frame_id", "instructions", "remaining_ticks"}
    assert o["png"][:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", o["png"][16:24]) == (640, 480)
    win(s)
    assert s.result is None and s.corner.owner
    assert s.verify() == "verified_win"
    assert s.report() == {"result": "verified_win", "restored": True,
                          "success": True, "execution": "trusted_synthetic_fixture"}
    assert s.corner.events == ["release_keys", "stop_and_reap", "release_owner", "restore"]
    assert s.corner.common_pid == 100 and s.corner.children == ()
    assert p.verify_replay(bundle, s.artifact, s.evidence())
    assert not s.submit(encode(request(s)))
    s.verify()
    assert len(s.corner.events) == 4


def test_tick_order_wall_gate_and_permanent_switch(bundle):
    s = session(bundle)
    assert s.submit(encode(request(s, "LEFT", 4)))
    assert s.state.player == (1, 1)  # Wall collision still consumes ticks.
    assert s.submit(encode(request(s, "RIGHT", 3)))
    assert s.state.player == (1, 1)
    assert s.submit(encode(request(s, "RIGHT", 1)))
    assert s.state.player == (2, 1) and s.state.key
    assert s.submit(encode(request(s, "RIGHT", 4)))
    assert s.state.switch and s.state.gates == (True,)
    assert s.submit(encode(request(s, "LEFT", 4)))
    assert s.state.switch
    a = s.artifact
    initial = p.State(3, (3, 1), gates=(False,))
    assert p.transition(a.rules, initial, "RIGHT").player == (3, 1)


@pytest.mark.parametrize("change", [
    {"buttons": ["WIN"]}, {"buttons": ["UP", "DOWN"]}, {"buttons": [1]},
    {"buttons": "RIGHT"}, {"ticks": 1.5}, {"ticks": True}, {"ticks": -1},
    {"ticks": 0}, {"ticks": 61}, {"seq": 0}, {"seq": 2}, {"seq": True},
    {"frame_id": "old"}, {"success": True}, {"player": [5, 1]},
    {"key": True}, {"event": "WIN"}, {"eval": "state.win=true"},
])
def test_invalid_input_does_not_advance_or_expose_state(bundle, change):
    s = session(bundle)
    original = p.state_hash(s.state)
    bad = {**request(s), **change}
    assert not s.submit(encode(bad))
    assert p.state_hash(s.state) == original and s.seq == 0
    assert s.submit(encode(request(s)))  # seq is consumed only on acceptance.
    assert s.rejects == 0


@pytest.mark.parametrize("raw", [b"{}", b"[]", b"x", b"x" * 1025,
    b'{"seq":1,"seq":1}', b'{"ticks":NaN}', b"\xff", b"[" * 1000])
def test_malformed_and_three_rejections_restore(bundle, raw):
    s = session(bundle)
    for _ in range(3):
        assert not s.submit(raw)
    assert s.state.tick == 0 and s.result == "invalid_input"
    assert s.verify() == "invalid_input"
    assert s.corner.previous_visible and len(s.corner.events) == 4


def test_stale_frame_and_duplicate_seq(bundle):
    s = session(bundle)
    old = request(s)
    assert s.submit(encode(old))
    original = p.state_hash(s.state)
    assert not s.submit(encode(old))
    assert not s.submit(encode({**request(s), "seq": 1}))
    assert p.state_hash(s.state) == original


def test_noop_timeout_replay_and_late_input(bundle):
    s = session(bundle)
    for _ in range(40):
        assert s.submit(encode(request(s, ticks=60)))
    assert s.state.tick == 2400 and s.result == "timeout"
    assert s.verify() == "timeout"
    before = s.evidence()
    assert not s.submit(encode(request(s, "RIGHT")))
    assert before == s.evidence()


def test_wall_cutoff_and_cancel_are_reproducible(bundle):
    now = [0]
    s = session(bundle, clock=lambda: now[0])
    assert s.submit(encode(request(s, "RIGHT")))
    now[0] = p.WALL_SECONDS
    assert not s.submit(encode(request(s, "RIGHT", 12)))
    assert s.state.tick == 4 and s.reason == "wall_limit" and s.verify() == "timeout"
    cancelled = session(bundle)
    cancelled.cancel()
    assert cancelled.verify() == "not_cleared"


def test_win_on_limit_tick_precedes_timeout(bundle):
    s = session(bundle)
    for _ in range(39):
        s.submit(encode(request(s, ticks=60)))
    s.submit(encode(request(s, ticks=44)))
    s.submit(encode(request(s, "RIGHT", 60)))
    assert s.state.tick == 2400 and s.state.candidate_win
    assert s.verify() == "verified_win"


@pytest.mark.parametrize("field,value", [
    ("artifact", "0" * 64), ("seed", 8), ("seed", True),
    ("versions", {**p.VERSIONS, "judge": "other"}), ("versions", {**p.VERSIONS, "schema": True}),
    ("hashes", []), ("cutoff_tick", 12), ("cutoff_tick", True), ("reason", "WIN"),
])
def test_replay_mismatch(bundle, field, value):
    s = win(session(bundle))
    evidence = s.evidence()
    evidence[field] = value
    assert not p.verify_replay(bundle, s.artifact, evidence)


def test_missing_tick_input_extra_input_and_detached_evidence(bundle):
    s = win(session(bundle))
    evidence = s.evidence()
    evidence["hashes"][0] = "0" * 64
    assert evidence != s.evidence()
    assert not p.verify_replay(bundle, s.artifact, evidence)
    evidence = s.evidence()
    evidence["inputs"] = []
    assert not p.verify_replay(bundle, s.artifact, evidence)
    evidence = s.evidence()
    evidence["inputs"].append(copy.deepcopy(evidence["inputs"][0]))
    assert not p.verify_replay(bundle, s.artifact, evidence)
    evidence = s.evidence()
    evidence["inputs"][0]["start_tick"] = False
    assert not p.verify_replay(bundle, s.artifact, evidence)


@pytest.mark.parametrize("name", ["game.mjs", "rules.json", "manifest.json"])
def test_frozen_version_change_or_missing_file_before_play_and_replay(bundle, name):
    artifact = p.Artifact.load(bundle)
    s = win(p.FixtureSession(bundle, artifact))
    (bundle / name).write_bytes((bundle / name).read_bytes() + b" ")
    with pytest.raises(p.Rejected):
        p.FixtureSession(bundle, artifact)
    assert s.verify() == "replay_mismatch"
    assert not s.report()["success"] and s.corner.previous_visible
    (bundle / name).unlink()
    assert not p.verify_replay(bundle, artifact, s.evidence())


@pytest.mark.parametrize("mutation", [
    lambda r: r["key"].update(cell=[16, 1]),
    lambda r: r["key"].update(cell=[True, 1]),
    lambda r: r["key"].update(cell=[0, 0]),
    lambda r: r["key"].update(id="player"),
    lambda r: r["key"].update(cell=[1, 1]),
    lambda r: r["gates"][0].update(requires=["gate"]),
    lambda r: r["gates"][0].update(requires=["switch", "switch"]),
    lambda r: r.update(enemies=[]),
    lambda r: r.update(cells=["." * 16] * 11),
])
def test_rules_reject_unsupported_or_ambiguous_entities(bundle, mutation):
    rules = json.loads((bundle / "rules.json").read_bytes())
    mutation(rules)
    rewrite(bundle, "rules.json", rules)
    with pytest.raises(p.Rejected):
        p.Artifact.load(bundle)


def test_bundle_bounds_links_files_and_versions(bundle, tmp_path):
    (bundle / "extra.js").write_bytes(b"not allowed")
    with pytest.raises(p.Rejected):
        p.Artifact.load(bundle)
    (bundle / "extra.js").unlink()
    (bundle / "game.mjs").unlink()
    (bundle / "game.mjs").symlink_to(FIXTURE / "game.mjs")
    with pytest.raises(p.Rejected):
        p.Artifact.load(bundle)
    (bundle / "game.mjs").unlink()
    (bundle / "game.mjs").write_bytes(b"x" * (p.MAX_BUNDLE + 1))
    with pytest.raises(p.Rejected):
        p.Artifact.load(bundle)


def test_forged_win_and_state_proposal_are_not_judge_evidence(bundle):
    # The fixture source itself advertises WIN/success from tick zero.
    s = session(bundle)
    assert b"success: true" in dict(s.artifact.files)["game.mjs"]
    assert not s.state.candidate_win and s.result is None
    expected = p.transition(s.artifact.rules, s.state, "RIGHT")
    p.check_proposal(expected, asdict(expected))
    for changed in ({"candidate_win": True}, {"player": [5, 1]}, {"key": True},
                    {"success": True}, {"switch": True}, {"alive": False}):
        with pytest.raises(p.Rejected, match="invalid_artifact"):
            p.check_proposal(expected, {**asdict(expected), **changed})
    assert s.state.tick == 0


def test_generated_execution_always_fail_closed_even_for_valid_bundle(bundle, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("must not execute a child process")
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    p.Artifact.load(bundle)
    with pytest.raises(p.Rejected, match="sandbox_violation"):
        p.start_generated(bundle)
    for source in (b"while(true) {}", b"throw Error('crash')", b"fetch('http://127.0.0.1')",
                   b"process.env", b"require('fs').readFileSync('/var/run/docker.sock')"):
        (bundle / "game.mjs").write_bytes(source)
        with pytest.raises(p.Rejected, match="sandbox_violation"):
            p.start_generated(bundle)


@pytest.mark.parametrize("terminal", ["not_cleared", "timeout", "build_failed",
    "invalid_artifact", "invalid_input", "sandbox_violation", "replay_mismatch"])
def test_mock_cleanup_once_on_all_failures(bundle, terminal):
    s = session(bundle)
    s._finish(terminal, terminal)
    s._finish(terminal, terminal)
    assert s.corner.events == ["release_keys", "stop_and_reap", "release_owner", "restore"]
    assert not s.corner.owner and s.corner.previous_visible
    assert s.corner.children == () and s.corner.common_pid == 100


def test_duplicate_ownership_and_restore_failure_are_not_success(bundle):
    corner = p.MockCorner(restore_ok=False)
    s = session(bundle, corner=corner)
    with pytest.raises(p.Rejected, match="owner_busy"):
        session(bundle, corner=corner)
    win(s).verify()
    assert s.result == "verified_win" and not s.report()["success"]
    assert not s.report()["restored"]


def test_asset_directory_links_fifo_and_file_count_are_rejected(bundle):
    assets = bundle / "assets"
    assets.symlink_to(FIXTURE, target_is_directory=True)
    with pytest.raises(p.Rejected):
        p.Artifact.load(bundle)
    assets.unlink()
    assets.mkdir()
    import os
    os.mkfifo(assets / "pipe.txt")
    with pytest.raises(p.Rejected):
        p.Artifact.load(bundle)
    (assets / "pipe.txt").unlink()
    for i in range(33):
        (assets / f"a{i}.txt").write_bytes(b"x")
    with pytest.raises(p.Rejected):
        p.Artifact.load(bundle)


def test_manifest_contract_and_wrong_hash_are_rejected(bundle):
    original = json.loads((bundle / "manifest.json").read_bytes())
    for change in ({"seed": True}, {"tick_hz": True}, {"max_ticks": True},
                   {"versions": {**p.VERSIONS, "schema": True}},
                   {"dependencies": ["external"]}, {"image_digest": "unreviewed"},
                   {"files": {}}, {"goal": "success=true"}, {"artifact_id": "../old"}):
        (bundle / "manifest.json").write_bytes(encode({**original, **change}))
        with pytest.raises(p.Rejected):
            p.Artifact.load(bundle)
