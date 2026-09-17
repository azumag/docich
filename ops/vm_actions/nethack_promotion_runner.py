#!/usr/bin/env python3
"""P6e: run seed-paired baseline/candidate canary episodes and gate promotion.

This is the ops-layer runner.  It reuses the reviewed container launcher and
the P6 catalog/trace verification, and hands the collected outcomes to the
pure promotion gate.  Both arms run the canary tactical baseline policy; the
only difference is the action catalog injected via ``DOCICH_CANARY_CATALOG``,
so a candidate is a catalog diff (enable/disable, priority, preconditions).

The production-isolation checks (fingerprint / container cleanup) are supplied
by the caller through ``isolation_check`` so this module stays unit-testable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from docich.nethack_action_spec import load_action_catalog, verify_trace_file
from docich.nethack_canary_tactics import SUPPORTED_ACTION_IDS
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


@dataclass(frozen=True)
class PromotionRunSpec:
    candidate_id: str
    baseline_id: str
    candidate_catalog: Path
    seeds: tuple[int, ...]
    player_prefix: str = "canary_prom"
    max_turns: int = 1000
    wall_timeout_s: float = 300.0
    inter_episode_timeout_s: float = 900.0


@dataclass(frozen=True)
class ArmResult:
    arm: str
    outcomes: tuple[EpisodeOutcome, ...]
    trace_unverified: int


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


def _request(arena: dict[str, Path], *, seed: int, player: str, max_turns: int, timeout_s: float) -> dict[str, object]:
    return {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "experiment_id": "p6e-promotion",
        "episode_id": "000",
        "arm": "baseline",
        "arena": {key: str(value) for key, value in arena.items()},
        "player_name": player,
        "max_turns": max_turns,
        "wall_timeout_s": timeout_s,
        "seed": seed,
        "controller": {"kind": "baseline_p3b"},
        "requirements": dict(REQUIREMENTS),
    }


def _catalog_env(catalog: Path | None, episode_root: Path) -> dict[str, str]:
    if catalog is None:
        return {}
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
    catalog: Path | None,
    work_root: Path,
    worker: Callable[..., dict[str, object]],
    docker: str,
    image: str,
    isolation_check: Callable[[], bool] | None = None,
) -> ArmResult:
    catalog_specs = load_action_catalog(catalog) if catalog is not None else None
    outcomes: list[EpisodeOutcome] = []
    trace_unverified = 0
    for seed in spec.seeds:
        arena = _arena(work_root / arm / f"seed-{seed:03d}" / "episode-000")
        env = dict(_catalog_env(catalog, arena["episode_root"]))
        env["DOCICH_CANARY_ACTION_TRACE"] = "1"
        request = _request(
            arena,
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
        if isolation_check is not None and not isolation_check():
            outcome = EpisodeOutcome(
                seed=outcome.seed,
                arm=outcome.arm,
                status=outcome.status,
                terminal_status=outcome.terminal_status,
                turns=outcome.turns,
                max_depth=outcome.max_depth,
                score=outcome.score,
                exit_reason=outcome.exit_reason,
                production_state_touched=True,
            )
        outcomes.append(outcome)
        trace = arena["episode_root"] / "action-trace.jsonl"
        if catalog_specs is not None:
            if not trace.is_file():
                # Candidate promotion is trace-gated. Absence of evidence must
                # fail closed rather than being treated as zero violations.
                trace_unverified += 1
            else:
                verifications = tuple(verify_trace_file(trace, catalog_specs))
                if not verifications:
                    # An empty trace is also absence of evidence for this
                    # catalog-driven candidate episode.
                    trace_unverified += 1
                else:
                    trace_unverified += sum(
                        1 for verification in verifications if not verification.verified
                    )
    return ArmResult(arm=arm, outcomes=tuple(outcomes), trace_unverified=trace_unverified)


def run_promotion(
    spec: PromotionRunSpec,
    *,
    work_root: Path,
    worker: Callable[..., dict[str, object]],
    docker: str,
    image: str,
    config: GateConfig | None = None,
    regression_green: bool = True,
    smoke_ok: bool = True,
    isolation_check: Callable[[], bool] | None = None,
) -> tuple[PromotionDecision, ArmResult, ArmResult]:
    """Run both arms for the same seeds and return the gate decision."""
    baseline = run_arm(
        spec, arm="baseline", catalog=None, work_root=work_root, worker=worker,
        docker=docker, image=image, isolation_check=isolation_check,
    )
    candidate = run_arm(
        spec, arm="candidate", catalog=spec.candidate_catalog, work_root=work_root,
        worker=worker, docker=docker, image=image, isolation_check=isolation_check,
    )
    inputs = PromotionInputs(
        candidate_id=spec.candidate_id,
        baseline_id=spec.baseline_id,
        baseline=baseline.outcomes,
        candidate=candidate.outcomes,
        trace_unverified=candidate.trace_unverified,
        regression_green=regression_green,
        smoke_ok=smoke_ok,
    )
    return evaluate_promotion(inputs, config), baseline, candidate


def run_improvement_cycle(
    spec: PromotionRunSpec,
    *,
    proposer,
    baseline_catalog: Path,
    work_root: Path,
    worker: Callable[..., dict[str, object]],
    docker: str,
    image: str,
    config: GateConfig | None = None,
    regression_green: bool = True,
    smoke_ok: bool = True,
    isolation_check: Callable[[], bool] | None = None,
) -> tuple[PromotionDecision, FailureSignal, tuple, ArmResult, ArmResult]:
    """Run baseline, ask the proposer for a catalog, verify it, and gate it."""
    baseline = run_arm(
        spec, arm="baseline", catalog=None, work_root=work_root, worker=worker,
        docker=docker, image=image, isolation_check=isolation_check,
    )
    signal = failure_signal_from_outcomes(baseline.outcomes)
    specs = load_action_catalog(baseline_catalog)
    request = build_proposal_request(signal, specs, allowed_action_ids=SUPPORTED_ACTION_IDS)
    candidate_specs = proposer.propose(request, allowed_action_ids=SUPPORTED_ACTION_IDS)
    candidate_path = work_root / "candidate-catalog.json"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(json.dumps(catalog_to_dict(candidate_specs), indent=2), encoding="utf-8")
    candidate = run_arm(
        spec, arm="candidate", catalog=candidate_path, work_root=work_root, worker=worker,
        docker=docker, image=image, isolation_check=isolation_check,
    )
    inputs = PromotionInputs(
        candidate_id=spec.candidate_id,
        baseline_id=spec.baseline_id,
        baseline=baseline.outcomes,
        candidate=candidate.outcomes,
        trace_unverified=candidate.trace_unverified,
        regression_green=regression_green,
        smoke_ok=smoke_ok,
    )
    return evaluate_promotion(inputs, config), signal, candidate_specs, baseline, candidate
