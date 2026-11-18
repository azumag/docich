"""Read-only shadow reporting and PAPER comparison (Issue #3623, L4c).

The reporting path deliberately does not build the worker's incremental ``ShadowBook``:
it iterates the ``shadow_fill`` stream itself in bounded pages so a summary never keeps
every past fill in memory.  PAPER is read through the real on-disk schema from
``docich.trading.ledger`` (``paper_fills`` with TEXT ``signal_context``) and is never
opened for writing (``path.as_uri()`` + ``mode=ro``).

A verified match needs one unambiguous PAPER row and one unambiguous shadow fill with the
same symbol, side, opportunity, strategy rule and observation window, plus equal
``experiment_digest`` values.  A LIVE per-symbol ``policy_digest`` is not comparable and is
never used for this check.  Rows without a comparable digest are reported as insufficient
evidence, not as matches.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterator, Mapping

from . import views
from .canonical import is_sha256_hex
from .shadow import (SHADOW_EVENT_KIND, SHADOW_EVENT_KINDS, SHADOW_MODE,
                     SHADOW_PAGE_SIZE, ShadowBook, ShadowBookError, ShadowFill,
                     apply_shadow_event, project_shadow_book, _page_size,
                     _symbol_parts, _side, _decimal, _nonnegative)

PAPER_FILLS_TABLE = "paper_fills"
MAX_PAPER_FILLS = 5_000
MATCH_WINDOW_S = 300.0
_MISSING_STATES = {
    "missing": "paper_ledger", "not_file": "paper_ledger", "no_table": "paper_table",
    "schema": "paper_schema", "read_error": "paper_read", "malformed": "paper_read",
}


@dataclass(frozen=True)
class PaperFill:
    fill_id: str
    opportunity_id: str
    strategy_id: str
    symbol: str
    side: str
    amount: Decimal
    price: Decimal
    quote_notional: Decimal
    filled_at: float
    signal_context: Mapping[str, object] | None
    experiment_digest: str | None
    bar_ts_ms: int | None
    fee: Decimal | None


@dataclass(frozen=True)
class PaperEvidence:
    """The PAPER read result: a valid empty table is not the same as a missing one."""

    state: str
    fills: tuple[PaperFill, ...] = ()
    truncated: bool = False

    @property
    def available(self) -> bool:
        return self.state in ("empty", "ok")


@dataclass(frozen=True)
class ShadowPlan:
    decision_key: str
    opportunity_id: str
    symbol: str
    side: str
    rule_id: str

    @classmethod
    def from_payload(cls, payload: Mapping) -> "ShadowPlan":
        if payload.get("mode") != SHADOW_MODE:
            raise ShadowBookError("a shadow plan must say mode=shadow")
        if payload.get("action") != "plan":
            raise ShadowBookError("not a shadow plan row")
        _symbol_parts(str(payload.get("symbol") or ""))
        return cls(
            decision_key=str(payload.get("decision_key") or ""),
            opportunity_id=str(payload.get("opportunity_id") or ""),
            symbol=str(payload.get("symbol") or ""),
            side=_side(payload.get("side")),
            rule_id=str(payload.get("rule_id") or ""),
        )


def _paper_decimal(value: object, name: str) -> Decimal:
    number = _decimal(value, name)
    if number < 0:
        raise ShadowBookError(f"{name} must not be negative")
    return number


def _parse_context(value: object) -> Mapping[str, object] | None:
    if value is None:
        return None
    try:
        context = json.loads(str(value))
    except (TypeError, ValueError) as exc:
        raise ShadowBookError("signal_context is not valid JSON") from exc
    if context is None:
        return None
    if not isinstance(context, dict):
        raise ShadowBookError("signal_context is not an object")
    return context


def _context_digest(context: Mapping[str, object] | None) -> str | None:
    if context is None:
        return None
    value = context.get("experiment_digest")
    return value if isinstance(value, str) and is_sha256_hex(value) else None


def _context_bar(context: Mapping[str, object] | None) -> int | None:
    if context is None:
        return None
    value = context.get("bar_ts_ms")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _context_fee(context: Mapping[str, object] | None) -> Decimal | None:
    if context is None:
        return None
    for name in ("fee_amount", "fee"):
        if name in context:
            try:
                return _nonnegative(context[name], name)
            except ShadowBookError:
                return None
    return None


def _paper_row(row: tuple) -> PaperFill:
    (fill_id, opportunity_id, strategy_id, symbol, side, amount, price,
     quote_notional, filled_at, context_text) = row
    _symbol_parts(str(symbol or ""))
    side = _side(side)
    try:
        stamp = float(filled_at)
    except (TypeError, ValueError) as exc:
        raise ShadowBookError("filled_at is not a finite number") from exc
    if not (-float("inf") < stamp < float("inf")):
        raise ShadowBookError("filled_at is not finite")
    context = _parse_context(context_text)
    return PaperFill(
        fill_id=str(fill_id or ""),
        opportunity_id=str(opportunity_id or ""),
        strategy_id=str(strategy_id or ""),
        symbol=str(symbol),
        side=side,
        amount=_paper_decimal(amount, "amount"),
        price=_paper_decimal(price, "price"),
        quote_notional=_paper_decimal(quote_notional, "quote_notional"),
        filled_at=stamp,
        signal_context=context,
        experiment_digest=_context_digest(context),
        bar_ts_ms=_context_bar(context),
        fee=_context_fee(context),
    )


def read_paper_fills(path) -> PaperEvidence:
    """Read PAPER fills from the real on-disk schema without creating or writing anything."""
    target = Path(path)
    if not target.exists():
        return PaperEvidence("missing")
    if not target.is_file():
        return PaperEvidence("not_file")
    conn = None
    try:
        conn = sqlite3.connect(f"{target.resolve().as_uri()}?mode=ro", uri=True)
        found = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (PAPER_FILLS_TABLE,)).fetchone()
        if found is None:
            return PaperEvidence("no_table")
        columns = {str(row[1]) for row in conn.execute(
            f"PRAGMA table_info({PAPER_FILLS_TABLE})")}
        required = {"opportunity_id", "strategy_id", "symbol", "side", "amount",
                    "price", "filled_at"}
        if not required <= columns:
            return PaperEvidence("schema")
        wanted = ["fill_id" if "fill_id" in columns else "NULL",
                  "opportunity_id", "strategy_id", "symbol", "side", "amount", "price",
                  "quote_notional" if "quote_notional" in columns else "NULL",
                  "filled_at",
                  "signal_context" if "signal_context" in columns else "NULL"]
        rows = conn.execute(
            f"SELECT {', '.join(wanted)} FROM {PAPER_FILLS_TABLE} "
            "ORDER BY filled_at DESC, rowid DESC LIMIT ?",
            (MAX_PAPER_FILLS + 1,)).fetchall()
    except sqlite3.Error:
        return PaperEvidence("read_error")
    finally:
        if conn is not None:
            conn.close()

    truncated = len(rows) > MAX_PAPER_FILLS
    rows = list(reversed(rows[:MAX_PAPER_FILLS]))
    try:
        fills = tuple(_paper_row(row) for row in rows)
    except (ShadowBookError, TypeError, ValueError):
        return PaperEvidence("malformed")
    return PaperEvidence("empty" if not fills else "ok", fills=fills, truncated=truncated)


def _iter_event_pages(ledger, kinds: str | tuple[str, ...], *,
                      page_size: int) -> Iterator[object]:
    size = _page_size(int(page_size))
    names = (kinds,) if isinstance(kinds, str) else tuple(kinds)
    after = 0
    while True:
        rows = views.events_of_kinds(ledger, names, after_event_id=after, limit=size)
        if not rows:
            return
        yield from rows
        advanced = int(rows[-1].event_id)
        if advanced <= after:
            raise ShadowBookError("an event page did not advance")
        after = advanced
        if len(rows) < size:
            return


def iter_shadow_fills(ledger, *, page_size: int = SHADOW_PAGE_SIZE) -> Iterator[ShadowFill]:
    """Stream every shadow fill, oldest first, without retaining the history."""
    for row in _iter_event_pages(ledger, SHADOW_EVENT_KIND, page_size=page_size):
        yield ShadowFill.from_event(row)


def shadow_plans(ledger, *, page_size: int = SHADOW_PAGE_SIZE) -> tuple[ShadowPlan, ...]:
    """Recorded shadow plan rows; trade-mode and skip rows are not plans."""
    plans: list[ShadowPlan] = []
    for row in _iter_event_pages(ledger, "live_decision", page_size=page_size):
        payload = row.payload
        if not isinstance(payload, Mapping) or payload.get("mode") != SHADOW_MODE:
            continue
        action = payload.get("action")
        if action == "skip":
            continue
        if action != "plan":
            raise ShadowBookError("a shadow decision row has an unknown action")
        plans.append(ShadowPlan.from_payload(payload))
    return tuple(plans)


@dataclass(frozen=True)
class ShadowMatch:
    decision_key: str
    paper_fill_id: str
    symbol: str
    opportunity_id: str
    side: str
    strategy_id: str
    amount_diff: Decimal
    price_diff: Decimal
    fee_diff: Decimal | None


@dataclass(frozen=True)
class ShadowComparison:
    evidence: bool
    plans: tuple[ShadowPlan, ...]
    fills: tuple[ShadowFill, ...]
    positions: tuple[str, ...]
    paired: int
    planned_without_fill: tuple[dict, ...]
    fills_without_plan: tuple[dict, ...]
    paper: PaperEvidence
    matches: tuple[ShadowMatch, ...]
    shadow_without_paper: tuple[dict, ...]
    paper_without_shadow: tuple[dict, ...]
    digest_verified: int
    digest_missing: int
    shadow_digest_missing: int
    digest_mismatch: int
    identity_missing: int
    strategy_mismatch: int
    window_mismatch: int
    ambiguous: int
    missing_evidence: tuple[str, ...]

    @property
    def paper_matched(self) -> int:
        return len(self.matches)



def _identity(item) -> tuple[str, str, str] | None:
    symbol = getattr(item, "symbol", "")
    opportunity_id = getattr(item, "opportunity_id", "")
    side = getattr(item, "side", "")
    if not symbol or not opportunity_id or side not in ("buy", "sell"):
        return None
    return str(symbol), str(side), str(opportunity_id)


def _strategy_matches(paper: PaperFill, fill: ShadowFill) -> bool:
    if not paper.strategy_id or not fill.rule_id:
        return False
    return paper.strategy_id.rsplit(":", 1)[-1] == fill.rule_id


def _in_window(paper: PaperFill, fill: ShadowFill) -> bool:
    if paper.bar_ts_ms is not None:
        return paper.bar_ts_ms == fill.bar_ts_ms
    if fill.bar_ts_ms > 0:
        start = fill.bar_ts_ms / 1000.0
        span = float(fill.timeframe_s) if fill.timeframe_s > 0 else MATCH_WINDOW_S
        return start <= paper.filled_at <= start + span
    if fill.executed_at_ms > 0:
        return abs(paper.filled_at - fill.executed_at_ms / 1000.0) <= MATCH_WINDOW_S
    return False


def _ref(item, kind: str, reason: str) -> dict[str, object]:
    return {"kind": kind, "reason": reason, "symbol": item.symbol,
            "opportunity_id": item.opportunity_id, "side": item.side,
            "strategy_id": getattr(item, "strategy_id", "")
            or getattr(item, "rule_id", ""),
            "decision_key": getattr(item, "decision_key", ""),
            "paper_fill_id": getattr(item, "fill_id", "") if kind == "paper" else "",
            "event_id": getattr(item, "event_id", 0),
            "bar_ts_ms": getattr(item, "bar_ts_ms", 0),
            "filled_at": getattr(item, "filled_at", None)}


def _digest_state(paper: PaperFill, fill: ShadowFill) -> str:
    if paper.experiment_digest is None:
        return "missing"
    if not fill.experiment_digest:
        return "shadow_missing"
    return "verified" if paper.experiment_digest == fill.experiment_digest else "mismatch"


def build_shadow_comparison(ledger, *, paper: PaperEvidence | None = None,
                            page_size: int = SHADOW_PAGE_SIZE) -> ShadowComparison:
    """Compare shadow plans/fills with PAPER evidence; never guesses a candidate."""
    paper = paper if isinstance(paper, PaperEvidence) else PaperEvidence("missing")
    plans = shadow_plans(ledger, page_size=page_size)
    fills: list[ShadowFill] = []
    book = ShadowBook()
    for row in _iter_event_pages(ledger, SHADOW_EVENT_KINDS, page_size=page_size):
        fill = apply_shadow_event(book, row)
        if fill is not None:
            fills.append(fill)
    fills_t = tuple(fills)

    plan_keys = {plan.decision_key for plan in plans}
    fill_keys = {fill.decision_key for fill in fills_t}
    planned_without_fill = tuple(
        {**_ref(plan, "plan", "no_shadow_fill"), "decision_key": plan.decision_key}
        for plan in plans if plan.decision_key not in fill_keys)
    fills_without_plan = tuple(
        {**_ref(fill, "fill", "no_shadow_plan"), "decision_key": fill.decision_key}
        for fill in fills_t if fill.decision_key not in plan_keys)
    paired = len(plan_keys & fill_keys)

    shadow_by_key: dict[tuple[str, str, str], list[ShadowFill]] = {}
    for fill in fills_t:
        key = _identity(fill)
        if key is not None:
            shadow_by_key.setdefault(key, []).append(fill)

    shadow_reasons: dict[int, dict] = {}
    for fill in fills_t:
        if _identity(fill) is None:
            shadow_reasons[id(fill)] = _ref(fill, "fill", "shadow_identity_missing")
    paper_without: list[dict] = []
    matches: list[ShadowMatch] = []
    used_shadow: set[int] = set()
    digest_missing = 0
    shadow_digest_missing = 0
    digest_mismatch = 0
    identity_missing = 0
    strategy_mismatch = 0
    window_mismatch = 0
    ambiguous = 0

    for item in paper.fills:
        key = _identity(item)
        if key is None:
            identity_missing += 1
            paper_without.append({**_ref(item, "paper", "paper_identity_missing"),
                                  "strategy_id": item.strategy_id})
            continue
        candidates = shadow_by_key.get(key, [])
        if not candidates:
            paper_without.append({**_ref(item, "paper", "shadow_fill_missing"),
                                  "experiment_digest": item.experiment_digest or ""})
            continue
        strategies = [fill for fill in candidates if _strategy_matches(item, fill)]
        if not strategies:
            strategy_mismatch += 1
            paper_without.append({**_ref(item, "paper", "paper_strategy_mismatch"),
                                  "strategies": sorted({fill.rule_id for fill in candidates})})
            continue
        windowed = [fill for fill in strategies if _in_window(item, fill)]
        if not windowed:
            window_mismatch += 1
            paper_without.append({**_ref(item, "paper", "paper_window_mismatch"),
                                  "shadow_digest": strategies[0].experiment_digest})
            continue
        if len(windowed) > 1:
            ambiguous += 1
            paper_without.append({**_ref(item, "paper", "ambiguous_shadow_candidate"),
                                  "candidates": len(windowed)})
            for fill in windowed:
                shadow_reasons.setdefault(id(fill), _ref(
                    fill, "fill", "ambiguous_paper_candidate"))
            continue
        fill = windowed[0]
        if id(fill) in used_shadow:
            ambiguous += 1
            paper_without.append({**_ref(item, "paper", "ambiguous_shadow_candidate"),
                                  "candidates": 1})
            continue
        digest_state = _digest_state(item, fill)
        if digest_state != "verified":
            if digest_state == "missing":
                digest_missing += 1
                reason = "paper_digest_missing"
            elif digest_state == "shadow_missing":
                shadow_digest_missing += 1
                reason = "shadow_digest_missing"
            else:
                digest_mismatch += 1
                reason = "paper_digest_mismatch"
            paper_without.append({**_ref(item, "paper", reason),
                                  "experiment_digest": item.experiment_digest or "",
                                  "shadow_digest": fill.experiment_digest})
            shadow_reasons.setdefault(id(fill), _ref(fill, "fill", reason))
            continue
        used_shadow.add(id(fill))
        matches.append(ShadowMatch(
            decision_key=fill.decision_key, paper_fill_id=item.fill_id,
            symbol=item.symbol, opportunity_id=item.opportunity_id, side=item.side,
            strategy_id=item.strategy_id, amount_diff=fill.amount - item.amount,
            price_diff=fill.price - item.price,
            fee_diff=None if item.fee is None else fill.fee - item.fee))

    for fill in fills_t:
        if id(fill) in used_shadow:
            continue
        shadow_reasons.setdefault(id(fill), _ref(fill, "fill", "paper_fill_missing"))
    shadow_without = tuple(shadow_reasons[id(fill)] for fill in fills_t
                           if id(fill) in shadow_reasons)

    missing: list[str] = []
    if not fills_t:
        missing.append("shadow_fill")
    if not plans:
        missing.append("shadow_plan")
    if planned_without_fill:
        missing.append("shadow_fill")
    if fills_without_plan:
        missing.append("shadow_plan")
    if not paper.available:
        missing.append(_MISSING_STATES.get(paper.state, "paper_ledger"))
    elif not paper.fills:
        missing.append("paper_fill")
    if shadow_without or paper_without:
        missing.append("paper_fill")
    if identity_missing:
        missing.append("paper_identity")
    if strategy_mismatch:
        missing.append("paper_strategy")
    if window_mismatch:
        missing.append("paper_window")
    if ambiguous:
        missing.append("ambiguous_paper")
    if digest_missing:
        missing.append("paper_digest")
    if shadow_digest_missing:
        missing.append("shadow_digest")
    if digest_mismatch:
        missing.append("paper_digest_mismatch")

    return ShadowComparison(
        evidence=bool(plans or fills_t), plans=plans, fills=fills_t,
        positions=tuple(sorted(book.positions)), paired=paired,
        planned_without_fill=planned_without_fill,
        fills_without_plan=fills_without_plan, paper=paper, matches=tuple(matches),
        shadow_without_paper=shadow_without,
        paper_without_shadow=tuple(paper_without),
        digest_verified=len(matches), digest_missing=digest_missing,
        shadow_digest_missing=shadow_digest_missing,
        digest_mismatch=digest_mismatch, identity_missing=identity_missing,
        strategy_mismatch=strategy_mismatch, window_mismatch=window_mismatch,
        ambiguous=ambiguous, missing_evidence=tuple(dict.fromkeys(missing)))


def render_shadow_report(comparison: ShadowComparison) -> str:
    """A deterministic, compact, read-only operator summary."""
    if comparison.evidence:
        lines = [f"shadow: evidence plans={len(comparison.plans)} "
                 f"fills={len(comparison.fills)} positions={len(comparison.positions)}"]
    else:
        lines = ["shadow: no evidence"]
    lines.extend([
        f"comparison.paired: {comparison.paired}",
        f"planned.without_fill: {len(comparison.planned_without_fill)}",
        f"fills.without_plan: {len(comparison.fills_without_plan)}",
    ])
    if comparison.paper.state == "missing":
        lines.append("paper: missing")
    elif comparison.paper.state == "no_table":
        lines.append("paper: no table")
    elif comparison.paper.state in ("read_error", "malformed", "schema", "not_file"):
        lines.append("paper: unreadable")
    elif not comparison.paper.fills:
        lines.append("paper: no fills")
    else:
        suffix = " (latest)" if comparison.paper.truncated else ""
        lines.append(f"paper: {len(comparison.paper.fills)} fills{suffix}")
    lines.extend([
        f"paper.matched: {comparison.paper_matched}",
        f"digest.verified: {comparison.digest_verified}",
        f"digest.missing: {comparison.digest_missing}",
        f"digest.shadow_missing: {comparison.shadow_digest_missing}",
        f"digest.mismatch: {comparison.digest_mismatch}",
    ])
    if comparison.missing_evidence:
        lines.append("missing.evidence: " + ",".join(comparison.missing_evidence))
    else:
        lines.append("missing.evidence: none")
    for item in comparison.matches:
        fee = "unavailable" if item.fee_diff is None else str(item.fee_diff)
        lines.append(
            f"match: decision_key={item.decision_key} paper={item.paper_fill_id} "
            f"symbol={item.symbol} side={item.side} amount.diff={item.amount_diff} "
            f"price.diff={item.price_diff} fee.diff={fee}")
    for group, kind in ((comparison.planned_without_fill, "planned"),
                        (comparison.fills_without_plan, "fill"),
                        (comparison.shadow_without_paper, "shadow"),
                        (comparison.paper_without_shadow, "paper")):
        for item in group:
            fields = [f"kind={kind}", f"reason={item.get('reason', '')}"]
            for name in ("symbol", "opportunity_id", "side", "decision_key"):
                value = item.get(name)
                if value:
                    fields.append(f"{name}={value}")
            lines.append("unmatched: " + " ".join(fields))
    return "\n".join(lines)


def shadow_counts(ledger) -> tuple[bool, int, int, int]:
    """(evidence, plans, fills, open simulated positions) for status/report callers."""
    book = project_shadow_book(ledger)
    plans = len(shadow_plans(ledger))
    fills = len(book.fill_ids)
    return bool(plans or fills), plans, fills, len(book.positions)
