"""Live shadow-only candidate strategist comparison for NetHack (P5e).

The candidate receives the same public observation used by the reviewed
NetHack policy, but its proposal is telemetry only.  This module never returns
or injects gameplay Actions and never mutates the active policy/config.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Literal

from .config import GameConfig, GlobalConfig
from .nethack_candidate_eval import CandidateManifest, load_candidate_manifest
from .nethack_inventory import parse_visible_inventory
from .nethack_observation import NethackObservation
from .nethack_policy import PolicyDecision
from .nethack_strategist import CommandStrategist, StrategistDispatchResult, evaluate_proposal, execution_plan
from .nethack_strategy import build_strategic_request

ShadowStatus = Literal[
    "disabled",
    "not_needed",
    "cooldown",
    "budget_exhausted",
    "error",
    "proposed",
]


@dataclass(frozen=True)
class CandidateShadowConfig:
    enabled: bool = False
    manifest: str = ""
    cooldown_s: float = 30.0
    max_calls: int = 24


@dataclass(frozen=True)
class CandidateShadowOutcome:
    status: ShadowStatus
    call_index: int = 0
    candidate_id: str | None = None
    version: str | None = None
    proposal_kind: str | None = None
    evaluation_status: str | None = None
    evaluation_reason: str | None = None
    unexpected_execution_actions: int = 0
    error: str | None = None


def _bounded_float(raw: object, *, default: float, minimum: float, maximum: float, name: str) -> float:
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"{name} must be numeric")
    value = float(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def load_candidate_shadow_config(game: GameConfig) -> CandidateShadowConfig:
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
    manifest = section.get("manifest", "")
    if not isinstance(manifest, str) or len(manifest) > 2048:
        raise ValueError("candidate_shadow manifest must be a string")
    manifest = manifest.strip()
    if enabled and not manifest:
        raise ValueError("enabled candidate_shadow requires manifest")
    max_calls = section.get("max_calls", 24)
    if type(max_calls) is not int or not 1 <= max_calls <= 1000:
        raise ValueError("candidate_shadow max_calls must be 1-1000")
    return CandidateShadowConfig(
        enabled=enabled,
        manifest=manifest,
        cooldown_s=_bounded_float(
            section.get("cooldown_s"), default=30.0, minimum=0.0, maximum=3600.0, name="candidate_shadow cooldown_s"
        ),
        max_calls=max_calls,
    )


def _action_dict(action: object) -> dict[str, object]:
    try:
        payload = asdict(action)
    except TypeError:
        return {"type": "unknown"}
    # Keep the live comparison log compact and explicit.
    return {key: value for key, value in payload.items() if value not in ("", [], 0, None)} | {"type": payload.get("type")}


class NethackCandidateShadowController:
    """Rate-limited live candidate shadow that cannot affect gameplay."""

    def __init__(
        self,
        g: GlobalConfig,
        game: GameConfig,
        *,
        config: CandidateShadowConfig | None = None,
        manifest: CandidateManifest | None = None,
        strategist: object | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        self.g = g
        self.game = game
        self.config = config or load_candidate_shadow_config(game)
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._calls = 0
        self._last_dispatch_at: float | None = None
        self._last_dispatch_intent: str | None = None

        self.manifest: CandidateManifest | None = manifest
        self.strategist = strategist
        if self.config.enabled:
            if self.manifest is None:
                path = Path(self.config.manifest).expanduser()
                if not path.is_absolute():
                    path = Path(getattr(g, "repo_root", ".")) / path
                self.manifest = load_candidate_manifest(path)
            if self.strategist is None:
                assert self.manifest is not None
                command = list(self.manifest.command) if isinstance(self.manifest.command, tuple) else self.manifest.command
                self.strategist = CommandStrategist(
                    command,
                    timeout_s=self.manifest.timeout_s,
                    max_request_bytes=self.manifest.max_request_bytes,
                    max_response_bytes=self.manifest.max_response_bytes,
                    cwd=Path(getattr(g, "repo_root", ".")),
                )

        if self.manifest is not None:
            base = Path(getattr(g, "state_dir", ".")) / "nethack" / "candidate_shadow"
            self.log_path = base / self.manifest.candidate_id / f"{self.manifest.version}.jsonl"
        else:
            self.log_path = Path(getattr(g, "state_dir", ".")) / "nethack" / "candidate_shadow" / "disabled.jsonl"

    @property
    def calls_used(self) -> int:
        return self._calls

    def _run_id(self) -> str | None:
        path = Path(getattr(self.g, "state_dir", ".")) / "nethack" / "current.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        run_id = raw.get("run_id") if isinstance(raw, dict) else None
        return run_id if isinstance(run_id, str) else None

    def _append_log(self, payload: dict[str, object]) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            fd = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
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
    ) -> CandidateShadowOutcome:
        if not self.config.enabled or self.manifest is None or self.strategist is None:
            return CandidateShadowOutcome(status="disabled")
        # P5e compares strategic decisions only. Routine reviewed exploration
        # remains token-free and does not need a shadow model call.
        if not decision.requires_llm:
            return CandidateShadowOutcome(
                status="not_needed",
                candidate_id=self.manifest.candidate_id,
                version=self.manifest.version,
            )
        if self._calls >= self.config.max_calls:
            return CandidateShadowOutcome(
                status="budget_exhausted",
                call_index=self._calls,
                candidate_id=self.manifest.candidate_id,
                version=self.manifest.version,
            )
        now = self._monotonic()
        if (
            self._last_dispatch_at is not None
            and self._last_dispatch_intent == decision.intent
            and now - self._last_dispatch_at < self.config.cooldown_s
        ):
            return CandidateShadowOutcome(
                status="cooldown",
                call_index=self._calls,
                candidate_id=self.manifest.candidate_id,
                version=self.manifest.version,
            )

        inventory = parse_visible_inventory(raw_text)
        request = build_strategic_request(obs, decision, inventory)
        self._calls += 1
        self._last_dispatch_at = now
        self._last_dispatch_intent = decision.intent
        call_index = self._calls
        try:
            result = self.strategist.dispatch(request)
        except Exception as exc:
            result = StrategistDispatchResult(status="error", error=str(exc).replace("\n", " ")[:240])

        proposal_kind: str | None = None
        evaluation_status: str | None = None
        evaluation_reason: str | None = None
        unexpected_actions = 0
        if result.status == "proposed" and result.proposal is not None:
            proposal_kind = result.proposal.kind
            evaluation = evaluate_proposal(
                request,
                result.proposal,
                current_observation=obs,
                current_inventory=inventory,
            )
            evaluation_status = evaluation.status
            evaluation_reason = evaluation.reason
            plan = execution_plan(evaluation)
            unexpected_actions = len(plan.actions)

        status: ShadowStatus = "proposed" if result.status == "proposed" else "error"
        outcome = CandidateShadowOutcome(
            status=status,
            call_index=call_index,
            candidate_id=self.manifest.candidate_id,
            version=self.manifest.version,
            proposal_kind=proposal_kind,
            evaluation_status=evaluation_status,
            evaluation_reason=evaluation_reason,
            unexpected_execution_actions=unexpected_actions,
            error=result.error,
        )
        self._append_log(
            {
                "schema_version": 1,
                "ts": self._wall_time(),
                "run_id": self._run_id(),
                "candidate_id": self.manifest.candidate_id,
                "version": self.manifest.version,
                "candidate_fingerprint": self.manifest.fingerprint,
                "status": outcome.status,
                "call_index": call_index,
                "baseline": {
                    "intent": decision.intent,
                    "reason": decision.reason,
                    "requires_llm": decision.requires_llm,
                    "actions": [_action_dict(action) for action in decision.actions],
                },
                "request": request.to_dict(),
                "proposal": result.proposal.to_dict() if result.proposal is not None else None,
                "evaluation": (
                    {"status": evaluation_status, "reason": evaluation_reason}
                    if evaluation_status is not None
                    else None
                ),
                "unexpected_execution_actions": unexpected_actions,
                "error": result.error,
                "execution": "shadow_only",
                "policy_effect": "none",
            }
        )
        return outcome
