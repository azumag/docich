"""Pure liquidity gate boundaries (D2)."""
from __future__ import annotations

import ast
from decimal import Decimal as D
from pathlib import Path

from docich.trading.depth import DepthBook, DepthLevel
from docich.trading.liquidity import (
    ACTIVITY_BARS,
    MAX_ENTRY_SPREAD_BPS,
    MIN_ACTIVE_BAR_FRACTION,
    active_bar_fraction,
    entry_block,
    spread_bps,
)
from docich.trading.market_data import MarketFrame


def book(bid: str = "100", ask: str = "100", *, amount: str = "1000") -> DepthBook:
    return DepthBook(
        "OAS/JPY",
        bids=(DepthLevel(D(bid), D(amount)),),
        asks=(DepthLevel(D(ask), D(amount)),),
        as_of=1_800_000_000.0,
    )


def frame(*volumes: str) -> MarketFrame:
    values = tuple(D(value) for value in volumes)
    return MarketFrame(
        symbol="OAS/JPY",
        timeframe_seconds=300,
        timestamps=tuple(1_800_000_000.0 + index * 300 for index in range(len(values))),
        closes=tuple(D("100") for _ in values),
        volumes=values,
    )


def test_constants_match_the_design() -> None:
    assert MAX_ENTRY_SPREAD_BPS == D("40")
    assert MIN_ACTIVE_BAR_FRACTION == D("0.5")
    assert ACTIVITY_BARS == 24


def test_spread_bps_uses_the_mid_price() -> None:
    # (101 - 99) / 100 * 10000 = 200 bps
    assert spread_bps(book("99", "101")) == D("200")


def test_spread_bps_is_none_without_a_usable_book() -> None:
    assert spread_bps(None) is None
    assert spread_bps(object()) is None
    assert spread_bps(book("101", "99")) is None  # crossed book is malformed


def test_spread_boundary_of_40_bps_passes() -> None:
    # Mid 1000, spread 4 -> (4 / 1000) * 10000 = 40 bps exactly.
    assert entry_block(book("998", "1002"), frame("1")) is None


def test_spread_above_40_bps_blocks() -> None:
    # Mid 1000 with a 4.1 spread -> 41 bps exactly.
    assert spread_bps(book("997.95", "1002.05")) == D("41.0000")
    assert entry_block(book("997.95", "1002.05"), frame("1")) == "spread_too_wide"


def test_active_bar_fraction_uses_the_requested_tail() -> None:
    assert active_bar_fraction(frame("0", "1", "0", "1"), bars=4) == D("0.5")
    assert active_bar_fraction(frame("0", "0", "1", "1"), bars=2) == D("1")
    assert active_bar_fraction(frame("0", "0"), bars=4) == D("0")


def test_active_bar_fraction_of_a_short_frame_uses_its_available_bars() -> None:
    # Production sends 25 bars; small live fixtures must not be called illiquid
    # merely because they carry only a few observations.
    assert active_bar_fraction(frame("1")) == D("1")
    assert active_bar_fraction(frame()) == D("0")


def test_half_active_bar_fraction_passes_and_below_blocks() -> None:
    half = frame(*(("1", "0") * 12))
    assert active_bar_fraction(half) == D("0.5")
    assert entry_block(book(), half) is None
    below = frame(*(("1", "0", "0", "0") * 6))
    assert active_bar_fraction(below) < D("0.5")
    assert entry_block(book(), below) == "illiquid"


def test_missing_book_wins_over_activity() -> None:
    assert entry_block(None, frame("0")) == "book_unavailable"


def test_full_entry_gate_accepts_the_boundaries() -> None:
    assert entry_block(book("998", "1002"), frame(*(("1", "0") * 12))) is None


def test_liquidity_module_imports_no_network_or_io_modules() -> None:
    source = Path(__file__).resolve().parents[1] / "src" / "docich" / "trading" / "liquidity.py"
    banned = {"socket", "ssl", "ccxt", "http", "urllib", "requests", "subprocess",
              "importlib", "asyncio", "pathlib", "os"}
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module.split(".")[0]]
        assert not (set(names) & banned), names
