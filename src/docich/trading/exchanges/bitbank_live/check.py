"""The owner's read-only check against bitbank (phase B).  Run it yourself; it sends no order.

    python -m docich.trading.exchanges.bitbank_live.check --state-dir <dir> [--from-env] [--symbols OAS/JPY ...]
    python -m docich.trading.exchanges.bitbank_live.check --public-only      # no key needed

It prints counts, shapes and units only: no key, no secret, no balance, no price, no order or trade detail.
Before anything is printed every line is compared with the key and secret; if one appears, nothing is printed
and the exit code is 2.  Only GET endpoints are used (the transport refuses everything else).

The report works on the *raw* rows, not through the strict reader: its job is to show how bitbank really answers,
including the answers the reader would refuse (a null ``remaining_amount``, an odd fee), so those are counted
instead of being turned into a bare error.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import Counter
from decimal import Decimal
from typing import Callable, Sequence

from ...live.exchange import ExchangeDataError, ExchangeError, OrderNotFound
from ...live.policy import INITIAL_SYMBOLS
from . import parse
from .credentials import CredentialsUnavailable, load_credentials
from .reader import BitbankReader, ReaderUnavailable
from .transport import CHECK_ONLY_ENDPOINTS, ENDPOINTS, CcxtReadTransport

DEFAULT_SYMBOLS = tuple(INITIAL_SYMBOLS)
SKEW_LIMIT_MS = 800


def _name(exc: BaseException) -> str:
    """Class, kind and (for parse errors) the field name: never a value (the messages carry none)."""
    extra = f" {exc}" if isinstance(exc, ExchangeDataError) else ""
    return f"{type(exc).__name__}({getattr(exc, 'health_kind', '-')}){extra}"


def _sign(value: Decimal) -> str:
    return "zero" if value == 0 else ("negative" if value < 0 else "positive")


def fee_shape(row: dict) -> str:
    """Which currency carries the fee, its sign, the role, and whether the 'occurred' field agrees."""
    def num(key):
        try:
            return parse.dec_str(row.get(key), key)
        except ExchangeDataError:
            return None
    base, quote, occurred = num("fee_amount_base"), num("fee_amount_quote"), row.get("fee_occurred_amount_quote")
    if base is None or quote is None:
        return "field-missing-or-unreadable"
    agree = "n/a" if occurred is None else ("agrees" if num("fee_occurred_amount_quote") == quote else "DISAGREES")
    where = ("both-zero" if base == 0 and quote == 0 else "both-nonzero" if base != 0 and quote != 0
             else f"base-{_sign(base)}" if base != 0 else f"quote-{_sign(quote)}")
    return f"{where}/{row.get('maker_taker', '?')}/occurred-{agree}"


def run_check(transport, symbols: Sequence[str], *, wall_clock: Callable[[], float] = time.time,
              secrets: Sequence[str] = (), public_only: bool = False) -> tuple[list[str], int]:
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

    # --- clock, for every symbol (is depth.timestamp "now"?  a quiet pair would show it)
    skews = {}
    for symbol in symbols:
        before = wall_clock()
        data = attempt(f"clock {symbol}", lambda s=symbol: transport.get("depth", {"pair": parse.pair_id(s)}))
        after = wall_clock()
        if data is not None:
            try:
                server = parse.ms_value(parse._field(data, "timestamp", "timestamp"), "timestamp")
                skews[symbol] = int(round((before + after) / 2 * 1000)) - server
            except ExchangeDataError as exc:
                failed = True
                say(f"clock {symbol}: FAILED {_name(exc)}")
    if skews:
        verdict = "within" if all(abs(v) <= SKEW_LIMIT_MS for v in skews.values()) else "OUTSIDE"
        say(f"clock: skew_ms per symbol {skews} ({verdict} +-{SKEW_LIMIT_MS}; local minus bitbank, from depth.timestamp)")

    # --- public market data: each symbol on its own, so one bad pair is named and the rest go on
    pairs_data = attempt("pairs", lambda: transport.get("pairs", {}))
    good = []
    if pairs_data is not None:
        for symbol in symbols:
            try:
                rules, rates = parse.market_rules_from(pairs_data, {s: 4 for s in symbol.split("/")}, [symbol])
                r = rules[symbol]
                say(f"market {symbol}: amount_step={r.amount_step} min_amount={r.min_amount} price_tick={r.price_tick} "
                    f"max_fee_rate={r.max_fee_rate} maker_quote={rates[symbol].maker_quote} "
                    f"taker_quote={rates[symbol].taker_quote} maker_base={rates[symbol].maker_base} "
                    f"taker_base={rates[symbol].taker_base}")
                good.append(symbol)
            except ExchangeDataError as exc:
                failed = True
                say(f"market {symbol}: BAD ({exc})")
    if public_only:
        say("public-only: private endpoints were not touched")
        return _finish(lines, secrets, 1 if failed else 0)

    # --- the reader on the symbols that are fine
    reader = None
    if good:
        try:
            reader = BitbankReader(transport, symbols=good, clock=wall_clock)
            say(f"reader: built for {len(good)}/{len(symbols)} symbols")
        except ReaderUnavailable as exc:
            failed = True
            say(f"reader: NOT BUILT ({exc})")

    # --- every GET endpoint is reachable with this key
    first = good[0] if good else symbols[0]
    probes = {"assets": ("assets", {}), "active_orders": ("active_orders", {"pair": parse.pair_id(first), "count": 5}),
              "trade_history": ("trade_history", {"pair": parse.pair_id(first), "count": 5}),
              "deposit_history": ("deposit_history", {}), "spot_status": ("spot_status", {})}
    for label, (endpoint, params) in probes.items():
        if attempt(f"endpoint {label}", lambda e=endpoint, p=params: transport.get(e, p)) is not None:
            say(f"endpoint {label}: ok")
    try:   # the answer to a non-existent order is information, not a failure
        transport.get("order", {"pair": parse.pair_id(first), "order_id": "1"})
        say("order lookup of a non-existent id: UNEXPECTEDLY SUCCEEDED")
    except OrderNotFound:
        say("order lookup of a non-existent id: OrderNotFound (code 50009, as the reader expects)")
    except ExchangeError as exc:
        say(f"order lookup of a non-existent id: {_name(exc)} (not 50009: the 'order is gone' rule needs review)")

    # --- balances: shape only (raw)
    assets = attempt("balances", lambda: transport.get("assets", {}))
    if isinstance(assets, dict) and isinstance(assets.get("assets"), list):
        rows = [r for r in assets["assets"] if isinstance(r, dict)]
        def d(row, key):
            try:
                return parse.dec_str(row.get(key), key)
            except ExchangeDataError:
                return None
        consistent = sum(1 for r in rows if None not in (d(r, "onhand_amount"), d(r, "free_amount"), d(r, "locked_amount"))
                         and d(r, "onhand_amount") == d(r, "free_amount") + d(r, "locked_amount"))
        precisions = {str(r.get("asset", "?")).upper(): r.get("amount_precision") for r in rows
                      if str(r.get("asset", "")).upper() in {s.split("/")[0] for s in symbols} | {"JPY"}}
        say(f"balances: currencies={len(rows)} nonzero={sum(1 for r in rows if (d(r, 'onhand_amount') or 0) != 0)} "
            f"onhand==free+locked in {consistent}/{len(rows)} amount_precision(watched)={precisions}")

    # --- open orders and fills per symbol, raw
    trade_rows: dict[str, list[dict]] = {}
    for symbol in good or symbols:
        pair = parse.pair_id(symbol)
        data = attempt(f"open orders {symbol}", lambda p=pair: transport.get("active_orders", {"pair": p, "count": 100}))
        if isinstance(data, dict) and isinstance(data.get("orders"), list):
            say(f"open orders {symbol}: count={len(data['orders'])} statuses="
                f"{dict(Counter(str(o.get('status')) for o in data['orders'] if isinstance(o, dict)))}")
        data = attempt(f"fills {symbol}", lambda p=pair: transport.get("trade_history", {"pair": p, "count": 1000,
                                                                                         "order": "desc"}))
        if isinstance(data, dict) and isinstance(data.get("trades"), list):
            trade_rows[symbol] = [t for t in data["trades"] if isinstance(t, dict)]
            say(f"fills {symbol}: count={len(trade_rows[symbol])}")
    all_trades = [t for rows in trade_rows.values() for t in rows]
    say(f"fee shapes (base/quote, sign, role, occurred): {dict(Counter(fee_shape(t) for t in all_trades))}")
    say(f"executed_at digits: {dict(Counter(len(str(t.get('executed_at'))) for t in all_trades))}  "
        f"id types: {dict(Counter(type(t.get('trade_id')).__name__ for t in all_trades))}")

    # --- remaining_amount, from raw order rows of orders seen in the trade history
    tally: Counter = Counter()
    seen = list(dict.fromkeys((s, t.get("order_id")) for s, rows in trade_rows.items() for t in rows))[:20]
    for symbol, order_id in seen:
        try:
            row = transport.get("order", {"pair": parse.pair_id(symbol), "order_id": str(order_id)})
        except OrderNotFound:
            tally["not_found"] += 1
            continue
        except ExchangeError as exc:
            tally[f"error:{_name(exc)}"] += 1
            continue
        try:
            start, executed = parse.dec_str(row.get("start_amount"), "start"), parse.dec_str(row.get("executed_amount"), "ex")
            remaining_raw = row.get("remaining_amount")
            if remaining_raw is None:
                relation = "remaining-null"
            else:
                remaining = parse.dec_str(remaining_raw, "rem")
                relation = ("start==executed+remaining" if start == executed + remaining else
                            "remaining==0" if remaining == 0 else "other")
        except ExchangeDataError:
            relation = "unreadable"
        tally[(row.get("type"), row.get("status"), relation)] += 1
    say(f"orders from history ({len(seen)} looked up; type, status, remaining relation): {dict(tally)}")

    # --- the unit of `since`: pivot on the median trade time so the two readings give different answers
    busiest = max(trade_rows, key=lambda s: len(trade_rows[s]), default=None)
    stamps = sorted(int(t["executed_at"]) for t in trade_rows.get(busiest, []) if str(t.get("executed_at")).isdigit())
    if busiest is None or len(stamps) < 4 or stamps[0] == stamps[-1]:
        say("since unit experiment: undetermined (needs one symbol with at least 4 trades at different times)")
    else:
        pivot = int(statistics.median_low(stamps))
        counts = {}
        for label, since in (("none", None), ("pivot_as_ms", pivot), ("pivot_as_sec", pivot // 1000)):
            params = {"pair": parse.pair_id(busiest), "count": 1000, "order": "desc"}
            if since is not None:
                params["since"] = since
            data = attempt(f"since experiment {label}", lambda p=params: transport.get("trade_history", p))
            if isinstance(data, dict) and isinstance(data.get("trades"), list):
                counts[label] = len(data["trades"])
        total, as_ms, as_sec = counts.get("none"), counts.get("pivot_as_ms"), counts.get("pivot_as_sec")
        if None in (total, as_ms, as_sec):
            verdict = "undetermined (a request failed)"
        elif 0 < as_ms < total and as_sec == total:
            verdict = "since is in MILLISECONDS"
        elif as_ms == 0 and 0 < as_sec < total:
            verdict = "since is in SECONDS"
        else:
            verdict = "undetermined"
        say(f"since unit experiment ({busiest}): counts {counts} -> {verdict}")

    # --- reader smoke: every read method on every good symbol, class names only
    if reader is not None:
        smoke: Counter = Counter()
        for symbol in good:
            for label, fn in (("book", lambda s=symbol: reader.fetch_order_book(s)),
                              ("open_orders", lambda s=symbol: reader.fetch_open_orders(s)),
                              ("fills", lambda s=symbol: reader.fetch_fills(s, since_ms=0))):
                try:
                    fn()
                    smoke[(label, "ok")] += 1
                except ExchangeError as exc:
                    smoke[(label, _name(exc))] += 1
        say(f"reader smoke (strict parsing): {dict(smoke)}")
    return _finish(lines, secrets, 1 if failed else 0)


def _finish(lines: list[str], secrets: Sequence[str], code: int) -> tuple[list[str], int]:
    for line in lines:
        for secret in secrets:
            if secret and secret in line:
                return (["refused to print: a line contained the key or secret"], 2)
    return lines, code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="read-only bitbank check (phase B)")
    parser.add_argument("--state-dir")
    parser.add_argument("--from-env", action="store_true", help="read the key from the environment, not the file")
    parser.add_argument("--public-only", action="store_true", help="only public data; no key is read or needed")
    parser.add_argument("--symbols", nargs="*", default=list(DEFAULT_SYMBOLS))
    args = parser.parse_args(argv)
    credentials = None
    if not args.public_only:
        if not args.state_dir:
            print("--state-dir is required (or use --public-only)")
            return 1
        try:
            credentials = load_credentials(args.state_dir, source="env" if args.from_env else "file")
        except CredentialsUnavailable as exc:
            print(f"credentials: NOT AVAILABLE ({exc.reason.value})")
            return 1
    try:
        transport = CcxtReadTransport(credentials, endpoints={**ENDPOINTS, **CHECK_ONLY_ENDPOINTS})
    except ModuleNotFoundError:
        print("ccxt is not installed here: pip install -r requirements-trading.txt")
        return 1
    secrets = credentials.reveal() if credentials is not None else ()
    lines, code = run_check(transport, args.symbols, secrets=secrets, public_only=args.public_only)
    print("\n".join(lines))
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
