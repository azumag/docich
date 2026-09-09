"""Terminal dashboard for the PAPER trading corner (P0-3, Issue #198).

Renders the 960x540 program view as 80x24 text through the existing CLI
presentation path (xterm + contain). Pure、日本語, no candles: only stored
closing prices are drawn (ASCII sparkline), forming/unconfirmed bars are
labeled, and missing data is shown as missing — never invented.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import time
import unicodedata
from pathlib import Path
from typing import Mapping

COLS = 80
ROWS = 24

_SPARK_LEVELS = "▁▂▃▄▅▆▇█"
_MISSING = "―"

HEADER_TITLE = "PAPER 暗号資産コーナー"


def _width(text: str) -> int:
    total = 0
    for char in str(text):
        total += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return total


def _fit(text: str, width: int) -> str:
    out: list[str] = []
    used = 0
    for char in str(text):
        char_width = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        if used + char_width > width:
            break
        out.append(char)
        used += char_width
    line = "".join(out)
    return line + " " * (width - used)


def _jst_hhmmss(epoch: float | None) -> str:
    if epoch is None:
        return "--:--:--"
    try:
        moment = _dt.datetime.fromtimestamp(float(epoch), tz=_dt.timezone.utc).astimezone(
            _dt.timezone(_dt.timedelta(hours=9), "JST")
        )
    except (TypeError, ValueError, OverflowError, OSError):
        return "--:--:--"
    return moment.strftime("%H:%M:%S")


def sparkline(values: list[float]) -> str:
    """ASCII sparkline from stored closes only. Flat input stays flat."""
    finite = [float(value) for value in values
              if isinstance(value, (int, float)) and not isinstance(value, bool)
              and math.isfinite(value)]
    if not finite:
        return _MISSING * 8
    low, high = min(finite), max(finite)
    if high <= low:
        return "▅" * min(24, len(finite))
    span = high - low
    marks = [_SPARK_LEVELS[min(7, int((value - low) / span * 8))] for value in finite]
    return "".join(marks[-24:])


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    if not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _positions(snapshot: Mapping[str, object]) -> list[tuple[str, str]]:
    positions = snapshot.get("open_positions")
    if not isinstance(positions, dict):
        return []
    return sorted(
        ((str(symbol), str(amount)) for symbol, amount in positions.items() if str(amount) != "0"),
        key=lambda item: item[0],
    )


def _fills(snapshot: Mapping[str, object]) -> list[dict]:
    fills = snapshot.get("recent_fills")
    if not isinstance(fills, list):
        return []
    return [fill for fill in fills if isinstance(fill, dict)][:3]


def _fresh_count(snapshot: Mapping[str, object]) -> tuple[int, int]:
    freshness = snapshot.get("market_freshness")
    if not isinstance(freshness, dict):
        return 0, 0
    fresh = sum(1 for entry in freshness.values()
                if isinstance(entry, dict) and entry.get("quality") == "fresh")
    return fresh, len(freshness)


def _focus_symbol(snapshot: Mapping[str, object]) -> str | None:
    """Deterministic focus: largest position, else first eligible symbol."""
    positions = _positions(snapshot)
    if positions:
        return positions[0][0]
    eligible = snapshot.get("eligible_symbols")
    if isinstance(eligible, list) and eligible:
        return str(eligible[0])
    return None


def render_dashboard(
    snapshot: Mapping[str, object] | None,
    closes: Mapping[str, list[float]],
    *,
    now: float,
    remaining_s: float | None = None,
) -> str:
    """Render the full 80x24 dashboard. Never raises on malformed input."""
    lines: list[str] = []
    try:
        return _render(snapshot or {}, closes or {}, now=now, remaining_s=remaining_s)
    except Exception:
        lines = [_fit(f"{HEADER_TITLE} | 表示を準備しています", COLS)]
        while len(lines) < ROWS:
            lines.append(" " * COLS)
        return "\n".join(lines[:ROWS])


def _render(snapshot: Mapping, closes: Mapping, *, now: float, remaining_s: float | None) -> str:
    lines: list[str] = []
    generated = _as_float(snapshot.get("snapshot_generated_at"))
    remaining = "--分" if remaining_s is None else f"{max(0, int(remaining_s // 60))}分"
    lines.append(_fit(f"{HEADER_TITLE} | {_jst_hhmmss(now)} JST | 残り{remaining} | 基準{_jst_hhmmss(generated)}", COLS))

    capital = snapshot.get("capital_reference", "?")
    deployed = snapshot.get("deployed_reference", "?")
    positions = _positions(snapshot)
    fresh, total = _fresh_count(snapshot)
    coverage_note = f" | 取得{fresh}/{total}" if total else " | 取得なし"
    lines.append(_fit(f"資金 {capital}円 投入 {deployed}円 保有 {len(positions)}銘柄{coverage_note}", COLS))

    focus = _focus_symbol(snapshot)
    if focus is None:
        lines.append(_fit("観測対象がありません（市場データ待ち）", COLS))
        lines.append(_fit("", COLS))
    else:
        series = [value for value in (closes.get(focus) or []) if isinstance(value, (int, float))]
        first = f"{series[0]:,.4f}".rstrip("0").rstrip(".") if series else "?"
        last = f"{series[-1]:,.4f}".rstrip("0").rstrip(".") if series else "?"
        lines.append(_fit(f"注目 {focus} {first} → {last}（5分足{len(series)}本 約2時間・終値のみ）", COLS))
        lines.append(_fit(sparkline([float(value) for value in series]), COLS))

    summary = snapshot.get("signal_summary")
    candidates = skipped = 0
    reasons: list[str] = []
    if isinstance(summary, dict):
        try:
            candidates = int(summary.get("candidate_count", 0) or 0)
        except (TypeError, ValueError):
            candidates = 0
        codes = summary.get("candidate_reason_codes")
        if isinstance(codes, list):
            reasons = [str(code) for code in codes[:3]]
    lines.append(_fit(f"BOT判断 候補{candidates}件" + (f" 主因:{','.join(reasons)}" if reasons else "（条件未達・見送り）"), COLS))
    skipped_codes: list[str] = []
    skipped_raw = snapshot.get("skipped_reason_codes")
    if isinstance(skipped_raw, list):
        skipped_codes = [str(code) for code in skipped_raw[:3]]
    if skipped_codes:
        lines.append(_fit(f"見送り {','.join(skipped_codes)}", COLS))
    else:
        lines.append(_fit("見送り理由なし（候補なし）", COLS))

    lines.append(_fit("保有上位:", COLS))
    for symbol, amount in positions[:3]:
        lines.append(_fit(f"  {symbol} {amount}", COLS))
    if not positions:
        lines.append(_fit("  なし", COLS))

    lines.append(_fit("直近約定:", COLS))
    fills = _fills(snapshot)
    for fill in fills:
        lines.append(_fit(
            f"  {fill.get('symbol', '?')} {fill.get('side', '?')} "
            f"{fill.get('amount', '?')}@{fill.get('price', '?')} {fill.get('quote', '')}", COLS))
    if not fills:
        lines.append(_fit("  なし（未取引は正常）", COLS))

    worker_state = str(snapshot.get("worker_state", "unknown"))
    seq = snapshot.get("snapshot_seq", "?")
    lines.append(_fit(f"状態:{worker_state} snapshot:#{seq} bitbank公開 模擬計算・実取引なし", COLS))
    lines.append(_fit("PAPER / 模擬取引", COLS))
    while len(lines) < ROWS:
        lines.append(" " * COLS)
    return "\n".join(lines[:ROWS])


def load_snapshot(state_dir: Path) -> tuple[dict, dict[str, list[float]]]:
    """Read status.json + cached closes. Malformed files yield empty inputs."""
    try:
        snapshot = json.loads((Path(state_dir) / "trading" / "status.json").read_text(encoding="utf-8"))
        if not isinstance(snapshot, dict):
            snapshot = {}
    except (OSError, ValueError):
        snapshot = {}
    closes: dict[str, list[float]] = {}
    try:
        cache = json.loads((Path(state_dir) / "trading" / "market_cache.json").read_text(encoding="utf-8"))
        symbols = cache.get("symbols") if isinstance(cache, dict) else None
        if isinstance(symbols, dict):
            for symbol, entry in symbols.items():
                if not isinstance(entry, dict):
                    continue
                raw = entry.get("closes")
                if not isinstance(raw, list):
                    continue
                values = []
                for value in raw[-24:]:
                    number = _as_float(value)
                    if number is not None and number > 0:
                        values.append(number)
                if values:
                    closes[str(symbol)] = values
    except (OSError, ValueError):
        pass
    return snapshot, closes


def watch_loop(*, state_dir: Path, interval_s: float = 2.0, now_fn=time.time) -> None:
    """Render forever for the program-view xterm. Stdout only, read-only inputs."""
    try:
        interval = float(interval_s)
    except (TypeError, ValueError):
        interval = 2.0
    if not math.isfinite(interval):
        interval = 2.0
    interval = min(30.0, max(0.5, interval))
    try:
        corner = json.loads((Path(state_dir) / "paper_corner.json").read_text(encoding="utf-8"))
        if isinstance(corner, dict):
            raw_ends = corner.get("ends_at")
            ends_at = float(raw_ends) if raw_ends is not None else None
    except (OSError, ValueError, TypeError):
        ends_at = None
    while True:
        now = float(now_fn())
        snapshot, closes = load_snapshot(state_dir)
        remaining = None if ends_at is None else max(0.0, ends_at - now)
        print("\033[2J\033[H" + render_dashboard(snapshot, closes, now=now, remaining_s=remaining), flush=True)
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docich trading dashboard-watch")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--once", action="store_true", help="render a single frame and exit")
    args = parser.parse_args(argv)
    state_dir = Path(args.state_dir)
    if args.once:
        snapshot, closes = load_snapshot(state_dir)
        print(render_dashboard(snapshot, closes, now=time.time(), remaining_s=None))
        return 0
    watch_loop(state_dir=state_dir, interval_s=args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
