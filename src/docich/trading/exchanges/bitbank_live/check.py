"""The owner's read-only check against a real account (phase B).  Run it yourself; it sends no order.

    python -m docich.trading.exchanges.bitbank_live.check --state-dir <dir> [--from-env] [--symbols OAS/JPY ...]

It prints counts, shapes and units only: no key, no secret, no balance, no price, no order or trade
detail.  Before anything is printed, every line is compared with the key and secret; if one appears,
nothing is printed and the exit code is 2.  Only GET endpoints are used (the transport refuses the rest).
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from typing import Callable, Sequence

from ...live.exchange import ExchangeDataError, ExchangeError, OrderNotFound
from . import parse
from .credentials import CredentialsUnavailable, load_credentials
from .reader import BitbankReader, ReaderUnavailable
from .transport import CHECK_ONLY_ENDPOINTS, ENDPOINTS, CcxtReadTransport

DEFAULT_SYMBOLS = ("OAS/JPY", "OMG/JPY", "XYM/JPY", "CYBER/JPY", "MONA/JPY", "ATOM/JPY", "KLAY/JPY", "LPT/JPY",
                   "AXS/JPY", "IMX/JPY", "BOBA/JPY")


def _name(exc: BaseException) -> str:
    return f"{type(exc).__name__}({getattr(exc, 'health_kind', '-')})"


def run_check(transport, symbols: Sequence[str], *, wall_clock: Callable[[], float] = time.time,
              secrets: Sequence[str] = ()) -> tuple[list[str], int]:
    """The report lines and an exit code (0 ok, 1 something failed, 2 a secret would have been printed)."""
    lines: list[str] = []
    failed = False

    def say(text: str) -> None:
        lines.append(text)

    def attempt(label: str, fn):
        nonlocal failed
        try:
            return fn()
        except ExchangeError as exc:
            failed = True
            say(f"{label}: FAILED {_name(exc)}")
        return None

    # --- clock
    from .factory import measure_skew_ms
    skew = attempt("clock", lambda: measure_skew_ms(transport, symbols[0], wall_clock=wall_clock))
    if skew is not None:
        say(f"clock: skew_ms={skew} (limit 800; local minus bitbank, from the public depth timestamp)")

    # --- reader (loads pairs and assets)
    reader = None
    try:
        reader = BitbankReader(transport, symbols=symbols, clock=wall_clock)
        say(f"reader: built for {len(symbols)} symbols")
    except ReaderUnavailable as exc:
        failed = True
        say(f"reader: NOT BUILT ({exc})")
    if reader is not None:
        for symbol in symbols:
            rules = reader.market_rules(symbol)
            say(f"market {symbol}: amount_step={rules.amount_step} min_amount={rules.min_amount} "
                f"price_tick={rules.price_tick} max_fee_rate={rules.max_fee_rate} base_unit={rules.base_unit} "
                f"quote_unit={rules.quote_unit}")

    # --- every GET endpoint is reachable with this key (counts only)
    probes = {
        "assets": ("assets", {}), "active_orders": ("active_orders", {"pair": parse.pair_id(symbols[0]), "count": 5}),
        "trade_history": ("trade_history", {"pair": parse.pair_id(symbols[0]), "count": 5}),
        "deposit_history": ("deposit_history", {}), "spot_status": ("spot_status", {})}
    for label, (endpoint, params) in probes.items():
        if attempt(f"endpoint {label}", lambda e=endpoint, p=params: transport.get(e, p)) is not None:
            say(f"endpoint {label}: ok")
    try:   # the answer to a non-existent order is information, not a failure
        transport.get("order", {"pair": parse.pair_id(symbols[0]), "order_id": "1"})
        say("order lookup of a non-existent id: UNEXPECTEDLY SUCCEEDED")
    except OrderNotFound:
        say("order lookup of a non-existent id: OrderNotFound (code 50009, as the reader expects)")
    except ExchangeError as exc:
        say(f"order lookup of a non-existent id: {_name(exc)} (not 50009: the 'order is gone' rule may need review)")

    if reader is None:
        return _finish(lines, secrets, 1)

    # --- balances: shape only
    snapshot = attempt("balances", reader.fetch_balance)
    if snapshot is not None:
        nonzero = [c for c, a in snapshot.assets.items() if a.total != 0]
        consistent = sum(1 for a in snapshot.assets.values() if a.total == a.free + a.locked)
        say(f"balances: currencies={len(snapshot.assets)} nonzero={len(nonzero)} "
            f"onhand==free+locked in {consistent}/{len(snapshot.assets)}")

    # --- open orders and fills per symbol (counts and shapes)
    sample_orders: list[tuple[str, str]] = []
    fee_shapes: Counter = Counter()
    roles: Counter = Counter()
    digit_lengths: Counter = Counter()
    negative_fees = 0
    for symbol in symbols:
        page = attempt(f"open orders {symbol}", lambda s=symbol: reader.fetch_open_orders(s))
        if page is not None:
            say(f"open orders {symbol}: count={len(page.orders)} complete={page.complete} "
                f"statuses={dict(Counter(o.status.value for o in page.orders))}")
        fills = attempt(f"fills {symbol}", lambda s=symbol: reader.fetch_fills(s, since_ms=0))
        if fills is not None:
            say(f"fills {symbol}: count={len(fills.fills)} complete={fills.complete}")
            for fill in fills.fills:
                fee_shapes[(fill.fee.status.value, fill.fee.currency == symbol.split('/')[0] if fill.fee.currency
                            else None)] += 1
                roles[fill.maker_taker] += 1
                digit_lengths[len(str(fill.executed_at_ms))] += 1
                negative_fees += bool(fill.fee.amount is not None and fill.fee.amount < 0)
                if len(sample_orders) < 20:
                    sample_orders.append((symbol, fill.exchange_order_id))
    say(f"fills overall: fee_shapes(status,in_base)={dict(fee_shapes)} maker_taker={dict(roles)} "
        f"executed_at_digits={dict(digit_lengths)} negative_fees={negative_fees}")

    # --- the meaning of remaining_amount (counts only)
    tally: Counter = Counter()
    for symbol, order_id in dict.fromkeys(sample_orders):
        try:
            snap = reader.fetch_order(symbol, order_id)
        except OrderNotFound:
            tally["not_found"] += 1
            continue
        except ExchangeError as exc:
            tally[f"error:{_name(exc)}"] += 1
            continue
        relation = "start==executed+remaining" if snap.amount == snap.executed_amount + snap.remaining_amount else (
            "remaining==0" if snap.remaining_amount == 0 else "other")
        tally[(snap.status.value, relation)] += 1
    say(f"orders from history ({len(dict.fromkeys(sample_orders))} looked up): {dict(tally)}")

    # --- since unit experiment on one symbol (counts only)
    base_ms = int(wall_clock() * 1000) - 24 * 3600 * 1000
    counts = {}
    for label, since in (("none", None), ("ms_24h_ago", base_ms), ("sec_24h_ago", base_ms // 1000)):
        params = {"pair": parse.pair_id(symbols[0]), "count": 1000, "order": "desc"}
        if since is not None:
            params["since"] = since
        data = attempt(f"since experiment {label}", lambda p=params: transport.get("trade_history", p))
        if isinstance(data, dict) and isinstance(data.get("trades"), list):
            counts[label] = len(data["trades"])
    say(f"since unit experiment ({symbols[0]}): trade counts {counts}  "
        f"(the unit is the one whose count is not the same as 'none' and not an empty/complete mismatch)")

    # --- reader smoke: every read method on every symbol, class names only
    smoke: Counter = Counter()
    for symbol in symbols:
        for label, fn in (("book", lambda s=symbol: reader.fetch_order_book(s)),
                          ("open_orders", lambda s=symbol: reader.fetch_open_orders(s)),
                          ("fills", lambda s=symbol: reader.fetch_fills(s, since_ms=0))):
            try:
                fn()
                smoke[(label, "ok")] += 1
            except ExchangeDataError as exc:
                smoke[(label, "DataError")] += 1
            except ExchangeError as exc:
                smoke[(label, type(exc).__name__)] += 1
    say(f"reader smoke: {dict(smoke)}")
    return _finish(lines, secrets, 1 if failed else 0)


def _finish(lines: list[str], secrets: Sequence[str], code: int) -> tuple[list[str], int]:
    for line in lines:
        for secret in secrets:
            if secret and secret in line:
                return (["refused to print: a line contained the key or secret"], 2)
    return lines, code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="read-only bitbank check (phase B)")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--from-env", action="store_true", help="read the key from the environment, not the file")
    parser.add_argument("--symbols", nargs="*", default=list(DEFAULT_SYMBOLS))
    args = parser.parse_args(argv)
    try:
        credentials = load_credentials(args.state_dir, source="env" if args.from_env else "file")
    except CredentialsUnavailable as exc:
        print(f"credentials: NOT AVAILABLE ({exc.reason.value})")
        return 1
    transport = CcxtReadTransport(credentials, endpoints={**ENDPOINTS, **CHECK_ONLY_ENDPOINTS})
    secrets = credentials.reveal()
    lines, code = run_check(transport, args.symbols, secrets=secrets)
    print("\n".join(lines))
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
