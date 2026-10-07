"""Non-production #380 run coordinator: build -> review -> freeze -> play -> replay.

Hermetic and offline: no model/API call, no child process, no network, and no
production corner registration. The fixed corner stays unregistered, which is
the ``enabled=false`` state the issue requires.

The ``build`` phase uses a trusted, deterministic *reference* builder to
materialise bundle bytes. That is what makes the whole pass executable and
regression-testable. It is not an AI author and it makes no model call: a
future AI builder plugs into :class:`BuildSpec` / ``ReferenceBuilder.build`` and
must pass exactly the same review, freeze, play and replay gates. Generated code
is never imported or executed -- ``start_generated`` stays unconditionally
closed -- so the generated path always terminates in ``sandbox_violation``, and
even a validated preflight report never opens it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from .selfmade_contract import validate_preflight
from .selfmade_prototype import (
    CONTROLS, MAX_BUNDLE, MAX_TICKS, VERSIONS, Artifact, FixtureSession,
    MockCorner, Rejected, Rules, _digest, _encode, _integer, _json, _require,
    state_hash, start_generated,
)

MAX_SOURCE = 64 * 1024
MAX_ASSETS = 8
MAX_ASSET_BYTES = 32 * 1024
MAX_STEPS = MAX_TICKS * 3
PHASES = ("build", "review", "freeze", "probe", "play", "replay", "cleanup")
RESTORE_EVENTS = ("release_keys", "stop_and_reap", "release_owner", "restore")
GOAL = "alive+key+switch+exit"

# Declared solver inputs that try to write host game state. Every one of them is
# refused by the existing strict four-field contract; the probe records that.
# This list is the *input* contract check, not a safety verdict about anything.
WRITE_ATTEMPTS = (
    ("success", {"success": True}),
    ("win_event", {"event": "WIN"}),
    ("win_flag", {"win": True}),
    ("position", {"player": [5, 1]}),
    ("inventory", {"key": True}),
    ("switch", {"switch": True}),
    ("state_object", {"state": {"candidate_win": True}}),
)

# Non-authoritative observation aid for the review phase. A match is recorded,
# never treated as a security verdict or as a reason to run or not run code.
SOURCE_FLAGS = (b"require(", b"process.", b"import(", b"fetch(", b"eval(",
                b"child_process", b"docker.sock", b"/etc/", b"env[")

CONDITIONS = (
    ("one_game_built",
     "one short 2D game is produced by the build phase and re-validated by the same loader"),
    ("freeze_blocks_write",
     "a post-freeze bundle change and a solver-side state write are both refused"),
    ("win_is_distinguished",
     "a normal-input clear is told apart from a forged WIN and from a timeout"),
    ("reproducible",
     "artifact, seed and the accepted input list reproduce the same result"),
    ("isolation_and_restore",
     "isolation/resource limits and post-failure exit plus previous-screen restore"),
)


@dataclass(frozen=True)
class BuildSpec:
    """Authoring input for exactly one bundle.

    The future AI builder's output shape: the bundle bytes it may influence,
    plus the fixed contract fields, which are not spec-settable.
    """
    artifact_id: str
    seed: int
    rules: dict
    source: bytes
    assets: tuple = ()


class ReferenceBuilder:
    """Deterministic offline builder; the seam a future AI builder plugs into.

    Same-input builds are byte-identical, so the frozen identity is reproducible
    from the spec. The builder writes only game.mjs, rules.json, manifest.json
    and declared assets, and it returns the artifact read back by the same
    :class:`Artifact` loader the runner later judges.
    """
    name = "trusted-reference-builder-v1"

    def build(self, spec, root):
        _require(type(spec) is BuildSpec, "invalid_spec")
        root = Path(root)
        _require(not root.is_symlink(), "invalid_build_dir")
        _require(type(spec.artifact_id) is str and
                 re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", spec.artifact_id), "invalid_id")
        _integer(spec.seed, 0, 2**32 - 1)
        _require(type(spec.source) is bytes and 0 < len(spec.source) <= MAX_SOURCE,
                 "invalid_source")
        rules = None
        if type(spec.rules) is dict:
            try:
                rules = _json(_encode(spec.rules))
            except (TypeError, ValueError, RecursionError) as exc:
                raise Rejected("invalid_rules") from exc
        _require(rules is not None, "invalid_rules")
        Rules.parse(rules)
        files = {"game.mjs": spec.source, "rules.json": _encode(rules)}
        assets = tuple(spec.assets)
        _require(len(assets) <= MAX_ASSETS, "asset_limit")
        for entry in assets:
            _require(type(entry) is tuple and len(entry) == 2, "invalid_asset")
            name, data = entry
            _require(type(name) is str and re.fullmatch(r"assets/[a-zA-Z0-9_-]+\.(png|txt)", name)
                     and type(data) is bytes and 0 < len(data) <= MAX_ASSET_BYTES
                     and name not in files, "invalid_asset")
            files[name] = data
        payload = sum(len(data) for data in files.values())
        _require(payload <= MAX_BUNDLE, "bundle_limit")
        manifest = {
            "artifact_id": spec.artifact_id, "versions": VERSIONS, "seed": spec.seed,
            "tick_hz": 20, "controls": CONTROLS, "goal": GOAL, "max_ticks": MAX_TICKS,
            "dependencies": [], "image_digest": None,
            "files": {name: _digest(files[name]) for name in sorted(files)},
        }
        manifest_bytes = _encode(manifest)
        _require(payload + len(manifest_bytes) <= MAX_BUNDLE, "bundle_limit")
        if root.exists():
            _require(root.is_dir() and not any(root.iterdir()), "build_dir_not_empty")
        else:
            _require(root.parent.is_dir(), "invalid_build_dir")
            try:
                root.mkdir()
            except OSError as exc:
                raise Rejected("unwritable_build_dir") from exc
        try:
            for name in sorted(files):
                path = root / name
                if name.startswith("assets/"):
                    (root / "assets").mkdir(exist_ok=True)
                path.write_bytes(files[name])
            (root / "manifest.json").write_bytes(manifest_bytes)
        except OSError as exc:
            raise Rejected("unwritable_build_dir") from exc
        return Artifact.load(root)


@dataclass(frozen=True)
class Review:
    """Mechanical bundle review. Unperformed human/AI review is never implied."""
    identity: str
    checks: tuple
    source_flags: tuple = ()
    code_review: str = "not_performed_no_reviewer"
    boot_smoke: str = "not_run_no_sandbox"

    @property
    def decision(self):
        return "pass" if all(outcome == "pass" for _, outcome in self.checks) else "invalid_artifact"

    @property
    def failures(self):
        return tuple(name for name, outcome in self.checks if outcome != "pass")


def _canonical(data):
    _require(type(data) is bytes and _encode(_json(data)) == data, "non_canonical_json")


def review_bundle(artifact):
    """Review the immutable snapshot: canonical bytes, bounds, source presence.

    Byte-canonical JSON is an *authoring* contract (it is what makes the frozen
    identity reproducible from the spec), not a safety property. Boot smoke and
    code review require a sandbox and a reviewer, neither of which exists here,
    so both are recorded as unperformed instead of being claimed.
    """
    files = dict(artifact.files)
    source = files.get("game.mjs", b"")
    checks = []
    for name, probe in (
        ("canonical_rules_json", lambda: _canonical(files["rules.json"])),
        ("canonical_manifest_json", lambda: _canonical(files["manifest.json"])),
        ("game_source_bounds", lambda: _require(
            type(source) is bytes and 0 < len(source) <= MAX_SOURCE, "invalid_source")),
        ("bundle_size_and_count", lambda: _require(
            len(files) <= 32 and sum(len(data) for data in files.values()) <= MAX_BUNDLE,
            "bundle_limit")),
    ):
        try:
            probe()
            checks.append((name, "pass"))
        except Rejected as exc:
            checks.append((name, str(exc)))
    return Review(artifact.identity, tuple(checks),
                  tuple(flag.decode() for flag in SOURCE_FLAGS if flag in source))


@dataclass(frozen=True)
class Frozen:
    """The frozen point: immutable snapshot re-checked against the disk."""
    identity: str
    seed: int
    files: tuple


def freeze(root, artifact):
    """Re-check the disk against the immutable snapshot; keep no writer handle."""
    artifact.check(root)
    return Frozen(artifact.identity, artifact.seed,
                  tuple((name, _digest(data)) for name, data in artifact.files))


def probe_input_contract(root, artifact):
    """Side probe on throwaway trusted sessions: inputs cannot write game state.

    One control shows a well-formed input is still accepted, so "everything was
    refused" cannot masquerade as a rejection. The bundle on disk is untouched.
    """
    def session():
        return FixtureSession(root, artifact, clock=lambda: 0)

    attempts = {}
    for name, extra in WRITE_ATTEMPTS:
        probe = session()
        try:
            before = state_hash(probe.state)
            accepted = probe.submit(_encode({"frame_id": probe.frame_id, "seq": 1,
                                             "buttons": [], "ticks": 1, **extra}))
            attempts[name] = ("rejected" if not accepted and probe.seq == 0
                              and state_hash(probe.state) == before else "accepted")
        finally:
            probe.cancel()
    control = session()
    try:
        before = state_hash(control.state)
        control.submit(_encode({"frame_id": control.frame_id, "seq": 1,
                                "buttons": [], "ticks": 1}))
        accepted = control.seq == 1 and control.state.tick == 1
        control_result = "accepted" if accepted else "rejected"
        control_state = "advanced" if state_hash(control.state) != before else "unchanged"
    finally:
        control.cancel()
    return {"state_write": "rejected" if all(outcome == "rejected" for outcome
                                             in attempts.values()) else "accepted",
            "attempts": attempts, "control": control_result,
            "control_state": control_state}


@dataclass(frozen=True)
class RunReport:
    """Detached run record. No section of it is a production or VM claim."""
    phases: tuple
    terminal: str
    success: bool
    restored: bool
    execution: str
    artifact: str
    seed: int
    review: Review
    probe: dict
    accepted_inputs: int
    dropped_inputs: int
    steps: int
    evidence: dict
    preflight: str
    acceptance: tuple

    def phase(self, name):
        return dict(self.phases)[name]


def _drive(session, inputs):
    """Feed the trusted driver's raw solver bytes; refuse to invent any state.

    The driver may supply at most MAX_STEPS inputs; a still-open play then ends
    as ``not_cleared``. Inputs offered after the terminal are dropped, not
    applied -- the same rule the design fixes for late model responses.
    """
    accepted = dropped = steps = 0
    iterator = None if callable(inputs) else iter(inputs)
    while (steps < MAX_STEPS and session.result is None
           and not session.state.candidate_win):
        if callable(inputs):
            raw = inputs(session.observation())
            if raw is None:
                break
        else:
            try:
                raw = next(iterator)
            except StopIteration:
                break
        steps += 1
        if session.submit(raw):
            accepted += 1
    if iterator is not None:
        for _ in iterator:
            dropped += 1
    return accepted, dropped, steps


def _finish(phases, terminal, box, corner):
    """Release the corner exactly once, then build the detached report."""
    if corner.owner:
        corner.finish()
    events = list(corner.events)
    if not events:
        # Nothing was hidden, so no restore was required; no owner or child leaked.
        phases["cleanup"] = "not_required"
    elif events == list(RESTORE_EVENTS):
        phases["cleanup"] = "once"
    else:
        phases["cleanup"] = "incomplete"
    restored = corner.previous_visible and not corner.owner
    review = box["review"]
    return RunReport(
        phases=tuple((name, phases[name]) for name in PHASES),
        terminal=terminal,
        success=(terminal == "verified_win" and restored
                 and phases["cleanup"] in ("once", "not_required")),
        restored=restored,
        execution=box["execution"],
        artifact=box["artifact"],
        seed=box["seed"],
        review=review,
        probe=box["probe"],
        accepted_inputs=box["accepted"],
        dropped_inputs=box["dropped"],
        steps=box["steps"],
        evidence=box["evidence"],
        preflight=box["preflight"],
        acceptance=_acceptance(phases, terminal, box, restored, review),
    )


def _acceptance(phases, terminal, box, restored, review):
    """Evaluate the issue's five non-production acceptance conditions.

    ``verified`` = demonstrated by this run's machine-checked evidence.
    ``partial`` = the pipeline is implemented and ran, but a declared external
    precondition (model call, OS sandbox) is absent, so it is not established.
    ``not_satisfied`` = the run's own evidence does not reach the condition.
    """
    stages = dict(phases)
    built = (stages["build"] == "ok" and stages["review"] == "pass"
             and stages["freeze"] == "ok")
    blocked = box["probe"] is not None and box["probe"]["state_write"] == "rejected"
    write_blocked = (blocked and box["probe"].get("control") == "accepted"
                     and box["probe"].get("control_state") == "advanced")
    # candidate_win is only a candidate until the fresh engine replays it; every
    # other play terminal must replay to itself.
    expected = {"candidate_win": "verified_win"}.get(box["play_terminal"],
                                                     box["play_terminal"])
    replayed = (stages["replay"] == expected and terminal == expected)
    out = []
    out.append(("one_game_built",
                "partial" if built else "not_satisfied",
                ("one complete bundle was materialised by %s and re-validated by the same "
                 "Artifact.load gate (identity %s); AI authorship, the model call and code "
                 "review are not exercised, so this is not an AI-authored game"
                 % (ReferenceBuilder.name, review.identity if review else "n/a"))
                if built else "the build/review/freeze phases did not complete"))
    out.append(("freeze_blocks_write",
                "verified" if write_blocked else "not_satisfied",
                ("the frozen snapshot is re-checked against the disk before play and before "
                 "replay, and every declared state-write input (%s) is refused while a "
                 "control input is still accepted"
                 % ", ".join(name for name, _ in WRITE_ATTEMPTS))
                if write_blocked else "post-freeze change / state-write rejection not shown"))
    if terminal == "verified_win":
        win = ("verified",
               "the trusted engine confirmed alive+key+switch+exit on the same tick; the "
               "bundle's own WIN/success tokens are never evidence")
    elif terminal in ("timeout", "invalid_input", "not_cleared"):
        win = ("verified", "%s is reported as a non-win terminal" % terminal)
    else:
        win = ("not_satisfied", "the run ended in %s; a win was not established" % terminal)
    out.append(("win_is_distinguished", win[0], win[1]))
    out.append(("reproducible",
                "verified" if replayed else "not_satisfied",
                ("a fresh trusted engine replayed the accepted input list from the start and "
                 "reproduced every tick hash and the %s result" % terminal) if replayed else
                "the replay phase (%s) did not reproduce the play result (%s)"
                % (stages["replay"], box["play_terminal"])))
    restore_ok = restored and stages["cleanup"] in ("once", "not_required")
    out.append(("isolation_and_restore",
                "partial" if restore_ok else "not_satisfied",
                ("corner cleanup ran once and the previous screen is restored on this "
                 "terminal; the OS sandbox, resource limits, collector and real child "
                 "kill/reap are not implemented, and generated execution stays closed")
                if restore_ok else "cleanup or previous-screen restore was not confirmed"))
    return tuple(out)


def _box(preflight):
    return {"review": None, "probe": None, "artifact": None, "seed": None,
            "accepted": 0, "dropped": 0, "steps": 0,
            "evidence": None, "execution": "trusted_synthetic_fixture",
            "preflight": preflight, "play_terminal": "not_run"}


def _gated(spec, root, builder, phases, box, corner):
    """Run the shared build/review/freeze/probe gates; True when the run may continue."""
    try:
        artifact = builder.build(spec, root)
    except Rejected as exc:
        phases["build"] = "failed:%s" % exc
        return None, _finish(phases, "build_failed", box, corner)
    box["artifact"], box["seed"] = artifact.identity, artifact.seed
    phases["build"] = "ok"
    review = review_bundle(artifact)
    box["review"] = review
    phases["review"] = "pass" if review.decision == "pass" else review.decision
    if review.decision != "pass":
        return None, _finish(phases, "invalid_artifact", box, corner)
    try:
        freeze(root, artifact)
    except Rejected as exc:
        phases["freeze"] = "failed:%s" % exc
        return None, _finish(phases, "invalid_artifact", box, corner)
    phases["freeze"] = "ok"
    probe = probe_input_contract(root, artifact)
    box["probe"] = probe
    phases["probe"] = probe["state_write"]
    return artifact, None


def run_fixture(spec, root, inputs=(), *, corner=None, clock=monotonic, builder=None):
    """One non-production pass on the trusted engine: build -> replay.

    ``inputs`` is either an iterable of raw solver bytes or a callable
    ``next_input(observation) -> bytes | None``. Both are the same solver
    surface: PNG, frame_id, instructions and remaining ticks in, strict JSON
    bytes out. Late inputs are dropped, never applied.
    """
    builder = builder or ReferenceBuilder()
    corner = corner or MockCorner()
    root = Path(root)
    phases = dict.fromkeys(PHASES, "not_run")
    box = _box("not_supplied")
    session = None
    try:
        artifact, report = _gated(spec, root, builder, phases, box, corner)
        if report is not None:
            return report
        try:
            session = FixtureSession(root, artifact, corner=corner, clock=clock)
        except Rejected as exc:
            phases["play"] = "failed:%s" % exc
            return _finish(phases, "invalid_artifact", box, corner)
        accepted, dropped, steps = _drive(session, inputs)
        box.update(accepted=accepted, dropped=dropped, steps=steps)
        if session.result is not None:
            phases["play"] = session.result
        elif session.state.candidate_win:
            phases["play"] = "candidate_win"
        else:
            session.cancel()
            phases["play"] = session.result
        box["play_terminal"] = session.result or phases["play"]
        if session.state.candidate_win or session.result is not None:
            box["evidence"] = session.evidence()
        try:
            terminal = session.verify()
        except Rejected as exc:
            phases["replay"] = "failed:%s" % exc
            return _finish(phases, "invalid_artifact", box, corner)
        phases["replay"] = terminal
        return _finish(phases, terminal, box, corner)
    finally:
        if corner.owner:  # no owner may outlive the run, whatever happened
            corner.finish()


def run_generated(spec, root, *, preflight=None, expected_image_digest=None, builder=None):
    """The generated-code path: same gates, and the execution gate stays closed.

    A validated preflight report is recorded as validated and *still* does not
    authorize execution, because no sandbox implementation exists here.
    """
    builder = builder or ReferenceBuilder()
    root = Path(root)
    phases = dict.fromkeys(PHASES, "not_run")
    status = "not_supplied"
    if preflight is not None or expected_image_digest is not None:
        try:
            validate_preflight(preflight, expected_image_digest=expected_image_digest)
            status = "validated"
        except Rejected as exc:
            status = "rejected:%s" % exc
    box = _box(status)
    box["execution"] = "generated_execution_closed"
    corner = MockCorner()
    artifact, report = _gated(spec, root, builder, phases, box, corner)
    if report is not None:
        return report
    try:
        start_generated(root)
    except Rejected as exc:
        phases["play"] = str(exc)
    else:  # pragma: no cover - the gate is unconditionally closed today
        phases["play"] = "closed_gate_missing"
    box["play_terminal"] = phases["play"]
    return _finish(phases, "sandbox_violation", box, corner)
