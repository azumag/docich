"""Non-production #380 run-coordinator regressions: no bundle code is executed."""
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from docich import selfmade_prototype as p
from docich import selfmade_run as r

FIXTURE = Path(__file__).parent / "fixtures" / "selfmade_key_switch"
RULES = json.loads((FIXTURE / "rules.json").read_bytes())
SOURCE = (FIXTURE / "game.mjs").read_bytes()


def spec(**changes):
    base = {"artifact_id": "run-key-switch-v1", "seed": 7,
            "rules": copy.deepcopy(RULES), "source": SOURCE}
    base.update(changes)
    return r.BuildSpec(**base)


def plan_solver(plan):
    """Trusted test driver: observation in, strict solver bytes out. Not a model."""
    state = {"seq": 0, "index": 0}

    def next_input(observation):
        if state["index"] >= len(plan):
            return None
        button, ticks = plan[state["index"]]
        state["index"] += 1
        state["seq"] += 1
        return json.dumps({"frame_id": observation["frame_id"], "seq": state["seq"],
                           "buttons": [button] if button else [],
                           "ticks": ticks}).encode()
    return next_input


def win(tmp_path, name="bundle", **kwargs):
    return r.run_fixture(spec(), tmp_path / name, plan_solver([("RIGHT", 16)]), **kwargs)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def rewrite(root, name, data):
    """Replace one file and keep the manifest hash list (and its bytes) canonical."""
    (root / name).write_bytes(data)
    manifest = json.loads((root / "manifest.json").read_bytes())
    manifest["files"][name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    (root / "manifest.json").write_bytes(canonical(manifest))


class LooseBuilder(r.ReferenceBuilder):
    """Stands in for a future builder that emits valid but non-canonical JSON."""
    name = "loose-test-builder"

    def build(self, spec, root):
        super().build(spec, root)
        rules = json.loads((Path(root) / "rules.json").read_bytes())
        rewrite(Path(root), "rules.json", json.dumps(rules, indent=2).encode())
        return p.Artifact.load(Path(root))


def test_one_pass_reaches_verified_win_and_restores(tmp_path):
    report = win(tmp_path)
    assert report.terminal == "verified_win" and report.success and report.restored
    assert report.phases == (("build", "ok"), ("review", "pass"), ("freeze", "ok"),
                             ("probe", "rejected"), ("play", "candidate_win"),
                             ("replay", "verified_win"), ("cleanup", "once"))
    assert tuple(name for name, _ in report.phases) == r.PHASES
    assert report.accepted_inputs == 1 and report.dropped_inputs == 0 and report.steps == 1
    assert report.execution == "trusted_synthetic_fixture" and report.preflight == "not_supplied"
    assert report.seed == 7 and report.artifact == report.review.identity
    assert report.review.decision == "pass" and report.review.failures == ()
    assert report.review.boot_smoke == "not_run_no_sandbox"
    assert report.review.code_review == "not_performed_no_reviewer"
    assert report.evidence["reason"] == "candidate_win" and report.evidence["cutoff_tick"] == 16
    # The judge evidence replays against the bundle the run actually froze.
    assert p.verify_replay(tmp_path / "bundle", p.Artifact.load(tmp_path / "bundle"),
                           report.evidence)
    assert tuple(condition for condition, _, _ in report.acceptance) == tuple(
        name for name, _ in r.CONDITIONS)
    assert dict((name, status) for name, status, _ in report.acceptance) == {
        "one_game_built": "partial", "freeze_blocks_write": "verified",
        "win_is_distinguished": "verified", "reproducible": "verified",
        "isolation_and_restore": "partial"}


def test_build_is_deterministic_for_a_spec_and_follows_the_seed(tmp_path):
    first = r.ReferenceBuilder().build(spec(), tmp_path / "a")
    second = r.ReferenceBuilder().build(spec(), tmp_path / "b")
    other = r.ReferenceBuilder().build(spec(seed=8), tmp_path / "c")
    assert first.identity == second.identity and first.files == second.files
    assert other.seed == 8 and other.identity != first.identity
    assert [name for name, _ in first.files] == ["game.mjs", "manifest.json", "rules.json"]


def test_same_spec_and_inputs_reproduce_the_same_result(tmp_path):
    evidence = []
    for name in ("first", "second"):
        report = win(tmp_path, name=name)
        assert report.terminal == "verified_win"
        evidence.append((report.artifact, report.seed, tuple(report.evidence["hashes"])))
    assert len(set(evidence)) == 1
    # The run never writes to the frozen bundle: an independent build is identical.
    reference = r.ReferenceBuilder().build(spec(), tmp_path / "reference")
    assert {path.name: path.read_bytes() for path in (tmp_path / "first").iterdir()} == \
           {path.name: path.read_bytes() for path in (tmp_path / "reference").iterdir()}
    assert reference.identity == evidence[0][0]


@pytest.mark.parametrize("name,changes", [
    ("seed_true", {"seed": True}), ("seed_negative", {"seed": -1}),
    ("seed_float", {"seed": 1.5}), ("bad_artifact_id", {"artifact_id": "../old"}),
    ("empty_source", {"source": b""}), ("source_str", {"source": "code"}),
    ("large_source", {"source": b"x" * (r.MAX_SOURCE + 1)}),
    ("rules_not_dict", {"rules": []}),
    ("rules_unknown_field", {"rules": {**copy.deepcopy(RULES), "enemies": []}}),
    ("rules_not_serializable", {"rules": {**copy.deepcopy(RULES), "cells": [object()]}}),
    ("cell_out_of_range", {"rules": {**copy.deepcopy(RULES),
                                     "key": {"id": "key", "cell": [16, 1]}}}),
    ("cell_on_wall", {"rules": {**copy.deepcopy(RULES),
                                "key": {"id": "key", "cell": [0, 0]}}}),
    ("duplicate_id", {"rules": {**copy.deepcopy(RULES),
                                "key": {"id": "player", "cell": [2, 1]}}}),
    ("asset_name", {"assets": (("logo.png", b"x"),)}),
    ("asset_data", {"assets": (("assets/a.png", "x"),)}),
    ("asset_too_large", {"assets": (("assets/a.png", b"x" * (r.MAX_ASSET_BYTES + 1)),)}),
    ("too_many_assets", {"assets": tuple(("assets/a%d.png" % i, b"x")
                                         for i in range(r.MAX_ASSETS + 1))}),
])
def test_build_rejects_out_of_contract_specs(tmp_path, name, changes):
    with pytest.raises(p.Rejected) as exc:
        r.ReferenceBuilder().build(spec(**changes), tmp_path / name)
    assert str(exc.value)
    assert not (tmp_path / name).exists() or not any((tmp_path / name).iterdir())


def test_build_directory_contract_is_fail_closed(tmp_path):
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep").write_bytes(b"1")
    with pytest.raises(p.Rejected, match="build_dir_not_empty"):
        r.ReferenceBuilder().build(spec(), occupied)
    assert (occupied / "keep").read_bytes() == b"1"
    with pytest.raises(p.Rejected, match="invalid_build_dir"):
        r.ReferenceBuilder().build(spec(), tmp_path / "absent" / "bundle")
    link = tmp_path / "link"
    link.symlink_to(tmp_path)
    with pytest.raises(p.Rejected, match="invalid_build_dir"):
        r.ReferenceBuilder().build(spec(), link)


def test_failed_build_reports_build_failed_and_takes_no_ownership(tmp_path):
    corner = p.MockCorner()
    report = r.run_fixture(spec(seed=True), tmp_path / "bundle", [], corner=corner)
    assert report.terminal == "build_failed" and not report.success and report.restored
    assert report.phase("build") == "failed:invalid_integer"
    assert report.phase("cleanup") == "not_required"
    assert report.evidence is None and report.artifact is None
    assert not corner.owner and corner.events == [] and corner.children == ()
    assert all(report.phase(phase) == "not_run" for phase in
               ("review", "freeze", "probe", "play", "replay"))


def test_review_gate_refuses_a_non_canonical_bundle_from_a_builder(tmp_path):
    report = r.run_fixture(spec(), tmp_path / "bundle", plan_solver([("RIGHT", 16)]),
                           builder=LooseBuilder())
    assert report.terminal == "invalid_artifact" and not report.success and report.restored
    assert report.phase("build") == "ok" and report.phase("review") == "invalid_artifact"
    assert report.review.decision == "invalid_artifact"
    assert report.review.failures == ("canonical_rules_json",)
    assert all(report.phase(phase) == "not_run" for phase in
               ("freeze", "probe", "play", "replay"))
    assert report.phase("cleanup") == "not_required"


def test_review_refuses_a_blank_source_bundle(tmp_path):
    artifact = r.ReferenceBuilder().build(spec(), tmp_path / "bundle")
    rewrite(tmp_path / "bundle", "game.mjs", b"")
    assert p.Artifact.load(tmp_path / "bundle").identity != artifact.identity
    review = r.review_bundle(p.Artifact.load(tmp_path / "bundle"))
    assert review.decision == "invalid_artifact" and review.failures == ("game_source_bounds",)
    assert r.review_bundle(artifact).decision == "pass"


def test_freeze_rejects_a_post_build_change_before_play(tmp_path):
    artifact = r.ReferenceBuilder().build(spec(), tmp_path / "bundle")
    rewrite(tmp_path / "bundle", "rules.json",
            json.dumps(copy.deepcopy(RULES)).encode().replace(b'"gates"', b'"gates"  '))
    p.Artifact.load(tmp_path / "bundle")  # still a loadable bundle, just not the frozen one
    with pytest.raises(p.Rejected, match="artifact_changed"):
        r.freeze(tmp_path / "bundle", artifact)
    frozen = r.freeze(tmp_path / "bundle", p.Artifact.load(tmp_path / "bundle"))
    assert frozen.identity == p.Artifact.load(tmp_path / "bundle").identity
    assert frozen.seed == 7 and dict(frozen.files)["game.mjs"]


def test_post_freeze_change_during_play_is_caught_by_replay(tmp_path):
    root = tmp_path / "bundle"
    state = {"seq": 0, "index": 0}
    plan = [("RIGHT", 8), ("RIGHT", 8)]

    def mutating_solver(observation):
        if state["index"] >= len(plan):
            return None
        button, ticks = plan[state["index"]]
        state["index"] += 1
        state["seq"] += 1
        if state["index"] == 2:
            (root / "rules.json").write_bytes((root / "rules.json").read_bytes() + b" ")
        return json.dumps({"frame_id": observation["frame_id"], "seq": state["seq"],
                           "buttons": [button], "ticks": ticks}).encode()

    report = r.run_fixture(spec(), root, mutating_solver)
    assert report.terminal == "replay_mismatch" and not report.success
    assert report.phase("play") == "candidate_win" and report.phase("replay") == "replay_mismatch"
    assert report.restored and report.phase("cleanup") == "once"
    assert dict((name, status) for name, status, _ in report.acceptance)[
        "reproducible"] == "not_satisfied"


def test_probe_refuses_state_writes_and_keeps_a_working_control(tmp_path):
    artifact = r.ReferenceBuilder().build(spec(), tmp_path / "bundle")
    probe = r.probe_input_contract(tmp_path / "bundle", artifact)
    assert probe["state_write"] == "rejected"
    assert set(probe["attempts"]) == set(name for name, _ in r.WRITE_ATTEMPTS)
    assert set(probe["attempts"].values()) == {"rejected"}
    assert (probe["control"], probe["control_state"]) == ("accepted", "advanced")
    # The bundle is untouched by the probe sessions.
    assert p.Artifact.load(tmp_path / "bundle").identity == artifact.identity


def test_a_forged_state_write_during_play_is_refused_and_not_applied(tmp_path):
    state = {"step": 0}

    def forged_then_win(observation):
        if state["step"] == 0:
            state["step"] = 1
            return json.dumps({"frame_id": observation["frame_id"], "seq": 1,
                               "buttons": [], "ticks": 1, "success": True}).encode()
        if state["step"] == 1:
            state["step"] = 2
            return json.dumps({"frame_id": observation["frame_id"], "seq": 1,
                               "buttons": ["RIGHT"], "ticks": 16}).encode()
        return None

    report = r.run_fixture(spec(), tmp_path / "bundle", forged_then_win)
    assert report.terminal == "verified_win"
    assert report.steps == 2 and report.accepted_inputs == 1
    assert report.evidence["inputs"] == [{"accepted": False, "start_tick": 0},
                                        report.evidence["inputs"][1]]
    accepted = report.evidence["inputs"][1]
    assert accepted["accepted"] and accepted["seq"] == 1 and accepted["start_tick"] == 0
    assert report.evidence["cutoff_tick"] == 16


def test_three_refusals_end_in_invalid_input_and_restore(tmp_path):
    def refused(_observation):
        return b'{"frame_id":"stale","seq":1,"buttons":[],"ticks":1}'

    report = r.run_fixture(spec(), tmp_path / "bundle", refused)
    assert report.terminal == "invalid_input" and report.phase("play") == "invalid_input"
    assert report.phase("replay") == "invalid_input" and report.steps == 3
    assert not report.success and report.restored and report.phase("cleanup") == "once"
    assert dict((name, status) for name, status, _ in report.acceptance)[
        "win_is_distinguished"] == "verified"


def test_no_input_ends_not_cleared_and_replays(tmp_path):
    report = r.run_fixture(spec(), tmp_path / "bundle", lambda _observation: None)
    assert report.terminal == "not_cleared" and report.phase("play") == "not_cleared"
    assert report.phase("replay") == "not_cleared" and report.steps == 0
    assert report.evidence["reason"] == "cancelled" and report.restored


def test_timeout_is_reported_as_a_non_win(tmp_path):
    report = r.run_fixture(spec(), tmp_path / "bundle", plan_solver([(None, 60)] * 40))
    assert report.terminal == "timeout" and not report.success and report.restored
    assert report.phase("play") == "timeout" and report.phase("replay") == "timeout"
    assert report.evidence["reason"] == "tick_limit"
    statuses = dict((name, status) for name, status, _ in report.acceptance)
    assert statuses["win_is_distinguished"] == "verified"
    assert statuses["reproducible"] == "verified"


def test_inputs_after_the_terminal_are_dropped(tmp_path):
    artifact = r.ReferenceBuilder().build(spec(), tmp_path / "reference")
    initial = p.State(0, artifact.rules.player, gates=(False,) * len(artifact.rules.gates))
    frame_id = p._digest(("%s:0:%s" % (artifact.identity,
                                      p.state_hash(initial))).encode())
    request = json.dumps({"frame_id": frame_id, "seq": 1, "buttons": ["RIGHT"],
                          "ticks": 16}).encode()
    report = r.run_fixture(spec(), tmp_path / "bundle", [request, request, request])
    assert report.terminal == "verified_win" and report.artifact == artifact.identity
    assert report.accepted_inputs == 1 and report.dropped_inputs == 2 and report.steps == 1
    assert report.evidence["cutoff_tick"] == 16


def test_failed_restore_is_never_a_successful_run(tmp_path):
    report = win(tmp_path, corner=p.MockCorner(restore_ok=False))
    assert report.terminal == "verified_win" and not report.success and not report.restored
    assert report.phase("cleanup") == "once"
    assert dict((name, status) for name, status, _ in report.acceptance)[
        "isolation_and_restore"] == "not_satisfied"


def test_restore_events_are_the_trusted_corner_sequence(tmp_path):
    corner = p.MockCorner()
    corner.acquire()
    corner.finish()
    assert list(corner.events) == list(r.RESTORE_EVENTS)
    report = win(tmp_path)
    assert report.phase("cleanup") == "once" and report.restored


def test_review_flags_are_observations_not_a_gate(tmp_path):
    flagged = b"// no network: require('net')\nexport function draw() { return 1; }\n"
    report = r.run_fixture(spec(source=flagged), tmp_path / "bundle",
                           plan_solver([("RIGHT", 16)]))
    assert "require(" in report.review.source_flags
    assert report.review.decision == "pass" and report.terminal == "verified_win"
    # The fixture source advertises WIN/success from tick zero and is never evidence.
    clean = win(tmp_path, name="clean")
    assert b"success: true" in SOURCE and clean.review.source_flags == ()
    assert clean.terminal == "verified_win"


def test_assets_are_carried_into_the_bundle(tmp_path):
    report = r.run_fixture(spec(assets=(("assets/logo.txt", b"self-made"),)),
                           tmp_path / "bundle", plan_solver([("RIGHT", 16)]))
    assert report.terminal == "verified_win" and report.success
    assert (tmp_path / "bundle" / "assets" / "logo.txt").read_bytes() == b"self-made"


def test_generated_execution_stays_closed_even_with_a_valid_preflight(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("must not execute a child process")
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", forbidden)

    plain = r.run_generated(spec(), tmp_path / "plain")
    assert plain.terminal == "sandbox_violation" and not plain.success and plain.restored
    assert plain.preflight == "not_supplied" and plain.phase("build") == "ok"
    assert plain.phase("play") == "sandbox_violation" and plain.phase("replay") == "not_run"
    assert plain.phase("cleanup") == "not_required" and plain.evidence is None
    assert plain.execution == "generated_execution_closed"

    digest = "sha256:" + "0" * 64
    report = {"version": 1, "image_digest": digest, "cpu_millicores": 1000,
              "memory_bytes": 256 * 1024 * 1024, "process_limit": 16,
              "output_bytes": 16 * 1024 * 1024, "tmp_bytes": 1024,
              "os_isolation": True, "non_root": True, "network_disabled": True,
              "no_capabilities": True, "bundle_read_only": True, "host_home_hidden": True,
              "credentials_hidden": True, "docker_socket_hidden": True,
              "no_host_fallback": True}
    validated = r.run_generated(spec(), tmp_path / "validated", preflight=report,
                                expected_image_digest=digest)
    assert validated.preflight == "validated" and validated.terminal == "sandbox_violation"

    broken = r.run_generated(spec(), tmp_path / "broken",
                             preflight={**report, "network_disabled": False},
                             expected_image_digest=digest)
    assert broken.preflight == "rejected:preflight_incomplete"
    assert broken.terminal == "sandbox_violation"


def test_runner_has_no_execution_or_production_registration_surface():
    source = Path(r.__file__).read_text()
    for forbidden in ("subprocess", "socket", "urllib", "Popen", "corner_catalog",
                      "game_switch", "corner_execution"):
        assert forbidden not in source, forbidden
    assert not hasattr(r, "register") and not hasattr(r, "start_production")
    for name in ("run_fixture", "run_generated", "review_bundle", "freeze",
                 "probe_input_contract", "BuildSpec", "RunReport", "Review"):
        assert hasattr(r, name), name
    assert r.ReferenceBuilder.name == "trusted-reference-builder-v1"
