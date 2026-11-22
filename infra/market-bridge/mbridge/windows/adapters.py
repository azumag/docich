"""Read-only kabu / MT5 adapters.

The vendor client is always injected.  Only quote/registration methods are
called: there is no order method to reach, and the source of this file is
scanned by the no-order-path regression test.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from ..common.contract import (Candidate, Quote, symbol_ok)
from ..common.timeutil import Caller, DeadlineExceeded

JST = ZoneInfo("Asia/Tokyo")
MAX_KABU_REGISTRATIONS = 50


@dataclass(frozen=True)
class AdapterReading:
    status: str
    reason: str
    market_state: str
    source_ts: float
    received_ts: float
    sequence: int
    quotes: tuple[Quote, ...] = ()
    candidates: tuple[Candidate, ...] = ()


def plan_registrations(held: tuple[str, ...], focused: tuple[str, ...],
                       ranking: tuple[str, ...], *, limit: int = MAX_KABU_REGISTRATIONS) -> tuple[str, ...]:
    """Holdings first, then focused, then ranking; never exceed the kabu slot cap.

    This only plans the symbols *we* register.  It never removes another
    client's registrations.
    """
    if limit < 0:
        raise ValueError("invalid_registration_limit")
    ordered: list[str] = []
    seen: set[str] = set()
    for symbol in (*held, *focused, *ranking):
        if not symbol_ok(symbol, "stocks") or symbol in seen:
            continue
        seen.add(symbol)
        ordered.append(symbol)
        if len(ordered) >= limit:
            break
    return tuple(ordered[:limit])


class KabuAdapter:
    """kabu station reader: 50-slot budget, WARMING_UP, fail-closed token."""

    source = "kabu-station"

    def __init__(self, client: Any, *, held_symbols: tuple[str, ...] = (),
                 source_epoch: str = "kabu-win-01", generation: int = 1,
                 clock: Callable[[], float] = time.time,
                 max_registrations: int = MAX_KABU_REGISTRATIONS,
                 preopen_end_hour: int = 9) -> None:
        self.client = client
        self.held_symbols = tuple(held_symbols)
        self.source_epoch = source_epoch
        self.generation = generation
        self.clock = clock
        self.max_registrations = max_registrations
        self.preopen_end_hour = preopen_end_hour
        self._sequence = 0
        self._registered: tuple[str, ...] = ()

    def _jst_hour(self, now: float) -> int:
        return datetime.fromtimestamp(now, JST).hour

    def set_subscriptions(self, symbols: tuple[str, ...], *, focused: tuple[str, ...] = ()) -> tuple[str, ...]:
        if not isinstance(symbols, (tuple, list)):
            raise ValueError("invalid_subscriptions")
        plan = plan_registrations(tuple(symbols), tuple(focused), (),
                                  limit=self.max_registrations)
        # Additive registration only.  No unregister_all / reset operation exists.
        self.client.register(plan)
        self._registered = plan
        return plan

    def read(self, now: float | None = None) -> AdapterReading:
        now = self.clock() if now is None else float(now)
        received = now
        self._sequence += 1
        empty = AdapterReading(status="unavailable", reason="upstream_unavailable",
                               market_state="unknown", source_ts=0.0,
                               received_ts=received, sequence=self._sequence)

        # Token validity is a hard gate: a valid login is not implied by process life.
        try:
            valid = bool(self.client.token_valid(now))
        except Exception:
            return empty
        if not valid:
            return AdapterReading(status="unavailable", reason="token_expired",
                                  market_state="unknown", source_ts=0.0,
                                  received_ts=received, sequence=self._sequence)

        try:
            rows = list(self.client.ranking(now))
        except Exception:
            return empty

        preopen = self._jst_hour(now) < self.preopen_end_hour
        if not rows and preopen:
            # Rankings are cleared overnight and stay empty until just after 09:00.
            # This is a warm-up state, explicitly NOT a connection failure.
            return AdapterReading(status="warming_up", reason="preopen_empty_ranking",
                                  market_state="preopen", source_ts=0.0,
                                  received_ts=received, sequence=self._sequence)

        symbols = self._registered or plan_registrations(
            self.held_symbols, (), tuple(str(r.get("symbol", "")) for r in rows),
            limit=self.max_registrations)
        quotes: list[Quote] = []
        newest = 0.0
        for symbol in symbols:
            try:
                item = self.client.quote(symbol, now)
            except Exception:
                return empty
            if not isinstance(item, dict) or not _numbers(item):
                return AdapterReading(status="unavailable", reason="upstream_error",
                                      market_state="unknown", source_ts=0.0,
                                      received_ts=received, sequence=self._sequence)
            source_ts = float(item["source_ts"])
            newest = max(newest, source_ts)
            quotes.append(Quote(symbol=symbol, bid=float(item["bid"]), ask=float(item["ask"]),
                                bid_size=float(item.get("bid_size", 0.0)),
                                ask_size=float(item.get("ask_size", 0.0)),
                                source_ts=source_ts, received_ts=received,
                                sequence=self._sequence))
        candidates = tuple(
            Candidate(symbol=str(r.get("symbol", "")), rank=int(r.get("rank", i + 1)),
                      score=float(r.get("score", 0.0)), source_ts=float(r.get("source_ts", 0.0)))
            for i, r in enumerate(rows[:self.max_registrations])
            if symbol_ok(r.get("symbol"), "stocks"))
        return AdapterReading(status="ready", reason="ok",
                              market_state="preopen" if preopen else "open",
                              source_ts=newest, received_ts=received,
                              sequence=self._sequence, quotes=tuple(quotes),
                              candidates=candidates)


def _numbers(item: dict) -> bool:
    keys = ("bid", "ask", "source_ts")
    try:
        return all(isinstance(item[k], (int, float)) for k in keys)
    except (KeyError, TypeError):
        return False


@dataclass(frozen=True)
class Mt5ChildResult:
    status: str
    reason: str
    initialized: bool = False
    server_verified: bool = False
    account_verified: bool = False
    symbols_ready: bool = False
    trade_allowed: bool = False  # published for diagnostics only; never required
    quotes: tuple[Quote, ...] = ()
    source_ts: float = 0.0


def decode_child_result(data: Any) -> Mt5ChildResult:
    if not isinstance(data, dict):
        return Mt5ChildResult(status="upstream_error", reason="upstream_error")
    quotes: list[Quote] = []
    for item in data.get("quotes", []) or []:
        if not isinstance(item, dict) or not symbol_ok(item.get("symbol"), "fx"):
            continue
        try:
            quotes.append(Quote(
                symbol=item["symbol"], bid=float(item["bid"]), ask=float(item["ask"]),
                bid_size=float(item.get("bid_size", 0.0)), ask_size=float(item.get("ask_size", 0.0)),
                source_ts=float(item["source_ts"]), received_ts=float(item.get("received_ts", 0.0)),
                sequence=int(item.get("sequence", 0))))
        except (KeyError, TypeError, ValueError):
            continue
    return Mt5ChildResult(
        status=str(data.get("status", "upstream_error")),
        reason=str(data.get("reason", "upstream_error")),
        initialized=bool(data.get("initialized", False)),
        server_verified=bool(data.get("server_verified", False)),
        account_verified=bool(data.get("account_verified", False)),
        symbols_ready=bool(data.get("symbols_ready", False)),
        trade_allowed=bool(data.get("trade_allowed", False)),
        quotes=tuple(quotes), source_ts=float(data.get("source_ts", 0.0) or 0.0))


class Mt5Adapter:
    """MT5 reader whose initialize() runs in an isolated, bounded child process."""

    source = "oanda-mt5-demo"

    def __init__(self, *, symbols: tuple[str, ...], expected_server: str,
                 expected_login: int, source_epoch: str = "mt5-win-01",
                 generation: int = 1, launcher: Callable[[], dict] | None = None,
                 deadline_s: float = 3.0, clock: Callable[[], float] = time.time,
                 caller: Caller | None = None, terminal_path: str = "") -> None:
        self.symbols = tuple(symbols)
        self.expected_server = expected_server
        self.expected_login = expected_login
        self.source_epoch = source_epoch
        self.generation = generation
        self.launcher = launcher
        self.deadline_s = deadline_s
        self.clock = clock
        self._caller = caller or Caller(workers=2, name="mbridge-mt5")
        self._owns_caller = caller is None
        self.terminal_path = terminal_path
        self._sequence = 0

    def close(self) -> None:
        if self._owns_caller:
            self._caller.close(wait=False)

    def _launch(self) -> dict:
        if self.launcher is not None:
            return self.launcher()
        payload = json.dumps({
            "symbols": self.symbols, "expected_server": self.expected_server,
            "expected_login": self.expected_login, "terminal_path": self.terminal_path,
        })
        # A fresh child per read: initialize() may hang or leave terminal state.
        proc = subprocess.run([sys.executable, "-m", "mbridge.windows.mt5_child"],
                              input=payload, text=True, capture_output=True,
                              timeout=self.deadline_s, check=False)
        try:
            return json.loads(proc.stdout or "{}")
        except (ValueError, TypeError):
            return {"status": "upstream_error", "reason": "upstream_error"}

    def read(self, now: float | None = None) -> AdapterReading:
        now = self.clock() if now is None else float(now)
        self._sequence += 1
        unavailable = lambda reason: AdapterReading(
            status="unavailable", reason=reason, market_state="unknown",
            source_ts=0.0, received_ts=now, sequence=self._sequence)
        try:
            result = decode_child_result(
                self._caller.call(self._launch, timeout_s=self.deadline_s))
        except DeadlineExceeded:
            return unavailable("adapter_hang")
        except Exception:
            return unavailable("upstream_error")
        if result.status == "initialize_timeout":
            return unavailable("adapter_hang")
        if result.status == "sdk_unavailable":
            return unavailable("upstream_unavailable")
        if not result.initialized:
            return unavailable("terminal_disconnected")
        if not (result.server_verified and result.account_verified and result.symbols_ready):
            return unavailable("wrong_terminal")
        # trade_allowed is intentionally ignored: market data must be readable
        # even when real trading is disabled in the terminal.
        quotes = tuple(q for q in result.quotes if now - q.source_ts <= 15.0
                       and q.source_ts <= now + 2.0)
        if not quotes:
            return unavailable("upstream_unavailable")
        return AdapterReading(status="ready", reason="ok", market_state="open",
                              source_ts=max(q.source_ts for q in quotes), received_ts=now,
                              sequence=self._sequence, quotes=quotes)
