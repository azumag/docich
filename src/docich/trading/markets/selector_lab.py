"""Bounded AI improvement and prospective evaluation for stock selection.

Selector tuning is deliberately independent from trade-entry/exit strategy tuning.
The model may suggest only a small allowlisted parameter delta. Adoption is based
on future, previously unobserved candidate snapshots, never on the model's prose or
on the selector's own score.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from .core import JST, decimal, digest, stamp
from .lab import write_json
from .selector import Candidate, SelectorPolicy, active_selector_policy, select_universe

D = Decimal

# Only fields that affect the current Candidate observations are mutable today.
# momentum_window_s / volume_window_s remain in SelectorPolicy for a future live
# provider that can generate independent windowed measurements, but changing
# them now would be a no-op and would make the A/B comparison dishonest.
MUTABLE_FIELDS = {
    "momentum_weight",
    "volume_accel_weight",
    "turnover_weight",
    "volatility_weight",
    "spread_penalty",
    "min_turnover_jpy",
    "max_spread_bps_for_selection",
    "candidate_count",
    "focused_universe_count",
    "replace_margin",
    "replace_cooldown_s",
    "warmup_s",
}

# Safety/measurement contracts are intentionally NOT mutable by AI:
# candidate_age_s controls feed freshness and exit_buffer_s protects the fixed
# 10:00 end. Risk capital/live authority/feed endpoints are outside this class.


def _read_json(path: Path) -> dict:
    try:
        body = json.loads(path.read_text())
        return body if isinstance(body, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _bounded_candidate(base: SelectorPolicy, changes: dict) -> SelectorPolicy:
    if not isinstance(changes, dict) or not changes or len(changes) > len(MUTABLE_FIELDS):
        raise ValueError("invalid selector changes")
    unknown = set(changes) - MUTABLE_FIELDS
    if unknown:
        raise ValueError("selector change contains immutable/unknown field")
    data = asdict(base)
    data.update(changes)
    candidate = SelectorPolicy(**data)

    def delta(name: str) -> Decimal:
        return abs(decimal(getattr(candidate, name)) - decimal(getattr(base, name)))

    for name, limit in {
        "momentum_weight": "0.50",
        "volume_accel_weight": "0.50",
        "turnover_weight": "0.50",
        "volatility_weight": "0.50",
        "spread_penalty": "0.50",
        "max_spread_bps_for_selection": 10,
        "candidate_count": 10,
        "focused_universe_count": 2,
        "replace_margin": "0.10",
        "replace_cooldown_s": 15,
        "warmup_s": 60,
    }.items():
        if name in changes and delta(name) > decimal(limit):
            raise ValueError(f"selector change too large: {name}")

    if "min_turnover_jpy" in changes:
        before = decimal(base.min_turnover_jpy)
        after = decimal(candidate.min_turnover_jpy)
        if after < before / 2 or after > before * 2:
            raise ValueError("selector turnover change too large")
    return candidate


def _selector_summary(root: Path) -> dict:
    state = _read_json(root / "selector-state.json")
    return {
        "status": state.get("status"),
        "focused_count": len(state.get("focused_symbols", [])) if isinstance(state.get("focused_symbols"), list) else 0,
        "held_count": len(state.get("held_symbols", [])) if isinstance(state.get("held_symbols"), list) else 0,
        "changed": bool(state.get("changed")),
        "policy": state.get("policy"),
    }


def propose_selector(root: Path, g, *, agents: str, report_id: str, report: dict,
                     now: float, generate=None) -> dict:
    """Create one bounded selector challenger for one completed stock report."""
    now = stamp(now)
    marker = root / "selector-challenger.json"
    status_path = root / "selector-improvement-status.json"
    if marker.exists():
        marker_body = _read_json(marker)
        return {"status": "forward_test_running", "id": marker_body.get("id"),
                "report_id": marker_body.get("report_id"), "as_of": now}

    previous = _read_json(status_path)
    if previous.get("report_id") == report_id and previous.get("status") in {
        "no_change", "forward_test", "needs_ai_configuration", "failed",
        "paper_adopted", "rejected", "awaiting_flat",
    }:
        return previous
    if not isinstance(report_id, str) or not report_id or not isinstance(report, dict):
        raise ValueError("invalid selector improvement report")
    if not agents or (generate is None and os.environ.get("DOCICH_ALLOW_REAL_AI") != "1"):
        result = {"status": "needs_ai_configuration", "report_id": report_id, "as_of": now}
        write_json(status_path, result)
        return result

    baseline = active_selector_policy(root)
    facts = {
        "report": report,
        "selector": _selector_summary(root),
        "current_policy": asdict(baseline),
    }
    prompt = (
        "日本株1時間PAPERスキャルピングの銘柄選定条件を1回だけ小さく改善する。"
        "以下は観測済み事実で、未来価格は含まない。返答はJSONのみ。"
        "changesには許可された項目のうち変更するものだけを書く。"
        "候補は今後の未観測スナップショットでbaselineと並列評価され、即時採用されない。"
        "資金、売買戦略、feed、URL、credential、momentum_window_s、volume_window_s、"
        "candidate_age_s、exit_buffer_s、live権限は変更禁止。"
        "許可項目: momentum_weight,volume_accel_weight,turnover_weight,volatility_weight,"
        "spread_penalty,min_turnover_jpy,max_spread_bps_for_selection,candidate_count,"
        "focused_universe_count,replace_margin,replace_cooldown_s,warmup_s。reasonは240字以内。"
        "形式 {\"changes\":{...},\"reason\":\"...\"}\n"
        + json.dumps(facts, ensure_ascii=False)
    )
    if generate is None:
        from ..ai_text import generate_text
        generate = lambda text: generate_text(
            g, label="RADIO:market-stocks-selector", agents=agents,
            prompt_text=text, timeout=120,
        )
    try:
        raw = generate(prompt)
        if not isinstance(raw, str) or len(raw) > 10000:
            raise ValueError("invalid model response")
        data = json.loads(raw)
        if set(data) != {"changes", "reason"} or not isinstance(data["reason"], str) or len(data["reason"]) > 240:
            raise ValueError("invalid selector proposal schema")
        candidate = _bounded_candidate(baseline, data["changes"])
        if candidate.version == baseline.version:
            result = {"status": "no_change", "report_id": report_id, "as_of": now}
        else:
            candidate_id = digest({
                "baseline": asdict(baseline),
                "candidate": asdict(candidate),
                "report_id": report_id,
            })[:24]
            marker_body = {
                "id": candidate_id,
                "baseline": asdict(baseline),
                "candidate": asdict(candidate),
                "created_at": now,
                "report_id": report_id,
                "reason": data["reason"],
                "mode": "paper",
                "live_enabled": False,
            }
            write_json(marker, marker_body)
            result = {"status": "forward_test", "id": candidate_id, "report_id": report_id, "as_of": now}
    except Exception as exc:
        result = {"status": "failed", "error": type(exc).__name__, "report_id": report_id, "as_of": now}
    write_json(status_path, result)
    return result


def _new_arm() -> dict:
    return {
        "focused_symbols": [],
        "last_replaced_at": 0,
        "prices": {},
        "samples": 0,
        "opportunity_sum_bps": "0",
        "churn": 0,
    }


def _opportunity(arm: dict, by_symbol: dict[str, Candidate]) -> None:
    total = decimal(arm.get("opportunity_sum_bps", "0"))
    samples = int(arm.get("samples", 0) or 0)
    previous = arm.get("prices") if isinstance(arm.get("prices"), dict) else {}
    focused = arm.get("focused_symbols") if isinstance(arm.get("focused_symbols"), list) else []
    for symbol in focused:
        current = by_symbol.get(symbol)
        if current is None or symbol not in previous:
            continue
        try:
            old = decimal(previous[symbol])
            new = decimal(current.price)
            if old <= 0 or new <= 0:
                continue
            movement = abs(new / old - 1) * D(10000)
            # Selection quality asks: did the chosen symbol subsequently move
            # enough to overcome one current spread? Entry/exit direction and
            # realized PnL remain StrategyPolicy's responsibility.
            total += movement - decimal(current.spread_bps)
            samples += 1
        except (ValueError, ArithmeticError):
            continue
    arm["samples"] = samples
    arm["opportunity_sum_bps"] = str(total)


def advance_selector_challenger(root: Path, candidates: list[Candidate], *, now: float,
                                session_start: float) -> dict:
    """Feed both selector arms the same future snapshots without changing main policy."""
    marker = _read_json(root / "selector-challenger.json")
    if not marker:
        return {"status": "none"}
    candidate_id = marker.get("id")
    if not isinstance(candidate_id, str) or len(candidate_id) != 24 or any(c not in "0123456789abcdef" for c in candidate_id):
        raise ValueError("invalid selector challenger ID")
    baseline = SelectorPolicy(**marker["baseline"])
    proposal = SelectorPolicy(**marker["candidate"])
    now = stamp(now)
    by_symbol = {row.symbol: row for row in candidates}
    experiment_path = root / "selector-experiment.json"
    experiment = _read_json(experiment_path)
    if experiment.get("id") != candidate_id:
        experiment = {
            "id": candidate_id,
            "created_at": marker["created_at"],
            "observations": 0,
            "days": [],
            "arms": {"baseline": _new_arm(), "candidate": _new_arm()},
        }
    day = dt.datetime.fromtimestamp(now, JST).date().isoformat()
    if day not in experiment["days"]:
        experiment["days"].append(day)
    experiment["days"] = experiment["days"][-10:]

    for name, policy in (("baseline", baseline), ("candidate", proposal)):
        arm = experiment["arms"][name]
        _opportunity(arm, by_symbol)
        last_replaced = arm.get("last_replaced_at", 0)
        if not isinstance(last_replaced, (int, float)) or last_replaced < 0 or last_replaced > now:
            last_replaced = 0
        selected = select_universe(
            candidates, policy, now=now,
            current_symbols=arm.get("focused_symbols", []),
            held_symbols=[],
            last_replaced_at=last_replaced,
            session_start=session_start,
        )
        if selected["changed"]:
            arm["churn"] = int(arm.get("churn", 0) or 0) + 1
        arm["focused_symbols"] = selected["focused_symbols"]
        arm["last_replaced_at"] = selected["last_replaced_at"]
        arm["prices"] = {
            symbol: by_symbol[symbol].price
            for symbol in arm["focused_symbols"] if symbol in by_symbol
        }
    experiment["observations"] = int(experiment.get("observations", 0) or 0) + 1
    experiment["as_of"] = now
    write_json(experiment_path, experiment)
    return selector_assessment(root)


def selector_assessment(root: Path) -> dict:
    marker = _read_json(root / "selector-challenger.json")
    experiment = _read_json(root / "selector-experiment.json")
    if not marker or experiment.get("id") != marker.get("id"):
        return {"status": "none" if not marker else "collecting"}
    arms = experiment.get("arms", {})
    baseline = arms.get("baseline", {})
    candidate = arms.get("candidate", {})

    def average(arm: dict) -> Decimal:
        samples = int(arm.get("samples", 0) or 0)
        return decimal(arm.get("opportunity_sum_bps", "0")) / samples if samples else D(0)

    baseline_samples = int(baseline.get("samples", 0) or 0)
    candidate_samples = int(candidate.get("samples", 0) or 0)
    days = experiment.get("days", []) if isinstance(experiment.get("days"), list) else []
    observations = max(1, int(experiment.get("observations", 0) or 0))
    ready = len(set(days)) >= 2 and min(baseline_samples, candidate_samples) >= 100
    base_avg = average(baseline)
    cand_avg = average(candidate)
    base_churn = D(int(baseline.get("churn", 0) or 0)) / observations
    cand_churn = D(int(candidate.get("churn", 0) or 0)) / observations
    result = {
        "status": "collecting",
        "id": marker.get("id"),
        "report_id": marker.get("report_id"),
        "days": len(set(days)),
        "samples": {"baseline": baseline_samples, "candidate": candidate_samples},
        "avg_opportunity_bps": {"baseline": str(base_avg), "candidate": str(cand_avg)},
        "churn_rate": {"baseline": str(base_churn), "candidate": str(cand_churn)},
    }
    if ready:
        passes = cand_avg >= base_avg + D(1) and cand_churn <= base_churn + D("0.05")
        result["status"] = "qualified" if passes else "rejected"
    return result


def finalize_selector_challenger(root: Path, *, now: float, has_positions: bool) -> dict:
    marker_path = root / "selector-challenger.json"
    marker = _read_json(marker_path)
    if not marker:
        return {"status": "none"}
    verdict = selector_assessment(root)
    if verdict.get("status") == "collecting":
        return verdict
    if verdict.get("status") == "qualified" and has_positions:
        result = {**verdict, "status": "awaiting_flat", "as_of": stamp(now)}
        write_json(root / "selector-improvement-status.json", result)
        return result

    folder = root / "selector-experiments" / str(marker.get("id"))
    final_status = "paper_adopted" if verdict.get("status") == "qualified" else "rejected"
    final = {**verdict, "status": final_status, "report_id": marker.get("report_id"),
             "as_of": stamp(now), "proposal": marker}
    write_json(folder / "verdict.json", final)
    if final_status == "paper_adopted":
        # Keep the active file deliberately minimal; provenance lives in the
        # immutable experiment verdict and never grants live/broker authority.
        write_json(root / "active-selector-policy.json", {"policy": marker["candidate"]})
    marker_path.unlink(missing_ok=True)
    (root / "selector-experiment.json").unlink(missing_ok=True)
    write_json(root / "selector-improvement-status.json", final)
    return final
