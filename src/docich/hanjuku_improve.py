"""Bounded post-run improvement proposals for the Hanjuku script bot (#954).

Terminal-only first unit: build one fixed-schema candidate from a finished
run's trace, compare it against the known-good baseline on the same trace
plus fixed fixtures, and record the verdict durably.

Hard boundaries (fail closed):

- The live control loop is untouched: no provider/LLM import, no adapter,
  no input, no chart/policy edit. An ``accepted`` verdict only means a human
  may propose the candidate in a PR (``applied`` is always ``False`` here;
  automatic promotion is a separate follow-up).
- A candidate may only tune the allowlisted thresholds in ``PARAM_SPEC``.
  Unknown keys, non-finite numbers, bools-as-numbers and out-of-range values
  are rejected, never clamped.
- Only the trace's exact runtime/generation/lease identity is accepted.
- The durable verdict carries allowlisted numbers and fixed reason enums.
  Raw exceptions, raw stderr, env and any unexpected trace keys are dropped,
  never persisted.
"""
from __future__ import annotations

import math
import time
from pathlib import Path

from .game_switch import atomic_write_json
from .hanjuku_run import (
    BATTLE_PHASE_DEBOUNCE,
    MAX_SAMPLE_GAP,
    STALL_SECONDS,
    TERMINAL_REASONS,
)

SCHEMA = 1
BASELINE_VERSION = 1
EVALUATION_FILE = "hanjuku_improvement_eval.json"

BASELINE_PARAMS = {
    "stall_seconds": float(STALL_SECONDS),
    "sample_gap_s": float(MAX_SAMPLE_GAP),
    "battle_debounce": int(BATTLE_PHASE_DEBOUNCE),
}

# Allowlisted tunable thresholds: (kind, minimum, maximum).
PARAM_SPEC = {
    "stall_seconds": ("number", 60.0, 900.0),
    "sample_gap_s": ("number", 5.0, 60.0),
    "battle_debounce": ("int", 1, 5),
}

REASONS = frozenset({
    "unknown_key",
    "missing_key",
    "bad_type",
    "bool_as_number",
    "non_finite",
    "out_of_range",
    "bad_summary",
    "identity_mismatch",
    "not_terminal",
    "bad_counter",
    "insufficient_evidence",
    "trace_inconsistent",
    "evaluator_inconsistent",
    "fixture_regression",
    "would_truncate_run",
    "accepted",
})

MIN_OBSERVATIONS = 30
MAX_RECORDS = 200000
MAX_DETAIL = 240


class HanjukuImproveError(ValueError):
    """Fail-closed rejection carrying a fixed reason enum."""

    def __init__(self, reason: str, detail: str = "") -> None:
        if reason not in REASONS:
            raise ValueError(f"unknown improve reason: {reason!r}")
        super().__init__(f"{reason}: {_clean(detail)}")
        self.reason = reason


def _clean(value: object) -> str:
    return str(value).replace("\n", " ")[:MAX_DETAIL]


def _as_float(value: object) -> float | None:
    """Finite float or ``None``. Bools are not numbers here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def validate_candidate(doc: object) -> dict:
    """Strictly validate an exact-schema candidate. Never clamps."""
    if not isinstance(doc, dict):
        raise HanjukuImproveError("bad_type", type(doc).__name__)
    keys = set(doc)
    if keys != set(PARAM_SPEC):
        extra = sorted(keys - set(PARAM_SPEC))
        if extra:
            raise HanjukuImproveError("unknown_key", ",".join(extra))
        raise HanjukuImproveError(
            "missing_key", ",".join(sorted(set(PARAM_SPEC) - keys)))
    clean: dict = {}
    for name, (kind, minimum, maximum) in PARAM_SPEC.items():
        value = doc[name]
        if isinstance(value, bool):
            raise HanjukuImproveError("bool_as_number", name)
        if kind == "int":
            if type(value) is not int:
                raise HanjukuImproveError("bad_type", name)
            number = float(value)
        else:
            if not isinstance(value, (int, float)):
                raise HanjukuImproveError("bad_type", name)
            number = float(value)
            if not math.isfinite(number):
                raise HanjukuImproveError("non_finite", name)
        if not minimum <= number <= maximum:
            raise HanjukuImproveError("out_of_range", name)
        clean[name] = int(value) if kind == "int" else number
    return clean


def _check_identity(identity: object) -> dict:
    if not isinstance(identity, dict):
        raise HanjukuImproveError("identity_mismatch", type(identity).__name__)
    want = {"game": "hanjuku-hero", "generation": None,
            "runtime_id": None, "lease_id": None}
    bound = {}
    for key in ("game", "runtime_id", "generation", "lease_id"):
        value = identity.get(key)
        if key == "game":
            if value != "hanjuku-hero":
                raise HanjukuImproveError("identity_mismatch", "game")
        elif key == "generation":
            if type(value) is not int or value < 1:
                raise HanjukuImproveError("identity_mismatch", key)
        elif not isinstance(value, str) or not value:
            raise HanjukuImproveError("identity_mismatch", key)
        bound[key] = value
    return bound


def _counter(run_state: dict, name: str) -> int:
    value = run_state.get(name, 0)
    if type(value) is not int or value < 0:
        raise HanjukuImproveError("bad_counter", name)
    return value


def summarize_run(run_state: object, events: object, identity: object) -> dict:
    """Reduce one finished run to allowlisted metrics. Secret-free."""
    bound = _check_identity(identity)
    if not isinstance(run_state, dict):
        raise HanjukuImproveError("bad_summary", "run_state")
    for key in ("game", "runtime_id", "generation", "lease_id"):
        if run_state.get(key) != bound[key]:
            raise HanjukuImproveError("identity_mismatch", key)
    terminal = run_state.get("terminal_reason")
    if terminal not in TERMINAL_REASONS:
        raise HanjukuImproveError("not_terminal", repr(terminal))
    observations = _counter(run_state, "observations")
    if observations < MIN_OBSERVATIONS:
        raise HanjukuImproveError(
            "insufficient_evidence", f"observations={observations}")
    obs_events: list = []
    if events is not None:
        if not isinstance(events, (list, tuple)):
            raise HanjukuImproveError("bad_summary", "events")
        for index, item in enumerate(events):
            if index >= MAX_RECORDS:
                break
            if isinstance(item, dict) and item.get("event") == "observation":
                obs_events.append(item)
    peak_unchanged = 0.0
    terminal_wall: float | None = None
    previous_at: float | None = None
    max_gap = 0.0
    for item in obs_events:
        stamp = _as_float(item.get("at"))
        if stamp is not None:
            if previous_at is not None and stamp >= previous_at:
                max_gap = max(max_gap, stamp - previous_at)
            previous_at = stamp if previous_at is None else max(previous_at, stamp)
            if item.get("terminal_reason") in TERMINAL_REASONS:
                terminal_wall = stamp if terminal_wall is None else max(
                    terminal_wall, stamp)
        unchanged = _as_float(item.get("unchanged_seconds"))
        if unchanged is not None:
            peak_unchanged = max(peak_unchanged, unchanged)
    wall = _as_float(run_state.get("observed_at"))
    if terminal_wall is None and wall is not None:
        terminal_wall = wall
    if terminal_wall is None:
        raise HanjukuImproveError("trace_inconsistent", "no-terminal-time")
    return {
        "schema": SCHEMA,
        "game": bound["game"],
        "runtime_id": bound["runtime_id"],
        "generation": bound["generation"],
        "lease_id": bound["lease_id"],
        "terminal_reason": terminal,
        "terminal_wall": terminal_wall,
        "observations": observations,
        "actions_sent": _counter(run_state, "actions_sent"),
        "battles_started": _counter(run_state, "battles_started"),
        "battles_finished": _counter(run_state, "battles_finished"),
        "peak_unchanged_s": peak_unchanged,
        "max_gap_s": max_gap,
        "observed_events": len(obs_events),
    }


def suggest_stall(summary: object) -> dict:
    """Deterministic stall suggestion: twice the observed non-terminal peak."""
    if not isinstance(summary, dict):
        raise HanjukuImproveError("bad_summary", "summary")
    peak = _as_float(summary.get("peak_unchanged_s"))
    if peak is None:
        raise HanjukuImproveError("bad_summary", "peak_unchanged_s")
    _, minimum, maximum = PARAM_SPEC["stall_seconds"]
    return {"stall_seconds": min(maximum, max(minimum, 2.0 * peak))}


def build_proposal(summary: object, adjustments: object) -> dict:
    """Bind validated adjustments to one run. No live state is touched."""
    if not isinstance(summary, dict):
        raise HanjukuImproveError("bad_summary", "summary")
    for key in ("runtime_id", "generation", "terminal_reason",
                "observations", "peak_unchanged_s"):
        if key not in summary:
            raise HanjukuImproveError("bad_summary", key)
    if not isinstance(adjustments, dict):
        raise HanjukuImproveError("bad_type", "adjustments")
    unknown = sorted(set(adjustments) - set(PARAM_SPEC))
    if unknown:
        raise HanjukuImproveError("unknown_key", ",".join(unknown))
    merged = dict(BASELINE_PARAMS)
    merged.update(adjustments)
    params = validate_candidate(merged)
    return {
        "schema": SCHEMA,
        "baseline_version": BASELINE_VERSION,
        "params": params,
        "changed": sorted(k for k in PARAM_SPEC if params[k] != BASELINE_PARAMS[k]),
        "run": {
            "runtime_id": summary["runtime_id"],
            "generation": summary["generation"],
        },
        "derived_from": {
            "terminal_reason": summary["terminal_reason"],
            "observations": summary["observations"],
            "peak_unchanged_s": summary["peak_unchanged_s"],
        },
    }


def _replay_latch(samples: list, params: dict) -> float | None:
    """Re-derive the earliest stalled-latch wall time (conservative offline).

    Mirrors the live detector's core: a gap beyond ``sample_gap_s``, a paused
    frame or a changed digest breaks the unchanged run (the two most recent
    distinct digests count as one screen for blinking cursors). Pure.
    """
    stall = _as_float(params.get("stall_seconds"))
    gap = _as_float(params.get("sample_gap_s"))
    if stall is None or gap is None:
        raise HanjukuImproveError("bad_type", "params")
    recent: list = []
    since: float | None = None
    previous_at: float | None = None
    previous_playing = True
    for at, digest, playing in samples:
        consecutive = (
            previous_at is not None
            and 0.0 <= at - previous_at <= gap
            and playing and previous_playing
        )
        unchanged = consecutive and digest in recent
        recent = [digest] + [d for d in recent if d != digest][:1]
        if not unchanged:
            since = at
        if since is not None and at - since >= stall:
            return at
        previous_at = at
        previous_playing = playing
    return None


def _samples(events: object) -> list:
    samples = []
    if not isinstance(events, (list, tuple)):
        return samples
    for index, item in enumerate(events):
        if index >= MAX_RECORDS:
            break
        if not isinstance(item, dict) or item.get("event") != "observation":
            continue
        at = _as_float(item.get("at"))
        digest = item.get("frame_sha256")
        playing = item.get("playing", True)
        if (at is None or not isinstance(digest, str) or not digest
                or not isinstance(playing, bool)):
            continue
        samples.append((at, digest, playing))
    samples.sort(key=lambda row: row[0])
    return samples


def _fixture_steady(total_s: float = 1000.0, step_s: float = 2.0) -> list:
    at = 1000.0
    samples = []
    while at - 1000.0 < total_s:
        samples.append((at, "steady", True))
        at += step_s
    return samples


def _fixture_gappy(total_s: float = 2000.0, step_s: float = 2.0) -> list:
    samples = []
    at = 1000.0
    while at - 1000.0 < total_s:
        samples.append((at, "steady", True))
        at += step_s
        if 1198.0 <= at < 1228.0:
            at = 1230.0  # one 30s observation gap mid-run
    return samples


def _fixture_flapping(total_s: float = 1000.0, step_s: float = 2.0) -> list:
    # Three rotating digests: with only two alternating frames the live
    # detector (and this replay) intentionally sees one blinking screen,
    # so the never-latch fixture must rotate three.
    samples = []
    at = 1000.0
    index = 0
    while at - 1000.0 < total_s:
        samples.append((at, f"frame-{index % 3}", True))
        index += 1
        at += step_s
    return samples


def _check_machinery() -> str | None:
    """The replay itself must reproduce the known-good timing first."""
    latch = _replay_latch(_fixture_steady(), BASELINE_PARAMS)
    if latch is None or not 299.0 <= latch - 1000.0 <= 302.0:
        return "evaluator_inconsistent"
    if _replay_latch(_fixture_flapping(), BASELINE_PARAMS) is not None:
        return "evaluator_inconsistent"
    return None


def _check_fixtures(params: dict) -> str | None:
    """A candidate must keep the detector alive and never fire on flapping."""
    if _replay_latch(_fixture_steady(), params) is None:
        return "fixture_regression"
    if _replay_latch(_fixture_flapping(), params) is not None:
        return "fixture_regression"
    if _replay_latch(_fixture_gappy(), params) is None:
        return "fixture_regression"
    return None


def _verdict(status: str, reason: str, summary: dict | None,
             proposal: dict | None, extra: dict | None = None) -> dict:
    body = {
        "schema": SCHEMA,
        "status": status,
        "reason": reason,
        "baseline_version": BASELINE_VERSION,
        "baseline_params": dict(BASELINE_PARAMS),
        "candidate_params": (dict(proposal["params"]) if proposal else None),
        "run": dict(proposal["run"]) if proposal else (
            {"runtime_id": summary.get("runtime_id"),
             "generation": summary.get("generation")} if summary else None),
        "summary": summary,
        "stages": {
            "proposal": "built" if proposal else "rejected",
            "evaluation": status,
            "promotion": "withheld",
        },
        "applied": False,
        "generated_at": time.time(),
    }
    if extra:
        body.update(extra)
    return body


def evaluate(run_state: object, events: object, identity: object,
             adjustments: object) -> dict:
    """Offline gates over one finished run. Never raises on bad content,
    never touches live state, never applies anything."""
    try:
        summary = summarize_run(run_state, events, identity)
    except HanjukuImproveError as exc:
        return _verdict("rejected", exc.reason, None, None,
                        {"detail": _clean(exc)})
    try:
        proposal = build_proposal(summary, adjustments)
    except HanjukuImproveError as exc:
        return _verdict("rejected", exc.reason, summary, None,
                        {"detail": _clean(exc)})
    params = proposal["params"]
    if params == BASELINE_PARAMS:
        return _verdict("rejected", "insufficient_evidence", summary, proposal,
                        {"detail": "candidate matches known-good baseline"})
    failed = _check_machinery()
    if failed is not None:
        return _verdict("rejected", failed, summary, proposal)
    failed = _check_fixtures(params)
    if failed is not None:
        return _verdict("rejected", failed, summary, proposal)
    if params["battle_debounce"] != BASELINE_PARAMS["battle_debounce"]:
        if summary["battles_started"] < 2 or summary["battles_finished"] < 1:
            return _verdict("rejected", "insufficient_evidence", summary,
                            proposal,
                            {"detail": "no battle evidence for debounce change"})
    samples = _samples(events)
    if len(samples) < MIN_OBSERVATIONS:
        return _verdict("rejected", "insufficient_evidence", summary, proposal,
                        {"detail": f"samples={len(samples)}"})
    baseline_latch = _replay_latch(samples, BASELINE_PARAMS)
    if baseline_latch is not None and baseline_latch < summary["terminal_wall"]:
        return _verdict("rejected", "trace_inconsistent", summary, proposal, {
            "detail": "known-good replay diverges from the recorded run"})
    candidate_latch = _replay_latch(samples, params)
    if candidate_latch is not None and candidate_latch < summary["terminal_wall"]:
        return _verdict("rejected", "would_truncate_run", summary, proposal, {
            "candidate_latch_wall": candidate_latch,
            "terminal_wall": summary["terminal_wall"],
        })
    return _verdict("accepted", "accepted", summary, proposal, {
        "candidate_latch_wall": candidate_latch,
        "baseline_latch_wall": baseline_latch,
    })


def record_evaluation(path: str | Path, verdict: object) -> Path:
    """Durably store one verdict. Only allowlisted fields are persisted."""
    if not isinstance(verdict, dict):
        raise HanjukuImproveError("bad_type", "verdict")
    if verdict.get("schema") != SCHEMA or verdict.get("status") not in {
            "accepted", "rejected"}:
        raise HanjukuImproveError("bad_summary", "verdict")
    target = Path(path)
    if target.suffix != ".json":
        target = target / EVALUATION_FILE
    if target.is_symlink():
        raise HanjukuImproveError("bad_summary", "symlink")
    payload = {
        "schema": SCHEMA,
        "status": verdict.get("status"),
        "reason": verdict.get("reason") if verdict.get("reason") in REASONS else "bad_summary",
        "detail": _clean(verdict.get("detail", "")),
        "baseline_version": BASELINE_VERSION,
        "baseline_params": dict(BASELINE_PARAMS),
        "candidate_params": verdict.get("candidate_params"),
        "run": verdict.get("run"),
        "summary": verdict.get("summary"),
        "stages": verdict.get("stages"),
        "applied": False,
        "candidate_latch_wall": verdict.get("candidate_latch_wall"),
        "baseline_latch_wall": verdict.get("baseline_latch_wall"),
        "generated_at": verdict.get("generated_at"),
    }
    atomic_write_json(target, payload)
    return target
