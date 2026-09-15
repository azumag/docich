"""Isolated, deterministic paper accounts. There is intentionally no live broker.

Prices are observed bid/ask quotes, never fabricated bars. SQLite commits account,
quotes, fills and metrics together, so restarts cannot replay an acknowledged fill.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
import sqlite3
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NY = ZoneInfo("America/New_York")
TITLES = {"stocks": "中華AIのデイトレ", "fx": "FXで大儲け結果発表"}
D = Decimal


def decimal(value) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not money")
    result = D(str(value))
    if not result.is_finite():
        raise ValueError("non-finite number")
    return result


def stamp(value) -> float:
    if isinstance(value, bool):
        raise ValueError("invalid timestamp")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("invalid timestamp")
    return value


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def report_window(market: str, now: float) -> tuple[float, float]:
    day = dt.datetime.fromtimestamp(stamp(now), JST)
    hour = 9 if market == "stocks" else 3
    begin = day.replace(hour=hour, minute=0, second=0, microsecond=0)
    return begin.timestamp(), (begin + dt.timedelta(minutes=60 if market == "stocks" else 30)).timestamp()


def fx_week_open(now: float) -> bool:
    local = dt.datetime.fromtimestamp(stamp(now), NY)
    return (local.weekday() < 4 or (local.weekday() == 4 and local.hour < 17)
            or (local.weekday() == 6 and local.hour >= 17))


@dataclass(frozen=True)
class Policy:
    kind: str = "momentum"
    lookback: int = 12
    entry_bps: int = 12
    stop_bps: int = 60
    take_bps: int = 100
    max_hold_s: int = 900

    def __post_init__(self):
        if self.kind not in ("momentum", "reversion"):
            raise ValueError("unsupported strategy")
        for key, lo, hi in (("lookback", 3, 120), ("entry_bps", 1, 200),
                            ("stop_bps", 5, 300), ("take_bps", 5, 600), ("max_hold_s", 30, 3600)):
            value = getattr(self, key)
            if type(value) is not int or not lo <= value <= hi:
                raise ValueError(f"invalid policy {key}")

    @property
    def version(self) -> str:
        return digest(asdict(self))[:20]


@dataclass(frozen=True)
class Limits:
    capital_jpy: str = "1000000"
    allocation: str = "0.30"
    position_fraction: str = "0.10"
    daily_loss_fraction: str = "0.02"
    fee_bps: str = "1"
    slippage_bps: str = "2"
    # Conservative continuous financing estimate, NOT broker swap settlement.
    financing_bps_per_day: str = "2"
    max_spread_bps: str = "30"
    quote_age_s: int = 15
    max_positions: int = 3
    lot: int = 100

    def __post_init__(self):
        for key in ("capital_jpy", "allocation", "position_fraction", "daily_loss_fraction",
                    "fee_bps", "slippage_bps", "financing_bps_per_day", "max_spread_bps"):
            if decimal(getattr(self, key)) < 0:
                raise ValueError(f"negative limit: {key}")
        if not D("1000") <= decimal(self.capital_jpy) <= D("1000000000"):
            raise ValueError("invalid paper capital")
        if not 0 < decimal(self.position_fraction) <= decimal(self.allocation) <= 1:
            raise ValueError("invalid paper allocation")
        if not 0 < decimal(self.daily_loss_fraction) <= D("0.1"):
            raise ValueError("invalid loss limit")
        if any(type(v) is not int or v < 1 for v in (self.quote_age_s, self.max_positions, self.lot)):
            raise ValueError("invalid integer limit")
        if self.quote_age_s > 60 or self.max_positions > 20:
            raise ValueError("unsafe limit")


@dataclass(frozen=True)
class Quote:
    symbol: str
    ts: float
    bid: str
    ask: str
    bid_size: str
    ask_size: str
    tradeable: bool
    source: str
    # This first implementation only accepts JPY-quoted instruments. Non-JPY
    # crosses require an audited conversion feed; do not silently assume 1:1.
    currency: str = "JPY"

    def validate(self, now: float, limits: Limits) -> None:
        if not re.fullmatch(r"[A-Z0-9_]{3,20}", self.symbol):
            raise ValueError("invalid symbol")
        age = stamp(now) - stamp(self.ts)
        bid, ask = decimal(self.bid), decimal(self.ask)
        if not 0 <= age <= limits.quote_age_s or not 0 < bid <= ask:
            raise ValueError("stale/future/crossed quote")
        if self.currency != "JPY" or self.tradeable is not True or not self.source:
            raise ValueError("untradeable or unsupported quote")
        if min(decimal(self.bid_size), decimal(self.ask_size)) <= 0:
            raise ValueError("missing executable liquidity")
        if (ask - bid) / ((ask + bid) / 2) * 10000 > decimal(limits.max_spread_bps):
            raise ValueError("spread too wide")

    @property
    def mid(self) -> Decimal:
        return (decimal(self.bid) + decimal(self.ask)) / 2


def signal(history: list, policy: Policy) -> int:
    if len(history) < policy.lookback:
        return 0
    recent = [decimal(x[1]) for x in history[-policy.lookback:]]
    change = (recent[-1] / recent[0] - 1) * 10000
    if policy.kind == "reversion":
        change = -(recent[-1] / (sum(recent) / len(recent)) - 1) * 10000
    return 1 if change >= policy.entry_bps else -1 if change <= -policy.entry_bps else 0


class PaperBook:
    def __init__(self, path: Path, market: str, limits: Limits, *, mode: str = "paper"):
        if mode != "paper":
            raise ValueError("live execution is not implemented or permitted")
        if market not in TITLES:
            raise ValueError("unknown market")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.market, self.limits = market, limits
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ticks (id TEXT PRIMARY KEY, ts REAL NOT NULL, body TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS ticks_time ON ticks(ts);
            CREATE TABLE IF NOT EXISTS fills (id TEXT PRIMARY KEY, ts REAL NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS metrics (ts REAL PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, body TEXT NOT NULL);
        """)
        contract = digest({"market": market, "limits": asdict(limits), "mode": mode})
        row = self.db.execute("SELECT body FROM account WHERE id=1").fetchone()
        if row and json.loads(row[0])["contract"] != contract:
            self.db.close()
            raise ValueError("account limits changed: explicit account migration is required")
        with self.db:
            if not row:
                body = {"contract": contract, "cash": limits.capital_jpy, "positions": {}, "history": {},
                        "last_entry": {}, "day": "", "day_equity": limits.capital_jpy,
                        "day_spent": "0", "blocked": [], "risk_stopped": False,
                        "peak": limits.capital_jpy, "max_drawdown": "0", "last_ts": 0}
                self.db.execute("INSERT INTO account VALUES (1,?)", (json.dumps(body),))

    def close(self):
        self.db.close()

    def state(self) -> dict:
        return json.loads(self.db.execute("SELECT body FROM account WHERE id=1").fetchone()[0])

    def _mark(self, state: dict, quotes: dict[str, Quote]) -> tuple[Decimal, Decimal]:
        unreal, deployed = D(0), D(0)
        for symbol, pos in state["positions"].items():
            q = quotes.get(symbol)
            if q:
                pos["mark"] = q.bid if pos["side"] == 1 else q.ask
                pos["mark_ts"] = q.ts
            px, qty, entry = decimal(pos["mark"]), decimal(pos["qty"]), decimal(pos["entry"])
            # Include estimated liquidation fees/slippage in open P&L.
            exit_cost = qty * px * (decimal(self.limits.fee_bps) + decimal(self.limits.slippage_bps)) / 10000
            unreal += (px - entry) * qty * pos["side"] - exit_cost
            deployed += qty * px
        return unreal, deployed

    def process(self, quotes: list[Quote], policy: Policy, *, now: float,
                allow_entries: bool, force_flat: bool = False) -> dict:
        now = stamp(now)
        if type(allow_entries) is not bool or type(force_flat) is not bool:
            raise ValueError("invalid execution gate")
        if len(quotes) > 50 or len({q.symbol for q in quotes}) != len(quotes):
            raise ValueError("duplicate/excess quotes")
        valid, errors = {}, []
        for q in quotes:
            try:
                q.validate(now, self.limits)
                valid[q.symbol] = q
            except (ValueError, ArithmeticError):
                errors.append(q.symbol)
        if self.market == "fx" and not fx_week_open(now):
            valid = {}
            allow_entries = False
        day = dt.datetime.fromtimestamp(now, JST).date().isoformat()
        tick_id = digest({"now": now, "quotes": [asdict(q) for q in quotes],
                          "entries": allow_entries, "flat": force_flat, "policy": policy.version})
        self.db.execute("BEGIN IMMEDIATE")
        try:
            state = self.state()
            if self.db.execute("SELECT 1 FROM ticks WHERE id=?", (tick_id,)).fetchone():
                self.db.rollback()
                return self.snapshot(now=now)
            if now < state["last_ts"]:
                raise ValueError("clock moved backwards")
            # Financing continues while the bot is off; close quotes may be absent.
            if self.market == "fx":
                for pos in state["positions"].values():
                    charge = (decimal(pos["qty"]) * decimal(pos["mark"])
                              * decimal(self.limits.financing_bps_per_day) / 10000
                              * decimal(now - pos["financed_at"]) / 86400)
                    state["cash"] = str(decimal(state["cash"]) - charge)
                    pos["financing"] = str(decimal(pos["financing"]) + charge)
                    pos["financed_at"] = now
            unreal, deployed = self._mark(state, valid)
            equity = decimal(state["cash"]) + unreal
            if state["day"] != day:
                state.update(day=day, day_equity=str(equity), day_spent="0", blocked=[], risk_stopped=False)
            if equity <= decimal(state["day_equity"]) * (1 - decimal(self.limits.daily_loss_fraction)):
                state["risk_stopped"] = True
            for symbol, q in valid.items():
                history = state["history"].setdefault(symbol, [])
                if not history or q.ts > history[-1][0]:
                    history.append([q.ts, str(q.mid)])
                    del history[:-120]
                direction = signal(history, policy)
                pos = state["positions"].get(symbol)
                if pos:
                    signed_return = ((decimal(pos["mark"]) / decimal(pos["entry"]) - 1)
                                     * pos["side"] * 10000)
                    close = (force_flat or state["risk_stopped"] or signed_return <= -pos["stop_bps"]
                             or signed_return >= pos["take_bps"] or now - pos["opened_at"] >= pos["max_hold_s"]
                             or (direction and direction != pos["side"]))
                    if close:
                        qty = decimal(pos["qty"])
                        available = decimal(q.bid_size if pos["side"] == 1 else q.ask_size)
                        # All-or-none conservative fills. Never pretend a halt or
                        # insufficient top-of-book liquidity was a successful exit.
                        if qty > available:
                            continue
                        price = decimal(pos["mark"]) * (1 - pos["side"] * decimal(self.limits.slippage_bps) / 10000)
                        fee = qty * price * decimal(self.limits.fee_bps) / 10000
                        gross = (price - decimal(pos["entry"])) * qty * pos["side"]
                        state["cash"] = str(decimal(state["cash"]) + gross - fee)
                        fill = {"kind": "close", "symbol": symbol, "side": pos["side"], "qty": str(qty),
                                "price": str(price), "fee": str(fee), "policy": pos["policy"], "ts": now,
                                "net_pnl_jpy": str(gross - fee - decimal(pos["entry_fee"]) - decimal(pos["financing"])),
                                "reason": "session_end" if force_flat else "risk_or_strategy"}
                        self.db.execute("INSERT INTO fills VALUES (?,?,?)", (digest([tick_id, symbol, "close"]), now, json.dumps(fill)))
                        del state["positions"][symbol]
                        state["blocked"].append(symbol)
                    continue
                if (not allow_entries or force_flat or state["risk_stopped"] or not direction
                        or len(state["positions"]) >= self.limits.max_positions
                        or q.ts <= state["last_entry"].get(symbol, 0)
                        or (self.market == "stocks" and (direction < 0 or symbol in state["blocked"]))):
                    continue
                unreal, deployed = self._mark(state, valid)
                equity = max(D(0), decimal(state["cash"]) + unreal)
                room = min(equity * decimal(self.limits.position_fraction),
                           equity * decimal(self.limits.allocation) - deployed)
                if self.market == "stocks":
                    room = min(room, decimal(self.limits.capital_jpy) - decimal(state["day_spent"]))
                px = decimal(q.ask if direction == 1 else q.bid)
                price = px * (1 + direction * decimal(self.limits.slippage_bps) / 10000)
                unit_cost = max(px, price) * (1 + decimal(self.limits.fee_bps) / 10000)
                lot = D(self.limits.lot)
                liquidity = decimal(q.ask_size if direction == 1 else q.bid_size)
                qty = (min(room / unit_cost, liquidity) / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
                if qty <= 0:
                    continue
                fee = qty * price * decimal(self.limits.fee_bps) / 10000
                state["cash"] = str(decimal(state["cash"]) - fee)
                state["day_spent"] = str(decimal(state["day_spent"]) + qty * price + fee)
                state["last_entry"][symbol] = q.ts
                state["positions"][symbol] = {"side": direction, "qty": str(qty), "entry": str(price),
                    "entry_fee": str(fee), "mark": q.bid if direction == 1 else q.ask, "mark_ts": q.ts,
                    "opened_at": now, "financed_at": now, "financing": "0", "policy": policy.version,
                    "stop_bps": policy.stop_bps, "take_bps": policy.take_bps, "max_hold_s": policy.max_hold_s}
                fill = {"kind": "open", "symbol": symbol, "side": direction, "qty": str(qty),
                        "price": str(price), "fee": str(fee), "policy": policy.version, "ts": now}
                self.db.execute("INSERT INTO fills VALUES (?,?,?)", (digest([tick_id, symbol, "open"]), now, json.dumps(fill)))
            unreal, deployed = self._mark(state, valid)
            equity = decimal(state["cash"]) + unreal
            state["peak"] = str(max(decimal(state["peak"]), equity))
            drawdown = (decimal(state["peak"]) - equity) / decimal(state["peak"])
            state["max_drawdown"] = str(max(decimal(state["max_drawdown"]), drawdown))
            state["last_ts"] = now
            # Raw ticks/metrics are bounded; fills, reports and job audit survive.
            # SQLite reuses freed pages, avoiding a periodic VACUUM stall.
            if now - state.get("pruned_at", 0) >= 3600:
                self.db.execute("DELETE FROM ticks WHERE id IN (SELECT id FROM ticks ORDER BY ts DESC LIMIT -1 OFFSET 20000)")
                self.db.execute("DELETE FROM metrics WHERE ts IN (SELECT ts FROM metrics ORDER BY ts DESC LIMIT -1 OFFSET 50000)")
                state["pruned_at"] = now
            metric = {"as_of": now, "equity_jpy": str(equity), "realized_jpy": str(decimal(state["cash"]) - decimal(self.limits.capital_jpy)),
                      "unrealized_jpy": str(unreal), "deployed_jpy": str(deployed), "policy": policy.version,
                      "max_drawdown": state["max_drawdown"], "positions": state["positions"],
                      "rejected_quotes": errors, "accepted_quotes": len(valid), "market_as_of": max((q.ts for q in valid.values()), default=0),
                      "pending_liquidation": bool(force_flat and state["positions"]), "risk_stopped": state["risk_stopped"]}
            self.db.execute("UPDATE account SET body=? WHERE id=1", (json.dumps(state),))
            self.db.execute("INSERT INTO ticks VALUES (?,?,?)", (tick_id, now, json.dumps([asdict(q) for q in quotes])))
            self.db.execute("INSERT OR REPLACE INTO metrics VALUES (?,?)", (now, json.dumps(metric)))
            self.db.commit()
            return metric
        except BaseException:
            self.db.rollback()
            raise

    def snapshot(self, *, now: float) -> dict:
        row = self.db.execute("SELECT body FROM metrics ORDER BY ts DESC LIMIT 1").fetchone()
        result = json.loads(row[0]) if row else {"as_of": 0, "positions": {}}
        result.update(market=self.market, title=TITLES[self.market], mode="paper",
                      stale=now - result.get("market_as_of", 0) > self.limits.quote_age_s,
                      recent_fills=[json.loads(r[0]) for r in self.db.execute("SELECT body FROM fills ORDER BY ts DESC LIMIT 12")])
        return result

    def report(self, end: float) -> dict:
        """Immutable cutoff report: FX [previous 03:00,03:00], stocks [09:00,10:00]."""
        end = stamp(end)
        begin = end - (3600 if self.market == "stocks" else 86400)
        key = f"{self.market}:{int(end)}"
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            existing = self.db.execute("SELECT body FROM reports WHERE id=?", (key,)).fetchone()
            if existing:
                return json.loads(existing[0])
            before = self.db.execute("SELECT ts,body FROM metrics WHERE ts<=? ORDER BY ts DESC LIMIT 1", (begin,)).fetchone()
            after = self.db.execute("SELECT ts,body FROM metrics WHERE ts<=? ORDER BY ts DESC LIMIT 1", (end,)).fetchone()
            first, last = (json.loads(before[1]) if before else None), (json.loads(after[1]) if after else None)
            fills = [json.loads(r[0]) for r in self.db.execute("SELECT body FROM fills WHERE ts>? AND ts<=? ORDER BY ts", (begin, end))]
            closed = [f for f in fills if f["kind"] == "close"]
            result = {"id": key, "market": self.market, "title": TITLES[self.market], "mode": "paper", "begin": begin, "end": end,
                      "complete": bool(first and last and end - last["as_of"] <= self.limits.quote_age_s
                                       and begin - first["as_of"] <= self.limits.quote_age_s
                                       and end - last.get("market_as_of", 0) <= self.limits.quote_age_s),
                      "period_pnl_jpy": str(decimal(last["equity_jpy"]) - decimal(first["equity_jpy"])) if first and last else None,
                      "closed_net_pnl_jpy": str(sum((decimal(f["net_pnl_jpy"]) for f in closed), D(0))),
                      "closed_trades": len(closed), "wins": sum(decimal(f["net_pnl_jpy"]) > 0 for f in closed),
                      "last": last, "fills": fills[-100:], "financing_model": "continuous_conservative_estimate"}
            self.db.execute("INSERT OR IGNORE INTO reports VALUES (?,?)", (key, json.dumps(result)))
            self.db.execute("INSERT OR IGNORE INTO jobs VALUES (?,?)", (key, json.dumps({"status": "pending", "report_id": key})))
            return json.loads(self.db.execute("SELECT body FROM reports WHERE id=?", (key,)).fetchone()[0])
