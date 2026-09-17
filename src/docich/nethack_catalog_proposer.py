"""Catalog proposer boundary for the P6 improvement loop.

A stalled or unhealthy canary episode produces a :class:`FailureSignal`.  An
external command (for example an LLM) receives a bounded public JSON request
containing that signal and the current reviewed action catalog, and may return
a candidate catalog.  The proposal never reaches the game directly: it must
pass :func:`docich.nethack_action_spec.parse_action_catalog` and may only use
action ids that already have a reviewed handler.

Adding a truly new capability (a new key effect) still needs reviewed code;
this boundary covers data-only candidate changes (enable/disable, priority,
preconditions, postconditions).
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from .nethack_action_spec import ActionSpec, CATALOG_SCHEMA_VERSION, parse_action_catalog

PROPOSAL_SCHEMA_VERSION = 1
DEFAULT_TIMEOUT_S = 20.0
MAX_TIMEOUT_S = 120.0
MAX_REQUEST_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 256 * 1024


class CatalogProposalError(RuntimeError):
    """Raised when a proposer cannot yield a valid, reviewed catalog."""


@dataclass(frozen=True)
class FailureSignal:
    exit_reason: str
    stall_intent: str | None
    conditions: tuple[str, ...]
    message: str
    turns: int | None
    max_depth: int | None

    def to_dict(self) -> dict[str, object]:
        return {
            "exit_reason": self.exit_reason,
            "stall_intent": self.stall_intent,
            "conditions": list(self.conditions),
            "message": self.message[:240],
            "turns": self.turns,
            "max_depth": self.max_depth,
        }


def failure_signal_from_outcome(outcome) -> FailureSignal:
    reason = outcome.exit_reason or ""
    intent = reason.split(":", 1)[1] if reason.startswith("policy_stall:") else None
    return FailureSignal(
        exit_reason=reason,
        stall_intent=intent,
        conditions=(),
        message="",
        turns=outcome.turns,
        max_depth=outcome.max_depth,
    )


def failure_signal_from_outcomes(outcomes: Sequence) -> FailureSignal:
    """Pick the most informative failure: a stall if any, else the worst fitness."""
    if not outcomes:
        raise ValueError("no outcomes to summarise")
    stalls = [o for o in outcomes if (o.exit_reason or "").startswith("policy_stall:")]
    chosen = stalls[0] if stalls else min(outcomes, key=lambda o: o.fitness())
    return failure_signal_from_outcome(chosen)


def catalog_to_dict(specs: tuple[ActionSpec, ...]) -> dict[str, object]:
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "actions": [spec.to_dict() for spec in specs],
    }


def build_proposal_request(
    signal: FailureSignal,
    specs: tuple[ActionSpec, ...],
    *,
    allowed_action_ids: frozenset[str],
    constraints: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "schema_version": PROPOSAL_SCHEMA_VERSION,
        "failure": signal.to_dict(),
        "catalog": catalog_to_dict(specs),
        "allowed_actions": sorted(allowed_action_ids),
        "constraints": list(
            constraints
            or (
                "return the full catalog with schema_version and an actions list",
                "only use ids from allowed_actions (handlers are reviewed code)",
                "risk_class and key_pattern are fixed per action id",
                "only enabled, priority, preconditions, postconditions, and description may change",
                "prefer the smallest change that removes the stall",
            )
        ),
    }


def _proposal_baseline(
    request: Mapping[str, object], *, allowed_action_ids: frozenset[str]
) -> tuple[ActionSpec, ...]:
    """Read the reviewed catalog embedded in the request and fail closed."""
    try:
        return parse_action_catalog(request.get("catalog"), allowed_action_ids=allowed_action_ids)
    except ValueError as exc:
        raise CatalogProposalError("proposer request does not contain a valid baseline catalog") from exc


def _validate_candidate_contract(
    baseline: tuple[ActionSpec, ...], candidate: tuple[ActionSpec, ...]
) -> None:
    """Keep code-owned action identity and key effects immutable.

    The proposer may tune only the documented data surface.  Replacing or
    omitting an action, changing its reviewed risk class, or changing the key
    pattern would silently expand the capability boundary and must require a
    reviewed code change instead of an LLM/data-only proposal.
    """
    baseline_by_id = {spec.id: spec for spec in baseline}
    candidate_by_id = {spec.id: spec for spec in candidate}
    if candidate_by_id.keys() != baseline_by_id.keys():
        raise CatalogProposalError("proposer catalog must preserve the full reviewed action set")
    for spec_id, baseline_spec in baseline_by_id.items():
        candidate_spec = candidate_by_id[spec_id]
        if candidate_spec.risk_class != baseline_spec.risk_class:
            raise CatalogProposalError(f"proposer catalog changed fixed risk_class for {spec_id}")
        if candidate_spec.key_pattern != baseline_spec.key_pattern:
            raise CatalogProposalError(f"proposer catalog changed fixed key_pattern for {spec_id}")


@dataclass(frozen=True)
class CommandCatalogProposer:
    """Run an external command that turns a proposal request into a catalog."""

    command: tuple[str, ...]
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_request_bytes: int = MAX_REQUEST_BYTES
    max_response_bytes: int = MAX_RESPONSE_BYTES
    runner: Callable[..., object] = subprocess.run

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("proposer command must not be empty")
        if not 0.1 <= float(self.timeout_s) <= MAX_TIMEOUT_S:
            raise ValueError(f"proposer timeout must be 0.1..{MAX_TIMEOUT_S}")

    def propose(
        self,
        request: Mapping[str, object],
        *,
        allowed_action_ids: frozenset[str],
    ) -> tuple[ActionSpec, ...]:
        baseline = _proposal_baseline(request, allowed_action_ids=allowed_action_ids)
        payload = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(payload) > self.max_request_bytes:
            raise CatalogProposalError("proposer request exceeds size limit")
        try:
            completed = self.runner(
                list(self.command),
                input=payload,
                capture_output=True,
                timeout=float(self.timeout_s),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CatalogProposalError("proposer command failed") from exc
        if getattr(completed, "returncode", 1) != 0:
            raise CatalogProposalError("proposer command returned non-zero")
        stdout = getattr(completed, "stdout", b"")
        if not isinstance(stdout, (bytes, bytearray)) or len(stdout) > self.max_response_bytes:
            raise CatalogProposalError("proposer response exceeds size limit")
        try:
            raw = json.loads(bytes(stdout).decode("utf-8", "strict"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise CatalogProposalError("proposer response is not valid JSON") from exc
        try:
            candidate = parse_action_catalog(raw, allowed_action_ids=allowed_action_ids)
        except ValueError as exc:
            raise CatalogProposalError(f"proposer catalog is invalid: {exc}") from exc
        _validate_candidate_contract(baseline, candidate)
        return candidate
