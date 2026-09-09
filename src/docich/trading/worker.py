"""Continuously usable paper-only crypto worker orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math
from pathlib import Path
import time
from typing import Any

from ..config import GlobalConfig
from .arbitrage import TopOfBook, find_triangle_symbols, scan_triangular_arbitrage
from .events import append_public_event, build_fill_event, build_settlement_event
from .freshness import (
    QUALITY_FRESH,
    MarketFreshness,
    assess_market_freshness,
)
from .ledger import PaperLedger
from .market_cache import load_cache, prune_cache, save_cache, store_frames
from .market_data import MarketFrame
from .notifications import deliver_pending_notifications
from .paper import PaperBroker
from .relative_value import scan_relative_value_opportunities
from .risk import CapitalPolicy, allocate_opportunities
from .status import build_public_status, write_public_status
from .settlement import settlement_observation_id, simulate_multileg_settlement
from .strategies import scan_opportunities, select_diversified_opportunities

D = Decimal
TIMEFRAME = "5m"
HISTORY_LIMIT = 24
BOOK_LIMIT = 20
ARB_MIN_EDGE_BPS = D("10")
ARB_PROBE_JPY = (D("1000"), D("3000"), D("10000"))
# Per-request public-data timeout (seconds). Enforced by the gateway
# transport (ccxt); the worker enforces the cycle budget below.
FETCH_TIMEOUT_S = 10
# Whole-cycle public-fetch budget (seconds, monotonic). Intentionally longer
# than the default 60s cycle interval: one slow cycle degrades to partial
# coverage and the loop sleep clamps at zero instead of compounding delays.
# Budgets bound a single cycle's fetches; they never gate trading decisions.
CYCLE_BUDGET_S = 120


@dataclass(frozen=True)
class WorkerCycleResult:
    cycle_index: int
    worker_state: str
    frame_error_count: int
    candidate_count: int
    selected_count: int
    arbitrage_candidate_count: int
    new_fill_count: int
    new_settlement_count: int
    error_codes: tuple[str, ...]
    last_success_at: float | None


def _read_previous_status(status_path) -> dict[str, object]:
    """Best-effort previous snapshot. Malformed/absent files yield {} (clean start)."""
    import json as _json

    try:
        raw = _json.loads(Path(status_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _preserved_symbols(previous: dict[str, object]) -> list[str]:
    symbols = previous.get("eligible_symbols")
    if not isinstance(symbols, list):
        return []
    return sorted({str(symbol) for symbol in symbols if str(symbol).strip()})


def _preserved_summary(previous: dict[str, object], key: str) -> dict[str, object]:
    summary = previous.get(key)
    return dict(summary) if isinstance(summary, dict) else {}


def _preserved_snapshot(previous: dict[str, object]) -> tuple[int | None, float | None, dict, dict]:
    seq = previous.get("snapshot_seq")
    generated = previous.get("snapshot_generated_at")
    freshness = previous.get("market_freshness")
    coverage = previous.get("coverage")
    seq_value: int | None = None
    if type(seq) is int:
        seq_value = seq
    generated_value: float | None = None
    if isinstance(generated, (int, float)) and not isinstance(generated, bool):
        candidate = float(generated)
        if math.isfinite(candidate):
            generated_value = candidate
    return (
        seq_value,
        generated_value,
        dict(freshness) if isinstance(freshness, dict) else {},
        dict(coverage) if isinstance(coverage, dict) else {},
    )


def _heartbeat_payload(
    g: GlobalConfig,
    ledger: PaperLedger,
    *,
    cycle_index: int,
    now: float,
    previous: dict[str, object],
) -> dict[str, object]:
    """Liveness heartbeat that preserves the last success snapshot.

    Unlike a fresh empty status, this never zeroes market_count or drops
    the last signal summary: readers keep seeing the last good observation
    until a newer successful batch replaces it.
    """
    capital = D(str(g.trading.paper_capital_jpy))
    seq, generated, freshness, coverage = _preserved_snapshot(previous)
    return build_public_status(
        worker_state="paper_worker_running",
        last_cycle_at=now,
        eligible_symbols=_preserved_symbols(previous),
        capital_reference=capital,
        deployed_reference=ledger.deployed_reference(),
        open_positions=ledger.positions(),
        recent_fills=ledger.recent_fills(limit=20),
        skipped_reason_codes=[],
        signal_summary=_preserved_summary(previous, "signal_summary") or None,
        worker_summary={**_preserved_summary(previous, "worker_summary"), "cycle_index": int(cycle_index)},
        heartbeat_at=now,
        snapshot_seq=seq,
        snapshot_generated_at=generated,
        market_freshness=freshness,
        coverage=coverage,
    )


def _rotation_order(symbols: list[str], cycle_index: int) -> list[str]:
    """Round-robin market order so an over-budget cycle cannot starve a symbol."""
    ordered = sorted(symbols)
    if not ordered:
        return ordered
    offset = max(0, int(cycle_index) - 1) % len(ordered)
    return ordered[offset:] + ordered[:offset]


def _status_payload(
    g: GlobalConfig,
    ledger: PaperLedger,
    *,
    worker_state: str,
    cycle_index: int,
    now: float,
    eligible_symbols,
    candidate_count: int = 0,
    selected_count: int = 0,
    rejected_count: int = 0,
    strategy_ids=(),
    candidate_reason_codes=(),
    skipped_reason_codes=(),
    signal_summary=None,
    worker_summary=None,
    frame_error_count: int = 0,
    arbitrage_candidate_count: int = 0,
    new_fill_count: int = 0,
    new_settlement_count: int = 0,
    error_codes=(),
    last_success_at: float | None = None,
):
    capital = D(str(g.trading.paper_capital_jpy))
    if signal_summary is None:
        signal_summary = {
            "candidate_count": candidate_count,
            "selected_count": selected_count,
            "rejected_count": rejected_count,
            "strategy_ids": sorted(set(strategy_ids)),
            "candidate_reason_codes": sorted(set(candidate_reason_codes)),
        }
    if worker_summary is None:
        worker_summary = {
            "cycle_index": int(cycle_index),
            "last_success_at": last_success_at,
            "next_cycle_at": float(now) + g.trading.interval_s,
            "frame_error_count": int(frame_error_count),
            "arbitrage_candidate_count": int(arbitrage_candidate_count),
            "new_fill_count": int(new_fill_count),
            "new_settlement_count": int(new_settlement_count),
            "error_codes": list(error_codes),
        }
    return build_public_status(
        worker_state=worker_state,
        last_cycle_at=now,
        eligible_symbols=eligible_symbols,
        capital_reference=capital,
        deployed_reference=ledger.deployed_reference(),
        open_positions=ledger.positions(),
        recent_fills=ledger.recent_fills(limit=20),
        skipped_reason_codes=list(skipped_reason_codes),
        signal_summary=signal_summary,
        worker_summary=worker_summary,
    )


def _run_arbitrage_phase(
    *,
    gateway: Any,
    markets,
    ledger: PaperLedger,
    event_path,
    now: float,
    observation_now_fn=time.time,
) -> tuple[int, int, list[str]]:
    """Scan and persist complete public-data multi-leg observations."""
    triangle_symbols = find_triangle_symbols(markets)
    if not triangle_symbols:
        return 0, 0, []

    pending = []
    try:
        circuit_statuses = gateway.fetch_circuit_break_statuses(triangle_symbols)
        depth_books = gateway.fetch_depth_books(triangle_symbols, now=now, limit=BOOK_LIMIT)
        expected = set(triangle_symbols)
        if set(circuit_statuses) != expected or set(depth_books) != expected:
            raise ValueError("arbitrage public data set is incomplete")
        # Freshness must use a local clock sampled *after* all public fetches.
        # Exchange timestamps may legitimately advance while the requests are in flight.
        observed_at = float(observation_now_fn())
        top_books = {
            symbol: TopOfBook(
                symbol,
                book.bids[0].price,
                book.asks[0].price,
                book.as_of,
                bid_amount=book.bids[0].amount,
                ask_amount=book.asks[0].amount,
            )
            for symbol, book in depth_books.items()
        }
        routes = scan_triangular_arbitrage(
            markets, top_books, now=observed_at, min_net_edge_bps=ARB_MIN_EDGE_BPS
        )
        jpy_routes = [
            route for route in routes
            if "JPY" in {leg.from_asset for leg in route.legs}
        ]
        for route in jpy_routes:
            for amount in ARB_PROBE_JPY:
                result = simulate_multileg_settlement(
                    route, depth_books, markets, circuit_statuses,
                    start_amount=amount, start_asset="JPY", now=observed_at,
                )
                pending.append((
                    settlement_observation_id(
                        route, result, depth_books, circuit_statuses, markets
                    ),
                    result,
                    observed_at,
                ))
    except Exception:
        # Never persist a partial arbitrage observation set from a failed fetch/scan.
        return 0, 0, ["arbitrage_data_error"]

    new_count = 0
    for settlement_id, result, observed_at in pending:
        recorded = ledger.record_multileg_settlement(
            settlement_id, result, observed_at=observed_at
        )
        if append_public_event(event_path, build_settlement_event(recorded)):
            new_count += 1
    return len({item[1].route_id for item in pending}), new_count, []


def _carry_snapshot(
    payload: dict[str, object],
    seq: int | None,
    generated: float | None,
    freshness: dict,
    coverage: dict,
) -> None:
    """Keep the previous snapshot identity: old values are never republished
    as new. Only a successful market batch or ledger update advances it."""
    payload["snapshot_seq"] = seq
    payload["snapshot_generated_at"] = generated
    payload["market_freshness"] = freshness
    payload["coverage"] = coverage


def run_worker_cycle(
    g: GlobalConfig,
    *,
    gateway: Any,
    cycle_index: int,
    now: float,
    last_success_at: float | None = None,
    observation_now_fn=time.time,
    monotonic_fn=time.monotonic,
) -> WorkerCycleResult:
    """Run one public-data strategy cycle and persist only paper evidence.

    The cycle heartbeat preserves the last success snapshot; only a
    successful market batch (or ledger update) advances the snapshot
    sequence. Symbols share a monotonic fetch budget and rotate each cycle
    so an over-budget cycle degrades to partial coverage instead of
    blocking.
    """
    state_dir = g.state_dir / "trading"
    ledger = PaperLedger(state_dir / "paper.sqlite3")
    status_path = state_dir / "status.json"
    event_path = state_dir / "events.jsonl"
    previous = _read_previous_status(status_path)
    prev_seq, prev_generated, prev_freshness, prev_coverage = _preserved_snapshot(previous)
    market_cache = load_cache((state_dir / "market_cache.json"))
    prune_cache(market_cache, now=now)
    try:
        write_public_status(
            status_path,
            _heartbeat_payload(g, ledger, cycle_index=cycle_index, now=now, previous=previous),
        )

        try:
            markets = gateway.discover_markets()
        except Exception:
            markets = None
        if not isinstance(markets, dict) or not markets:
            prev_worker_summary = _preserved_summary(previous, "worker_summary")
            prev_worker_summary["cycle_index"] = int(cycle_index)
            prev_worker_summary["error_codes"] = ["market_discovery_error"]
            payload = _status_payload(
                g, ledger, worker_state="paper_worker_degraded", cycle_index=cycle_index,
                now=now, eligible_symbols=_preserved_symbols(previous),
                skipped_reason_codes=[],
                signal_summary=_preserved_summary(previous, "signal_summary") or None,
                worker_summary=prev_worker_summary,
                error_codes=("market_discovery_error",), last_success_at=last_success_at,
            )
            _carry_snapshot(payload, prev_seq, prev_generated, prev_freshness, prev_coverage)
            payload["heartbeat_at"] = float(now)
            write_public_status(status_path, payload)
            return WorkerCycleResult(
                cycle_index=cycle_index, worker_state="paper_worker_degraded",
                frame_error_count=0, candidate_count=0, selected_count=0,
                arbitrage_candidate_count=0, new_fill_count=0, new_settlement_count=0,
                error_codes=("market_discovery_error",), last_success_at=last_success_at,
            )

        ordered = _rotation_order([str(symbol) for symbol in markets], cycle_index)
        fetch_deadline = float(monotonic_fn()) + CYCLE_BUDGET_S
        frames: dict[str, MarketFrame] = {}
        freshness: dict[str, dict[str, object]] = {}
        frame_error_count = 0
        attempted = 0
        budget_exceeded = False
        for symbol in ordered:
            if float(monotonic_fn()) >= fetch_deadline:
                budget_exceeded = True
                break
            attempted += 1
            try:
                batch = gateway.fetch_market_frames(
                    [symbol], timeframe=TIMEFRAME, limit=HISTORY_LIMIT, now=now
                )
                fetched_at = float(observation_now_fn())
                frame = batch.get(symbol) if isinstance(batch, dict) else None
                if not isinstance(frame, MarketFrame) or frame.symbol != symbol:
                    raise ValueError("public frame missing or mismatched")
                assessment = assess_market_freshness(
                    symbol, fetched_at=fetched_at, data_as_of=frame.as_of,
                    timeframe_s=frame.timeframe_seconds,
                    last_bar_start=frame.timestamps[-1] if frame.timestamps else None,
                    now=now,
                )
                freshness[symbol] = assessment.payload()
                if assessment.quality != QUALITY_FRESH:
                    frame_error_count += 1
                    continue
                frames[symbol] = frame
                store_frames(
                    market_cache, symbol, fetched_at=fetched_at, data_as_of=frame.as_of,
                    last_bar_start=frame.timestamps[-1] if frame.timestamps else None,
                    timeframe_s=frame.timeframe_seconds, last_close=str(frame.last_price),
                    closes=[str(value) for value in frame.closes], now=now,
                )
            except Exception:
                # A bad public market is isolated; cached values are never
                # reused for trading decisions, only for display/history.
                frame_error_count += 1
                freshness[symbol] = assess_market_freshness(
                    symbol, fetched_at=None, data_as_of=None, timeframe_s=None,
                    last_bar_start=None, now=now, error="fetch_error",
                ).payload()
        if budget_exceeded:
            for symbol in ordered[attempted:]:
                freshness[symbol] = assess_market_freshness(
                    symbol, fetched_at=None, data_as_of=None, timeframe_s=None,
                    last_bar_start=None, now=now, attempted=False,
                ).payload()
        save_cache((state_dir / "market_cache.json"), market_cache)

        candidates = tuple(scan_opportunities(frames, now=now)) + tuple(
            scan_relative_value_opportunities(frames, markets, now=now)
        )
        selection = select_diversified_opportunities(candidates, frames)
        prices = {symbol: frame.last_price for symbol, frame in frames.items()}
        capital = D(str(g.trading.paper_capital_jpy))
        deployed_before = ledger.deployed_reference()
        available_jpy = max(D("0"), capital - deployed_before)
        allocation = allocate_opportunities(
            selection.selected,
            markets=markets,
            prices=prices,
            quote_to_reference={"JPY": D("1")},
            available_quote={"JPY": available_jpy},
            capital_reference=capital,
            deployed_reference=deployed_before,
            policy=CapitalPolicy(),
            now=now,
        )

        broker = PaperBroker(ledger)
        new_fill_count = 0
        for decision in allocation.decisions:
            fill = broker.fill(decision, timestamp=now)
            if append_public_event(event_path, build_fill_event(fill)):
                new_fill_count += 1

        errors: list[str] = []
        if frame_error_count:
            errors.append("frame_fetch_error")
        if budget_exceeded:
            errors.append("cycle_budget_exceeded")
        arbitrage_candidate_count = 0
        new_settlement_count = 0
        # The budget is checked at the arbitrage gate only: depth/circuit
        # fetches inside the phase are bounded per request by the gateway
        # transport timeout (ccxt), not preempted mid-phase.
        if float(monotonic_fn()) >= fetch_deadline:
            errors.append("arbitrage_skipped_budget")
        else:
            arbitrage_candidate_count, new_settlement_count, arbitrage_errors = _run_arbitrage_phase(
                gateway=gateway, markets=markets, ledger=ledger, event_path=event_path, now=now,
                observation_now_fn=observation_now_fn,
            )
            errors.extend(arbitrage_errors)
        rejected_codes = [item.reason_code for item in selection.rejected]
        skipped_codes = rejected_codes + [item.reason_code for item in allocation.skipped]
        # Two distinct success notions (documented, not a contradiction):
        # - last_success_at: last fully-clean cycle (no errors). Monitoring use.
        # - snapshot_generated_at: last cycle that delivered a successful
        #   market batch or ledger update, including partial ones. Display use:
        #   a partial batch is still newer data than a frozen snapshot.
        success_at = last_success_at if errors else float(now)
        state = "paper_worker_degraded" if errors else "paper_worker_idle"
        snapshot_seq = prev_seq
        snapshot_generated = prev_generated
        if frames or new_fill_count or new_settlement_count:
            snapshot_seq = (prev_seq or 0) + 1
            snapshot_generated = float(now)
        coverage = {
            "attempted": attempted,
            "total": len(ordered),
            "carried_symbols": sorted(set(_preserved_symbols(previous)) - set(frames)),
            "budget_exceeded": budget_exceeded,
        }
        payload = _status_payload(
            g,
            ledger,
            worker_state=state,
            cycle_index=cycle_index,
            now=now,
            eligible_symbols=markets.keys(),
            candidate_count=len(candidates),
            selected_count=len(selection.selected),
            rejected_count=len(selection.rejected),
            strategy_ids=[item.strategy_id for item in candidates],
            candidate_reason_codes=[item.reason_code for item in candidates],
            skipped_reason_codes=skipped_codes,
            frame_error_count=frame_error_count,
            arbitrage_candidate_count=arbitrage_candidate_count,
            new_fill_count=new_fill_count,
            new_settlement_count=new_settlement_count,
            error_codes=errors,
            last_success_at=success_at,
        )
        payload["heartbeat_at"] = float(now)
        payload["snapshot_seq"] = snapshot_seq
        payload["snapshot_generated_at"] = snapshot_generated
        payload["market_freshness"] = freshness
        payload["coverage"] = coverage
        write_public_status(status_path, payload)
        return WorkerCycleResult(
            cycle_index=cycle_index,
            worker_state=state,
            frame_error_count=frame_error_count,
            candidate_count=len(candidates),
            selected_count=len(selection.selected),
            arbitrage_candidate_count=arbitrage_candidate_count,
            new_fill_count=new_fill_count,
            new_settlement_count=new_settlement_count,
            error_codes=tuple(errors),
            last_success_at=success_at,
        )
    finally:
        ledger.close()

def _write_cycle_failure_status(
    g: GlobalConfig, *, cycle_index: int, now: float, last_success_at: float | None
) -> None:
    state_dir = g.state_dir / "trading"
    ledger = PaperLedger(state_dir / "paper.sqlite3")
    try:
        previous = _read_previous_status(state_dir / "status.json")
        seq, generated, freshness, _coverage = _preserved_snapshot(previous)
        prev_worker_summary = _preserved_summary(previous, "worker_summary")
        prev_worker_summary["cycle_index"] = int(cycle_index)
        prev_worker_summary["error_codes"] = ["worker_cycle_error"]
        payload = _status_payload(
            g, ledger, worker_state="paper_worker_degraded", cycle_index=cycle_index,
            now=now, eligible_symbols=_preserved_symbols(previous),
            skipped_reason_codes=[],
            signal_summary=_preserved_summary(previous, "signal_summary") or None,
            worker_summary=prev_worker_summary,
            error_codes=("worker_cycle_error",),
            last_success_at=last_success_at,
        )
        _carry_snapshot(payload, seq, generated, freshness, {})
        payload["heartbeat_at"] = float(now)
        write_public_status(state_dir / "status.json", payload)
    finally:
        ledger.close()


def _default_gateway_factory():
    from .exchanges.bitbank_ccxt import BitbankPublicGateway

    return BitbankPublicGateway(timeout_ms=FETCH_TIMEOUT_S * 1000)


def run_paper_worker(
    g: GlobalConfig,
    *,
    gateway_factory=_default_gateway_factory,
    sleep_fn=time.sleep,
    now_fn=time.time,
    max_cycles: int | None = None,
) -> None:
    """Run paper cycles forever; `max_cycles` is only for deterministic tests."""
    if max_cycles is not None and (type(max_cycles) is not int or max_cycles <= 0):
        raise ValueError("max_cycles must be a positive integer or None")
    cycle_index = 0
    last_success_at: float | None = None
    gateway = None
    while True:
        cycle_index += 1
        started_at = float(now_fn())
        try:
            if gateway is None:
                gateway = gateway_factory()
            result = run_worker_cycle(
                g, gateway=gateway, cycle_index=cycle_index, now=started_at,
                last_success_at=last_success_at,
            )
            last_success_at = result.last_success_at
            if g.trading.notifications_enabled:
                try:
                    notification_at = float(now_fn())
                    deliver_pending_notifications(g, now=notification_at)
                except Exception:
                    # Viewer-output failures are isolated from market-data/trading cycles.
                    print("[trading] notification delivery error", flush=True)
        except Exception as exc:
            # Internal logs may contain public-endpoint exception text; public JSON never does.
            print(f"[trading] paper worker cycle error: {exc}", flush=True)
            gateway = None
            _write_cycle_failure_status(
                g, cycle_index=cycle_index, now=started_at, last_success_at=last_success_at
            )
        if max_cycles is not None and cycle_index >= max_cycles:
            return
        ended_at = float(now_fn())
        sleep_fn(max(0.0, float(g.trading.interval_s) - max(0.0, ended_at - started_at)))
