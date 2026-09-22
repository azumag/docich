"""Non-blocking live candidate shadow evaluation for NetHack (P5e).

The production NetHack action is decided before this sidecar is called. A
candidate strategist runs on a bounded daemon worker and its proposal is only
logged/evaluated; it is never returned to the agent loop and never sends a key
to NetHack.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from .actions import Action
from .config import GameConfig, GlobalConfig
from .nethack_candidate_eval import CandidateManifest, load_candidate_manifest
from .nethack_inventory import VisibleInventoryItem, parse_visible_inventory
from .nethack_observation import NethackObservation
from .nethack_policy import PolicyDecision
from .nethack_regression import NethackRegressionError, _load_suite
from .nethack_strategist import (
    DISPATCH_ERROR_KINDS,
    CommandStrategist,
    DispatchErrorKind,
    ProposalEvaluation,
    StrategistDispatchResult,
    dispatch_error_kind_for_exception,
    evaluate_proposal,
    execution_plan,
)
from .nethack_strategy import StrategicRequest, build_strategic_request


CandidateShadowStatus = Literal[
    "disabled",
    "not_needed",
    "cooldown",
    "budget_exhausted",
    "busy",
    "queued",
    "proposed",
    "error",
]


class NethackCandidateShadowError(RuntimeError):
    """Candidate shadow cannot be enabled without trusted offline evidence."""


@dataclass(frozen=True)
class NethackCandidateShadowConfig:
    enabled: bool = False
    manifest_path: str = ""
    cooldown_s: float = 30.0
    max_calls: int = 24


@dataclass(frozen=True)
class CandidateShadowOutcome:
    status: CandidateShadowStatus
    call_index: int = 0
    proposal_kind: str | None = None
    evaluation_status: str | None = None
    evaluation_reason: str | None = None
    # Raw detail is process-local only; persisted logs keep ``error_kind``.
    error: str | None = None
    error_kind: DispatchErrorKind | None = None


def _safe_error_kind(raw: object) -> DispatchErrorKind:
    """Fail closed to a finite category; never persist producer-supplied text."""
    if isinstance(raw, str) and raw in DISPATCH_ERROR_KINDS:
        return cast(DispatchErrorKind, raw)
    return "internal_error"


@dataclass(frozen=True)
class _ShadowJob:
    call_index: int
    request: StrategicRequest
    observation: NethackObservation
    inventory: tuple[VisibleInventoryItem, ...]
    decision: PolicyDecision
    baseline_actions: tuple[dict[str, object], ...]
    run_context: dict[str, object]
    critical_tags: tuple[str, ...]
    queued_at: float


def _bounded_number(
    raw: object,
    *,
    default: float,
    minimum: float,
    maximum: float,
    name: str,
) -> float:
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"{name} must be numeric")
    value = float(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def load_candidate_shadow_config(game: GameConfig) -> NethackCandidateShadowConfig:
    raw = game.raw if isinstance(game.raw, dict) else {}
    nethack = raw.get("nethack", {})
    section = nethack.get("candidate_shadow", {}) if isinstance(nethack, dict) else {}
    if section is None:
        section = {}
    if not isinstance(section, dict):
        raise ValueError("[nethack.candidate_shadow] must be a table")

    enabled = section.get("enabled", False)
    if type(enabled) is not bool:
        raise ValueError("candidate_shadow enabled must be boolean")
    manifest_path = section.get("manifest", "")
    if not isinstance(manifest_path, str) or len(manifest_path) > 4096:
        raise ValueError("candidate_shadow manifest must be a path string")
    manifest_path = manifest_path.strip()
    if enabled and not manifest_path:
        raise ValueError("enabled candidate_shadow requires manifest")
    max_calls = section.get("max_calls", 24)
    if type(max_calls) is not int or not 1 <= max_calls <= 1000:
        raise ValueError("candidate_shadow max_calls must be 1-1000")
    return NethackCandidateShadowConfig(
        enabled=enabled,
        manifest_path=manifest_path,
        cooldown_s=_bounded_number(
            section.get("cooldown_s"),
            default=30.0,
            minimum=0.0,
            maximum=3600.0,
            name="candidate_shadow cooldown_s",
        ),
        max_calls=max_calls,
    )


def _read_json_bounded(path: Path, *, max_bytes: int = 2 * 1024 * 1024) -> dict[str, object]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise NethackCandidateShadowError(f"candidate shadow evidence missing: {path.name}") from exc
    if size > max_bytes:
        raise NethackCandidateShadowError(f"candidate shadow evidence too large: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NethackCandidateShadowError(f"candidate shadow evidence invalid: {path.name}") from exc
    if not isinstance(value, dict):
        raise NethackCandidateShadowError(f"candidate shadow evidence must be object: {path.name}")
    return value


def _resolve_manifest_path(g: GlobalConfig, configured: str) -> Path:
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = Path(g.repo_root) / path
    return path


def _offline_gate(
    g: GlobalConfig,
    manifest: CandidateManifest,
) -> tuple[str, dict[str, object]]:
    """Require a matching successful P5d offline report before live shadow use."""
    regression_root = Path(g.state_dir) / "nethack" / "regression"
    try:
        suite = _load_suite(regression_root / "suite.json")
    except NethackRegressionError as exc:
        raise NethackCandidateShadowError("candidate shadow regression suite is not trusted") from exc
    suite_id = suite.get("suite_id")
    if not isinstance(suite_id, str) or len(suite_id) != 64:
        raise NethackCandidateShadowError("candidate shadow suite_id is invalid")
    if manifest.expected_suite_id is not None and manifest.expected_suite_id != suite_id:
        raise NethackCandidateShadowError("candidate shadow manifest targets a different suite")
    report_path = (
        regression_root
        / "candidates"
        / manifest.candidate_id
        / manifest.version
        / f"{suite_id}.json"
    )
    report = _read_json_bounded(report_path)
    required = {
        "candidate_id": manifest.candidate_id,
        "candidate_version": manifest.version,
        "candidate_fingerprint": manifest.fingerprint,
        "command_sha256": manifest.command_hash,
        "suite_id": suite_id,
        "status": "completed",
        "baseline_contract_passed": True,
        "candidate_safety_contract_passed": True,
        "eligible_for_behavior_review": True,
        "eligible_for_promotion_review": False,
        "automatic_promotion": False,
        "policy_effect": "none",
    }
    for key, expected in required.items():
        if report.get(key) != expected:
            raise NethackCandidateShadowError(
                f"candidate shadow offline gate failed: {key}"
            )
    return suite_id, report


def _action_summary(action: Action) -> dict[str, object]:
    result: dict[str, object] = {"type": action.type}
    if action.type == "text":
        result["text"] = action.text
    elif action.type == "key":
        result["keys"] = list(action.keys)
    elif action.type == "special":
        result["key"] = action.key
    elif action.type == "wait":
        result["ms"] = action.ms
    elif action.type == "pad":
        result["buttons"] = list(action.buttons)
    return result


def _critical_tags(obs: NethackObservation, decision: PolicyDecision) -> tuple[str, ...]:
    tags: list[str] = []
    ratio = obs.vitals.hp_ratio
    if ratio is not None and ratio <= 0.50:
        tags.append("low_hp")
    if ratio is not None and ratio <= 0.25:
        tags.append("critical_hp")
    food = {"Hungry", "Weak", "Fainting", "Fainted", "Starved"}
    impair = {"Blind", "Conf", "Stun", "Hallu"}
    if any(item in food for item in obs.conditions):
        tags.append("food_risk")
    if any(item in impair for item in obs.conditions):
        tags.append("impaired")
    if obs.prompt != "none":
        tags.append(f"prompt:{obs.prompt}")
    if decision.intent in {
        "assess_contact",
        "survival_emergency",
        "status_emergency",
        "food_emergency",
    }:
        tags.append(f"intent:{decision.intent}")
    return tuple(dict.fromkeys(tags))


def _run_context(state_dir: Path) -> dict[str, object]:
    """Best-effort read of atomically-written P1 run identity; never blocks play."""
    root = state_dir / "nethack"
    try:
        current = json.loads((root / "current.json").read_text(encoding="utf-8"))
        if not isinstance(current, dict):
            return {}
        run_id = current.get("run_id")
        if not isinstance(run_id, str) or str(uuid.UUID(run_id)) != run_id:
            return {}
        run = json.loads((root / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))
        if not isinstance(run, dict) or run.get("run_id") != run_id:
            return {}
        expedition = run.get("expedition")
        sessions = run.get("sessions")
        result: dict[str, object] = {"run_id": run_id}
        if type(expedition) is int and expedition > 0:
            result["expedition"] = expedition
        if isinstance(sessions, list) and sessions:
            result["session_index"] = len(sessions) - 1
        status = run.get("status")
        if isinstance(status, str):
            result["run_status"] = status
        return result
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return {}


class NethackCandidateShadowController:
    """Queue candidate strategic evaluation after production actions are fixed."""

    def __init__(
        self,
        g: GlobalConfig,
        game: GameConfig,
        *,
        config: NethackCandidateShadowConfig | None = None,
        strategist: object | None = None,
        monotonic=time.monotonic,
        wall_time=time.time,
    ) -> None:
        self.g = g
        self.game = game
        self.config = config or load_candidate_shadow_config(game)
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._calls = 0
        self._last_dispatch_at: float | None = None
        self._last_dispatch_intent: str | None = None
        self._last_completed = CandidateShadowOutcome(status="disabled")
        self._state_lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()
        self._queue: queue.Queue[_ShadowJob] = queue.Queue(maxsize=1)
        self._worker: threading.Thread | None = None
        self.manifest: CandidateManifest | None = None
        self.suite_id: str | None = None
        self.strategist: object | None = None

        if not self.config.enabled:
            return

        manifest_path = _resolve_manifest_path(g, self.config.manifest_path)
        self.manifest = load_candidate_manifest(manifest_path)
        self.suite_id, _ = _offline_gate(g, self.manifest)
        if strategist is not None:
            self.strategist = strategist
        else:
            command = (
                self.manifest.command
                if isinstance(self.manifest.command, str)
                else list(self.manifest.command)
            )
            self.strategist = CommandStrategist(
                command,
                timeout_s=self.manifest.timeout_s,
                max_request_bytes=self.manifest.max_request_bytes,
                max_response_bytes=self.manifest.max_response_bytes,
                cwd=Path(g.repo_root),
            )
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="nethack-candidate-shadow",
            daemon=True,
        )
        self._worker.start()

    @property
    def calls_used(self) -> int:
        return self._calls

    @property
    def last_completed(self) -> CandidateShadowOutcome:
        with self._state_lock:
            return self._last_completed

    def wait_for_idle(self, timeout: float = 2.0) -> bool:
        """Test/diagnostic helper; gameplay never waits on this."""
        return self._idle.wait(timeout=max(0.0, timeout))

    def _set_completed(self, outcome: CandidateShadowOutcome) -> None:
        with self._state_lock:
            self._last_completed = outcome

    def _log_path(self, context: dict[str, object]) -> Path:
        assert self.manifest is not None
        run_id = context.get("run_id")
        leaf = f"{run_id}.jsonl" if isinstance(run_id, str) else "untracked.jsonl"
        return (
            Path(self.g.state_dir)
            / "nethack"
            / "candidate-shadow"
            / self.manifest.candidate_id
            / self.manifest.version
            / leaf
        )

    def _append_log(self, context: dict[str, object], payload: dict[str, object]) -> None:
        path = self._log_path(context)
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(path.parent, 0o700)
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.fchmod(fd, 0o600)
                encoded = line.encode("utf-8")
                offset = 0
                while offset < len(encoded):
                    offset += os.write(fd, encoded[offset:])
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            return

    def consider(
        self,
        raw_text: str,
        obs: NethackObservation,
        decision: PolicyDecision,
        baseline_actions: tuple[Action, ...] | list[Action],
    ) -> CandidateShadowOutcome:
        if not self.config.enabled or self.manifest is None or self.strategist is None:
            return CandidateShadowOutcome(status="disabled")
        if not decision.requires_llm:
            return CandidateShadowOutcome(status="not_needed", call_index=self._calls)
        now = self._monotonic()
        if self._calls >= self.config.max_calls:
            return CandidateShadowOutcome(status="budget_exhausted", call_index=self._calls)
        if (
            self._last_dispatch_at is not None
            and self._last_dispatch_intent == decision.intent
            and now - self._last_dispatch_at < self.config.cooldown_s
        ):
            return CandidateShadowOutcome(status="cooldown", call_index=self._calls)

        inventory = parse_visible_inventory(raw_text)
        request = build_strategic_request(obs, decision, inventory)
        context = _run_context(Path(self.g.state_dir))
        job = _ShadowJob(
            call_index=self._calls + 1,
            request=request,
            observation=obs,
            inventory=tuple(inventory),
            decision=decision,
            baseline_actions=tuple(_action_summary(item) for item in baseline_actions),
            run_context=context,
            critical_tags=_critical_tags(obs, decision),
            queued_at=self._wall_time(),
        )
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            return CandidateShadowOutcome(status="busy", call_index=self._calls)
        self._calls += 1
        self._last_dispatch_at = now
        self._last_dispatch_intent = decision.intent
        self._idle.clear()
        return CandidateShadowOutcome(status="queued", call_index=self._calls)

    def _worker_loop(self) -> None:
        while True:
            job = self._queue.get()
            try:
                self._evaluate_job(job)
            except Exception as exc:
                outcome = CandidateShadowOutcome(
                    status="error",
                    call_index=job.call_index,
                    error=str(exc).replace("\n", " ")[:240],
                    error_kind=dispatch_error_kind_for_exception(exc),
                )
                self._set_completed(outcome)
                self._write_job_log(job, outcome, None, None, None)
            finally:
                self._queue.task_done()
                if self._queue.empty():
                    self._idle.set()

    def _evaluate_job(self, job: _ShadowJob) -> None:
        assert self.strategist is not None
        try:
            result = self.strategist.dispatch(job.request)
        except Exception as exc:
            result = StrategistDispatchResult(
                status="error",
                error=str(exc).replace("\n", " ")[:240],
                error_kind=dispatch_error_kind_for_exception(exc),
            )
        evaluation: ProposalEvaluation | None = None
        plan = None
        if result.status == "proposed" and result.proposal is not None:
            evaluation = evaluate_proposal(
                job.request,
                result.proposal,
                current_observation=job.observation,
                current_inventory=job.inventory,
            )
            plan = execution_plan(evaluation)
        outcome = CandidateShadowOutcome(
            status="proposed" if result.status == "proposed" else "error",
            call_index=job.call_index,
            proposal_kind=(result.proposal.kind if result.proposal is not None else None),
            evaluation_status=(evaluation.status if evaluation is not None else None),
            evaluation_reason=(evaluation.reason if evaluation is not None else None),
            error=result.error,
            error_kind=(None if result.status == "proposed" else _safe_error_kind(result.error_kind)),
        )
        self._set_completed(outcome)
        self._write_job_log(job, outcome, result, evaluation, plan)

    def _write_job_log(
        self,
        job: _ShadowJob,
        outcome: CandidateShadowOutcome,
        result: StrategistDispatchResult | None,
        evaluation: ProposalEvaluation | None,
        plan: object | None,
    ) -> None:
        assert self.manifest is not None
        action_count = len(getattr(plan, "actions", ())) if plan is not None else 0
        payload: dict[str, object] = {
            "schema_version": 1,
            "ts": self._wall_time(),
            "queued_at": job.queued_at,
            "candidate_id": self.manifest.candidate_id,
            "candidate_version": self.manifest.version,
            "candidate_fingerprint": self.manifest.fingerprint,
            "command_sha256": self.manifest.command_hash,
            "suite_id": self.suite_id,
            **job.run_context,
            "turn": job.observation.vitals.turn,
            "dungeon_level": job.observation.vitals.dungeon_level,
            "critical_tags": list(job.critical_tags),
            "current_decision": {
                "layer": job.decision.layer,
                "intent": job.decision.intent,
                "reason": job.decision.reason,
                "requires_llm": job.decision.requires_llm,
            },
            "current_actions": list(job.baseline_actions),
            "request": job.request.to_dict(),
            "candidate_status": outcome.status,
            "candidate_proposal": (
                result.proposal.to_dict()
                if result is not None and result.proposal is not None
                else None
            ),
            "candidate_evaluation": (
                {"status": evaluation.status, "reason": evaluation.reason}
                if evaluation is not None
                else None
            ),
            "candidate_error_kind": (
                _safe_error_kind(outcome.error_kind) if outcome.status == "error" else None
            ),
            "would_execute_allowed": bool(getattr(plan, "allowed", False)) if plan is not None else False,
            "would_execute_action_count": action_count,
            "candidate_action_sent": False,
            "execution": "candidate_shadow_only",
            "policy_effect": "none",
        }
        self._append_log(job.run_context, payload)
