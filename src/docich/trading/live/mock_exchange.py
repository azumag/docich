"""A deterministic in-memory exchange for the LIVE tests (Issue #3623, L2).

The only ``OrderSender`` in the code base.  No network, no randomness: time comes from an
injected clock, ids are counters, and every failure the real world can produce
(lost request, lost response, rejection, lagging visibility, missing or late fees,
duplicated or conflicting fills, truncated pages, stale balances, clock skew) is injected on
purpose through ``faults`` or the test-only helpers.

With ``state_path`` the state is written (tmp + fsync + rename) right after every change,
*before* the call returns or raises, so a test can SIGKILL a child process in the middle of
the executor and still read what the "exchange" did.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Callable, Mapping

from .exchange import (AssetBalance, BalanceSnapshot, CancelKind, CancelOutcome, Capabilities, ExchangeAmbiguous,
                       ExchangeDataError, ExchangeRejected, FeeInfo, FeeStatus, Fill, FillPage, MarketRules,
                       OrderBook, OrderNotFound, OrderPage, OrderRequest, OrderSnapshot, OrderStatus)

_ZERO = Decimal(0)
_OPEN = ("UNFILLED", "PARTIALLY_FILLED", "INACTIVE")
_READ_OPS = ("fetch_order", "fetch_order_by_client_id", "fetch_open_orders", "fetch_fills",
             "fetch_balance", "fetch_order_book")


@dataclass(frozen=True)
class MockFees:
    maker_rate: Decimal = Decimal("0")
    taker_rate: Decimal = Decimal("0.001")
    currency: str = "quote"            # "quote" | "base": what a *buy* is charged in (a sell pays in quote)
    quantum: Decimal = Decimal("0.0001")


@dataclass
class Fault:
    op: str                    # "submit" | "cancel" | "fill" | one of the read ops
    kind: str
    nth: int = 1               # fires on the nth matching call
    params: dict = field(default_factory=dict)


class FaultQueue:
    def __init__(self) -> None:
        self._items: list[Fault] = []

    def push(self, op: str, kind: str, *, nth: int = 1, **params) -> None:
        self._items.append(Fault(op, kind, nth, dict(params)))

    def take(self, op: str, kinds: tuple[str, ...] | None = None) -> Fault | None:
        """Every matching fault counts this call; the first one that reaches its nth call fires."""
        matching = [i for i in self._items if i.op == op and (kinds is None or i.kind in kinds)]
        for item in matching:
            item.nth -= 1
        for item in matching:
            if item.nth <= 0:
                self._items.remove(item)
                return item
        return None

    def __len__(self) -> int:
        return len(self._items)


@dataclass
class _Order:
    eid: str
    seq: int
    origin: str                     # "bot" | "external"
    symbol: str
    side: str
    order_type: str
    amount: Decimal
    price: Decimal | None
    status: str
    executed: Decimal
    remaining: Decimal
    quote_spent: Decimal            # sum(notional of its fills)
    ordered_at_ms: int
    client_order_id: str | None = None
    hidden_polls: int = 0


@dataclass
class _FillRec:
    trade_id: str
    eid: str
    symbol: str
    side: str
    amount: Decimal
    price: Decimal
    fee_amount: Decimal
    fee_currency: str
    role: str
    executed_at_ms: int
    fee_hidden: bool = False
    fee_backfill_after: int | None = None
    delay: int = 0


def _enc(obj):
    if isinstance(obj, Decimal):
        return {"$d": str(obj)}
    if isinstance(obj, dict):
        return {k: _enc(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_enc(v) for v in obj]
    return obj


def _dec(obj):
    if isinstance(obj, dict):
        if set(obj) == {"$d"}:
            return Decimal(obj["$d"])
        return {k: _dec(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_dec(v) for v in obj]
    return obj


class MockExchange:
    kind = "mock"

    def __init__(self, *, clock_ms: Callable[[], int], markets: Mapping[str, MarketRules],
                 balances: Mapping[str, Decimal], fees: MockFees, capabilities: Capabilities,
                 state_path: Path | None = None, order_id_start: int = 1_000_001) -> None:
        self._clock_ms = clock_ms
        self._markets = dict(markets)
        self._fees = fees
        self._caps = capabilities
        self._state_path = Path(state_path) if state_path is not None else None
        self.faults = FaultQueue()
        self.submissions: list[dict] = []
        self._orders: dict[str, _Order] = {}
        self._fills: list[_FillRec] = []
        self._totals: dict[str, Decimal] = {k.upper(): Decimal(v) for k, v in balances.items()}
        self._books: dict[str, dict[str, list[list[Decimal]]]] = {}
        self._order_seq = int(order_id_start)
        self._trade_seq = 1
        self._skew_ms = 0
        self._stale: tuple[int, dict[str, Decimal]] | None = None
        if self._state_path is not None and self._state_path.is_file():
            self._load()

    # ------------------------------------------------------------------ helpers
    def _now(self) -> int:
        return int(self._clock_ms()) + self._skew_ms

    def _market(self, symbol: str) -> MarketRules:
        try:
            return self._markets[symbol]
        except KeyError as exc:
            raise ExchangeRejected("unknown_market", symbol) from exc

    def _buy_margin(self) -> Decimal:
        """Worst-case quote cost per unit of notional for a buy.  The fee is charged in
        ``m.quote`` only when fees are quote-denominated; a base-denominated buy pays it
        out of the coins it receives."""
        if self._fees.currency != "quote":
            return Decimal(1)
        return 1 + max(self._fees.maker_rate, self._fees.taker_rate, _ZERO)

    def _locked(self) -> dict[str, Decimal]:
        locked: dict[str, Decimal] = {}
        for o in self._orders.values():
            if o.status not in _OPEN or o.order_type != "limit":
                continue
            m = self._markets[o.symbol]
            if o.side == "buy":
                locked[m.quote] = locked.get(m.quote, _ZERO) + o.remaining * o.price * self._buy_margin()
            else:
                locked[m.base] = locked.get(m.base, _ZERO) + o.remaining
        return locked

    def _free(self, currency: str) -> Decimal:
        return self._totals.get(currency, _ZERO) - self._locked().get(currency, _ZERO)

    def _snap(self, o: _Order) -> OrderSnapshot:
        average = (o.quote_spent / o.executed) if o.executed > 0 else None
        return OrderSnapshot(
            exchange_order_id=o.eid, symbol=o.symbol, side=o.side, order_type=o.order_type, amount=o.amount,
            price=o.price, status=OrderStatus(o.status), executed_amount=o.executed, remaining_amount=o.remaining,
            average_price=average, ordered_at_ms=o.ordered_at_ms,
            client_order_id=o.client_order_id if self._caps.client_order_id else None,
            raw={"id": o.eid, "status": o.status})

    def _fill_view(self, f: _FillRec) -> Fill:
        known = not f.fee_hidden
        fee = FeeInfo(FeeStatus.KNOWN, f.fee_amount, f.fee_currency) if known else FeeInfo(FeeStatus.UNKNOWN)
        return Fill(trade_id=f.trade_id, exchange_order_id=f.eid, symbol=f.symbol, side=f.side, amount=f.amount,
                    price=f.price, quote_notional=None, fee=fee, maker_taker=f.role,
                    executed_at_ms=f.executed_at_ms, raw={"trade_id": f.trade_id})

    def _read_fault(self, op: str):
        fault = self.faults.take(op)
        if fault is None:
            return None
        if fault.kind == "READ_TIMEOUT":
            raise ExchangeAmbiguous(f"{op}: timeout")
        if fault.kind == "MALFORMED":
            raise ExchangeDataError(f"{op}: malformed response")
        return fault

    def _tick_visibility(self) -> None:
        for o in self._orders.values():
            if o.hidden_polls > 0:
                o.hidden_polls -= 1

    # ------------------------------------------------------------------ matching
    def _apply_fill(self, o: _Order, amount: Decimal, price: Decimal, role: str) -> None:
        m = self._markets[o.symbol]
        notional = amount * price
        rate = self._fees.maker_rate if role == "maker" else self._fees.taker_rate
        if o.side == "buy" and self._fees.currency == "base":
            fee_currency, fee = m.base, (amount * rate).quantize(self._fees.quantum, ROUND_HALF_UP)
        else:
            fee_currency, fee = m.quote, (notional * rate).quantize(self._fees.quantum, ROUND_HALF_UP)
        if o.side == "buy":
            self._totals[m.base] = self._totals.get(m.base, _ZERO) + amount
            self._totals[m.quote] = self._totals.get(m.quote, _ZERO) - notional
        else:
            self._totals[m.base] = self._totals.get(m.base, _ZERO) - amount
            self._totals[m.quote] = self._totals.get(m.quote, _ZERO) + notional
        self._totals[fee_currency] = self._totals.get(fee_currency, _ZERO) - fee
        o.executed += amount
        o.remaining -= amount
        o.quote_spent += notional
        o.status = "FULLY_FILLED" if o.remaining <= 0 else "PARTIALLY_FILLED"
        record = _FillRec(trade_id=str(self._trade_seq), eid=o.eid, symbol=o.symbol, side=o.side, amount=amount,
                          price=price, fee_amount=fee, fee_currency=fee_currency, role=role,
                          executed_at_ms=self._now())
        self._trade_seq += 1
        fault = self.faults.take("fill")
        if fault is not None:
            if fault.kind == "FEE_MISSING":
                record.fee_hidden = True
                record.fee_backfill_after = fault.params.get("backfill_after")
            elif fault.kind == "FILL_DELAY":
                record.delay = int(fault.params.get("n", 1))
        self._fills.append(record)

    def _take_liquidity(self, o: _Order) -> None:
        levels = self._books.get(o.symbol, {}).get("asks" if o.side == "buy" else "bids", [])
        for level in list(levels):
            if o.remaining <= 0:
                break
            price, available = level
            if o.order_type == "limit" and ((o.side == "buy" and price > o.price) or
                                            (o.side == "sell" and price < o.price)):
                break
            take = min(o.remaining, available)
            self._apply_fill(o, take, price, "taker")
            level[1] -= take
            if level[1] <= 0:
                levels.remove(level)

    def _estimate_market_cost(self, req: OrderRequest) -> Decimal:
        levels = self._books.get(req.symbol, {}).get("asks", [])
        need, cost = req.amount, _ZERO
        for price, available in levels:
            take = min(need, available)
            cost += take * price
            need -= take
            if need <= 0:
                break
        return cost

    # ------------------------------------------------------------------ sender
    def submit_order(self, req: OrderRequest) -> OrderSnapshot:
        fault = self.faults.take("submit", ("LOST_REQUEST", "LOST_RESPONSE", "REJECT"))
        if fault is not None and fault.kind == "LOST_REQUEST":
            raise ExchangeAmbiguous("submit: request lost")
        record = {"symbol": req.symbol, "side": req.side, "order_type": req.order_type,
                  "amount": str(req.amount), "price": None if req.price is None else str(req.price),
                  "client_order_id": req.client_order_id, "outcome": "rejected", "exchange_order_id": None}
        self.submissions.append(record)
        try:
            if fault is not None and fault.kind == "REJECT":
                raise ExchangeRejected(str(fault.params.get("code", "rejected")))
            o = self._create(req)
        except ExchangeRejected:
            self._persist()
            raise
        record.update(outcome="created", exchange_order_id=o.eid)
        hide = self.faults.take("submit", ("VISIBILITY_LAG",))
        if hide is not None:
            o.hidden_polls = int(hide.params.get("n", 1))
        snapshot = self._snap(o)
        self._persist()
        if fault is not None and fault.kind == "LOST_RESPONSE":
            raise ExchangeAmbiguous("submit: response lost")
        return snapshot

    def _create(self, req: OrderRequest, *, origin: str = "bot") -> _Order:
        m = self._market(req.symbol)
        if req.side not in ("buy", "sell") or req.order_type not in ("limit", "market"):
            raise ExchangeRejected("bad_request", "side/type")
        if req.amount <= 0 or req.amount < m.min_amount:
            raise ExchangeRejected("amount_below_min", str(req.amount))
        if req.amount % m.amount_step != 0:
            raise ExchangeRejected("amount_step", str(req.amount))
        if req.order_type == "limit":
            if req.price is None or req.price <= 0:
                raise ExchangeRejected("limit_needs_price")
            if req.price % m.price_tick != 0:
                raise ExchangeRejected("price_tick", str(req.price))
        if (req.client_order_id and self._caps.client_order_id and origin == "bot" and
                any(x.client_order_id == req.client_order_id for x in self._orders.values())):
            raise ExchangeRejected("duplicate_client_order_id", req.client_order_id)
        if req.side == "buy":
            cost = (req.amount * req.price) if req.order_type == "limit" else self._estimate_market_cost(req)
            if cost * self._buy_margin() > self._free(m.quote):
                raise ExchangeRejected("insufficient_funds", m.quote)
        elif req.amount > self._free(m.base):
            raise ExchangeRejected("insufficient_funds", m.base)
        eid = str(self._order_seq)
        o = _Order(eid=eid, seq=self._order_seq, origin=origin, symbol=req.symbol, side=req.side,
                   order_type=req.order_type, amount=req.amount, price=req.price, status="UNFILLED",
                   executed=_ZERO, remaining=req.amount, quote_spent=_ZERO, ordered_at_ms=self._now(),
                   client_order_id=req.client_order_id)
        self._order_seq += 1
        self._orders[eid] = o
        self._take_liquidity(o)
        if o.order_type == "market":
            if o.executed == 0:
                del self._orders[eid]
                raise ExchangeRejected("no_liquidity", req.symbol)
            if o.remaining > 0:
                o.status = "CANCELED_PARTIALLY_FILLED"
        return o

    def cancel_order(self, symbol: str, exchange_order_id: str) -> CancelOutcome:
        fault = self.faults.take("cancel", ("LOST_REQUEST", "LOST_RESPONSE", "CANCEL_RACE"))
        if fault is not None and fault.kind == "LOST_REQUEST":
            raise ExchangeAmbiguous("cancel: request lost")
        o = self._orders.get(str(exchange_order_id))
        if o is None or o.symbol != symbol:
            raise OrderNotFound(str(exchange_order_id))
        if fault is not None and fault.kind == "CANCEL_RACE" and o.status in _OPEN:
            self._apply_fill(o, o.remaining, o.price if o.price is not None else _ZERO, "maker")
        if o.status in _OPEN:
            o.status = "CANCELED_PARTIALLY_FILLED" if o.executed > 0 else "CANCELED_UNFILLED"
            outcome = CancelOutcome(CancelKind.CANCELED, self._snap(o))
        else:
            outcome = CancelOutcome(CancelKind.ALREADY_CLOSED, self._snap(o))
        self._persist()
        if fault is not None and fault.kind == "LOST_RESPONSE":
            raise ExchangeAmbiguous("cancel: response lost")
        return outcome

    # ------------------------------------------------------------------ reader
    @property
    def capabilities(self) -> Capabilities:
        return self._caps

    def market_rules(self, symbol: str) -> MarketRules:
        try:
            return self._markets[symbol]
        except KeyError as exc:
            raise ExchangeDataError(f"unknown market {symbol}") from exc

    def fetch_balance(self) -> BalanceSnapshot:
        self._read_fault("fetch_balance")
        totals = self._totals
        if self._stale is not None:
            remaining, frozen = self._stale
            totals = frozen
            self._stale = (remaining - 1, frozen) if remaining > 1 else None
        locked = self._locked()
        assets = {}
        for currency, total in sorted(totals.items()):
            held = locked.get(currency, _ZERO)
            assets[currency] = AssetBalance(total, total - held, held)
        return BalanceSnapshot(assets=assets, exchange_ts_ms=self._now(), fetched_at=float(self._clock_ms()) / 1000)

    def fetch_order_book(self, symbol: str, *, depth: int = 20) -> OrderBook:
        self._read_fault("fetch_order_book")
        self._market(symbol)
        book = self._books.get(symbol, {"bids": [], "asks": []})
        return OrderBook(symbol, tuple((p, a) for p, a in book["bids"][:depth]),
                         tuple((p, a) for p, a in book["asks"][:depth]), self._now())

    def fetch_order(self, symbol: str, exchange_order_id: str) -> OrderSnapshot:
        self._read_fault("fetch_order")
        o = self._orders.get(str(exchange_order_id))
        hidden = o is not None and o.hidden_polls > 0
        self._tick_visibility()
        self._persist()  # the consumed polls are a change too: keep them over a restart
        if o is None or o.symbol != symbol or hidden:
            raise OrderNotFound(str(exchange_order_id))
        return self._snap(o)

    def fetch_order_by_client_id(self, symbol: str, client_order_id: str) -> OrderSnapshot | None:
        self._read_fault("fetch_order_by_client_id")
        if not self._caps.fetch_by_client_id:
            raise ExchangeDataError("this exchange cannot look up by client order id")
        found = [o for o in self._orders.values() if o.symbol == symbol and o.client_order_id == client_order_id]
        hidden = bool(found) and found[0].hidden_polls > 0
        self._tick_visibility()
        self._persist()
        return self._snap(found[0]) if found and not hidden else None

    def fetch_open_orders(self, symbol: str) -> OrderPage:
        fault = self._read_fault("fetch_open_orders")
        orders = [o for o in self._orders.values() if o.symbol == symbol and o.status in _OPEN
                  and o.hidden_polls <= 0]
        self._tick_visibility()
        self._persist()
        orders.sort(key=lambda o: o.seq)
        views = [self._snap(o) for o in orders]
        if fault is not None and fault.kind == "TRUNCATED_PAGE":
            return OrderPage(tuple(views[:-1]), complete=False)
        return OrderPage(tuple(views), complete=True)

    def fetch_fills(self, symbol: str, *, since_ms: int, exchange_order_id: str | None = None) -> FillPage:
        fault = self._read_fault("fetch_fills")
        out: list[Fill] = []
        for f in self._fills:
            if f.symbol != symbol or f.executed_at_ms < since_ms:
                continue
            if exchange_order_id is not None and self._caps.fills_by_order and f.eid != exchange_order_id:
                continue
            if self._orders[f.eid].hidden_polls > 0:
                continue
            if f.delay > 0:
                f.delay -= 1
                continue
            if f.fee_hidden and f.fee_backfill_after is not None:
                if f.fee_backfill_after <= 0:
                    f.fee_hidden = False
                else:
                    f.fee_backfill_after -= 1
            out.append(self._fill_view(f))
        self._tick_visibility()
        self._persist()
        complete = True
        if fault is not None:
            if fault.kind == "TRUNCATED_PAGE":  # also when nothing is visible: the page may be cut off
                out, complete = out[:-1], False
            elif out and fault.kind == "DUPLICATE_FILL":
                out.append(out[0])
            elif out and fault.kind == "CONFLICTING_FILL":
                first = out[0]
                out.append(Fill(first.trade_id, first.exchange_order_id, first.symbol, first.side, first.amount,
                                first.price + Decimal(1), first.quote_notional, first.fee, first.maker_taker,
                                first.executed_at_ms, first.raw))
        return FillPage(tuple(out), complete)

    # ------------------------------------------------------------------ test-only controls
    def set_book(self, symbol: str, *, bids=(), asks=()) -> None:
        self._market(symbol)
        self._books[symbol] = {
            "bids": [[Decimal(p), Decimal(a)] for p, a in sorted(bids, key=lambda x: -Decimal(x[0]))],
            "asks": [[Decimal(p), Decimal(a)] for p, a in sorted(asks, key=lambda x: Decimal(x[0]))]}
        self._persist()

    def trade_against(self, symbol: str, *, taker_side: str, price, amount) -> Decimal:
        """An outside taker hits resting orders (ours or anyone's); fills at the resting order's price."""
        price, left = Decimal(price), Decimal(amount)
        if taker_side == "sell":
            resting = sorted((o for o in self._orders.values() if o.symbol == symbol and o.side == "buy"
                              and o.status in _OPEN and o.order_type == "limit" and o.price >= price),
                             key=lambda o: (-o.price, o.seq))
        else:
            resting = sorted((o for o in self._orders.values() if o.symbol == symbol and o.side == "sell"
                              and o.status in _OPEN and o.order_type == "limit" and o.price <= price),
                             key=lambda o: (o.price, o.seq))
        done = _ZERO
        for o in resting:
            if left <= 0:
                break
            take = min(left, o.remaining)
            self._apply_fill(o, take, o.price, "maker")
            left -= take
            done += take
        self._persist()
        return done

    def place_external_order(self, req: OrderRequest) -> str:
        """An order the bot did not make (the owner's manual order): in the account, in no ledger."""
        o = self._create(req, origin="external")
        self._persist()
        return o.eid

    def adjust_balance(self, currency: str, delta) -> None:
        currency = currency.upper()
        self._totals[currency] = self._totals.get(currency, _ZERO) + Decimal(delta)
        self._persist()

    def lag_balance(self, polls: int) -> None:
        """The next ``polls`` balance reads return the balance as it is right now (stale afterwards)."""
        self._stale = (int(polls), dict(self._totals))

    def set_clock_skew(self, ms: int) -> None:
        self._skew_ms = int(ms)
        self._persist()

    def totals(self) -> dict[str, Decimal]:
        return dict(self._totals)

    def all_orders(self) -> list[OrderSnapshot]:
        return [self._snap(o) for o in sorted(self._orders.values(), key=lambda o: o.seq)]

    def all_fills(self) -> list[Fill]:
        return [Fill(f.trade_id, f.eid, f.symbol, f.side, f.amount, f.price, None,
                     FeeInfo(FeeStatus.KNOWN, f.fee_amount, f.fee_currency), f.role, f.executed_at_ms)
                for f in self._fills]

    # ------------------------------------------------------------------ persistence
    def _state(self) -> dict:
        return {"orders": [asdict(o) for o in self._orders.values()], "fills": [asdict(f) for f in self._fills],
                "totals": self._totals, "books": self._books, "order_seq": self._order_seq,
                "trade_seq": self._trade_seq, "submissions": self.submissions, "skew_ms": self._skew_ms}

    def _persist(self) -> None:
        if self._state_path is None:
            return
        tmp = self._state_path.with_name(self._state_path.name + ".tmp")
        data = json.dumps(_enc(self._state()), sort_keys=True).encode("utf-8")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, self._state_path)

    def _load(self) -> None:
        state = _dec(json.loads(self._state_path.read_text(encoding="utf-8")))
        self._orders = {o["eid"]: _Order(**o) for o in state["orders"]}
        self._fills = [_FillRec(**f) for f in state["fills"]]
        self._totals = state["totals"]
        self._books = state["books"]
        self._order_seq = state["order_seq"]
        self._trade_seq = state["trade_seq"]
        self.submissions = state["submissions"]
        self._skew_ms = state["skew_ms"]
