"""LIVE orders for a promoted free strategy, by mirroring its PAPER position weights.

Owner decisions 2026-11-27 (Issue #3623):

* the free strategy trades like the other LIVE strategies, as a percentage of the account's
  capital C (no separate capital pot);
* it may hold a symbol that a per-symbol strategy also holds (separate positions, see
  ``inputs.build_cycle_inputs(free_digests=...)``);
* the AI-written code never runs in the LIVE worker.  The free-strategy worker (which has the
  gVisor sandbox) keeps the promoted artifact running as a PAPER "mirror" experiment and
  writes ``<state_dir>/trading/free-strategies/live_weights.json``: the mirror's position
  weights (position value / equity per symbol).  This module only reads that file;
* a loss stop: when the free strategy's LIVE result since its promotion (cash result plus
  the open positions at the last close) reaches ``-LOSS_STOP_FRACTION x C at promotion``,
  its new buys stop until the next promotion (a new digest).  Its sells keep following the
  mirror.

Per cycle (trade mode, after the per-symbol decisions took their share of the funds):

* a free position entered under an OLDER free policy is sold out;
* a free position whose symbol the mirror no longer holds (weight 0) is sold out;
* for each symbol with a positive weight, when ``weight x C`` exceeds the position's value at
  the last close by at least ``MIN_REBALANCE_FRACTION x C``, one buy of the difference (at
  most 30% of C) is sized by the same ``size_orders`` as every LIVE buy.  A position above
  its target is held, not trimmed (a known simplification).

Weights that are missing, malformed, stale (older than ``MAX_WEIGHTS_AGE_S``) or for another
artifact produce no buys and no mirror exits; positions of an older free policy are still
sold out.
"""
from __future__ import annotations

import dataclasses
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

from ..models import Opportunity, SkipDecision
from . import views
from .decide import DecisionSet, book_age_problem
from .inputs import sell_attempts_for
from .lease import LeaseLost
from .paths import live_layout
from .signals import LiveSignals
from .sizing import Funds, SymbolMarket, size_orders

if TYPE_CHECKING:
    from .cycle import CycleContext, CycleDeps, CycleState

WEIGHTS_RELATIVE = Path("trading") / "free-strategies" / "live_weights.json"
STATE_FILENAME = "free_strategy_state.json"
DECISION_PREFIX = "live1f"
MAX_WEIGHTS_AGE_S = 900.0
MAX_FILE_BYTES = 64 * 1024
LOSS_STOP_FRACTION = Decimal("0.10")
MIN_REBALANCE_FRACTION = Decimal("0.005")
MAX_BUY_FRACTION = Decimal("0.30")
LOSS_STOP_CAUSE = "free_loss_stop"
ZERO = Decimal(0)


@dataclass(frozen=True)
class MirrorWeights:
    artifact: str
    experiment: str
    observed_at: float
    weights: Mapping[str, Decimal]
    symbols: tuple[str, ...] = ()


def _read_json(path: Path) -> object:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            return None
        return json.loads(os.read(fd, MAX_FILE_BYTES + 1).decode("utf-8"))
    except (OSError, ValueError):
        return None
    finally:
        os.close(fd)


def read_weights(state_dir, *, now: float) -> MirrorWeights | None:
    """The mirror's weights, or ``None`` when missing, malformed or stale."""
    data = _read_json(Path(state_dir) / WEIGHTS_RELATIVE)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        return None
    try:
        artifact, experiment = str(data["artifact"]), str(data["experiment"])
        observed_at = float(data["observed_at"])
        raw = data["weights"]
        if not isinstance(raw, dict):
            return None
        weights = {str(symbol): Decimal(str(value)) for symbol, value in raw.items()}
        symbols = tuple(sorted(str(symbol) for symbol in data.get("symbols") or ()))
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None
    if not (0 <= now - observed_at <= MAX_WEIGHTS_AGE_S):
        return None
    if any(not value.is_finite() or value < 0 or value > 1 for value in weights.values()):
        return None
    if sum(weights.values(), ZERO) > 1:
        return None
    if not set(weights) <= set(symbols):
        return None
    return MirrorWeights(artifact, experiment, observed_at, weights, symbols)


# ---- loss stop -------------------------------------------------------------------------
def _state_path(state_dir) -> Path:
    return live_layout(state_dir).root / STATE_FILENAME


def _write_state(state_dir, payload: Mapping) -> None:
    root = live_layout(state_dir).root
    fd, tmp = tempfile.mkstemp(dir=root, prefix=".free-state-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, _state_path(state_dir))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def free_result_jpy(ledger, digest: str, last_closes: Mapping[str, Decimal]) -> Decimal | None:
    """Cash result of every position entered under ``digest`` plus open base at the last close.

    ``None`` when an open position's symbol has no last close (the loss cannot be bounded).
    """
    with ledger.read_cursor() as cur:
        ids = {row[0] for row in cur.execute(
            "SELECT position_id FROM positions WHERE entry_policy_digest=?", (digest,))}
    total = ZERO
    base: dict[str, Decimal] = {}
    for fill in views.all_fills(ledger):
        if fill.position_id not in ids:
            continue
        sign = 1 if fill.side == "sell" else -1
        total += sign * Decimal(fill.quote_notional)
        quote = fill.symbol.split("/")[1]
        held = base.setdefault(fill.symbol, ZERO)
        held += Decimal(fill.amount) if fill.side == "buy" else -Decimal(fill.amount)
        if fill.fee_status == "known" and fill.fee_amount is not None:
            if fill.fee_currency == quote:
                total -= Decimal(fill.fee_amount)
            else:
                held -= Decimal(fill.fee_amount)   # a base fee comes out of the inventory
        base[fill.symbol] = held
    for symbol, amount in base.items():
        if amount > 0:
            price = last_closes.get(symbol)
            if price is None:
                return None
            total += amount * price
    return total


def loss_stop(state_dir, ledger, free, *, capital: Decimal, last_closes, now: float) -> bool:
    """True while the free strategy's buys are stopped.  Resets on a new free digest."""
    data = _read_json(_state_path(state_dir))
    if not isinstance(data, dict) or data.get("digest") != free.digest:
        data = {"digest": free.digest, "baseline_capital": format(capital, "f"),
                "set_at": now, "stopped": False}
        _write_state(state_dir, data)
    if data.get("stopped"):
        return True
    try:
        baseline = Decimal(str(data["baseline_capital"]))
    except (KeyError, InvalidOperation):
        return True
    result = free_result_jpy(ledger, free.digest, last_closes)
    if result is None:
        return False   # not provable this cycle; buys also need every close, so none happen
    if baseline > 0 and result <= -LOSS_STOP_FRACTION * baseline:
        _write_state(state_dir, {**data, "stopped": True, "stopped_at": now,
                                 "result_jpy": format(result, "f")})
        return True
    return False


# ---- decisions ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _SizingPolicy:
    digest: str
    max_opportunity_fraction: Decimal


@dataclass(frozen=True)
class _SizingSet:
    policies: Mapping[str, _SizingPolicy]


def _opportunity(symbol: str, side: str, fraction: Decimal, observed_at: float, now: float,
                 reason: str) -> Opportunity:
    stamp = int(observed_at)
    kind = "free-entry" if side == "buy" else "free-exit"
    return Opportunity(opportunity_id=f"{kind}:{symbol}:{stamp}", strategy_id=f"free:{kind}",
                       symbol=symbol, side=side, score=Decimal("0.5"), expected_edge_bps=ZERO,
                       max_notional_fraction=min(MAX_BUY_FRACTION, max(fraction, Decimal("0.0001"))),
                       expires_at=now + 300.0, reason_code=reason)


def build_free_decisions(deps: "CycleDeps", state: "CycleState", ctx: "CycleContext",
                         base: DecisionSet) -> DecisionSet | None:
    """The free strategy's sized plans for this cycle, after ``base`` (per-symbol) decisions."""
    free = getattr(deps.policy_set, "free", None)
    state_dir = getattr(deps, "state_dir", None)
    if free is None or state_dir is None or ctx.balance is None:
        return None
    inputs = base.inputs
    held = inputs.free_held
    weights = read_weights(state_dir, now=ctx.now)
    if weights is not None and (weights.artifact != free.artifact or weights.experiment != free.experiment):
        weights = None
    observed_at = ctx.now if weights is None else weights.observed_at

    last_closes = {symbol: frame.last_price for symbol, frame in ctx.frames.items()}
    funds = inputs.funds
    capital = Decimal(funds.capital)
    stopped = loss_stop(state_dir, deps.ledger, free, capital=capital, last_closes=last_closes,
                        now=ctx.now)

    exits: list[Opportunity] = []
    entries: list[Opportunity] = []
    skips: list[SkipDecision] = []
    for symbol in sorted(held):
        position = held[symbol]
        if position.entry_policy_digest != free.digest:
            exits.append(_opportunity(symbol, "sell", Decimal(1), observed_at, ctx.now, "free_policy_replaced"))
        elif weights is not None and weights.weights.get(symbol, ZERO) == 0:
            exits.append(_opportunity(symbol, "sell", Decimal(1), observed_at, ctx.now, "free_mirror_exit"))
    if weights is not None and capital > 0:
        exiting = {item.symbol for item in exits}
        for symbol in free.symbols:
            weight = weights.weights.get(symbol, ZERO)
            if weight <= 0 or symbol in exiting or symbol in inputs.free_ambiguous_symbols:
                continue
            price = last_closes.get(symbol)
            if price is None:
                continue
            position = inputs.free_positions.get(symbol)
            current = ZERO if position is None else position.net_base * price
            gap = weight * capital - current
            if gap >= MIN_REBALANCE_FRACTION * capital:
                entries.append(_opportunity(symbol, "buy", gap / capital, observed_at, ctx.now,
                                            "free_mirror_entry"))
    if not entries and not exits:
        return None

    # ---- markets (rules + a fresh frame + a fresh book) ------------------------------------
    rules = deps.executor.rules()
    markets: dict[str, SymbolMarket] = {}
    for symbol in sorted({item.symbol for item in entries + exits}):
        frame, rule = ctx.frames.get(symbol), rules.get(symbol)
        if frame is None or rule is None:
            continue
        existing = base.markets.get(symbol)
        if existing is not None:
            markets[symbol] = existing
            continue
        try:
            book = deps.reader.fetch_order_book(symbol, depth=20)
        except LeaseLost:
            raise
        except Exception:
            continue
        if book.symbol != symbol or book_age_problem(deps.clock(), book.exchange_ts_ms) is not None:
            continue
        markets[symbol] = SymbolMarket(rule, book, frame.last_price)

    # ---- funds left after the per-symbol plans --------------------------------------------
    if base.result is not None:
        funds = Funds(capital=capital, deployed=capital - base.result.headroom_left,
                      free_quote=base.result.free_quote_left)
    planned_buys = set() if base.result is None else {plan.symbol for plan in base.result.buys}
    planned_sells = set() if base.result is None else {plan.symbol for plan in base.result.sells}
    sizing_set = _SizingSet({symbol: _SizingPolicy(free.digest, MAX_BUY_FRACTION)
                             for symbol in set(free.symbols) | set(held)})
    signals = LiveSignals(tuple(entries), tuple(exits), (), {})
    attempts = sell_attempts_for(deps.ledger, signals.exits, held, sizing_set,
                                 decision_prefix=DECISION_PREFIX)
    buys_blocked = base.buys_blocked or stopped
    causes = tuple(sorted(set(base.block_causes) | ({LOSS_STOP_CAUSE} if stopped else set())))
    try:
        result = size_orders(
            signals, sizing_set, markets, held, funds, now=ctx.now,
            decided_keys=frozenset(views.decision_keys(deps.ledger, f"{DECISION_PREFIX}:")),
            open_buy_symbols=inputs.open_buy_symbols | planned_buys,
            open_sell_symbols=inputs.open_sell_symbols | planned_sells,
            legacy_position_symbols=frozenset(),
            ambiguous_position_symbols=frozenset(),
            sell_attempts=attempts, buys_blocked=buys_blocked,
            decision_prefix=DECISION_PREFIX)
    except ValueError:
        return None
    for plan in result.sells + result.buys:
        _record(deps, state, ctx, "plan", plan.decision_key, plan.reason_code, {
            "action": "plan", "side": plan.side, "symbol": plan.symbol,
            "opportunity_id": plan.opportunity_id, "reason": plan.reason_code,
            "decision_key": plan.decision_key, "policy_digest": plan.policy_digest,
            "rule_id": plan.rule_id, "amount": format(plan.amount, "f"),
            "price": format(plan.price, "f"), "mode": ctx.mode or "",
            "cycle": str(state.cycle_number), "blocks": ",".join(causes), "strategy": "free"})
    for skip in list(skips) + list(result.skipped):
        _record(deps, state, ctx, "skip", skip.opportunity_id, str(skip.reason_code), {
            "action": "skip", "side": str(skip.side), "symbol": skip.symbol,
            "opportunity_id": skip.opportunity_id, "reason": str(skip.reason_code),
            "mode": ctx.mode or "", "cycle": str(state.cycle_number),
            "blocks": ",".join(causes), "strategy": "free"})
    return dataclasses.replace(base, signals=signals, result=result, buys_blocked=buys_blocked,
                               block_causes=causes, markets=markets, record_failures=0)


def _record(deps, state, ctx, action: str, identity: str, reason: str, payload: dict) -> None:
    key = (action, identity, reason)
    if key in state.dedupe:
        return
    try:
        deps.ledger.record_live_event(deps.lease, "live_decision", payload, now=ctx.now)
    except LeaseLost:
        raise
    except Exception:
        return
    state.dedupe[key] = state.cycle_number


__all__ = ["read_weights", "build_free_decisions", "loss_stop", "free_result_jpy",
           "MirrorWeights", "LOSS_STOP_FRACTION", "MAX_WEIGHTS_AGE_S", "WEIGHTS_RELATIVE"]
