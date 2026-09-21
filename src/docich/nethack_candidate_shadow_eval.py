"""Offline aggregation of P5e live candidate-shadow evidence (P5f).

The candidate did not control the game, so this module never treats the live
run outcome as candidate performance. It only measures proposal behavior and
correlates it with the production run's public/terminal evidence.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .config import ConfigError, GlobalConfig, load_global
from .game_switch import atomic_write_json
from .nethack_candidate_eval import CandidateManifest, load_candidate_manifest
from .nethack_candidate_shadow import NethackCandidateShadowError, _offline_gate

REPORT_SCHEMA_VERSION = 1
MAX_LOG_FILES = 4096
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_LINE_BYTES = 256 * 1024


class NethackCandidateShadowEvalError(RuntimeError):
    """Live candidate-shadow evidence cannot be evaluated safely."""


@dataclass
class _RunBucket:
    run_id: str
    events: int = 0
    proposed: int = 0
    errors: int = 0
    approved: int = 0
    rejected: int = 0
    hold: int = 0
    nonhold: int = 0
    current_action_events: int = 0
    current_no_action_events: int = 0
    nonhold_on_current_no_action: int = 0
    critical_events: int = 0
    critical_rejected: int = 0
    critical_errors: int = 0
    unexpected_would_actions: int = 0
    proposal_kinds: Counter[str] = field(default_factory=Counter)
    current_intents: Counter[str] = field(default_factory=Counter)
    critical_tags: Counter[str] = field(default_factory=Counter)
    intent_proposals: Counter[str] = field(default_factory=Counter)

    def add(self, event: dict[str, object]) -> None:
        self.events += 1
        status = event.get("candidate_status")
        if status == "proposed":
            self.proposed += 1
        elif status == "error":
            self.errors += 1

        proposal = event.get("candidate_proposal")
        proposal_kind: str | None = None
        if isinstance(proposal, dict):
            kind = proposal.get("kind")
            if isinstance(kind, str) and kind:
                proposal_kind = kind
                self.proposal_kinds[kind] += 1
                # Keep the legacy report fields for schema compatibility, but
                # classify the new explicit rest proposal with the old
                # wait/no-op bucket.  ``hold`` is accepted only for historical
                # shadow logs; it is no longer a proposal schema value.
                if kind in {"hold", "rest"}:
                    self.hold += 1
                else:
                    self.nonhold += 1

        evaluation = event.get("candidate_evaluation")
        evaluation_status: str | None = None
        if isinstance(evaluation, dict):
            value = evaluation.get("status")
            if isinstance(value, str):
                evaluation_status = value
                if value == "approved":
                    self.approved += 1
                elif value == "rejected":
                    self.rejected += 1

        decision = event.get("current_decision")
        intent: str | None = None
        if isinstance(decision, dict):
            value = decision.get("intent")
            if isinstance(value, str) and value:
                intent = value
                self.current_intents[value] += 1
        if intent is not None and proposal_kind is not None:
            self.intent_proposals[f"{intent}|{proposal_kind}"] += 1

        actions = event.get("current_actions")
        action_count = len(actions) if isinstance(actions, list) else 0
        if action_count:
            self.current_action_events += 1
        else:
            self.current_no_action_events += 1
            if proposal_kind is not None and proposal_kind not in {"hold", "rest"}:
                self.nonhold_on_current_no_action += 1

        tags = event.get("critical_tags")
        critical = False
        if isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, str) and tag:
                    self.critical_tags[tag] += 1
                    critical = True
        if critical:
            self.critical_events += 1
            if evaluation_status == "rejected":
                self.critical_rejected += 1
            if status == "error":
                self.critical_errors += 1

        would_count = event.get("would_execute_action_count")
        if type(would_count) is int and would_count > 0:
            self.unexpected_would_actions += 1


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _read_json(path: Path, *, max_bytes: int = 8 * 1024 * 1024) -> dict[str, object] | None:
    try:
        if path.stat().st_size > max_bytes:
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _run_terminal_evidence(state_dir: Path, run_id: str) -> dict[str, object]:
    payload = _read_json(state_dir / "nethack" / "runs" / f"{run_id}.json")
    if payload is None or payload.get("run_id") != run_id:
        return {}
    result: dict[str, object] = {}
    for key in ("expedition", "status", "score", "turns", "max_depth", "death_reason", "got_amulet"):
        value = payload.get(key)
        if value is not None:
            result[key] = value
    retrospective = payload.get("retrospective")
    if isinstance(retrospective, dict):
        for source, target in (
            ("death_signature", "death_signature"),
            ("same_death_total_count", "same_death_total_count"),
        ):
            value = retrospective.get(source)
            if value is not None:
                result[target] = value
    return result


def _valid_uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        return None
    return canonical if canonical == value else None


def _validate_identity(
    event: dict[str, object],
    manifest: CandidateManifest,
    suite_id: str,
) -> str | None:
    expected = {
        "schema_version": 1,
        "candidate_id": manifest.candidate_id,
        "candidate_version": manifest.version,
        "candidate_fingerprint": manifest.fingerprint,
        "command_sha256": manifest.command_hash,
        "suite_id": suite_id,
        "candidate_action_sent": False,
        "execution": "candidate_shadow_only",
        "policy_effect": "none",
    }
    for key, value in expected.items():
        if event.get(key) != value:
            return key
    return None


def evaluate_live_candidate_shadow(
    g: GlobalConfig,
    manifest: CandidateManifest,
    *,
    now: dt.datetime | None = None,
) -> dict[str, object]:
    timestamp = now or dt.datetime.now(dt.timezone.utc)
    if timestamp.tzinfo is None:
        raise NethackCandidateShadowEvalError("now must be timezone-aware")
    try:
        suite_id, _ = _offline_gate(g, manifest)
    except NethackCandidateShadowError as exc:
        raise NethackCandidateShadowEvalError("candidate is not eligible for live shadow analysis") from exc

    root = (
        Path(g.state_dir)
        / "nethack"
        / "candidate-shadow"
        / manifest.candidate_id
        / manifest.version
    )
    try:
        files = sorted(path for path in root.glob("*.jsonl") if path.is_file())
    except OSError as exc:
        raise NethackCandidateShadowEvalError("candidate shadow log directory cannot be listed") from exc
    if len(files) > MAX_LOG_FILES:
        raise NethackCandidateShadowEvalError("candidate shadow log file count exceeds limit")

    total_bytes = 0
    buckets: dict[str, _RunBucket] = {}
    malformed_lines = 0
    invalid_identity_events = 0
    safety_violation_events = 0
    total_events = 0

    for path in files:
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise NethackCandidateShadowEvalError(f"candidate shadow log stat failed: {path.name}") from exc
        total_bytes += size
        if total_bytes > MAX_TOTAL_BYTES:
            raise NethackCandidateShadowEvalError("candidate shadow logs exceed total size limit")
        try:
            with path.open("rb") as stream:
                for raw_line in stream:
                    if len(raw_line) > MAX_LINE_BYTES:
                        malformed_lines += 1
                        continue
                    try:
                        event = json.loads(raw_line.decode("utf-8"))
                    except (UnicodeError, json.JSONDecodeError):
                        malformed_lines += 1
                        continue
                    if not isinstance(event, dict):
                        malformed_lines += 1
                        continue
                    bad_key = _validate_identity(event, manifest, suite_id)
                    if bad_key is not None:
                        invalid_identity_events += 1
                        if bad_key in {"candidate_action_sent", "execution", "policy_effect"}:
                            safety_violation_events += 1
                        continue
                    total_events += 1
                    run_id = _valid_uuid(event.get("run_id")) or "untracked"
                    bucket = buckets.setdefault(run_id, _RunBucket(run_id=run_id))
                    bucket.add(event)
        except OSError as exc:
            raise NethackCandidateShadowEvalError(f"candidate shadow log read failed: {path.name}") from exc

    run_results: list[dict[str, object]] = []
    proposal_kinds: Counter[str] = Counter()
    intent_proposals: Counter[str] = Counter()
    critical_tags: Counter[str] = Counter()
    totals = _RunBucket(run_id="__all__")
    terminal_correlated_runs = 0
    signature_groups: dict[str, dict[str, object]] = {}

    for run_id in sorted(buckets):
        bucket = buckets[run_id]
        totals.events += bucket.events
        totals.proposed += bucket.proposed
        totals.errors += bucket.errors
        totals.approved += bucket.approved
        totals.rejected += bucket.rejected
        totals.hold += bucket.hold
        totals.nonhold += bucket.nonhold
        totals.current_action_events += bucket.current_action_events
        totals.current_no_action_events += bucket.current_no_action_events
        totals.nonhold_on_current_no_action += bucket.nonhold_on_current_no_action
        totals.critical_events += bucket.critical_events
        totals.critical_rejected += bucket.critical_rejected
        totals.critical_errors += bucket.critical_errors
        totals.unexpected_would_actions += bucket.unexpected_would_actions
        proposal_kinds.update(bucket.proposal_kinds)
        intent_proposals.update(bucket.intent_proposals)
        critical_tags.update(bucket.critical_tags)

        terminal = {} if run_id == "untracked" else _run_terminal_evidence(Path(g.state_dir), run_id)
        if terminal.get("status") in {"dead", "ascended", "ended", "ended_unknown"}:
            terminal_correlated_runs += 1
        result: dict[str, object] = {
            "run_id": run_id,
            "events": bucket.events,
            "candidate_proposed": bucket.proposed,
            "candidate_errors": bucket.errors,
            "approved": bucket.approved,
            "rejected": bucket.rejected,
            "reject_rate": _rate(bucket.rejected, bucket.approved + bucket.rejected),
            "proposal_kind_counts": dict(sorted(bucket.proposal_kinds.items())),
            "current_intent_counts": dict(sorted(bucket.current_intents.items())),
            "intent_proposal_counts": dict(sorted(bucket.intent_proposals.items())),
            "critical_events": bucket.critical_events,
            "critical_rejected": bucket.critical_rejected,
            "critical_errors": bucket.critical_errors,
            "critical_tag_counts": dict(sorted(bucket.critical_tags.items())),
            "candidate_nonhold_on_current_no_action": bucket.nonhold_on_current_no_action,
            "candidate_nonhold_on_current_no_action_rate": _rate(
                bucket.nonhold_on_current_no_action,
                bucket.current_no_action_events,
            ),
            "unexpected_would_execute_action_events": bucket.unexpected_would_actions,
            "terminal_evidence": terminal,
        }
        run_results.append(result)

        signature = terminal.get("death_signature")
        if isinstance(signature, str) and signature:
            group = signature_groups.setdefault(
                signature,
                {
                    "death_signature": signature,
                    "runs": 0,
                    "events": 0,
                    "rejected": 0,
                    "errors": 0,
                    "proposal_kind_counts": Counter(),
                },
            )
            group["runs"] = int(group["runs"]) + 1
            group["events"] = int(group["events"]) + bucket.events
            group["rejected"] = int(group["rejected"]) + bucket.rejected
            group["errors"] = int(group["errors"]) + bucket.errors
            kinds = group["proposal_kind_counts"]
            if isinstance(kinds, Counter):
                kinds.update(bucket.proposal_kinds)

    signature_output: list[dict[str, object]] = []
    for signature in sorted(signature_groups):
        raw = signature_groups[signature]
        kinds = raw["proposal_kind_counts"]
        signature_output.append(
            {
                "death_signature": signature,
                "runs": raw["runs"],
                "events": raw["events"],
                "rejected": raw["rejected"],
                "errors": raw["errors"],
                "proposal_kind_counts": dict(sorted(kinds.items())) if isinstance(kinds, Counter) else {},
                "interpretation": "correlation_only",
            }
        )

    integrity = (
        malformed_lines == 0
        and invalid_identity_events == 0
        and safety_violation_events == 0
        and totals.unexpected_would_actions == 0
    )
    report: dict[str, object] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "evaluated_at": timestamp.isoformat(),
        "candidate_id": manifest.candidate_id,
        "candidate_version": manifest.version,
        "candidate_fingerprint": manifest.fingerprint,
        "command_sha256": manifest.command_hash,
        "suite_id": suite_id,
        "status": "completed" if total_events else "no_data",
        "log_files": len(files),
        "total_events": total_events,
        "malformed_lines": malformed_lines,
        "invalid_identity_events": invalid_identity_events,
        "safety_violation_events": safety_violation_events,
        "candidate_shadow_integrity_passed": integrity,
        "candidate_proposed": totals.proposed,
        "candidate_errors": totals.errors,
        "dispatch_error_rate": _rate(totals.errors, totals.events),
        "approved_proposals": totals.approved,
        "rejected_proposals": totals.rejected,
        "proposal_reject_rate": _rate(totals.rejected, totals.approved + totals.rejected),
        "proposal_kind_counts": dict(sorted(proposal_kinds.items())),
        "intent_proposal_counts": dict(sorted(intent_proposals.items())),
        "critical_events": totals.critical_events,
        "critical_rejected": totals.critical_rejected,
        "critical_errors": totals.critical_errors,
        "critical_tag_counts": dict(sorted(critical_tags.items())),
        "candidate_nonhold_on_current_no_action": totals.nonhold_on_current_no_action,
        "candidate_nonhold_on_current_no_action_rate": _rate(
            totals.nonhold_on_current_no_action,
            totals.current_no_action_events,
        ),
        "unexpected_would_execute_action_events": totals.unexpected_would_actions,
        "tracked_runs": sum(1 for key in buckets if key != "untracked"),
        "terminal_correlated_runs": terminal_correlated_runs,
        "runs": run_results,
        "death_signature_correlations": signature_output,
        "correlation_is_not_causation": True,
        # Candidate did not control these runs. Outcome/performance attribution is
        # therefore explicitly out of scope for P5f.
        "performance_improvement_assessed": False,
        "eligible_for_promotion_review": False,
        "automatic_promotion": False,
        "policy_effect": "none",
    }
    root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(root / "summary.json", report)
    return report


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich-nethack-candidate-shadow-evaluate")
    parser.add_argument("--config", metavar="PATH")
    parser.add_argument("--manifest", required=True, metavar="PATH")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        g = load_global(_repo_root(), Path(args.config) if args.config else None)
        manifest = load_candidate_manifest(Path(args.manifest))
        report = evaluate_live_candidate_shadow(g, manifest)
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (
        ConfigError,
        NethackCandidateShadowEvalError,
        ValueError,
    ) as exc:
        print(f"docich: エラー: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
