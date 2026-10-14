"""Canary promotion gate (P6d).

Capability and policy candidates are promoted by a machine contract, not by a
human in the runtime.  A candidate is promoted only when every reviewed gate
passes; otherwise the previous known-good candidate stays in place.  This
module is the pure decision logic; the episode runner that produces the inputs
is separate so this can be unit-tested exhaustively.

Fitness is lexicographic ``(max_depth, score, turns)``.  Depth is the primary
term on purpose: promoting on ``turns`` alone would make an endless-rest
policy the optimum (the P5k canary introduced ``rest`` precisely because of
that risk).
"""
from __future__ import annotations

from dataclasses import dataclass, field

PROMOTE = "promote"
REJECT = "reject"

# Machine-readable reject reasons.
REASON_SMOKE = "smoke_gate_failed"
REASON_ISOLATION = "candidate_isolation_violation"
REASON_TRACE = "trace_unverified"
REASON_REGRESSION = "regression_failed"
REASON_INSUFFICIENT = "insufficient_episodes"
REASON_UNPAIRED = "unpaired_seeds"
REASON_FITNESS = "fitness_regression"
REASON_DEGENERATE = "degenerate_fitness"

_FINISHED_TERMINALS = frozenset({"dead", "ascended", "ended", "ended_unknown"})


@dataclass(frozen=True)
class EpisodeOutcome:
    seed: int
    arm: str
    status: str
    terminal_status: str
    turns: int | None
    max_depth: int | None
    score: int | None
    exit_reason: str = ""
    production_state_touched: bool = False
    production_fingerprint_unchanged: bool = True
    container_cleanup_ok: bool = True

    def fitness(self) -> tuple[int, int, int]:
        return (
            self.max_depth or 0,
            self.score if self.score is not None else 0,
            self.turns or 0,
        )

    def isolation_ok(self) -> bool:
        return (
            self.production_state_touched is False
            and self.production_fingerprint_unchanged is True
            and self.container_cleanup_ok is True
        )

    def reached_terminal(self) -> bool:
        return self.terminal_status in _FINISHED_TERMINALS


@dataclass(frozen=True)
class GateConfig:
    min_episodes_per_arm: int = 3
    # Fraction of paired seeds where the candidate must be non-regressed.
    required_non_regression: float = 1.0
    require_trace_verified: bool = True
    require_regression_green: bool = True
    # Guard against promoting on turns alone: the candidate must not lose depth
    # on any paired seed.
    max_depth_non_regression: bool = True
    # NetHack is not fully reproducible even with a controlled RNG seed (time
    # dependent code), so turns may fluctuate a little between two runs.  A
    # candidate within this fraction of the baseline turns is still treated as
    # non-regressed.
    turn_tolerance_ratio: float = 0.15


@dataclass(frozen=True)
class PromotionInputs:
    candidate_id: str
    baseline_id: str
    baseline: tuple[EpisodeOutcome, ...]
    candidate: tuple[EpisodeOutcome, ...]
    trace_unverified: int = 0
    regression_green: bool = True
    smoke_ok: bool = True

    def trace_verified(self) -> bool:
        return self.trace_unverified == 0


@dataclass(frozen=True)
class PromotionDecision:
    status: str
    candidate_id: str
    baseline_id: str
    reasons: tuple[str, ...] = field(default_factory=tuple)
    promote: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "promote": self.promote,
            "candidate_id": self.candidate_id,
            "baseline_id": self.baseline_id,
            "reasons": list(self.reasons),
        }


def _reject(inputs: PromotionInputs, reasons: list[str]) -> PromotionDecision:
    return PromotionDecision(
        status=REJECT,
        candidate_id=inputs.candidate_id,
        baseline_id=inputs.baseline_id,
        reasons=tuple(reasons),
        promote=False,
    )


def paired_seeds(inputs: PromotionInputs) -> tuple[int, ...]:
    return tuple(sorted({item.seed for item in inputs.candidate}))


def _non_regressed(
    baseline: EpisodeOutcome, candidate: EpisodeOutcome, config: GateConfig
) -> bool:
    """Depth and score must not regress; turns may drift within tolerance."""
    if (candidate.max_depth or 0) < (baseline.max_depth or 0):
        return False
    if (candidate.score or 0) < (baseline.score or 0):
        return False
    baseline_turns = baseline.turns or 0
    candidate_turns = candidate.turns or 0
    if baseline_turns > 0 and candidate_turns < baseline_turns * (1.0 - config.turn_tolerance_ratio):
        return False
    return True


def evaluate_promotion(
    inputs: PromotionInputs, config: GateConfig | None = None
) -> PromotionDecision:
    """Return the machine promotion decision for a canary candidate."""
    config = config or GateConfig()
    reasons: list[str] = []

    if not inputs.smoke_ok:
        reasons.append(REASON_SMOKE)
    if not inputs.regression_green and config.require_regression_green:
        reasons.append(REASON_REGRESSION)
    if not inputs.trace_verified() and config.require_trace_verified:
        reasons.append(REASON_TRACE)
    if any(not item.isolation_ok() for item in inputs.candidate):
        reasons.append(REASON_ISOLATION)
    if reasons:
        return _reject(inputs, reasons)

    if (
        len(inputs.baseline) < config.min_episodes_per_arm
        or len(inputs.candidate) < config.min_episodes_per_arm
    ):
        return _reject(inputs, [REASON_INSUFFICIENT])

    baseline_by_seed = {item.seed: item for item in inputs.baseline}
    candidate_by_seed = {item.seed: item for item in inputs.candidate}
    if set(baseline_by_seed) != set(candidate_by_seed):
        return _reject(inputs, [REASON_UNPAIRED])

    non_regressed = 0
    depth_regressed = False
    for seed, candidate in candidate_by_seed.items():
        baseline = baseline_by_seed[seed]
        if _non_regressed(baseline, candidate, config):
            non_regressed += 1
        if config.max_depth_non_regression and (candidate.max_depth or 0) < (baseline.max_depth or 0):
            depth_regressed = True

    # A candidate that never reaches any finished terminal and only "wins" on
    # turns is the degenerate endless-rest case.
    if not any(item.reached_terminal() for item in inputs.candidate) and not any(
        item.reached_terminal() for item in inputs.baseline
    ):
        if all((item.max_depth or 0) == 0 for item in inputs.candidate):
            return _reject(inputs, [REASON_DEGENERATE])

    fraction = non_regressed / len(candidate_by_seed)
    if depth_regressed or fraction < config.required_non_regression:
        return _reject(inputs, [REASON_FITNESS])

    return PromotionDecision(
        status=PROMOTE,
        candidate_id=inputs.candidate_id,
        baseline_id=inputs.baseline_id,
        reasons=(),
        promote=True,
    )


@dataclass(frozen=True)
class KnownGood:
    candidate_id: str

    def to_dict(self) -> dict[str, object]:
        return {"candidate_id": self.candidate_id}


def apply_decision(known_good: KnownGood, decision: PromotionDecision) -> KnownGood:
    """Apply a decision: promote moves the pointer, reject keeps the known-good."""
    if decision.promote:
        return KnownGood(candidate_id=decision.candidate_id)
    return known_good
