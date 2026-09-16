"""Offline candidate-strategist replay over P5c regression suites (P5d).

A candidate is an explicitly supplied, versioned external command.  It receives
only a validated public StrategicRequest.  Candidate proposals are parsed by
the existing bounded strategist contract and re-checked by the P3d public-state
evaluator.  Nothing in this module writes gameplay config, changes the current
brain, widens executor allowlists, or sends NetHack keypresses.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import ConfigError, GlobalConfig, load_global
from .game_switch import atomic_write_json
from .nethack_inventory import VisibleInventoryItem
from .nethack_observation import NethackObservation, Vitals, normalize_tty
from .nethack_policy import NethackLayeredPolicy
from .nethack_regression import NethackRegressionError, _load_suite, evaluate_suite
from .nethack_strategist import (
    CommandStrategist,
    StrategistDispatchResult,
    evaluate_proposal,
    execution_plan,
)
from .nethack_strategy import StrategicRequest, build_strategic_request

MANIFEST_SCHEMA_VERSION = 1
CANDIDATE_REPORT_SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 64 * 1024
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_ALLOWED_REQUEST_KEYS = frozenset(
    {"schema_version", "intent", "reason", "observation", "inventory", "constraints"}
)
_ALLOWED_OBSERVATION_KEYS = frozenset(
    {"message", "prompt", "player", "vitals", "conditions", "local_map"}
)
_ALLOWED_VITAL_KEYS = frozenset(
    {
        "hp", "hp_max", "hp_ratio", "power", "power_max", "ac",
        "experience_level", "dungeon_level", "gold", "turn",
    }
)
_ALLOWED_ITEM_KEYS = frozenset(
    {"letter", "description", "quantity", "buc", "equipped", "unpaid", "category_hint"}
)
_ALLOWED_PROMPTS = frozenset({"none", "more", "yes_no", "direction", "selection", "text"})
_ALLOWED_CONDITIONS = frozenset(
    {
        "Hungry", "Weak", "Fainting", "Fainted", "Starved", "Blind", "Conf", "Stun",
        "Hallu", "Sick", "FoodPois", "Ill", "Slime", "Strngl", "Deaf", "Lev", "Fly", "Ride",
    }
)


class NethackCandidateError(RuntimeError):
    """Candidate manifest/replay cannot be evaluated safely."""


@dataclass(frozen=True)
class CandidateManifest:
    candidate_id: str
    version: str
    command: str | tuple[str, ...]
    timeout_s: float = 20.0
    max_request_bytes: int = 32_768
    max_response_bytes: int = 16_384
    max_cases: int = 64
    min_replay_cases: int = 1
    expected_suite_id: str | None = None

    @property
    def command_hash(self) -> str:
        raw = self.command if isinstance(self.command, str) else list(self.command)
        return hashlib.sha256(
            json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @property
    def fingerprint(self) -> str:
        payload = {
            "candidate_id": self.candidate_id,
            "version": self.version,
            "command_sha256": self.command_hash,
            "timeout_s": self.timeout_s,
            "max_request_bytes": self.max_request_bytes,
            "max_response_bytes": self.max_response_bytes,
            "max_cases": self.max_cases,
            "min_replay_cases": self.min_replay_cases,
            "expected_suite_id": self.expected_suite_id,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _exact_keys(raw: dict[str, object], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(raw) - set(allowed))
    if unknown:
        raise NethackCandidateError(f"{where} contains unknown fields: {unknown}")


def _bounded_int(raw: object, *, default: int, minimum: int, maximum: int, name: str) -> int:
    if raw is None:
        return default
    if type(raw) is not int or not minimum <= raw <= maximum:
        raise NethackCandidateError(f"{name} must be {minimum}-{maximum}")
    return raw


def _bounded_float(raw: object, *, default: float, minimum: float, maximum: float, name: str) -> float:
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise NethackCandidateError(f"{name} must be numeric")
    value = float(raw)
    if not minimum <= value <= maximum:
        raise NethackCandidateError(f"{name} must be {minimum}-{maximum}")
    return value


def load_candidate_manifest(path: Path) -> CandidateManifest:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise NethackCandidateError("candidate manifestを読めません") from exc
    if size > MAX_MANIFEST_BYTES:
        raise NethackCandidateError("candidate manifest size limit超過")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NethackCandidateError("candidate manifest JSONが不正です") from exc
    if not isinstance(raw, dict):
        raise NethackCandidateError("candidate manifest root must be object")
    allowed = frozenset(
        {
            "schema_version", "candidate_id", "version", "command", "timeout_s",
            "max_request_bytes", "max_response_bytes", "max_cases", "min_replay_cases",
            "expected_suite_id",
        }
    )
    _exact_keys(raw, allowed, "candidate manifest")
    if raw.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise NethackCandidateError("candidate manifest schema_versionが不正です")
    candidate_id = raw.get("candidate_id")
    version = raw.get("version")
    if not isinstance(candidate_id, str) or _ID_RE.fullmatch(candidate_id) is None:
        raise NethackCandidateError("candidate_idが不正です")
    if not isinstance(version, str) or _ID_RE.fullmatch(version) is None:
        raise NethackCandidateError("candidate versionが不正です")
    command_raw = raw.get("command")
    command: str | tuple[str, ...]
    if isinstance(command_raw, str) and command_raw.strip() and len(command_raw) <= 4096:
        command = command_raw
    elif (
        isinstance(command_raw, list)
        and 1 <= len(command_raw) <= 32
        and all(isinstance(item, str) and item and len(item) <= 2048 for item in command_raw)
    ):
        command = tuple(command_raw)
    else:
        raise NethackCandidateError("candidate command must be non-empty string/string array")
    expected = raw.get("expected_suite_id")
    if expected is not None and (
        not isinstance(expected, str)
        or len(expected) != 64
        or any(ch not in "0123456789abcdef" for ch in expected)
    ):
        raise NethackCandidateError("expected_suite_idが不正です")
    return CandidateManifest(
        candidate_id=candidate_id,
        version=version,
        command=command,
        timeout_s=_bounded_float(raw.get("timeout_s"), default=20.0, minimum=0.1, maximum=120.0, name="timeout_s"),
        max_request_bytes=_bounded_int(raw.get("max_request_bytes"), default=32_768, minimum=1024, maximum=1_048_576, name="max_request_bytes"),
        max_response_bytes=_bounded_int(raw.get("max_response_bytes"), default=16_384, minimum=1024, maximum=1_048_576, name="max_response_bytes"),
        max_cases=_bounded_int(raw.get("max_cases"), default=64, minimum=1, maximum=512, name="max_cases"),
        min_replay_cases=_bounded_int(raw.get("min_replay_cases"), default=1, minimum=1, maximum=512, name="min_replay_cases"),
        expected_suite_id=expected,
    )


def _optional_int(raw: dict[str, object], key: str) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise NethackCandidateError(f"replay observation vitals.{key} invalid")
    return value


def _parse_inventory(raw: object) -> tuple[VisibleInventoryItem, ...]:
    if not isinstance(raw, list) or len(raw) > 80:
        raise NethackCandidateError("replay inventory invalid")
    result: list[VisibleInventoryItem] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise NethackCandidateError("replay inventory item invalid")
        _exact_keys(item, _ALLOWED_ITEM_KEYS, "replay inventory item")
        letter = item.get("letter")
        description = item.get("description")
        quantity = item.get("quantity")
        buc = item.get("buc")
        equipped = item.get("equipped")
        unpaid = item.get("unpaid")
        category = item.get("category_hint")
        if (
            not isinstance(letter, str) or len(letter) != 1 or not letter.isascii()
            or letter in seen
            or not isinstance(description, str) or len(description) > 500
            or (quantity is not None and (type(quantity) is not int or quantity < 0))
            or buc not in {"unknown", "blessed", "uncursed", "cursed"}
            or type(equipped) is not bool
            or type(unpaid) is not bool
            or not isinstance(category, str) or len(category) > 80
        ):
            raise NethackCandidateError("replay inventory item fields invalid")
        seen.add(letter)
        result.append(
            VisibleInventoryItem(
                letter=letter,
                description=description,
                quantity=quantity,
                buc=buc,
                equipped=equipped,
                unpaid=unpaid,
                category_hint=category,
            )
        )
    return tuple(result)


def parse_public_replay_request(raw: object) -> tuple[StrategicRequest, NethackObservation, tuple[VisibleInventoryItem, ...]]:
    """Strictly revalidate a P3e request before giving it to a candidate."""
    if not isinstance(raw, dict):
        raise NethackCandidateError("replay request must be object")
    _exact_keys(raw, _ALLOWED_REQUEST_KEYS, "replay request")
    if raw.get("schema_version") != 1:
        raise NethackCandidateError("replay request schema_version invalid")
    intent = raw.get("intent")
    reason = raw.get("reason")
    if not isinstance(intent, str) or not intent or len(intent) > 120:
        raise NethackCandidateError("replay intent invalid")
    if not isinstance(reason, str) or not reason or len(reason) > 1000:
        raise NethackCandidateError("replay reason invalid")

    public = raw.get("observation")
    if not isinstance(public, dict):
        raise NethackCandidateError("replay observation invalid")
    _exact_keys(public, _ALLOWED_OBSERVATION_KEYS, "replay observation")
    message = public.get("message", "")
    prompt = public.get("prompt", "none")
    conditions_raw = public.get("conditions", [])
    local_map_raw = public.get("local_map", [])
    if not isinstance(message, str) or len(message) > 1024:
        raise NethackCandidateError("replay message invalid")
    if not isinstance(prompt, str) or prompt not in _ALLOWED_PROMPTS:
        raise NethackCandidateError("replay prompt invalid")
    if (
        not isinstance(conditions_raw, list)
        or len(conditions_raw) > 32
        or not all(isinstance(item, str) and item in _ALLOWED_CONDITIONS for item in conditions_raw)
    ):
        raise NethackCandidateError("replay conditions invalid")
    if (
        not isinstance(local_map_raw, list)
        or len(local_map_raw) > 17
        or not all(isinstance(row, str) and len(row) <= 80 for row in local_map_raw)
    ):
        raise NethackCandidateError("replay local_map invalid")
    player_raw = public.get("player")
    if player_raw is not None and not (
        isinstance(player_raw, list)
        and len(player_raw) == 2
        and all(type(value) is int and value >= 0 for value in player_raw)
    ):
        raise NethackCandidateError("replay player invalid")

    vitals_raw = public.get("vitals", {})
    if not isinstance(vitals_raw, dict):
        raise NethackCandidateError("replay vitals invalid")
    _exact_keys(vitals_raw, _ALLOWED_VITAL_KEYS, "replay vitals")
    vitals = Vitals(
        hp=_optional_int(vitals_raw, "hp"),
        hp_max=_optional_int(vitals_raw, "hp_max"),
        power=_optional_int(vitals_raw, "power"),
        power_max=_optional_int(vitals_raw, "power_max"),
        ac=_optional_int(vitals_raw, "ac"),
        experience_level=_optional_int(vitals_raw, "experience_level"),
        dungeon_level=_optional_int(vitals_raw, "dungeon_level"),
        gold=_optional_int(vitals_raw, "gold"),
        turn=_optional_int(vitals_raw, "turn"),
    )
    hp_ratio_raw = vitals_raw.get("hp_ratio")
    if hp_ratio_raw is not None:
        if isinstance(hp_ratio_raw, bool) or not isinstance(hp_ratio_raw, (int, float)):
            raise NethackCandidateError("replay hp_ratio invalid")
        hp_ratio = float(hp_ratio_raw)
        if not 0.0 <= hp_ratio <= 1.0:
            raise NethackCandidateError("replay hp_ratio invalid")
        derived = vitals.hp_ratio
        if derived is not None and abs(hp_ratio - derived) > 1e-6:
            raise NethackCandidateError("replay hp_ratio inconsistent with visible HP")

    inventory = _parse_inventory(raw.get("inventory", []))
    constraints_raw = raw.get("constraints", [])
    if (
        not isinstance(constraints_raw, list)
        or len(constraints_raw) > 32
        or not all(isinstance(item, str) and len(item) <= 500 for item in constraints_raw)
    ):
        raise NethackCandidateError("replay constraints invalid")

    request = StrategicRequest(
        schema_version=1,
        intent=intent,
        reason=reason,
        observation=dict(public),
        inventory=[item.public_dict() for item in inventory],
        constraints=tuple(constraints_raw),
    )
    observation = NethackObservation(
        raw_text="",
        message=message,
        map_rows=tuple(local_map_raw),
        status_lines=(),
        # The recorded player coordinates refer to the full TTY map, while
        # local_map is cropped; the P3d evaluator does not need player here.
        player=None,
        vitals=vitals,
        conditions=tuple(dict.fromkeys(conditions_raw)),
        prompt=prompt,
    )
    return request, observation, inventory


def _request_from_synthetic_case(case: dict[str, object]) -> tuple[StrategicRequest, NethackObservation, tuple[VisibleInventoryItem, ...]] | None:
    kind = case.get("kind")
    if kind not in {"policy_survival_emergency", "policy_food_emergency"}:
        return None
    fixture = case.get("fixture")
    if not isinstance(fixture, dict):
        return None
    tty = fixture.get("tty")
    cols = fixture.get("cols")
    rows = fixture.get("rows")
    if not isinstance(tty, str) or type(cols) is not int or type(rows) is not int:
        return None
    obs = normalize_tty(tty, cols=cols, rows=rows)
    decision = NethackLayeredPolicy().decide(obs)
    if decision.layer != "strategic" or not decision.requires_llm:
        return None
    request = build_strategic_request(obs, decision, ())
    return request, obs, ()


def _replay_for_case(case: dict[str, object]) -> tuple[str, StrategicRequest, NethackObservation, tuple[VisibleInventoryItem, ...]] | None:
    raw = case.get("replay_request")
    if raw is not None:
        try:
            request, obs, inventory = parse_public_replay_request(raw)
            return "recorded_public_request", request, obs, inventory
        except NethackCandidateError:
            # Never forward malformed/unknown fields.  A reviewed synthetic
            # fixture may still provide a safe fallback for known case kinds.
            pass
    synthetic = _request_from_synthetic_case(case)
    if synthetic is None:
        return None
    request, obs, inventory = synthetic
    return "synthetic_public_fixture", request, obs, inventory


def _manifest_command_for_strategist(manifest: CandidateManifest) -> str | list[str]:
    return manifest.command if isinstance(manifest.command, str) else list(manifest.command)


def evaluate_candidate(
    g: GlobalConfig,
    manifest: CandidateManifest,
    *,
    suite_path: Path | None = None,
    strategist: object | None = None,
    now: dt.datetime | None = None,
) -> dict[str, object]:
    timestamp = now or dt.datetime.now(dt.timezone.utc)
    if timestamp.tzinfo is None:
        raise NethackCandidateError("now must be timezone-aware")
    regression_root = Path(g.state_dir) / "nethack" / "regression"
    path = suite_path or regression_root / "suite.json"
    suite = _load_suite(path)
    suite_id = str(suite["suite_id"])
    if manifest.expected_suite_id is not None and manifest.expected_suite_id != suite_id:
        raise NethackCandidateError("candidate expected_suite_id does not match suite")

    baseline = evaluate_suite(g, suite_path=path, now=timestamp)
    if baseline.get("baseline_contract_passed") is not True:
        report = {
            "schema_version": CANDIDATE_REPORT_SCHEMA_VERSION,
            "evaluated_at": timestamp.isoformat(),
            "candidate_id": manifest.candidate_id,
            "candidate_version": manifest.version,
            "candidate_fingerprint": manifest.fingerprint,
            "command_sha256": manifest.command_hash,
            "suite_id": suite_id,
            "status": "blocked_baseline",
            "baseline_contract_passed": False,
            "replayable_cases": 0,
            "evaluated_cases": 0,
            "coverage_complete": False,
            "candidate_safety_contract_passed": False,
            "eligible_for_behavior_review": False,
            "eligible_for_promotion_review": False,
            "performance_improvement_assessed": False,
            "automatic_promotion": False,
            "policy_effect": "none",
            "results": [],
        }
        _write_candidate_report(regression_root, manifest, suite_id, report)
        return report

    if strategist is None:
        strategist = CommandStrategist(
            _manifest_command_for_strategist(manifest),
            timeout_s=manifest.timeout_s,
            max_request_bytes=manifest.max_request_bytes,
            max_response_bytes=manifest.max_response_bytes,
            cwd=g.repo_root,
        )

    raw_cases = suite.get("cases")
    assert isinstance(raw_cases, list)
    replayable: list[tuple[dict[str, object], str, StrategicRequest, NethackObservation, tuple[VisibleInventoryItem, ...]]] = []
    for case in raw_cases:
        if not isinstance(case, dict):
            continue
        replay = _replay_for_case(case)
        if replay is None:
            continue
        source, request, obs, inventory = replay
        replayable.append((case, source, request, obs, inventory))

    selected = replayable[: manifest.max_cases]
    results: list[dict[str, object]] = []
    kind_counts: Counter[str] = Counter()
    dispatch_errors = 0
    rejected = 0
    approved = 0
    unexpected_actions = 0
    for case, source, request, obs, inventory in selected:
        case_id = str(case.get("case_id", ""))
        try:
            dispatch = strategist.dispatch(request)
        except Exception as exc:
            dispatch = StrategistDispatchResult(status="error", error=str(exc).replace("\n", " ")[:240])
        if dispatch.status != "proposed" or dispatch.proposal is None:
            dispatch_errors += 1
            results.append(
                {
                    "case_id": case_id,
                    "request_source": source,
                    "status": "dispatch_error",
                    "error": dispatch.error,
                    "proposal_kind": None,
                    "evaluation_status": None,
                    "execution_allowed": False,
                    "execution_action_count": 0,
                }
            )
            continue

        proposal = dispatch.proposal
        kind_counts[proposal.kind] += 1
        evaluation = evaluate_proposal(
            request,
            proposal,
            current_observation=obs,
            current_inventory=inventory,
        )
        plan = execution_plan(evaluation)
        if evaluation.approved:
            approved += 1
        else:
            rejected += 1
        action_count = len(plan.actions)
        if action_count:
            unexpected_actions += 1
        results.append(
            {
                "case_id": case_id,
                "request_source": source,
                "status": "proposed",
                "proposal_kind": proposal.kind,
                "evaluation_status": evaluation.status,
                "evaluation_reason": evaluation.reason,
                # P3d may mark hold as allowed, but its plan is still a no-op.
                "execution_allowed": plan.allowed,
                "execution_action_count": action_count,
            }
        )

    coverage_complete = len(selected) == len(replayable)
    minimum_met = len(selected) >= manifest.min_replay_cases
    safety_passed = (
        minimum_met
        and coverage_complete
        and dispatch_errors == 0
        and rejected == 0
        and unexpected_actions == 0
    )
    report = {
        "schema_version": CANDIDATE_REPORT_SCHEMA_VERSION,
        "evaluated_at": timestamp.isoformat(),
        "candidate_id": manifest.candidate_id,
        "candidate_version": manifest.version,
        "candidate_fingerprint": manifest.fingerprint,
        "command_sha256": manifest.command_hash,
        "suite_id": suite_id,
        "status": "completed",
        "baseline_contract_passed": True,
        "replayable_cases": len(replayable),
        "evaluated_cases": len(selected),
        "skipped_due_to_max_cases": max(0, len(replayable) - len(selected)),
        "minimum_replay_cases": manifest.min_replay_cases,
        "minimum_replay_cases_met": minimum_met,
        "coverage_complete": coverage_complete,
        "dispatch_errors": dispatch_errors,
        "approved_proposals": approved,
        "rejected_proposals": rejected,
        "unexpected_execution_actions": unexpected_actions,
        "proposal_kind_counts": dict(sorted(kind_counts.items())),
        "candidate_safety_contract_passed": safety_passed,
        "eligible_for_behavior_review": safety_passed,
        # P5d deliberately has no outcome/performance evidence, so safety
        # replay alone can never authorize promotion.
        "eligible_for_promotion_review": False,
        "performance_improvement_assessed": False,
        "automatic_promotion": False,
        "policy_effect": "none",
        "results": results,
    }
    _write_candidate_report(regression_root, manifest, suite_id, report)
    return report


def _write_candidate_report(
    regression_root: Path,
    manifest: CandidateManifest,
    suite_id: str,
    report: dict[str, object],
) -> None:
    path = (
        regression_root
        / "candidates"
        / manifest.candidate_id
        / manifest.version
        / f"{suite_id}.json"
    )
    atomic_write_json(path, report)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich-nethack-candidate-evaluate")
    parser.add_argument("--config", metavar="PATH")
    parser.add_argument("--manifest", required=True, metavar="PATH")
    parser.add_argument("--suite", metavar="PATH")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        g = load_global(_repo_root(), Path(args.config) if args.config else None)
        manifest = load_candidate_manifest(Path(args.manifest))
        report = evaluate_candidate(
            g,
            manifest,
            suite_path=Path(args.suite) if args.suite else None,
        )
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (ConfigError, NethackRegressionError, NethackCandidateError, ValueError) as exc:
        print(f"docich: エラー: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
