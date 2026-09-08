"""Continuously usable paper-only crypto worker orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..config import GlobalConfig
from .events import append_public_event, build_fill_event
from .ledger import PaperLedger
from .market_data import MarketFrame
from .paper import PaperBroker
from .relative_value import scan_relative_value_opportunities
from .risk import CapitalPolicy, allocate_opportunities
from .status import build_public_status, write_public_status
from .strategies import scan_opportunities, select_diversified_opportunities

D = Decimal
TIMEFRAME = "5m"
HISTORY_LIMIT = 24
BOOK_LIMIT = 20
ARB_MIN_EDGE_BPS = D("10")
ARB_PROBE_JPY = (D("1000"), D("3000"), D("10000"))


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
    frame_error_count: int = 0,
    arbitrage_candidate_count: int = 0,
    new_fill_count: int = 0,
    new_settlement_count: int = 0,
    error_codes=(),
    last_success_at: float | None = None,
):
    capital = D(str(g.trading.paper_capital_jpy))
    return build_public_status(
        worker_state=worker_state,
        last_cycle_at=now,
        eligible_symbols=eligible_symbols,
        capital_reference=capital,
        deployed_reference=ledger.deployed_reference(),
        open_positions=ledger.positions(),
        recent_fills=ledger.recent_fills(limit=20),
        skipped_reason_codes=list(skipped_reason_codes),
        signal_summary={
            "candidate_count": candidate_count,
            "selected_count": selected_count,
            "rejected_count": rejected_count,
            "strategy_ids": sorted(set(strategy_ids)),
            "candidate_reason_codes": sorted(set(candidate_reason_codes)),
        },
        worker_summary={
            "cycle_index": int(cycle_index),
            "last_success_at": last_success_at,
            "next_cycle_at": float(now) + g.trading.interval_s,
            "frame_error_count": int(frame_error_count),
            "arbitrage_candidate_count": int(arbitrage_candidate_count),
            "new_fill_count": int(new_fill_count),
            "new_settlement_count": int(new_settlement_count),
            "error_codes": list(error_codes),
        },
    )


def run_worker_cycle(
    g: GlobalConfig,
    *,
    gateway: Any,
    cycle_index: int,
    now: float,
    last_success_at: float | None = None,
) -> WorkerCycleResult:
    """Run one public-data strategy cycle and persist only paper evidence."""
    state_dir = g.state_dir / "trading"
    ledger = PaperLedger(state_dir / "paper.sqlite3")
    status_path = state_dir / "status.json"
    event_path = state_dir / "events.jsonl"
    try:
        running = _status_payload(
            g,
            ledger,
            worker_state="paper_worker_running",
            cycle_index=cycle_index,
            now=now,
            eligible_symbols=(),
            last_success_at=last_success_at,
        )
        write_public_status(status_path, running)

        markets = gateway.discover_markets()
        frames: dict[str, MarketFrame] = {}
        frame_error_count = 0
        for symbol in sorted(markets):
            try:
                batch = gateway.fetch_market_frames(
                    [symbol], timeframe=TIMEFRAME, limit=HISTORY_LIMIT, now=now
                )
                frame = batch.get(symbol) if isinstance(batch, dict) else None
                if not isinstance(frame, MarketFrame) or frame.symbol != symbol:
                    raise ValueError("public frame missing or mismatched")
                frames[symbol] = frame
            except Exception:
                # A bad public market is isolated; stale/cached values are never reused.
                frame_error_count += 1

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
        rejected_codes = [item.reason_code for item in selection.rejected]
        skipped_codes = rejected_codes + [item.reason_code for item in allocation.skipped]
        success_at = last_success_at if errors else float(now)
        state = "paper_worker_degraded" if errors else "paper_worker_idle"
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
            new_fill_count=new_fill_count,
            error_codes=errors,
            last_success_at=success_at,
        )
        write_public_status(status_path, payload)
        return WorkerCycleResult(
            cycle_index=cycle_index,
            worker_state=state,
            frame_error_count=frame_error_count,
            candidate_count=len(candidates),
            selected_count=len(selection.selected),
            arbitrage_candidate_count=0,
            new_fill_count=new_fill_count,
            new_settlement_count=0,
            error_codes=tuple(errors),
            last_success_at=success_at,
        )
    finally:
        ledger.close()
