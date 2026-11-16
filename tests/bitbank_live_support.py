"""Synthetic bitbank responses and fake transports for the L3 tests (no network, no real data)."""
from decimal import Decimal as D

from docich.trading.live.exchange import FeeStatus
from docich.trading.exchanges.bitbank_live.parse import pair_id

SENTINEL_KEY = "SENTINEL-KEY-0000"
SENTINEL_SECRET = "SENTINEL-SECRET-0000"
SYMBOLS = ("OAS/JPY", "XYM/JPY")
T0 = 1_700_000_000_000


def pair_row(symbol="OAS/JPY", **kw):
    base, quote = symbol.split("/")
    row = {"name": pair_id(symbol), "base_asset": base.lower(), "quote_asset": quote.lower(),
           "maker_fee_rate_base": "0", "taker_fee_rate_base": "0", "maker_fee_rate_quote": "0.0002",
           "taker_fee_rate_quote": "0.0012", "unit_amount": "1", "limit_max_amount": "1000000",
           "market_max_amount": "1000000", "price_digits": 3, "amount_digits": 0, "is_enabled": True,
           "stop_order": False, "stop_order_and_cancel": False}
    row.update(kw)
    return row


def pairs_payload(symbols=SYMBOLS, **per_symbol):
    return {"pairs": [pair_row(s, **per_symbol.get(s, {})) for s in symbols] + [pair_row("ZZZ/JPY")]}


def assets_payload(jpy=("10000", "0"), coins=None, precision=4):
    coins = coins if coins is not None else {"OAS": ("0", "0"), "XYM": ("0", "0")}
    rows = [{"asset": "jpy", "amount_precision": 4, "onhand_amount": jpy[0], "locked_amount": jpy[1],
             "free_amount": str(D(jpy[0]) - D(jpy[1])), "withdrawing_amount": "0"}]
    for name, (onhand, locked) in coins.items():
        rows.append({"asset": name.lower(), "amount_precision": precision, "onhand_amount": onhand,
                     "locked_amount": locked, "free_amount": str(D(onhand) - D(locked)), "withdrawing_amount": "0"})
    return {"assets": rows}


def order_row(**kw):
    row = {"order_id": 1000001, "pair": "oas_jpy", "side": "buy", "type": "limit", "start_amount": "10",
           "remaining_amount": "10", "executed_amount": "0", "price": "100", "average_price": "0",
           "ordered_at": T0, "status": "UNFILLED"}
    row.update(kw)
    return row


def trade_row(**kw):
    row = {"trade_id": 5001, "pair": "oas_jpy", "order_id": 1000001, "side": "buy", "type": "limit",
           "amount": "10", "price": "100", "maker_taker": "taker", "fee_amount_base": "0",
           "fee_amount_quote": "1.2", "fee_occurred_amount_quote": "1.2", "executed_at": T0 + 1000}
    row.update(kw)
    return row


def depth_payload():
    return {"asks": [["101", "5"], ["102", "7"]], "bids": [["100", "4"], ["99", "9"]], "timestamp": T0}


class FakeTransport:
    """``script``: endpoint -> payload, an exception (raised), a callable(params), or a list of those (consumed)."""

    def __init__(self, script=None):
        self.script = dict(script or {})
        self.calls = []

    def get(self, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        item = self.script[endpoint]
        if isinstance(item, list):
            item = item.pop(0) if len(item) > 1 else item[0]
        if callable(item):
            item = item(params)
        if isinstance(item, BaseException):
            raise item
        return item


def default_script(**over):
    script = {"pairs": pairs_payload(), "assets": assets_payload(), "depth": depth_payload(),
              "order": order_row(), "active_orders": {"orders": []}, "trade_history": {"trades": []}}
    script.update(over)
    return script


# ---- render a MockExchange as a bitbank transport (for the contract tests) ----------------
def _fee_fields(fill):
    if fill.fee.status is FeeStatus.UNKNOWN:
        return {}
    base_asset = fill.symbol.split("/")[0]
    if fill.fee.currency == base_asset:
        return {"fee_amount_base": str(fill.fee.amount), "fee_amount_quote": "0", "fee_occurred_amount_quote": "0"}
    return {"fee_amount_base": "0", "fee_amount_quote": str(fill.fee.amount),
            "fee_occurred_amount_quote": str(fill.fee.amount)}


def render_order(snap):
    return {"order_id": int(snap.exchange_order_id), "pair": pair_id(snap.symbol), "side": snap.side,
            "type": snap.order_type, "start_amount": str(snap.amount), "remaining_amount": str(snap.remaining_amount),
            "executed_amount": str(snap.executed_amount),
            "price": None if snap.price is None else str(snap.price),
            "average_price": "0" if snap.average_price is None else str(snap.average_price),
            "ordered_at": snap.ordered_at_ms, "status": snap.status.value}


def render_trade(fill):
    row = {"trade_id": int(fill.trade_id), "pair": pair_id(fill.symbol), "order_id": int(fill.exchange_order_id),
           "side": fill.side, "type": "limit", "amount": str(fill.amount), "price": str(fill.price),
           "maker_taker": fill.maker_taker, "executed_at": fill.executed_at_ms}
    row.update(_fee_fields(fill))
    return row


def render_bitbank(mock):
    """A transport that answers from the mock's current state (including its injected faults)."""
    from docich.trading.live.exchange import OrderNotFound

    def pairs(_):
        rows = []
        for symbol, rules in mock._markets.items():
            rate = str(rules.max_fee_rate)
            rows.append(pair_row(symbol, taker_fee_rate_base=rate, taker_fee_rate_quote=rate,
                                 maker_fee_rate_base="0", maker_fee_rate_quote="0",
                                 unit_amount=str(rules.min_amount),
                                 amount_digits=-rules.amount_step.as_tuple().exponent,
                                 price_digits=-rules.price_tick.as_tuple().exponent))
        return {"pairs": rows}

    def assets(_):
        snapshot = mock.fetch_balance()
        rows = []
        for currency, asset in snapshot.assets.items():
            unit = next((m.quote_unit for m in mock._markets.values() if m.quote == currency),
                        next((m.base_unit for m in mock._markets.values() if m.base == currency), D("0.0001")))
            rows.append({"asset": currency.lower(), "amount_precision": -unit.as_tuple().exponent,
                         "onhand_amount": str(asset.total), "locked_amount": str(asset.locked),
                         "free_amount": str(asset.free), "withdrawing_amount": "0"})
        return {"assets": rows}

    def symbol_of(params):
        base, quote = params["pair"].upper().split("_")
        return f"{base}/{quote}"

    def order(params):
        return render_order(mock.fetch_order(symbol_of(params), params["order_id"]))

    def active(params):
        return {"orders": [render_order(o) for o in mock.fetch_open_orders(symbol_of(params)).orders][:params["count"]]}

    def trades(params):
        fills = mock.fetch_fills(symbol_of(params), since_ms=0, exchange_order_id=params.get("order_id")).fills
        rows = [render_trade(f) for f in sorted(fills, key=lambda f: (f.executed_at_ms, int(f.trade_id)), reverse=True)]
        return {"trades": rows[:params["count"]]}

    def depth(params):
        book = mock.fetch_order_book(symbol_of(params))
        return {"asks": [[str(p), str(a)] for p, a in book.asks], "bids": [[str(p), str(a)] for p, a in book.bids],
                "timestamp": book.exchange_ts_ms}

    return FakeTransport({"pairs": pairs, "assets": assets, "order": order, "active_orders": active,
                          "trade_history": trades, "depth": depth})
