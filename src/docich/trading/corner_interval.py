"""PAPER explanations interval: boundary cursor, fills, and pick selection.

Boundary cursor is persisted in paper.sqlite3 so a PAPER corner explanation
can be re-run (after a crash) without explaining fills twice or missing any.

Cursor key: (filled_at, fill_id). ``fill_id`` is the ledger primary key, so the
key is total-ordered and a retry never re-includes or skips a fill.

Design for docich#2454 (per DSH confirmed values, #issuecomment-7077954226):

- The boundary is fixed at explanation-generation time: the cursor is the
  "last included fill" (filled_at, fill_id).
- The interval is half-open [cursor after, now): fills whose key is strictly
  greater than the cursor and whose filled_at <= now.
- Saving the cursor IS the "explanation completed" definition. On a retry the
  cursor leaves already-explained fills out and no fill is lost.
- First run: if no cursor row exists, the lower bound is unset, so the
  population is "from operation start" and no fill is dropped by the cursor
  filter.

M1-M5 / recommended notes (docich#2454 Pre-read review):
- M1: get_cursor returns None when no cursor row exists (so the whole
  ledger is the first-run population, including the first fill).
- M2: cursor writes go through _upsert_cursor_exclusive within one connection;
  the caller commits the explanation record and the cursor in the same
  transaction.
- M3: pick tiers are ①|realized|max -> ②amount max -> ③state change
  (position build / full close) -> ④stop_loss/risk_rejected/deviation.
  One fill per tier, in order.
- M4: set_cursor only accepts a strictly newer key (monotone guard).
- M5: fills_in_interval / interval_realized use the (filled_at, fill_id) key
  boundary, so same-second fills are handled consistently (population and
  realized window agree).
"""
from __future__ import annotations

import sqlite3
import time
from decimal import Decimal
from pathlib import Path
from typing import Mapping

# Upper limit of reads when the interval has many fills: read all (full
# enumeration) when <= FULL_ENUM_LIMIT, otherwise rely on the selection pass.
# Currently fills_in_interval always reads the full ledger via _rows for
# cursor correctness; FULL_ENUM_LIMIT is the threshold below which the
# efficient path is used. See _rows used by fills_in_interval.
FULL_ENUM_LIMIT = 10000

# DSH confirmed pick-up upper limit.
MAX_PICKS = 3

# Priority order for pick tiers (0 = first tier)
_PICK_TIERS = (
    # ① realized P/L absolute value maximum
    "realized",
    # ② amount/quantity maximum
    "amount",
    # ③ position build / full close (state change)
    "state_change",
    # ④ stop-loss / risk-rejection / deviation (cost/risk)
    "cost_risk",
)

ZERO = Decimal("0")


def _conn(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{str(db_path.resolve())}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_cursor_table(db_path: Path) -> None:
    """Create the explanation state table if it does not exist yet."""
    uri = f"file:{str(db_path.resolve())}?mode=rw"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS paper_corner_explanation_state ("
            "corner_key TEXT PRIMARY KEY, "
            "high_water_ts REAL, "
            "high_water_fill_id TEXT, "
            "updated_at REAL)"
        )
        conn.commit()
    finally:
        conn.close()


def get_cursor(db_path: Path, corner_key: str = "paper") -> tuple[float, str] | None:
    """Return the current boundary cursor, or None when no cursor row exists.

    M1: on the first run there is no row, so the lower bound is unset. This
    lets fills_in_interval(None) return the full ledger (operation start),
    including the very first fill.
    """
    if not db_path.is_file():
        return None
    conn = _conn(db_path)
    try:
        try:
            row = conn.execute(
                "SELECT high_water_ts, high_water_fill_id FROM paper_corner_explanation_state"
                " WHERE corner_key = ?",
                (corner_key,),
            ).fetchone()
        except sqlite3.OperationalError as exc:
            # A read-only consumer must not create the state table as a side effect.
            if "no such table" in str(exc).lower():
                return None
            raise
    finally:
        conn.close()
    if row is None:
        return None
    return (float(row["high_water_ts"]), str(row["high_water_fill_id"]))


def first_cursor(db_path: Path) -> tuple[float, str] | None:
    """Return the first fill in the ledger (operation start population)."""
    conn = sqlite3.connect(f"file:{str(db_path.resolve())}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT filled_at, fill_id FROM paper_fills ORDER BY filled_at ASC, fill_id ASC"
            " LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return (float(row["filled_at"]), str(row["fill_id"]))


def _upsert_cursor_exclusive(conn: sqlite3.Connection, corner_key: str, filled_at: float, fill_id: str, now: float) -> None:
    """Insert/replace the cursor inside an existing transaction (caller commits).

    M4: a backward update (new key <= current key) is ignored, so mid-
    interpretation retries never regress the boundary. The caller must insert
    the explanation record and commit the cursor in the same connection so the
    two are atomic.
    """
    cur = conn.execute(
        "SELECT high_water_ts, high_water_fill_id FROM paper_corner_explanation_state"
        " WHERE corner_key = ?",
        (corner_key,),
    )
    existing = cur.fetchone()
    new_key = (filled_at, str(fill_id))
    if existing is not None:
        # Support both default tuple rows and sqlite3.Row connections: callers
        # own the transaction and may not have configured a row factory.
        existing_ts = existing[0]
        existing_id = str(existing[1])
        if new_key <= (existing_ts, existing_id):
            return
    conn.execute(
        "INSERT INTO paper_corner_explanation_state (corner_key, high_water_ts, high_water_fill_id, updated_at)"
        " VALUES (?, ?, ?, ?)"
        " ON CONFLICT(corner_key) DO UPDATE SET high_water_ts = excluded.high_water_ts,"
        " high_water_fill_id = excluded.high_water_fill_id, updated_at = excluded.updated_at",
        (corner_key, filled_at, fill_id, now),
    )


def set_cursor(db_path: Path, corner_key: str, filled_at: float, fill_id: str, now: float) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_cursor_table(db_path)
    conn = sqlite3.connect(f"file:{str(db_path.resolve())}?mode=rw", uri=True)
    try:
        _upsert_cursor_exclusive(conn, corner_key, filled_at, fill_id, now)
        conn.commit()
    finally:
        conn.close()


def fills_in_interval(db_path: Path, cursor: tuple[float, str] | None, now: float | None = None) -> list[Mapping[str, object]]:
    """All fills in [cursor after, now), sorted by (filled_at, fill_id).

    The lower bound is a strict key comparison: a fill whose (filled_at,
    fill_id) is <= the cursor is excluded, so the cursor's own fill is never
    re-explained. When cursor is None the population is the full ledger.
    """
    now = time.time() if now is None else float(now)
    rows = _rows(db_path)
    out = []
    for r in rows:
        filled_at = float(r["filled_at"])
        if filled_at > now:
            continue
        if cursor is not None:
            # Strictly after the last included fill (M5 key comparison).
            if (filled_at, str(r["fill_id"])) <= cursor:
                continue
        out.append(r)
    out.sort(key=lambda r: (float(r["filled_at"]), str(r["fill_id"])))
    return out


def _rows(db_path: Path) -> list[dict[str, object]]:
    conn = _conn(db_path)
    try:
        return [
            {
                "fill_id": r["fill_id"],
                "symbol": r["symbol"],
                "side": r["side"],
                "amount": r["amount"],
                "price": r["price"],
                "filled_at": r["filled_at"],
                "reason_code": r["reason_code"],
            }
            for r in conn.execute(
                "SELECT fill_id, symbol, side, amount, price, filled_at, reason_code"
                " FROM paper_fills ORDER BY filled_at ASC, fill_id ASC"
            )
        ]
    finally:
        conn.close()


def realized_for_fill(db_path: Path, fill_id: str) -> Decimal | None:
    """Realized P/L of one sell fill (None for buys/unknown)."""
    from docich.trading.performance import realized_pnl_for_fill

    return realized_pnl_for_fill(db_path, fill_id)


def interval_realized(db_path: Path, cursor: tuple[float, str] | None, now: float | None = None) -> Decimal:
    """Sum of realized P/L for fills within the interval.

    Computed in a single FIFO pass over the full ledger, so the population is
    exactly fills_in_interval's and the same-second boundary (M5) behaves
    consistently. When cursor is None, the lower bound is the ledger start
    (first run).
    """
    now = time.time() if now is None else float(now)
    realized = ZERO
    lots: dict[str, list[Decimal]] = {}

    rows = sorted(
        _rows(db_path),
        key=lambda row: (float(row["filled_at"]), str(row["fill_id"])),
    )
    for r in rows:
        filled = float(r["filled_at"])
        if filled > now:
            continue
        in_interval = cursor is None or (filled, str(r["fill_id"])) > cursor
        symbol = str(r["symbol"])
        side = str(r.get("side"))
        amount = Decimal(str(r.get("amount") or ZERO))
        price = Decimal(str(r.get("price") or ZERO))
        if side == "buy":
            # Carry pre-cursor lots forward so a later interval sell retains
            # its true FIFO cost basis.
            held, cost = lots.setdefault(symbol, [ZERO, ZERO])
            lots[symbol] = [held + amount, cost + amount * price]
            continue
        if side != "sell":
            continue
        held, cost = lots.get(symbol, [ZERO, ZERO])
        if held <= ZERO:
            continue
        sold = min(amount, held)
        average = cost / held
        if in_interval:
            realized += sold * (price - average)
        lots[symbol] = [held - sold, cost - (average * sold)]
    return realized


def _pick_record(fill: Mapping[str, object]) -> dict[str, object]:
    return {
        "fill_id": str(fill["fill_id"]),
        "symbol": str(fill.get("symbol")),
        "side": str(fill.get("side")),
        "amount": str(fill.get("amount")),
        "price": str(fill.get("price")),
        "filled_at": float(fill["filled_at"]),
        "reason_code": str(fill.get("reason_code") or ""),
        "signal_context": fill.get("signal_context"),
    }


def select_picks(
    db_path: Path,
    all_fills: list[Mapping[str, object]],
    max_picks: int = MAX_PICKS,
) -> list[dict[str, object]]:
    """Select up to ``max_picks`` representative fills, one per tier in order.

    Tier priority (①→④):
      1. realized P/L absolute value maximum
      2. amount/quantity maximum
      3. position build / full close (state change)
      4. stop-loss / risk-rejection / deviation (cost/risk)

    Each tier contributes at most one fill (the best of that tier), and the
    next tier is considered only if earlier tiers exhausted. Tie-break within
    a tier uses (filled_at, fill_id).
    """
    picks = []
    chosen = set()

    def realized(r: Mapping[str, object]) -> Decimal:
        return realized_for_fill(db_path, str(r["fill_id"])) or ZERO

    def amount(r: Mapping[str, object]) -> Decimal:
        return abs(Decimal(str(r.get("amount")) or ZERO))

    def state_change_ids(fills: list[Mapping[str, object]]) -> set[str]:
        lots: dict[str, list[Decimal]] = {}
        ids: set[str] = set()
        for r in sorted(
            fills, key=lambda x: (float(x["filled_at"]), str(x["fill_id"]))
        ):
            symbol = str(r["symbol"])
            side = str(r.get("side"))
            amount_v = Decimal(str(r.get("amount")) or ZERO)
            price = Decimal(str(r.get("price")) or ZERO)
            held, cost = lots.setdefault(symbol, [ZERO, ZERO])
            if side == "buy":
                if held == ZERO:
                    ids.add(str(r["fill_id"]))
                lots[symbol] = [held + amount_v, cost + amount_v * price]
            elif side == "sell":
                sold = min(amount_v, held)
                if held > ZERO:
                    if held - sold == ZERO:
                        ids.add(str(r["fill_id"]))
                    lots[symbol] = [held - sold, cost - (average := cost / held * sold)]
        return ids

    change_ids = state_change_ids(all_fills)

    def is_state_change(r: Mapping[str, object]) -> bool:
        return str(r["fill_id"]) in change_ids

    def is_cost_risk(r: Mapping[str, object]) -> bool:
        return str(r.get("reason_code") or "").strip() in ("stop_loss", "risk_rejected", "deviation")

    tiers: tuple[tuple[str, object], ...] = (
        ("realized", lambda r: abs(realized(r))),
        ("amount", amount),
        ("state_change", lambda r: 1 if is_state_change(r) else 0),
        ("cost_risk", lambda r: 1 if is_cost_risk(r) else 0),
    )

    def tier_best(idx: int) -> Mapping[str, object] | None:
        best = None
        best_score = None
        for r in all_fills:
            fid = str(r["fill_id"])
            if fid in chosen:
                continue
            score = tiers[idx][1](r)
            # State-change and cost/risk tiers are predicates, not rankings:
            # an empty tier must not consume a slot with an arbitrary score 0.
            if idx >= 2 and score <= 0:
                continue
            if best is None or score > best_score:
                best = r
                best_score = score
            elif score == best_score:
                cur_key = (float(r["filled_at"]), fid)
                best_key = (float(best["filled_at"]), str(best["fill_id"]))
                if cur_key < best_key:
                    best = r
        return best

    idx = 0
    while len(picks) < max_picks and idx < len(_PICK_TIERS):
        candidate = tier_best(idx)
        if candidate is None:
            idx += 1
            continue
        chosen.add(str(candidate["fill_id"]))
        picks.append(candidate)
        idx += 1

    return [_pick_record(p) for p in picks]
