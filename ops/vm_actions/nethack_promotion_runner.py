#!/usr/bin/env python3
"""P6e: run seed-paired baseline/candidate canary episodes and gate promotion.

This is the ops-layer runner.  It reuses the reviewed container launcher and
the P6 catalog/trace verification, and hands the collected outcomes to the
pure promotion gate.  Both arms run the canary tactical baseline policy; the
only difference is the action catalog injected via ``DOCICH_CANARY_CATALOG``,
so a candidate is a catalog diff (enable/disable, priority, preconditions,
or a new action id reusing a reviewed effect).

Contract notes for the automatic (post-expedition) path:

* Both arms run with an **explicit catalog artifact**: baseline with the
  known-good catalog, candidate with the proposed catalog.  The image-bundled
  default catalog is never substituted for a named known-good, so the gate
  decision can be tied to the artifact that was actually injected.
* ``ArmResult.catalog_sha256`` records the digest of that injected artifact.
* trace verification covers **both** arms, not just the candidate.  Absence of
  evidence is counted as unverified, never as zero violations.
* the promotion gates (regression / smoke / production isolation) are
  explicit arguments without permissive defaults: a caller that cannot supply
  the evidence does not get a passing default, it fails to call at all.

The production-isolation checks (fingerprint / container cleanup) are supplied
by the caller through ``isolation_check`` so this module stays unit-testable.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Mapping

from docich.nethack_action_spec import load_action_catalog, verify_trace_file
from docich.nethack_canary_tactics import SUPPORTED_EFFECTS
from docich.nethack_catalog_proposer import (
    FailureSignal,
    build_proposal_request,
    catalog_to_dict,
    failure_signal_from_outcomes,
)
from docich.nethack_promotion_gate import (
    EpisodeOutcome,
    GateConfig,
    PromotionDecision,
    PromotionInputs,
    evaluate_promotion,
)

REQUEST_SCHEMA_VERSION = 1
REQUIREMENTS = {
    "isolation_mode": "container",
    "production_state_must_remain_untouched": True,
    "wizard_mode": False,
    "explore_mode": False,
}

# The canary worker's ``arm`` field selects the **controller kind**, not the
# experiment arm: ``baseline`` means the reviewed ``baseline_p3b`` policy, and
# ``candidate`` means the candidate-strategist broker controller.  Both
# promotion arms replay the reviewed baseline policy and differ only in the
# injected catalog, so both must use the ``baseline`` controller.  The
# experiment arm is carried by ``ArmResult.arm`` and by per-arm trace
# ownership; recording an arm from the controller kind would let a candidate
# episode be read back as a baseline result.
CONTROLLER_KIND = "baseline_p3b"
WIRE_ARM = "baseline"

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class PromotionRunError(RuntimeError):
    """The run spec is not usable as promotion evidence."""


@dataclass(frozen=True)
class PromotionRunSpec:
    candidate_id: str
    baseline_id: str
    baseline_catalog: Path
    seeds: tuple[int, ...]
    # Explicit candidate artifact for ``run_promotion``.  ``run_improvement_cycle``
    # generates its own candidate catalog from the proposer, so it leaves this unset.
    candidate_catalog: Path | None = None
    player_prefix: str = "canary_prom"
    max_turns: int = 1000
    wall_timeout_s: float = 300.0
    inter_episode_timeout_s: float = 900.0

    def experiment_id(self) -> str:
        return _experiment_id(self.baseline_id, self.candidate_id)


@dataclass(frozen=True)
class ArmResult:
    arm: str
    experiment_id: str
    controller_kind: str
    catalog_sha256: str
    outcomes: tuple[EpisodeOutcome, ...]
    trace_unverified: int


def _sanitize_id(raw: object) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(raw).strip())
    cleaned = re.sub(r"^[^A-Za-z0-9]+", "", cleaned)
    return cleaned[:32] or "unknown"


def _experiment_id(baseline_id: str, candidate_id: str) -> str:
    experiment = f"promo-{_sanitize_id(baseline_id)}-vs-{_sanitize_id(candidate_id)}"[:80]
    if _ID_RE.fullmatch(experiment) is None:  # pragma: no cover - defensive
        raise PromotionRunError("promotion experiment id is not usable as evidence")
    return experiment


def _catalog_digest(catalog: Path) -> str:
    return "sha256:" + hashlib.sha256(Path(catalog).read_bytes()).hexdigest()


def _validate_spec(spec: PromotionRunSpec, *, require_candidate_catalog: bool = True) -> None:
    """Reject specs whose evidence could be duplicated or misread."""
    for label, value in (("baseline_id", spec.baseline_id), ("candidate_id", spec.candidate_id)):
        if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
            raise PromotionRunError(f"promotion {label} must match {_ID_RE.pattern}")
    if spec.baseline_id == spec.candidate_id:
        raise PromotionRunError("promotion baseline_id and candidate_id must differ")
    seeds = spec.seeds
    if not seeds:
        raise PromotionRunError("promotion run spec needs at least one seed")
    if any(type(seed) is not int or seed < 0 for seed in seeds):
        raise PromotionRunError("promotion seeds must be non-negative integers")
    if len(set(seeds)) != len(seeds):
        # Duplicated seeds would collapse in the paired comparison and let one
        # episode stand in for two.
        raise PromotionRunError("promotion run spec has duplicate seeds")
    catalogs: list[tuple[str, Path]] = [("baseline_catalog", Path(spec.baseline_catalog))]
    if require_candidate_catalog:
        if spec.candidate_catalog is None:
            # No image-bundled candidate fallback: the artifact under test must
            # be named explicitly.
            raise PromotionRunError("promotion candidate_catalog is required")
        catalogs.append(("candidate_catalog", Path(spec.candidate_catalog)))
    for label, path in catalogs:
        if not path.is_file():
            raise PromotionRunError(f"promotion {label} is not a readable file: {path}")


def _arena(root: Path) -> dict[str, Path]:
    playground = root / "playground"
    save = playground / "save"
    dumps = playground / "dumps"
    for path in (root, playground, save, dumps):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return {
        "episode_root": root.resolve(),
        "playground_dir": playground.resolve(),
        "save_dir": save.resolve(),
        "xlogfile": (playground / "xlogfile").resolve(),
        "dump_dir": dumps.resolve(),
    }


def _request(
    arena: dict[str, Path],
    *,
    experiment_id: str,
    episode_id: str,
    seed: int,
    player: str,
    max_turns: int,
    timeout_s: float,
) -> dict[str, object]:
    return {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "episode_id": episode_id,
        # Wire arm = controller kind.  See CONTROLLER_KIND above.
        "arm": WIRE_ARM,
        "arena": {key: str(value) for key, value in arena.items()},
        "player_name": player,
        "max_turns": max_turns,
        "wall_timeout_s": timeout_s,
        "seed": seed,
        "controller": {"kind": CONTROLLER_KIND},
        "requirements": dict(REQUIREMENTS),
    }


def _catalog_env(catalog: Path, episode_root: Path) -> dict[str, str]:
    destination = episode_root / "catalog.json"
    destination.write_text(Path(catalog).read_text(encoding="utf-8"), encoding="utf-8")
    return {"DOCICH_CANARY_CATALOG": "/canary/episode/catalog.json"}


def _episode_outcome(seed: int, arm: str, result: Mapping[str, object]) -> EpisodeOutcome:
    terminal = result.get("terminal_status")
    return EpisodeOutcome(
        seed=seed,
        arm=arm,
        status=str(result.get("worker_status", "unknown")),
        terminal_status=str(terminal) if isinstance(terminal, str) else "",
        turns=result.get("turns") if type(result.get("turns")) is int else None,
        max_depth=result.get("max_depth") if type(result.get("max_depth")) is int else None,
        score=result.get("score") if type(result.get("score")) is int else None,
        exit_reason=str(result.get("exit_reason", "")),
        production_state_touched=result.get("production_state_touched") is not False,
    )


def run_arm(
    spec: PromotionRunSpec,
    *,
    arm: str,
    catalog: Path,
    work_root: Path,
    worker: Callable[..., dict[str, object]],
    docker: str,
    image: str,
    isolation_check: Callable[[], bool],
) -> ArmResult:
    if arm not in {"baseline", "candidate"}:
        raise PromotionRunError(f"unknown promotion arm: {arm}")
    specs = load_action_catalog(catalog)
    experiment = spec.experiment_id()
    outcomes: list[EpisodeOutcome] = []
    trace_unverified = 0
    for index, seed in enumerate(spec.seeds):
        episode_id = f"{index:03d}"
        arena = _arena(work_root / arm / f"seed-{seed:03d}" / f"episode-{episode_id}")
        env = dict(_catalog_env(catalog, arena["episode_root"]))
        env["DOCICH_CANARY_ACTION_TRACE"] = "1"
        request = _request(
            arena,
            experiment_id=experiment,
            episode_id=episode_id,
            seed=seed,
            player=f"{spec.player_prefix}_{arm}_{seed:03d}"[:31],
            max_turns=spec.max_turns,
            timeout_s=spec.wall_timeout_s,
        )
        result = worker(
            json.dumps(request, ensure_ascii=False, separators=(",", ":")),
            docker=docker,
            image=image,
            extra_env=env,
        )
        outcome = _episode_outcome(seed, arm, result)
        if not isolation_check():
            # A failed production-isolation probe is never a passing default.
            outcome = replace(outcome, production_state_touched=True)
        outcomes.append(outcome)
        trace = arena["episode_root"] / "action-trace.jsonl"
        if not trace.is_file():
            # Promotion is trace-gated. Absence of evidence must fail closed
            # rather than being treated as zero violations.
            trace_unverified += 1
        else:
            verifications = tuple(verify_trace_file(trace, specs))
            if not verifications:
                # An empty trace is also absence of evidence for this
                # catalog-driven episode.
                trace_unverified += 1
            else:
                trace_unverified += sum(
                    1 for verification in verifications if not verification.verified
                )
    return ArmResult(
        arm=arm,
        experiment_id=experiment,
        controller_kind=CONTROLLER_KIND,
        catalog_sha256=_catalog_digest(catalog),
        outcomes=tuple(outcomes),
        trace_unverified=trace_unverified,
    )


def _inputs(
    spec: PromotionRunSpec,
    baseline: ArmResult,
    candidate: ArmResult,
    *,
    regression_green: bool,
    smoke_ok: bool,
) -> PromotionInputs:
    return PromotionInputs(
        candidate_id=spec.candidate_id,
        baseline_id=spec.baseline_id,
        baseline=baseline.outcomes,
        candidate=candidate.outcomes,
        # Both arms are trace-gated; an unverified baseline episode is not
        # "clean by default".
        trace_unverified=baseline.trace_unverified + candidate.trace_unverified,
        regression_green=regression_green,
        smoke_ok=smoke_ok,
    )


def run_promotion(
    spec: PromotionRunSpec,
    *,
    work_root: Path,
    worker: Callable[..., dict[str, object]],
    docker: str,
    image: str,
    config: GateConfig | None = None,
    regression_green: bool,
    smoke_ok: bool,
    isolation_check: Callable[[], bool],
) -> tuple[PromotionDecision, ArmResult, ArmResult]:
    """Run both arms for the same seeds and return the gate decision."""
    _validate_spec(spec)
    assert spec.candidate_catalog is not None  # _validate_spec rejects a missing artifact
    baseline = run_arm(
        spec, arm="baseline", catalog=spec.baseline_catalog, work_root=work_root,
        worker=worker, docker=docker, image=image, isolation_check=isolation_check,
    )
    candidate = run_arm(
        spec, arm="candidate", catalog=spec.candidate_catalog, work_root=work_root,
        worker=worker, docker=docker, image=image, isolation_check=isolation_check,
    )
    inputs = _inputs(spec, baseline, candidate, regression_green=regression_green, smoke_ok=smoke_ok)
    return evaluate_promotion(inputs, config), baseline, candidate


def run_improvement_cycle(
    spec: PromotionRunSpec,
    *,
    proposer,
    work_root: Path,
    worker: Callable[..., dict[str, object]],
    docker: str,
    image: str,
    config: GateConfig | None = None,
    regression_green: bool,
    smoke_ok: bool,
    isolation_check: Callable[[], bool],
) -> tuple[PromotionDecision, FailureSignal, tuple, ArmResult, ArmResult]:
    """Run baseline, ask the proposer for a catalog, verify it, and gate it."""
    # The candidate artifact does not exist yet; it is generated below.
    _validate_spec(spec, require_candidate_catalog=False)
    baseline = run_arm(
        spec, arm="baseline", catalog=spec.baseline_catalog, work_root=work_root,
        worker=worker, docker=docker, image=image, isolation_check=isolation_check,
    )
    signal = failure_signal_from_outcomes(baseline.outcomes)
    specs = load_action_catalog(spec.baseline_catalog)
    request = build_proposal_request(signal, specs, allowed_effects=SUPPORTED_EFFECTS)
    candidate_specs = proposer.propose(request, allowed_effects=SUPPORTED_EFFECTS)
    candidate_path = work_root / "candidate-catalog.json"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(json.dumps(catalog_to_dict(candidate_specs), indent=2), encoding="utf-8")
    candidate = run_arm(
        spec, arm="candidate", catalog=candidate_path, work_root=work_root,
        worker=worker, docker=docker, image=image, isolation_check=isolation_check,
    )
    inputs = _inputs(spec, baseline, candidate, regression_green=regression_green, smoke_ok=smoke_ok)
    return evaluate_promotion(inputs, config), signal, candidate_specs, baseline, candidate
