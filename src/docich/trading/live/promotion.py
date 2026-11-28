"""Automatic PAPER -> LIVE promotion of per-symbol slot strategies (Issue #3623 §2.6).

Owner decision 2026-11-27: a PAPER slot strategy replaces (or adds) the LIVE policy of
its symbol automatically when ALL of the following hold, judged from recorded facts only:

1. Rule v4 (``promotion_shadow``): the cell (slot experiment x symbol) reached
   ``E >= THRESHOLD`` with at least ``MIN_BLOCKS`` JST day blocks built only from
   ``paper-cost-v3`` fills (book VWAP plus taker fee).  No promotion can happen
   until five days of v3 evidence exist; older close-priced v2 blocks never count.
2. It beats LIVE on the same days: over the cell's own day blocks, the mean of
   ``candidate block - LIVE block`` is positive, where the LIVE block is the mean net
   return per realized LIVE sell of that symbol on that day (0 on a day LIVE sold
   nothing).  A symbol LIVE does not trade yet is judged on rule v4 alone and added.
3. Not too often: a symbol whose LIVE policy changed less than ``MIN_HOLD_S`` (7 days)
   ago is not touched (this also limits each symbol to one promotion per 7 days).
4. The owner's stop switch ``<live root>/owner/promotion.disabled`` is absent, and no
   safety trip that blocks new buys is active.
5. The executable rules are the cell's own experiment, read from the slot's active
   experiment file or its evaluation history, and they must round-trip exactly and fit
   the LIVE policy bounds (JPY pair, ``0 < max_notional_fraction <= 0.30``).

``judge`` only reads.  ``apply`` is the one write: it records a ``promotion`` activation
in the LIVE ledger (the worker holds the writer lease) and returns the new policy set.
An open position keeps the exit rules of the policy it was entered under and new buys
of that symbol wait until it is flat (``decide.entry_exit_policies`` and the existing
``legacy_position_open`` skip), so a promotion never mixes two strategies in one position.
A symbol is never removed by a promotion.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import stat
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from .. import promotion_shadow as shadow
from ..cell_metrics import aggregate_cell_metrics
from ..models import TradingValidationError
from ..strategy_lab import (EVALUATION_HISTORY_FILENAME, EXPERIMENT_FILENAME,
                            StrategyExperiment, StrategyLabError)
from ..symbol_slots import slot_dir
from . import views
from .canonical import canonical_json, sha256_hex
from .paths import live_layout
from .policy import (FreePolicy, LivePolicy, LivePolicyError, LivePolicySet, build_free_policy,
                     build_policy, policy_set_from_policies, strict_experiment)

RULE_ID = "promotion-v1"
STOP_FILENAME = "promotion.disabled"
STATUS_FILENAME = "promotion_status.json"
MIN_HOLD_S = 7 * 86400
CHECK_INTERVAL_S = 3600.0
MAX_FILE_BYTES = 2 * 1024 * 1024
JST = dt.timezone(dt.timedelta(hours=9), "JST")
_ZERO = Decimal(0)


def _num(value: float) -> str:
    """Numbers in the evidence are fixed-point strings (canonical JSON has no floats)."""
    return format(Decimal(repr(float(value))).quantize(Decimal("1e-8")), "f")


@dataclass(frozen=True)
class PromotionChange:
    symbol: str
    experiment_id: str
    policy: LivePolicy
    previous_digest: str | None
    e_value: float
    blocks: int
    candidate_mean: float
    live_mean: float | None     # None: LIVE does not trade this symbol yet

    def evidence(self) -> dict:
        return {"symbol": self.symbol, "experiment_id": self.experiment_id,
                "policy_digest": self.policy.digest, "previous_digest": self.previous_digest,
                "e_value": _num(self.e_value), "blocks": self.blocks,
                "candidate_mean": _num(self.candidate_mean),
                "live_mean": None if self.live_mean is None else _num(self.live_mean)}


@dataclass(frozen=True)
class PromotionDecision:
    status: str                              # "ok" | "stopped" | "safety" | "unavailable"
    changes: tuple[PromotionChange, ...] = ()
    rejected: tuple[dict, ...] = ()
    base_digest: str | None = None
    judged_at: float = 0.0
    reason: str | None = None
    extra: dict = field(default_factory=dict)
    free: FreePolicy | None = None           # a free strategy to adopt (its PAPER mirror)

    def evidence(self) -> dict:
        return {"rule": RULE_ID, "shadow_rule": shadow.RULE_VERSION,
                "threshold": _num(shadow.THRESHOLD), "min_blocks": shadow.MIN_BLOCKS,
                "min_hold_s": MIN_HOLD_S, "base_digest": self.base_digest,
                "judged_at": _num(self.judged_at),
                "changes": [change.evidence() for change in self.changes],
                "free": None if self.free is None else self.free.payload()}

    def evidence_digest(self) -> str:
        return sha256_hex(canonical_json(self.evidence()))

    def status_payload(self) -> dict:
        return {"schema_version": 1, "rule": RULE_ID, "status": self.status,
                "reason": self.reason, "judged_at": self.judged_at,
                "base_digest": None if self.base_digest is None else self.base_digest[:12],
                "changes": [change.evidence() for change in self.changes],
                "free": None if self.free is None else {"artifact": self.free.artifact[:12],
                                                        "symbols": list(self.free.symbols)},
                "rejected": list(self.rejected)[:20], **self.extra}


# ---- inputs ---------------------------------------------------------------------------
def promotion_stopped(state_dir) -> bool:
    """The owner's stop switch: any entry at the path (even a broken one) stops promotion."""
    path = live_layout(state_dir).owner_dir / STOP_FILENAME
    return os.path.lexists(path)


def _jst_day(epoch: float) -> str:
    return dt.datetime.fromtimestamp(float(epoch), tz=dt.timezone.utc).astimezone(JST).date().isoformat()


def live_return_blocks(ledger) -> dict[str, dict[str, float]]:
    """``{symbol: {jst_day: mean net return per realized sell}}`` from LIVE fills.

    The same average-cost replay as the WebUI's realized P&L (``views.cost_basis_steps``):
    one return per sell fill, ``(proceeds - known quote fee - consumed cost) / consumed
    cost``.  A sell without a provable basis is left out (never a zero return).
    """
    by_position: dict[str, list[views.FillRow]] = {}
    for fill in views.all_fills(ledger):
        if fill.position_id is None or not str(fill.symbol).endswith("/JPY"):
            continue
        by_position.setdefault(fill.position_id, []).append(fill)
    sums: dict[tuple[str, str], list] = {}
    for fills in by_position.values():
        before = views.CostBasis(_ZERO, _ZERO, _ZERO, None)
        for fill, after in views.cost_basis_steps(fills, _ZERO):
            if fill.side == "sell":
                consumed = before.cost - after.cost
                if before.net_base > 0 and consumed > 0:
                    fee = _ZERO
                    if (fill.fee_status == "known" and fill.fee_amount is not None
                            and fill.fee_currency == "JPY"):
                        fee = Decimal(fill.fee_amount)
                    ret = (Decimal(fill.quote_notional) - fee - consumed) / consumed
                    slot = sums.setdefault((fill.symbol, _jst_day(fill.executed_at)), [0, _ZERO])
                    slot[0] += 1
                    slot[1] += ret
            before = after
    blocks: dict[str, dict[str, float]] = {}
    for (symbol, day), (count, total) in sums.items():
        blocks.setdefault(symbol, {})[day] = float(total / count)
    return blocks


def last_change_times(ledger) -> dict[str, float]:
    """When each symbol's LIVE policy last changed (any activation reason)."""
    with ledger.read_cursor() as cur:
        rows = cur.execute("SELECT changed_symbols, activated_at FROM policy_activations "
                           "ORDER BY activation_id").fetchall()
    times: dict[str, float] = {}
    for changed, at in rows:
        for symbol in str(changed or "").split(","):
            if symbol:
                times[symbol] = float(at)
    return times


def _read_json(path: Path) -> object:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            return None
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            return json.loads(handle.read(MAX_FILE_BYTES + 1).decode("utf-8"))
    except (OSError, ValueError):
        return None
    finally:
        if fd >= 0:
            os.close(fd)


def _strict(payload: object) -> StrategyExperiment | None:
    if not isinstance(payload, Mapping):
        return None
    try:
        return strict_experiment(payload)
    except (LivePolicyError, StrategyLabError, TradingValidationError, TypeError, ValueError):
        return None


def cell_experiment(trading_dir, symbol: str, experiment_id: str) -> StrategyExperiment | None:
    """The executable rules of a slot cell: the slot's active experiment, else its history."""
    directory = slot_dir(trading_dir, symbol)
    active = _read_json(directory / EXPERIMENT_FILENAME)
    if isinstance(active, Mapping) and active.get("experiment_id") == experiment_id:
        spec = _strict(active)
        if spec is not None:
            return spec
    history = _read_json(directory / EVALUATION_HISTORY_FILENAME)
    entries = history.get("entries") if isinstance(history, Mapping) else None
    for entry in reversed(entries if isinstance(entries, list) else []):
        if isinstance(entry, Mapping) and entry.get("experiment_id") == experiment_id:
            spec = _strict(entry.get("experiment"))
            if spec is not None and spec.experiment_id == experiment_id:
                return spec
    return None


# ---- judge ------------------------------------------------------------------------------
def _reject(rejected: list, symbol: str, experiment_id: str, reason: str) -> None:
    rejected.append({"symbol": symbol, "experiment_id": experiment_id, "reason": reason})


def judge(state_dir, ledger, active: LivePolicySet, *, now: float, safety_blocked: bool,
          cells: list | None = None) -> PromotionDecision:
    """Decide which symbols to promote now.  Reads only; never raises.

    ``cells`` (tests) replaces the PAPER cell aggregation of ``<state_dir>/trading``.
    """
    base = active.digest
    if promotion_stopped(state_dir):
        return PromotionDecision("stopped", base_digest=base, judged_at=now, reason="owner_stop")
    if safety_blocked:
        return PromotionDecision("safety", base_digest=base, judged_at=now, reason="safety_trip")
    trading_dir = Path(state_dir) / "trading"
    try:
        if cells is None:
            result = aggregate_cell_metrics(trading_dir / "paper.sqlite3")
            cells = result.get("cells")
            if cells is None:
                return PromotionDecision("unavailable", base_digest=base, judged_at=now,
                                         reason=str(result.get("reason_code", "ledger_unavailable"))[:60])
        live_blocks = live_return_blocks(ledger)
        changed_at = last_change_times(ledger)
    except Exception as exc:
        return PromotionDecision("unavailable", base_digest=base, judged_at=now,
                                 reason=f"inputs:{type(exc).__name__}")

    rejected: list[dict] = []
    best: dict[str, PromotionChange] = {}
    for cell in shadow._slot_cells(cells):
        symbol = str(cell["symbol"])
        experiment_id = str(cell["experiment_id"])
        blocks = shadow._blocks(cell)
        if not blocks:
            continue
        outcome = shadow.e_process(blocks)
        if outcome["promoted_day"] is None:
            continue
        if not symbol.endswith("/JPY"):
            _reject(rejected, symbol, experiment_id, "not_jpy")
            continue
        current = active.policies.get(symbol)
        live_mean = None
        if current is not None:
            days = live_blocks.get(symbol, {})
            diffs = [value - days.get(day, 0.0) for day, value in blocks]
            live_mean = sum(days.get(day, 0.0) for day, _ in blocks) / len(blocks)
            if not sum(diffs) / len(diffs) > 0:
                _reject(rejected, symbol, experiment_id, "not_better_than_live")
                continue
        last = changed_at.get(symbol)
        if current is not None and last is not None and now - last < MIN_HOLD_S:
            _reject(rejected, symbol, experiment_id, "min_hold")
            continue
        spec = cell_experiment(trading_dir, symbol, experiment_id)
        if spec is None:
            _reject(rejected, symbol, experiment_id, "rules_unavailable")
            continue
        try:
            policy = build_policy(spec, symbol)
        except (LivePolicyError, TradingValidationError, ValueError, TypeError):
            _reject(rejected, symbol, experiment_id, "policy_out_of_bounds")
            continue
        if current is not None and current.digest == policy.digest:
            continue  # already LIVE
        change = PromotionChange(
            symbol=symbol, experiment_id=experiment_id, policy=policy,
            previous_digest=None if current is None else current.digest,
            e_value=float(outcome["e_value"]), blocks=int(outcome["blocks"]),
            candidate_mean=sum(value for _, value in blocks) / len(blocks),
            live_mean=live_mean)
        held = best.get(symbol)
        if held is None or change.e_value > held.e_value:
            best[symbol] = change
    changes = tuple(best[symbol] for symbol in sorted(best))
    return PromotionDecision("ok", changes=changes, rejected=tuple(rejected),
                             base_digest=base, judged_at=now,
                             free=free_adoption(state_dir, active, now=now, rejected=rejected))


def free_adoption(state_dir, active: LivePolicySet, *, now: float, rejected: list) -> FreePolicy | None:
    """The free strategy to adopt: the PAPER mirror's artifact, when it is new to LIVE.

    The PAPER side promotes (rule ``free-v1``, beat the current mirror, 7-day hold) and
    publishes the mirror's weights; LIVE follows the artifact in the fresh weights file.
    """
    from .free_mirror import read_weights

    weights = read_weights(state_dir, now=now)
    if weights is None:
        return None
    current = active.free
    if current is not None and (current.artifact, current.experiment) == (weights.artifact, weights.experiment):
        return None
    try:
        return build_free_policy(artifact=weights.artifact, experiment=weights.experiment,
                                 symbols=weights.symbols, promoted_at=str(int(now)))
    except LivePolicyError:
        _reject(rejected, "*free*", weights.experiment, "free_policy_invalid")
        return None


# ---- apply ------------------------------------------------------------------------------
def promoted_set(active: LivePolicySet, decision: PromotionDecision) -> LivePolicySet:
    policies = dict(active.policies)
    for change in decision.changes:
        policies[change.symbol] = change.policy
    free = decision.free if decision.free is not None else active.free
    return policy_set_from_policies(policies, free)


def apply(ledger, lease, active: LivePolicySet, decision: PromotionDecision, *,
          now: float) -> LivePolicySet | None:
    """Record the promotion; returns the new active set, or ``None`` when nothing changed.

    Refuses (returns ``None``) when the decision was judged against another active set or
    the ledger's active set moved meanwhile.  The ledger's own ``policy_activated`` event
    (reason ``promotion``, actor ``promotion_judge``, the changed symbols and the evidence
    digest) is the record; the evidence itself goes to the status file.
    """
    if decision.status != "ok" or not (decision.changes or decision.free is not None):
        return None
    if decision.base_digest != active.digest or ledger.active_policy_set_digest() != active.digest:
        return None
    new_set = promoted_set(active, decision)
    if new_set.digest == active.digest:
        return None
    ledger.record_activation(lease, new_set, reason="promotion",
                             evidence_digest=decision.evidence_digest(), now=now)
    return new_set


def write_status(state_dir, payload: Mapping) -> None:
    """Best-effort status for the WebUI/diagnostics (0600, atomic); never raises."""
    try:
        root = live_layout(state_dir).root
        if not root.is_dir() or root.is_symlink():
            return
        fd, tmp = tempfile.mkstemp(dir=root, prefix=".promotion-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(dict(payload), handle, ensure_ascii=False, sort_keys=True)
            os.chmod(tmp, 0o600)
            os.replace(tmp, root / STATUS_FILENAME)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except Exception:
        pass


def read_status(state_dir) -> dict | None:
    data = _read_json(live_layout(state_dir).root / STATUS_FILENAME)
    return data if isinstance(data, dict) else None


__all__ = ["PromotionChange", "PromotionDecision", "judge", "apply", "promoted_set",
           "promotion_stopped", "live_return_blocks", "last_change_times", "cell_experiment",
           "write_status", "read_status", "STOP_FILENAME", "MIN_HOLD_S", "CHECK_INTERVAL_S"]
