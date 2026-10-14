"""Declarative PAPER strategy experiments.

AI may synthesize combinations of allowlisted market features for PAPER trading.
The worker evaluates the JSON spec; no generated code is executed.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from statistics import fmean, pstdev
from typing import Mapping, Sequence

from .market_data import MarketFrame
from .models import Opportunity, TradingValidationError, as_decimal
from .paper import paper_execution_price

D = Decimal
EXPERIMENT_FILENAME = "paper_strategy_experiment.json"
PENDING_FILENAME = "paper_strategy_pending.json"
EVALUATION_FILENAME = "paper_strategy_evaluation.json"
EVALUATION_HISTORY_FILENAME = "paper_strategy_evaluation_history.json"
PROMOTION_FILENAME = "paper_strategy_promotion_candidate.json"
EVALUATION_HISTORY_MAX = 20
_EVALUATION_HISTORY_KEYS = (
    "schema_version",
    "experiment_id",
    "activated_at",
    "closed_sells",
    "wins",
    "win_rate",
    "realized_pnl_jpy",
    "profit_factor",
    "max_realized_drawdown_pct",
    "ignored_unpaired_exits",
    "promotion_ready", "status", "reason_code", "evaluated_at",
    "accounting_scope", "attribution_method", "inventory_complete", "open_position_count",
    "self_entry_realized_pnl_jpy", "carry_in_realized_pnl_jpy",
    "self_entry_closed_sells", "carry_in_closed_sells", "mixed_origin_closed_sells",
)

ENTRY_FEATURES = {
    "return_bps", "zscore", "rsi", "sma_gap_bps", "volatility_bps",
    "breakout_bps", "drawdown_bps",
}
EXIT_FEATURES = ENTRY_FEATURES | {"pnl_bps", "hold_minutes"}
OPS = {">=", "<=", ">", "<"}
COMBINE = {"all", "any"}
MAX_RULES = 8
MAX_CONDITIONS = 4
MAX_LOOKBACK = 24
MAX_NOTIONAL = D("0.30")


class StrategyLabError(ValueError):
    pass


@dataclass(frozen=True)
class Condition:
    feature: str
    op: str
    threshold: Decimal
    lookback: int | None = None

    def __post_init__(self):
        feature = str(self.feature).strip()
        op = str(self.op).strip()
        if op not in OPS:
            raise StrategyLabError("condition op is invalid")
        object.__setattr__(self, "feature", feature)
        object.__setattr__(self, "op", op)
        threshold = as_decimal(self.threshold, "threshold")
        object.__setattr__(self, "threshold", threshold)
        if self.lookback is not None:
            if type(self.lookback) is not int or not 2 <= self.lookback <= MAX_LOOKBACK:
                raise StrategyLabError("lookback must be 2..24")


@dataclass(frozen=True)
class Rule:
    rule_id: str
    combine: str
    conditions: tuple[Condition, ...]
    max_notional_fraction: Decimal = D("0.10")

    def __post_init__(self):
        rid = "".join(ch for ch in str(self.rule_id).strip() if ch.isalnum() or ch in "._-")[:48]
        if not rid:
            raise StrategyLabError("rule_id is required")
        object.__setattr__(self, "rule_id", rid)
        combine = str(self.combine).strip().lower()
        if combine not in COMBINE:
            raise StrategyLabError("combine must be all or any")
        object.__setattr__(self, "combine", combine)
        if not 1 <= len(self.conditions) <= MAX_CONDITIONS:
            raise StrategyLabError("rule must have 1..4 conditions")
        fraction = as_decimal(self.max_notional_fraction, "max_notional_fraction")
        if fraction <= 0 or fraction > MAX_NOTIONAL:
            raise StrategyLabError("max_notional_fraction must be in (0, 0.30]")
        object.__setattr__(self, "max_notional_fraction", fraction)


@dataclass(frozen=True)
class StrategyExperiment:
    experiment_id: str
    name: str
    thesis: str
    entry_rules: tuple[Rule, ...]
    exit_rules: tuple[Rule, ...]
    max_pair_correlation: Decimal = D("0.85")
    activated_at: float = 0.0

    def __post_init__(self):
        exp = "".join(ch for ch in str(self.experiment_id).strip() if ch.isalnum() or ch in "._-")[:64]
        if not exp:
            raise StrategyLabError("experiment_id is required")
        object.__setattr__(self, "experiment_id", exp)
        name = str(self.name).strip()[:120]
        thesis = str(self.thesis).strip()[:600]
        if not name or not thesis:
            raise StrategyLabError("name and thesis are required")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "thesis", thesis)
        if not 1 <= len(self.entry_rules) <= MAX_RULES:
            raise StrategyLabError("entry_rules must have 1..8 rules")
        if not 1 <= len(self.exit_rules) <= MAX_RULES:
            raise StrategyLabError("exit_rules must have 1..8 rules")
        corr = as_decimal(self.max_pair_correlation, "max_pair_correlation")
        if corr < 0 or corr > 1:
            raise StrategyLabError("max_pair_correlation must be in [0,1]")
        object.__setattr__(self, "max_pair_correlation", corr)
        if not math.isfinite(float(self.activated_at)) or float(self.activated_at) < 0:
            raise StrategyLabError("activated_at must be finite and nonnegative")


@dataclass(frozen=True)
class ScanResult:
    opportunities: tuple[Opportunity, ...]
    reason_contexts: dict[str, dict[str, object]]


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        tmp.unlink(missing_ok=True)


def _lookback(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise StrategyLabError("lookback is invalid")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise StrategyLabError("lookback is invalid") from exc
    if str(result) != str(value).strip() and not isinstance(value, int):
        try:
            if float(value) != result:
                raise StrategyLabError("lookback is invalid")
        except (TypeError, ValueError):
            raise StrategyLabError("lookback is invalid")
    if not 2 <= result <= MAX_LOOKBACK:
        raise StrategyLabError("lookback must be 2..24")
    return result


def _condition_from_mapping(raw: Mapping[str, object], *, exit_rule: bool) -> Condition:
    if not isinstance(raw, Mapping):
        raise StrategyLabError("condition must be an object")
    feature = str(raw.get("feature") or "").strip()
    allowed = EXIT_FEATURES if exit_rule else ENTRY_FEATURES
    if feature not in allowed:
        raise StrategyLabError(f"unsupported feature: {feature}")
    lookback = _lookback(raw.get("lookback"))
    if feature in ENTRY_FEATURES and lookback is None:
        raise StrategyLabError(f"{feature} requires lookback")
    if feature in {"pnl_bps", "hold_minutes"}:
        lookback = None
    return Condition(
        feature=feature,
        op=str(raw.get("op") or ""),
        threshold=raw.get("threshold"),
        lookback=lookback,
    )


def _rule_from_mapping(raw: Mapping[str, object], *, exit_rule: bool) -> Rule:
    if not isinstance(raw, Mapping):
        raise StrategyLabError("rule must be an object")
    conditions_raw = raw.get("conditions")
    if not isinstance(conditions_raw, list):
        raise StrategyLabError("conditions must be a list")
    return Rule(
        rule_id=str(raw.get("rule_id") or ""),
        combine=str(raw.get("combine") or "all"),
        conditions=tuple(_condition_from_mapping(item, exit_rule=exit_rule) for item in conditions_raw),
        max_notional_fraction=raw.get("max_notional_fraction", "0.10"),
    )


def experiment_from_mapping(data: Mapping[str, object], *, activated_at: float | None = None) -> StrategyExperiment:
    if not isinstance(data, Mapping):
        raise StrategyLabError("strategy_experiment must be an object")
    entries = data.get("entry_rules")
    exits = data.get("exit_rules")
    if not isinstance(entries, list) or not isinstance(exits, list):
        raise StrategyLabError("entry_rules and exit_rules must be lists")
    stamp = data.get("activated_at", 0.0) if activated_at is None else activated_at
    return StrategyExperiment(
        experiment_id=str(data.get("experiment_id") or ""),
        name=str(data.get("name") or ""),
        thesis=str(data.get("thesis") or ""),
        entry_rules=tuple(_rule_from_mapping(item, exit_rule=False) for item in entries),
        exit_rules=tuple(_rule_from_mapping(item, exit_rule=True) for item in exits),
        max_pair_correlation=data.get("max_pair_correlation", "0.85"),
        activated_at=float(stamp or 0.0),
    )


def experiment_to_payload(spec: StrategyExperiment) -> dict[str, object]:
    def rule_payload(rule: Rule) -> dict[str, object]:
        return {
            "rule_id": rule.rule_id,
            "combine": rule.combine,
            "max_notional_fraction": str(rule.max_notional_fraction),
            "conditions": [
                {
                    "feature": cond.feature,
                    "op": cond.op,
                    "threshold": str(cond.threshold),
                    **({"lookback": cond.lookback} if cond.lookback is not None else {}),
                }
                for cond in rule.conditions
            ],
        }
    return {
        "schema_version": 1,
        "experiment_id": spec.experiment_id,
        "name": spec.name,
        "thesis": spec.thesis,
        "entry_rules": [rule_payload(rule) for rule in spec.entry_rules],
        "exit_rules": [rule_payload(rule) for rule in spec.exit_rules],
        "max_pair_correlation": str(spec.max_pair_correlation),
        "activated_at": float(spec.activated_at),
    }


def load_strategy_experiment(trading_dir) -> StrategyExperiment | None:
    path = Path(trading_dir) / EXPERIMENT_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, Mapping) or data.get("schema_version") != 1:
            return None
        return experiment_from_mapping(data)
    except (OSError, ValueError, StrategyLabError, TradingValidationError):
        return None


def save_strategy_experiment(trading_dir, spec: StrategyExperiment, *, activated_at: float | None = None) -> Path:
    stamp = time.time() if activated_at is None else float(activated_at)
    active = StrategyExperiment(
        experiment_id=spec.experiment_id,
        name=spec.name,
        thesis=spec.thesis,
        entry_rules=spec.entry_rules,
        exit_rules=spec.exit_rules,
        max_pair_correlation=spec.max_pair_correlation,
        activated_at=stamp,
    )
    target = Path(trading_dir) / EXPERIMENT_FILENAME
    _atomic_json(target, experiment_to_payload(active))
    return target


def load_pending_experiment(trading_dir) -> StrategyExperiment | None:
    """Load the queued next candidate, or None when absent/invalid.

    Pending candidates are validated on save, so a malformed file is treated as
    unusable evidence (never activated) instead of raising into the improvement
    job.
    """
    path = Path(trading_dir) / PENDING_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, Mapping) or data.get("schema_version") != 1:
            return None
        return experiment_from_mapping(data)
    except (OSError, ValueError, StrategyLabError, TradingValidationError):
        return None


def save_pending_experiment(trading_dir, spec: StrategyExperiment, *, proposed_at: float) -> Path:
    payload = experiment_to_payload(spec)
    payload["proposed_at"] = float(proposed_at)
    target = Path(trading_dir) / PENDING_FILENAME
    _atomic_json(target, payload)
    return target


def _returns(frame: MarketFrame, lookback: int) -> list[float]:
    closes = frame.closes[-(lookback + 1):]
    return [
        float(cur / prev - D("1"))
        for prev, cur in zip(closes, closes[1:])
        if prev > 0
    ]


def _feature(frame: MarketFrame, feature: str, lookback: int | None, *, pnl_bps=None, hold_minutes=None) -> Decimal | None:
    if feature == "pnl_bps":
        return None if pnl_bps is None else D(str(pnl_bps))
    if feature == "hold_minutes":
        return None if hold_minutes is None else D(str(hold_minutes))
    if lookback is None or len(frame.closes) < lookback + (1 if feature in {"return_bps", "volatility_bps"} else 0):
        return None
    closes = frame.closes
    last = closes[-1]
    window = closes[-lookback:]
    if feature == "return_bps":
        start = closes[-(lookback + 1)]
        return (last / start - D("1")) * D("10000") if start > 0 else None
    if feature == "zscore":
        vals = [float(x) for x in window]
        std = pstdev(vals)
        return None if std <= 0 else D(str((vals[-1] - fmean(vals)) / std))
    if feature == "rsi":
        if len(closes) < lookback + 1:
            return None
        changes = [float(b - a) for a, b in zip(closes[-(lookback + 1):], closes[-lookback:])]
        gains = sum(max(0.0, x) for x in changes) / lookback
        losses = sum(max(0.0, -x) for x in changes) / lookback
        if losses == 0:
            return D("100")
        rs = gains / losses
        return D(str(100.0 - 100.0 / (1.0 + rs)))
    if feature == "sma_gap_bps":
        mean = D(str(fmean(float(x) for x in window)))
        return None if mean <= 0 else (last / mean - D("1")) * D("10000")
    if feature == "volatility_bps":
        values = _returns(frame, lookback)
        return None if len(values) < 2 else D(str(pstdev(values) * 10000.0))
    if feature == "breakout_bps":
        previous = window[:-1]
        if not previous:
            return None
        ceiling = max(previous)
        return None if ceiling <= 0 else (last / ceiling - D("1")) * D("10000")
    if feature == "drawdown_bps":
        peak = max(window)
        return None if peak <= 0 else (last / peak - D("1")) * D("10000")
    return None


def _condition_met(value: Decimal, cond: Condition) -> bool:
    if cond.op == ">=":
        return value >= cond.threshold
    if cond.op == "<=":
        return value <= cond.threshold
    if cond.op == ">":
        return value > cond.threshold
    return value < cond.threshold


def _condition_context(cond: Condition, observed: Decimal) -> dict[str, object]:
    unit = "value"
    if cond.feature == "pnl_bps":
        # Distinguish new fee-inclusive exit estimates from persisted historical
        # signals, whose pnl_bps used the gross market price.
        unit = "net_pnl_bps"
    elif cond.feature.endswith("_bps"):
        unit = "bps"
    elif cond.feature == "rsi":
        unit = "rsi"
    elif cond.feature == "zscore":
        unit = "zscore"
    elif cond.feature == "hold_minutes":
        unit = "minutes"
    return {
        "feature": cond.feature,
        "observed": str(observed),
        "threshold": str(cond.threshold),
        "op": cond.op,
        "unit": unit,
        **({"lookback": cond.lookback} if cond.lookback is not None else {}),
    }


def _evaluate_rule(
    frame: MarketFrame,
    rule: Rule,
    *,
    pnl_bps: Decimal | None = None,
    hold_minutes: Decimal | None = None,
) -> tuple[bool, list[dict[str, object]]]:
    results: list[bool] = []
    contexts: list[dict[str, object]] = []
    for cond in rule.conditions:
        value = _feature(
            frame, cond.feature, cond.lookback, pnl_bps=pnl_bps, hold_minutes=hold_minutes
        )
        if value is None:
            results.append(False)
            continue
        hit = _condition_met(value, cond)
        results.append(hit)
        contexts.append(_condition_context(cond, value))
    matched = all(results) if rule.combine == "all" else any(results)
    return matched, contexts


def _score_context(contexts: Sequence[Mapping[str, object]]) -> Decimal:
    if not contexts:
        return D("0.5")
    margins: list[Decimal] = []
    for item in contexts:
        try:
            obs = as_decimal(item["observed"], "observed")
            threshold = as_decimal(item["threshold"], "threshold")
            op = item["op"]
            if op in {">=", ">"}:
                margin = obs - threshold
            elif op in {"<=", "<"}:
                margin = threshold - obs
            else:
                continue
            # An OR rule can retain unmatched conditions in its evidence.
            # Their distance from a threshold must never increase its score.
            if margin < 0 or (margin == 0 and op in {">", "<"}):
                continue
            denom = max(abs(threshold), D("1"))
            margins.append(margin / denom)
        except (KeyError, TypeError, ValueError):
            pass
    return D("0.5") if not margins else min(D("1"), D("0.5") + max(margins) / D("2"))


def scan_experiment_entries(frames: Mapping[str, MarketFrame], spec: StrategyExperiment, *, now: float) -> ScanResult:
    opportunities: list[Opportunity] = []
    contexts: dict[str, dict[str, object]] = {}
    for symbol in sorted(frames):
        frame = frames[symbol]
        for rule in spec.entry_rules:
            matched, conditions = _evaluate_rule(frame, rule)
            if not matched:
                continue
            oid = f"lab:{spec.experiment_id}:{rule.rule_id}:{symbol}:{int(frame.as_of)}"
            opp = Opportunity(
                opportunity_id=oid,
                strategy_id=f"lab:{spec.experiment_id}:{rule.rule_id}",
                symbol=symbol,
                side="buy",
                score=_score_context(conditions),
                expected_edge_bps=D("1"),
                max_notional_fraction=rule.max_notional_fraction,
                expires_at=float(now) + frame.timeframe_seconds * 2,
                reason_code="paper_lab_entry",
            )
            opportunities.append(opp)
            contexts[oid] = {
                "kind": "lab_entry",
                "experiment_id": spec.experiment_id,
                "rule_id": rule.rule_id,
                "combine": rule.combine,
                "conditions": conditions[:MAX_CONDITIONS],
            }
    return ScanResult(tuple(opportunities), contexts)


def scan_experiment_exits(
    frames: Mapping[str, MarketFrame],
    cost_basis: Mapping[str, object],
    spec: StrategyExperiment,
    *,
    now: float,
) -> ScanResult:
    opportunities: list[Opportunity] = []
    contexts: dict[str, dict[str, object]] = {}
    for symbol in sorted(cost_basis):
        frame = frames.get(str(symbol))
        entry = cost_basis[symbol]
        if frame is None or not isinstance(entry, (tuple, list)) or len(entry) < 3:
            continue
        average = as_decimal(entry[1], "average_price")
        if average <= 0:
            continue
        # Cost basis already includes the buy fee/slippage; include the sell
        # leg here so a small apparent gain cannot become a loss on execution.
        exit_price = paper_execution_price(frame.last_price, "sell")
        pnl_bps = (exit_price / average - D("1")) * D("10000")
        hold_minutes = D(str(max(0.0, float(now) - float(entry[2])) / 60.0))
        for rule in spec.exit_rules:
            matched, conditions = _evaluate_rule(
                frame, rule, pnl_bps=pnl_bps, hold_minutes=hold_minutes
            )
            if not matched:
                continue
            oid = f"lab-exit:{spec.experiment_id}:{rule.rule_id}:{symbol}:{int(frame.as_of)}"
            opp = Opportunity(
                opportunity_id=oid,
                strategy_id=f"lab-exit:{spec.experiment_id}:{rule.rule_id}",
                symbol=str(symbol),
                side="sell",
                score=_score_context(conditions),
                expected_edge_bps=abs(pnl_bps),
                max_notional_fraction=D("1"),
                expires_at=float(now) + frame.timeframe_seconds * 2,
                reason_code="paper_lab_exit",
            )
            opportunities.append(opp)
            contexts[oid] = {
                "kind": "lab_exit",
                "experiment_id": spec.experiment_id,
                "rule_id": rule.rule_id,
                "combine": rule.combine,
                "conditions": conditions[:MAX_CONDITIONS],
                "average_price": str(average),
                "last_price": str(frame.last_price),
                "pnl_bps": str(pnl_bps),
                "hold_minutes": str(hold_minutes),
            }
            break
    return ScanResult(tuple(opportunities), contexts)


def built_in_reason_context(
    opportunity: Opportunity,
    frames: Mapping[str, MarketFrame],
    cost_basis: Mapping[str, object],
    *,
    now: float,
    momentum_lookback: int,
    momentum_threshold_bps: Decimal,
    mean_reversion_lookback: int,
    mean_reversion_z: Decimal,
    take_profit: Decimal,
    stop_loss: Decimal,
    max_hold_s: float,
) -> dict[str, object] | None:
    frame = frames.get(opportunity.symbol)
    if frame is None:
        return None
    code = opportunity.reason_code
    if code == "momentum_breakout" and len(frame.closes) >= momentum_lookback + 1:
        observed = (frame.last_price / frame.closes[-(momentum_lookback + 1)] - D("1")) * D("10000")
        return {
            "kind": "builtin_entry",
            "conditions": [_condition_context(
                Condition("return_bps", ">=", momentum_threshold_bps, momentum_lookback), observed
            )],
        }
    if code == "mean_reversion_discount" and len(frame.closes) >= mean_reversion_lookback:
        value = _feature(frame, "zscore", mean_reversion_lookback)
        if value is not None:
            return {
                "kind": "builtin_entry",
                "conditions": [_condition_context(
                    Condition("zscore", "<=", mean_reversion_z, mean_reversion_lookback), value
                )],
            }
    if code in {"take_profit", "stop_loss", "max_hold"}:
        entry = cost_basis.get(opportunity.symbol)
        if not isinstance(entry, (tuple, list)) or len(entry) < 3:
            return None
        average = as_decimal(entry[1], "average_price")
        exit_price = paper_execution_price(frame.last_price, "sell")
        pnl_bps = (exit_price / average - D("1")) * D("10000")
        hold_minutes = D(str(max(0.0, float(now) - float(entry[2])) / 60.0))
        if code == "take_profit":
            cond = Condition("pnl_bps", ">=", take_profit * D("10000"))
            observed = pnl_bps
        elif code == "stop_loss":
            cond = Condition("pnl_bps", "<=", -stop_loss * D("10000"))
            observed = pnl_bps
        else:
            cond = Condition("hold_minutes", ">=", D(str(float(max_hold_s) / 60.0)))
            observed = hold_minutes
        return {
            "kind": "builtin_exit",
            "conditions": [_condition_context(cond, observed)],
            "average_price": str(average),
            "last_price": str(frame.last_price),
            "pnl_bps": str(pnl_bps),
            "hold_minutes": str(hold_minutes),
        }
    return None


def _ledger_rows(db_path: Path, *, since: float) -> list[tuple[str, str, str, Decimal, Decimal, float]]:
    if not db_path.is_file():
        return []
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
        rows = conn.execute(
            """SELECT symbol, side, strategy_id, amount, price, filled_at
                 FROM paper_fills WHERE filled_at >= ? ORDER BY filled_at, rowid""",
            (float(since),),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        if conn is not None:
            conn.close()
    result = []
    for symbol, side, strategy_id, amount, price, filled_at in rows:
        try:
            result.append((
                str(symbol), str(side), str(strategy_id),
                D(str(amount)), D(str(price)), float(filled_at),
            ))
        except (InvalidOperation, ValueError, TypeError):
            continue
    return result


def evaluate_experiment(trading_dir, spec: StrategyExperiment, *, capital_jpy: object, now=None) -> dict[str, object]:
    """Compatibility entrypoint for the canonical, provenance-aware evaluator."""
    from .strategy_metrics import evaluate_strategy_experiment
    return evaluate_strategy_experiment(trading_dir, spec, capital_jpy=capital_jpy, now=now)


def _record_evaluation_history(trading_dir: Path, evaluation: Mapping[str, object], spec: StrategyExperiment) -> None:
    """Upsert one bounded row per experiment so improvement can compare them.

    Best effort: the current-evaluation file is the primary record and a
    history write failure must never fail the improvement job.
    """
    experiment_id = str(evaluation.get("experiment_id") or "")
    if not experiment_id:
        return
    entry = {"evaluated_at": time.time()}
    for key in _EVALUATION_HISTORY_KEYS:
        if key in evaluation:
            entry[key] = evaluation[key]
    # Keep the executable conditions next to their result. A version name and
    # a P/L number alone cannot tell the next researcher which rule failed.
    entry["experiment"] = experiment_to_payload(spec)
    identity = (experiment_id, evaluation.get("activated_at", spec.activated_at))
    entries = [
        item for item in load_evaluation_history(trading_dir, limit=EVALUATION_HISTORY_MAX)
        if (item.get("experiment_id"), item.get("activated_at", 0.0)) != identity
    ]
    entry.setdefault("activated_at", spec.activated_at)
    entries.append(entry)
    try:
        _atomic_json(
            Path(trading_dir) / EVALUATION_HISTORY_FILENAME,
            {"schema_version": 1, "entries": entries[-EVALUATION_HISTORY_MAX:]},
        )
    except OSError:
        pass


def load_evaluation_history(trading_dir, *, limit: int = 6) -> list[dict[str, object]]:
    """Most recent evaluation rows per experiment, oldest first (bounded)."""
    if limit <= 0:
        return []
    path = Path(trading_dir) / EVALUATION_HISTORY_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    raw = data.get("entries") if isinstance(data, Mapping) else None
    if not isinstance(raw, list):
        return []
    entries = [
        dict(item) for item in raw
        if isinstance(item, Mapping) and item.get("experiment_id")
    ]
    return entries[-int(limit):]


def persist_evaluation(trading_dir, evaluation: Mapping[str, object], spec: StrategyExperiment) -> None:
    target = Path(trading_dir)
    _atomic_json(target / EVALUATION_FILENAME, evaluation)
    _record_evaluation_history(target, evaluation, spec)
    if evaluation.get("promotion_ready") is True:
        _atomic_json(
            target / PROMOTION_FILENAME,
            {
                "schema_version": 1,
                "status": "candidate",
                "experiment": experiment_to_payload(spec),
                "evaluation": dict(evaluation),
                "note": "PAPER評価を満たした昇格候補。実運用への自動反映は行わない。",
            },
        )
