"""Versioned NetHack regression suite compiled from P5 candidate lessons.

P5c does not modify gameplay policy.  It converts durable lesson evidence into
self-contained regression cases and re-runs the currently reviewed safety
contracts.  Where P3e public StrategicRequest evidence is still available, it
is attached as replay input for later candidate-strategist evaluation.

No case in this module authorizes keypresses and no passing report promotes a
candidate automatically.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .actions import Action
from .config import ConfigError, GlobalConfig, load_global
from .game_switch import atomic_write_json
from .nethack_inventory import VisibleInventoryItem
from .nethack_observation import normalize_tty
from .nethack_policy import NethackLayeredPolicy
from .nethack_retrospective import SOURCE_NAME as RETROSPECTIVE_SOURCE
from .nethack_retrospective import normalize_death_signature
from .nethack_strategist import evaluate_proposal, execution_plan
from .nethack_strategy import StrategicProposal, StrategicRequest

SUITE_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
LESSON_MEMORY_SCHEMA_VERSION = 1
MAX_LESSONS_BYTES = 16 * 1024 * 1024
MAX_ADVISORY_BYTES = 32 * 1024 * 1024
MAX_CASES = 2048

CaseKind = Literal[
    "policy_survival_emergency",
    "policy_food_emergency",
    "proposal_freshness_gate",
    "repeated_death_memory",
    "terminal_unknown_guard",
    "evidence_gap_guard",
    "manual_review",
]


class NethackRegressionError(RuntimeError):
    """Regression evidence or suite data cannot be trusted."""


@dataclass(frozen=True)
class RegressionCase:
    case_id: str
    lesson_key: str
    category: str
    kind: CaseKind
    scored: bool
    evidence_count: int
    run_ids: tuple[str, ...]
    source_expeditions: tuple[int, ...]
    fixture: dict[str, object]
    expectation: dict[str, object]
    replay_request: dict[str, object] | None = None
    evidence: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "lesson_key": self.lesson_key,
            "category": self.category,
            "kind": self.kind,
            "scored": self.scored,
            "evidence_count": self.evidence_count,
            "run_ids": list(self.run_ids),
            "source_expeditions": list(self.source_expeditions),
            "fixture": self.fixture,
            "expectation": self.expectation,
            "replay_request": self.replay_request,
            "evidence": self.evidence or {},
            "policy_effect": "none",
        }


@dataclass(frozen=True)
class RegressionCaseResult:
    case_id: str
    kind: str
    scored: bool
    passed: bool | None
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "kind": self.kind,
            "scored": self.scored,
            "passed": self.passed,
            "detail": self.detail,
        }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _canonical_hash(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_bounded_json(path: Path, *, max_bytes: int) -> dict[str, object]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise NethackRegressionError(f"JSONを読めません: {path}") from exc
    if size > max_bytes:
        raise NethackRegressionError(f"JSON size limit超過: {path.name}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NethackRegressionError(f"JSONが不正です: {path.name}") from exc
    if not isinstance(raw, dict):
        raise NethackRegressionError(f"JSON rootがobjectではありません: {path.name}")
    return raw


def _validate_uuid(value: object, *, where: str) -> str:
    if not isinstance(value, str):
        raise NethackRegressionError(f"{where} run_idが不正です")
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise NethackRegressionError(f"{where} run_idが不正です") from exc
    if canonical != value:
        raise NethackRegressionError(f"{where} run_idが標準UUID形式ではありません")
    return value


def _load_lessons(root: Path) -> tuple[dict[str, object], ...]:
    payload = _read_bounded_json(root / "lessons.json", max_bytes=MAX_LESSONS_BYTES)
    if payload.get("schema_version") != LESSON_MEMORY_SCHEMA_VERSION:
        raise NethackRegressionError("lessons.json schema_versionが不正です")
    if payload.get("policy_effect") != "none":
        raise NethackRegressionError("lessons.json must remain policy_effect=none")
    raw_lessons = payload.get("lessons")
    if not isinstance(raw_lessons, list):
        raise NethackRegressionError("lessons.json lessonsがlistではありません")
    if len(raw_lessons) > MAX_CASES:
        raise NethackRegressionError("lesson count exceeds regression case limit")

    lessons: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in raw_lessons:
        if not isinstance(raw, dict):
            raise NethackRegressionError("lesson entryがobjectではありません")
        if raw.get("source") != RETROSPECTIVE_SOURCE:
            continue
        if raw.get("status") != "candidate" or raw.get("policy_effect") != "none":
            raise NethackRegressionError("P5 lesson is not candidate/policy_effect=none")
        key = raw.get("lesson_key")
        category = raw.get("category")
        count = raw.get("evidence_count")
        run_ids = raw.get("run_ids")
        if not isinstance(key, str) or not key or len(key) > 128:
            raise NethackRegressionError("lesson_keyが不正です")
        if key in seen:
            raise NethackRegressionError("duplicate lesson_key")
        seen.add(key)
        if not isinstance(category, str) or not category or len(category) > 80:
            raise NethackRegressionError("lesson categoryが不正です")
        if type(count) is not int or count < 1:
            raise NethackRegressionError("lesson evidence_countが不正です")
        if not isinstance(run_ids, list) or len(run_ids) > 20:
            raise NethackRegressionError("lesson run_idsが不正です")
        canonical_ids = [_validate_uuid(item, where="lesson") for item in run_ids]
        item = dict(raw)
        item["run_ids"] = canonical_ids
        lessons.append(item)
    return tuple(lessons)


def _load_run(root: Path, run_id: str) -> dict[str, object] | None:
    path = root / "runs" / f"{run_id}.json"
    if not path.is_file():
        return None
    payload = _read_bounded_json(path, max_bytes=8 * 1024 * 1024)
    if payload.get("schema_version") != 1 or payload.get("run_id") != run_id:
        raise NethackRegressionError(f"run fileが不正です: {run_id}")
    expedition = payload.get("expedition")
    if type(expedition) is not int or expedition < 1:
        raise NethackRegressionError(f"run expeditionが不正です: {run_id}")
    return payload


def _lesson_evidence_from_run(
    run: dict[str, object], lesson_key: str
) -> dict[str, object] | None:
    lessons = run.get("lessons")
    if not isinstance(lessons, list):
        return None
    for item in lessons:
        if (
            isinstance(item, dict)
            and item.get("source") == RETROSPECTIVE_SOURCE
            and item.get("lesson_key") == lesson_key
        ):
            evidence = item.get("evidence")
            return dict(evidence) if isinstance(evidence, dict) else {}
    return None


def _run_window(run: dict[str, object]) -> tuple[float | None, float | None]:
    start = run.get("started_epoch")
    start_epoch = float(start) if type(start) is int and start >= 0 else None
    end_epoch: float | None = None
    terminal = run.get("terminal")
    if isinstance(terminal, dict):
        end = terminal.get("endtime")
        if type(end) is int and end >= 0:
            end_epoch = float(end)
    if end_epoch is None:
        finished = run.get("last_finished_at")
        if isinstance(finished, str):
            try:
                end_epoch = dt.datetime.fromisoformat(finished).timestamp()
            except ValueError:
                pass
    return start_epoch, end_epoch


def _advisory_events(root: Path, run: dict[str, object]) -> tuple[dict[str, object], ...]:
    path = root / "strategist" / "advisory.jsonl"
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return ()
    except OSError:
        return ()
    if size > MAX_ADVISORY_BYTES:
        return ()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return ()
    start_epoch, end_epoch = _run_window(run)
    events: list[dict[str, object]] = []
    for line in lines:
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            continue
        ts = raw.get("ts")
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            continue
        value = float(ts)
        if start_epoch is not None and value < start_epoch - 5.0:
            continue
        if end_epoch is not None and value > end_epoch + 5.0:
            continue
        # Keep only public replay material.  The P3e request schema already
        # excludes hidden NetHack engine state.
        request = raw.get("request")
        if request is not None and not isinstance(request, dict):
            request = None
        proposal = raw.get("proposal")
        if proposal is not None and not isinstance(proposal, dict):
            proposal = None
        evaluation = raw.get("evaluation")
        if evaluation is not None and not isinstance(evaluation, dict):
            evaluation = None
        events.append(
            {
                "ts": value,
                "intent": raw.get("intent"),
                "reason": raw.get("reason"),
                "request": request,
                "proposal": proposal,
                "evaluation": evaluation,
                "status": raw.get("status"),
            }
        )
    return tuple(events)


def _best_replay_request(
    root: Path, runs: tuple[dict[str, object], ...], *, intent: str | None = None,
    rejected_only: bool = False,
) -> dict[str, object] | None:
    candidates: list[tuple[float, dict[str, object]]] = []
    for run in runs:
        for event in _advisory_events(root, run):
            if intent is not None and event.get("intent") != intent:
                continue
            if rejected_only:
                evaluation = event.get("evaluation")
                if not isinstance(evaluation, dict) or evaluation.get("status") != "rejected":
                    continue
            request = event.get("request")
            if isinstance(request, dict):
                candidates.append((float(event["ts"]), dict(request)))
    return None if not candidates else max(candidates, key=lambda item: item[0])[1]


def _case_id(lesson_key: str, kind: str) -> str:
    return hashlib.sha256(f"{lesson_key}\0{kind}".encode("utf-8")).hexdigest()[:24]


def _base_case_data(
    root: Path, lesson: dict[str, object]
) -> tuple[str, str, int, tuple[str, ...], tuple[dict[str, object], ...], tuple[int, ...], list[dict[str, object]]]:
    key = str(lesson["lesson_key"])
    category = str(lesson["category"])
    count = int(lesson["evidence_count"])
    run_ids = tuple(str(item) for item in lesson["run_ids"])
    runs: list[dict[str, object]] = []
    expeditions: list[int] = []
    evidence: list[dict[str, object]] = []
    for run_id in run_ids:
        run = _load_run(root, run_id)
        if run is None:
            continue
        runs.append(run)
        expedition = run.get("expedition")
        if type(expedition) is int:
            expeditions.append(expedition)
        item = _lesson_evidence_from_run(run, key)
        if item is not None:
            evidence.append(item)
    return key, category, count, run_ids, tuple(runs), tuple(sorted(set(expeditions))), evidence[-20:]


def _compile_case(root: Path, lesson: dict[str, object]) -> RegressionCase:
    key, category, count, run_ids, runs, expeditions, evidence_items = _base_case_data(root, lesson)
    common_evidence: dict[str, object] = {
        "lesson_text": lesson.get("text"),
        "lesson_evidence": evidence_items,
    }

    if category == "survival_signal":
        kind: CaseKind = "policy_survival_emergency"
        return RegressionCase(
            case_id=_case_id(key, kind), lesson_key=key, category=category, kind=kind,
            scored=True, evidence_count=count, run_ids=run_ids, source_expeditions=expeditions,
            fixture={
                "origin": "category_template",
                "tty": "msg\n###@.\n     \nDlvl:2 HP:2(10) Pw:4(4) AC:5 Exp:2\nT:12\n",
                "cols": 80,
                "rows": 5,
            },
            expectation={
                "layer": "strategic", "intent": "survival_emergency",
                "requires_llm": True, "actions": [],
            },
            replay_request=_best_replay_request(root, runs, intent="survival_emergency"),
            evidence=common_evidence,
        )

    if category == "food_survival":
        kind = "policy_food_emergency"
        return RegressionCase(
            case_id=_case_id(key, kind), lesson_key=key, category=category, kind=kind,
            scored=True, evidence_count=count, run_ids=run_ids, source_expeditions=expeditions,
            fixture={
                "origin": "category_template",
                "tty": "msg\n###@.\n     \nDlvl:2 HP:10(10) Pw:4(4) AC:5 Exp:2\nT:12 Weak\n",
                "cols": 80,
                "rows": 5,
            },
            expectation={
                "layer": "strategic", "intent": "food_emergency",
                "requires_llm": True, "actions": [],
            },
            replay_request=_best_replay_request(root, runs, intent="food_emergency"),
            evidence=common_evidence,
        )

    if category == "proposal_drift":
        kind = "proposal_freshness_gate"
        return RegressionCase(
            case_id=_case_id(key, kind), lesson_key=key, category=category, kind=kind,
            scored=True, evidence_count=count, run_ids=run_ids, source_expeditions=expeditions,
            fixture={
                "origin": "safety_template",
                "request_inventory": [
                    {
                        "letter": "a", "description": "an uncursed food ration", "quantity": 1,
                        "buc": "uncursed", "equipped": False, "unpaid": False,
                        "category_hint": "food",
                    }
                ],
                "current_inventory": [
                    {
                        "letter": "a", "description": "a potion called cloudy", "quantity": 1,
                        "buc": "unknown", "equipped": False, "unpaid": False,
                        "category_hint": "potion",
                    }
                ],
                "proposal": {
                    "schema_version": 1, "kind": "consume", "rationale": "fixture",
                    "inventory_letter": "a", "prompt_answer": None, "narration": "",
                },
            },
            expectation={"evaluation_status": "rejected", "execution_allowed": False},
            replay_request=_best_replay_request(root, runs, rejected_only=True),
            evidence=common_evidence,
        )

    if category == "repeated_death":
        kind = "repeated_death_memory"
        signatures: list[str] = []
        totals: list[int] = []
        for item in evidence_items:
            sig = item.get("death_signature")
            total = item.get("total")
            if isinstance(sig, str) and sig:
                signatures.append(sig)
            if type(total) is int and total >= 1:
                totals.append(total)
        return RegressionCase(
            case_id=_case_id(key, kind), lesson_key=key, category=category, kind=kind,
            scored=True, evidence_count=count, run_ids=run_ids, source_expeditions=expeditions,
            fixture={"origin": "run_evidence", "death_signatures": signatures, "recorded_totals": totals},
            expectation={"minimum_recorded_repeat_total": 2, "requires_signature": True},
            evidence=common_evidence,
        )

    if category == "terminal_evidence":
        kind = "terminal_unknown_guard"
        return RegressionCase(
            case_id=_case_id(key, kind), lesson_key=key, category=category, kind=kind,
            scored=True, evidence_count=count, run_ids=run_ids, source_expeditions=expeditions,
            fixture={"origin": "safety_template", "death_reason": None},
            expectation={"death_signature": None, "policy_effect": "none"},
            evidence=common_evidence,
        )

    if category == "evidence_gap":
        kind = "evidence_gap_guard"
        return RegressionCase(
            case_id=_case_id(key, kind), lesson_key=key, category=category, kind=kind,
            scored=True, evidence_count=count, run_ids=run_ids, source_expeditions=expeditions,
            fixture={"origin": "lesson_memory", "advisory_evidence_required": False},
            expectation={"policy_effect": "none", "automatic_policy_change": False},
            evidence=common_evidence,
        )

    kind = "manual_review"
    return RegressionCase(
        case_id=_case_id(key, kind), lesson_key=key, category=category, kind=kind,
        scored=False, evidence_count=count, run_ids=run_ids, source_expeditions=expeditions,
        fixture={"origin": "lesson_memory"},
        expectation={"manual_review": True},
        evidence=common_evidence,
    )


def build_suite(g: GlobalConfig, *, now: dt.datetime | None = None) -> dict[str, object]:
    timestamp = now or dt.datetime.now(dt.timezone.utc)
    if timestamp.tzinfo is None:
        raise NethackRegressionError("now must be timezone-aware")
    root = Path(g.state_dir) / "nethack"
    lessons = _load_lessons(root)
    cases = tuple(_compile_case(root, lesson) for lesson in lessons)
    case_dicts = [case.to_dict() for case in cases]
    suite_content = {
        "schema_version": SUITE_SCHEMA_VERSION,
        "generated_at": timestamp.isoformat(),
        "source": "p5c_lessons_regression",
        "cases": case_dicts,
        "policy_effect": "none",
        "automatic_promotion": False,
    }
    suite_id = _canonical_hash({"schema_version": SUITE_SCHEMA_VERSION, "cases": case_dicts})
    suite = {"suite_id": suite_id, **suite_content}
    output = root / "regression" / "suite.json"
    atomic_write_json(output, suite)
    return suite


def _action_dicts(actions: tuple[Action, ...]) -> list[dict[str, object]]:
    return [
        {
            "type": action.type,
            "text": action.text,
            "key": action.key,
            "keys": list(action.keys),
            "ms": action.ms,
        }
        for action in actions
    ]


def _inventory_item(raw: dict[str, object]) -> VisibleInventoryItem:
    return VisibleInventoryItem(
        letter=str(raw["letter"]),
        description=str(raw["description"]),
        quantity=raw.get("quantity") if type(raw.get("quantity")) is int else None,
        buc=str(raw.get("buc", "unknown")),
        equipped=raw.get("equipped") is True,
        unpaid=raw.get("unpaid") is True,
        category_hint=str(raw.get("category_hint", "unknown")),
    )


def _evaluate_case(raw: dict[str, object]) -> RegressionCaseResult:
    case_id = str(raw.get("case_id", ""))
    kind = str(raw.get("kind", ""))
    scored = raw.get("scored") is True
    fixture = raw.get("fixture")
    expectation = raw.get("expectation")
    if not isinstance(fixture, dict) or not isinstance(expectation, dict):
        return RegressionCaseResult(case_id, kind, scored, False if scored else None, "invalid fixture")

    if not scored or kind == "manual_review":
        return RegressionCaseResult(case_id, kind, False, None, "informational/manual review")

    if kind in {"policy_survival_emergency", "policy_food_emergency"}:
        tty = fixture.get("tty")
        cols = fixture.get("cols")
        rows = fixture.get("rows")
        if not isinstance(tty, str) or type(cols) is not int or type(rows) is not int:
            return RegressionCaseResult(case_id, kind, True, False, "invalid policy fixture")
        obs = normalize_tty(tty, cols=cols, rows=rows)
        decision = NethackLayeredPolicy().decide(obs)
        passed = (
            decision.layer == expectation.get("layer")
            and decision.intent == expectation.get("intent")
            and decision.requires_llm is expectation.get("requires_llm")
            and _action_dicts(decision.actions) == expectation.get("actions")
        )
        return RegressionCaseResult(
            case_id, kind, True, passed,
            f"got layer={decision.layer} intent={decision.intent} actions={len(decision.actions)}",
        )

    if kind == "proposal_freshness_gate":
        before_raw = fixture.get("request_inventory")
        current_raw = fixture.get("current_inventory")
        proposal_raw = fixture.get("proposal")
        if not isinstance(before_raw, list) or not isinstance(current_raw, list) or not isinstance(proposal_raw, dict):
            return RegressionCaseResult(case_id, kind, True, False, "invalid proposal fixture")
        try:
            before = tuple(_inventory_item(item) for item in before_raw if isinstance(item, dict))
            current = tuple(_inventory_item(item) for item in current_raw if isinstance(item, dict))
            request = StrategicRequest(
                schema_version=1,
                intent="survival_emergency",
                reason="regression fixture",
                observation={},
                inventory=[item.public_dict() for item in before],
                constraints=(),
            )
            proposal = StrategicProposal(
                schema_version=1,
                kind=str(proposal_raw.get("kind")),  # type: ignore[arg-type]
                rationale=str(proposal_raw.get("rationale", "fixture")),
                inventory_letter=proposal_raw.get("inventory_letter") if isinstance(proposal_raw.get("inventory_letter"), str) else None,
                prompt_answer=None,
                narration="",
            )
            obs = normalize_tty(
                "msg\n###@.\n     \nDlvl:2 HP:2(10) Pw:4(4) AC:5 Exp:2\nT:12\n",
                cols=80,
                rows=5,
            )
            evaluation = evaluate_proposal(
                request,
                proposal,
                current_observation=obs,
                current_inventory=current,
            )
            plan = execution_plan(evaluation)
        except Exception as exc:
            return RegressionCaseResult(case_id, kind, True, False, f"fixture error: {str(exc)[:160]}")
        passed = (
            evaluation.status == expectation.get("evaluation_status")
            and plan.allowed is expectation.get("execution_allowed")
        )
        return RegressionCaseResult(
            case_id, kind, True, passed,
            f"evaluation={evaluation.status} execution_allowed={plan.allowed}",
        )

    if kind == "repeated_death_memory":
        signatures = fixture.get("death_signatures")
        totals = fixture.get("recorded_totals")
        minimum = expectation.get("minimum_recorded_repeat_total")
        requires_signature = expectation.get("requires_signature") is True
        valid_signatures = isinstance(signatures, list) and any(isinstance(item, str) and item for item in signatures)
        maximum = max((item for item in totals if type(item) is int), default=0) if isinstance(totals, list) else 0
        passed = (not requires_signature or valid_signatures) and type(minimum) is int and maximum >= minimum
        return RegressionCaseResult(case_id, kind, True, passed, f"signature={valid_signatures} max_total={maximum}")

    if kind == "terminal_unknown_guard":
        signature = normalize_death_signature(fixture.get("death_reason"))
        passed = signature is expectation.get("death_signature") and raw.get("policy_effect") == expectation.get("policy_effect")
        return RegressionCaseResult(case_id, kind, True, passed, f"death_signature={signature!r}")

    if kind == "evidence_gap_guard":
        passed = raw.get("policy_effect") == "none" and expectation.get("automatic_policy_change") is False
        return RegressionCaseResult(case_id, kind, True, passed, "candidate evidence remains non-operative")

    return RegressionCaseResult(case_id, kind, True, False, "unknown scored case kind")


def _load_suite(path: Path) -> dict[str, object]:
    payload = _read_bounded_json(path, max_bytes=64 * 1024 * 1024)
    if payload.get("schema_version") != SUITE_SCHEMA_VERSION:
        raise NethackRegressionError("regression suite schema_versionが不正です")
    if payload.get("policy_effect") != "none" or payload.get("automatic_promotion") is not False:
        raise NethackRegressionError("regression suite may not enable policy/promotion")
    suite_id = payload.get("suite_id")
    cases = payload.get("cases")
    if not isinstance(suite_id, str) or len(suite_id) != 64:
        raise NethackRegressionError("regression suite_idが不正です")
    if not isinstance(cases, list) or len(cases) > MAX_CASES:
        raise NethackRegressionError("regression casesが不正です")
    expected_id = _canonical_hash({"schema_version": SUITE_SCHEMA_VERSION, "cases": cases})
    if suite_id != expected_id:
        raise NethackRegressionError("regression suite content hashが一致しません")
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise NethackRegressionError("regression caseがobjectではありません")
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise NethackRegressionError("regression case_idが不正/重複です")
        seen.add(case_id)
        if case.get("policy_effect") != "none":
            raise NethackRegressionError("regression case may not affect policy")
    return payload


def evaluate_suite(g: GlobalConfig, *, suite_path: Path | None = None, now: dt.datetime | None = None) -> dict[str, object]:
    timestamp = now or dt.datetime.now(dt.timezone.utc)
    if timestamp.tzinfo is None:
        raise NethackRegressionError("now must be timezone-aware")
    root = Path(g.state_dir) / "nethack" / "regression"
    path = suite_path or root / "suite.json"
    suite = _load_suite(path)
    raw_cases = suite["cases"]
    assert isinstance(raw_cases, list)
    results = [_evaluate_case(case) for case in raw_cases if isinstance(case, dict)]
    scored = [item for item in results if item.scored]
    passed = sum(1 for item in scored if item.passed is True)
    failed = sum(1 for item in scored if item.passed is False)
    informational = sum(1 for item in results if not item.scored)
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "evaluated_at": timestamp.isoformat(),
        "suite_id": suite["suite_id"],
        "total_cases": len(results),
        "scored_cases": len(scored),
        "passed_cases": passed,
        "failed_cases": failed,
        "informational_cases": informational,
        "baseline_contract_passed": failed == 0,
        "ready_for_candidate_evaluation": bool(scored) and failed == 0,
        "results": [item.to_dict() for item in results],
        "policy_effect": "none",
        "automatic_promotion": False,
    }
    atomic_write_json(root / "latest_report.json", report)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich-nethack-regression")
    parser.add_argument("--config", metavar="PATH")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build")
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--suite", metavar="PATH")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        g = load_global(_repo_root(), Path(args.config) if args.config else None)
        if args.command == "build":
            payload = build_suite(g)
        else:
            payload = evaluate_suite(
                g,
                suite_path=Path(args.suite) if getattr(args, "suite", None) else None,
            )
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (ConfigError, NethackRegressionError, ValueError) as exc:
        print(f"docich: エラー: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
