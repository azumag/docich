"""Host-only PAPER settlement against a later observed order book.

Orders are synthetic IOC targets. Participation and slippage are deliberately
conservative assumptions, recorded in each experiment, not execution promises.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
import math

from ..depth import DepthBook
from ..models import MarketInfo
from .contract import StrategyError, decimal_text, encode
from .store import LabStore

D = Decimal


def _round(amount: Decimal, step: Decimal) -> Decimal:
    return (amount / step).to_integral_value(rounding=ROUND_DOWN) * step


def _quote(amount: Decimal, levels, *, participation: Decimal, price_factor: Decimal) -> tuple[Decimal, Decimal]:
    remaining, quote = amount, D(0)
    for level in levels:
        take = min(remaining, level.amount * participation)
        quote += take * level.price * price_factor
        remaining -= take
        if remaining <= 0:
            break
    return amount - remaining, quote


def validate_market(symbol: str, market: MarketInfo | None, book: DepthBook | None,
                    status, *, now: float) -> None:
    if (market is None or market.symbol != symbol or market.quote != "JPY" or not market.active
            or not market.spot or not market.market_order_enabled):
        raise StrategyError("market_unavailable")
    if any(value is None for value in (market.amount_step, market.min_amount,
                                      market.taker_fee_rate_base, market.taker_fee_rate_quote)):
        raise StrategyError("market_constraints_missing")
    if (book is None or book.symbol != symbol or not 0 <= now - book.as_of <= 2
            or book.bids[0].price >= book.asks[0].price):
        raise StrategyError("depth_unavailable")
    if (status is None or not 0 <= now - status.fetched_at <= 5
            or status.mode != "NONE" or status.fee_type != "NORMAL"):
        raise StrategyError("market_status_unavailable")


def liquidation_value(
    amount: Decimal,
    market: MarketInfo,
    book: DepthBook,
    slippage: Decimal,
    participation: Decimal = D(1),
) -> Decimal:
    order = amount / (1 + market.taker_fee_rate_base)
    filled, gross = _quote(order, book.bids, participation=participation, price_factor=1 - slippage)
    if filled < order:
        raise StrategyError("valuation_depth_insufficient")
    return gross * (1 - market.taker_fee_rate_quote)


def _record_sample(store: LabStore, identity: str, exp: dict, *, now: float, equity: Decimal) -> None:
    policy = exp["policy"]
    store.db.execute("""INSERT INTO samples VALUES (?,?,?,?) ON CONFLICT(experiment,bucket)
        DO UPDATE SET observed_at=excluded.observed_at,equity=excluded.equity
        WHERE excluded.observed_at>samples.observed_at""",
        (identity, int((now - exp["created_at"]) // policy["sample_seconds"]), now, decimal_text(equity)))


def settle_observation(store: LabStore, identity: str, *, markets: dict, books: dict,
                       statuses: dict, now: float) -> dict:
    if not math.isfinite(now):
        raise StrategyError("invalid_time")
    with store.transaction():
        exp = store.experiment(identity)
        if exp["phase"] not in {"research", "paper_validating", "paused"}:
            return exp
        expired = now >= exp["end_at"]
        paused = exp["phase"] == "paused"
        policy, account = exp["policy"], exp["account"]
        cash = D(account["cash_jpy"])
        positions = {symbol: D(amount) for symbol, amount in account["positions"].items()}
        # A paused or expired experiment cannot create new synthetic fills.
        pending = {} if expired or paused else dict(exp["pending"])
        symbols = set(positions) if expired or paused else set(positions) | set(pending)
        slippage = D(policy["slippage_bps"]) / 10000
        participation = D(policy["book_participation"])
        for symbol in symbols:
            validate_market(symbol, markets.get(symbol), books.get(symbol), statuses.get(symbol), now=now)
        value = sum(liquidation_value(
            amount, markets[symbol], books[symbol], slippage, participation
        ) for symbol, amount in positions.items())
        equity_before = cash + value
        peak = max(D(account["peak_equity"]), equity_before)
        drawdown_hit = equity_before < peak * (1 - D(policy["stop_drawdown_fraction"]))

        if expired:
            account = {"cash_jpy": decimal_text(cash),
                       "positions": {s: decimal_text(q) for s, q in positions.items()},
                       "peak_equity": decimal_text(peak)}
            last_error = "drawdown_limit" if drawdown_hit else exp["last_error"]
            store.db.execute("""UPDATE experiments SET phase='review_due',account=?,pending=?,
                last_error=?,revision=revision+1 WHERE id=?""",
                (encode(account), encode({}), last_error, identity))
            _record_sample(store, identity, exp, now=now, equity=equity_before)
            return store.experiment(identity)

        if paused:
            # Manual/risk pauses stop decisions, not observation.  A later
            # drawdown breach upgrades the stop reason and prevents resume.
            account = {"cash_jpy": decimal_text(cash),
                       "positions": {s: decimal_text(q) for s, q in positions.items()},
                       "peak_equity": decimal_text(peak)}
            last_error = "drawdown_limit" if drawdown_hit else exp["last_error"]
            store.db.execute("""UPDATE experiments SET account=?,pending=?,last_error=?,
                revision=revision+1 WHERE id=?""",
                (encode(account), encode({}), last_error, identity))
            _record_sample(store, identity, exp, now=now, equity=equity_before)
            return store.experiment(identity)

        if drawdown_hit:
            store.db.execute("UPDATE experiments SET phase='paused',last_error='drawdown_limit',pending=?,revision=revision+1 WHERE id=?",
                             (encode({}), identity))
            pending.clear()
        # Sells release virtual cash first. The whole observation commits atomically.
        ordered = sorted(pending, key=lambda symbol: (D(pending[symbol]["quantity"]) >= positions.get(symbol, D(0)), symbol))
        for symbol in ordered:
            target, market, book = pending[symbol], markets[symbol], books[symbol]
            # A cached/pre-decision book can never fill the target. It stays pending.
            if book.as_of <= target["accepted_at"]:
                continue
            held = positions.get(symbol, D(0))
            delta = D(target["quantity"]) - held
            side = "buy" if delta > 0 else "sell"
            base_fee, quote_fee = market.taker_fee_rate_base, market.taker_fee_rate_quote
            factor = 1 + slippage if side == "buy" else 1 - slippage
            levels = book.asks if side == "buy" else book.bids
            desired = abs(delta) / (1 - base_fee if side == "buy" else 1 + base_fee)
            # Each target is IOC on its first eligible observation, including rejection.
            del pending[symbol]
            if side == "buy":
                deployed = sum(liquidation_value(
                    q, markets[s], books[s], slippage, participation
                ) for s, q in positions.items())
                cap = min(D(policy["capital_jpy"]), cash + deployed) * D(policy["max_deployed_fraction"])
                budget = max(D(0), min(cash, cap - deployed))
                # Worst visible ask gives an upper bound on quote consumption.
                desired = min(desired, budget / (levels[-1].price * factor * (1 + quote_fee)))
            capacity = sum(level.amount for level in levels) * participation
            amount = _round(min(desired, capacity), market.amount_step)
            if amount <= 0 or amount < market.min_amount:
                continue
            filled, gross = _quote(amount, levels, participation=participation, price_factor=factor)
            if filled != amount or (market.min_cost is not None and gross < market.min_cost):
                continue
            fee_base, fee_quote = amount * base_fee, gross * quote_fee
            if side == "buy":
                if gross + fee_quote > cash:
                    continue
                cash -= gross + fee_quote
                held += amount - fee_base
            else:
                if amount + fee_base > held:
                    continue
                cash += gross - fee_quote
                held -= amount + fee_base
            if held > 0:
                positions[symbol] = held
            else:
                positions.pop(symbol, None)
            fill = {"symbol": symbol, "side": side, "amount": decimal_text(amount),
                    "price": decimal_text(gross / amount), "fee_base": decimal_text(fee_base),
                    "fee_jpy": decimal_text(fee_quote), "observed_at": now,
                    "book_as_of": book.as_of, "target_run_id": target["run_id"],
                    "partial": held != D(target["quantity"])}
            store.db.execute("INSERT INTO fills VALUES (?,?,?,?)",
                             (target["run_id"] + ":" + symbol, identity, now, encode(fill)))
        equity = cash + sum(liquidation_value(
            q, markets[s], books[s], slippage, participation
        ) for s, q in positions.items())
        account = {"cash_jpy": decimal_text(cash), "positions": {s: decimal_text(q) for s, q in positions.items()},
                   "peak_equity": decimal_text(max(peak, equity))}
        store.db.execute("UPDATE experiments SET account=?,pending=?,revision=revision+1 WHERE id=?",
                         (encode(account), encode(pending), identity))
        _record_sample(store, identity, exp, now=now, equity=equity)
        return store.experiment(identity)
