"""Cost-inclusive PAPER evidence. Research review never grants live authority."""
from __future__ import annotations

from decimal import Decimal
import math
from pathlib import Path
import sqlite3

from .contract import decode, decimal_text, encode
from .store import LabStore


def evaluate(store: LabStore, identity: str, *, now: float) -> dict:
    exp = store.experiment(identity)
    rows = store.db.execute("SELECT observed_at,equity FROM samples WHERE experiment=? ORDER BY bucket", (identity,)).fetchall()
    fills = store.db.execute("SELECT payload FROM fills WHERE experiment=? ORDER BY observed_at,id", (identity,)).fetchall()
    capital = Decimal(exp["policy"]["capital_jpy"])
    peak, drawdown = capital, Decimal(0)
    for row in rows:
        equity = Decimal(row["equity"])
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak)
    last = Decimal(rows[-1]["equity"]) if rows else None
    observation = rows[-1]["observed_at"] if rows else None
    expected = max(1, math.ceil((min(now, exp["end_at"]) - exp["created_at"]) / 3600))
    coverage = min(1.0, len(rows) / expected)
    report = {
        "schema_version": 1, "artifact": exp["artifact"], "evaluated_at": now,
        "window_end": exp["end_at"], "observation_at": observation,
        "sample_count": len(rows), "coverage_fraction": coverage, "fill_count": len(fills),
        "equity_jpy": None if last is None else decimal_text(last),
        "net_pnl_jpy": None if last is None else decimal_text(last - capital),
        "cash_benchmark_jpy": decimal_text(capital), "max_drawdown_fraction": decimal_text(drawdown),
        "research_review_due": now >= exp["end_at"],
        "live_eligible": False,
        "blockers": ["independent_holdout_required", "multiple_testing_adjustment_required",
                     "live_risk_policy_required", "live_broker_not_implemented"],
    }
    if now < exp["end_at"]:
        report["blockers"].append("forward_window_incomplete")
    if coverage < .8:
        report["blockers"].append("insufficient_observations")
    if observation is None or now - observation > 2 * exp["policy"]["sample_seconds"]:
        report["blockers"].append("valuation_stale")
    with store.transaction():
        store.db.execute("INSERT OR REPLACE INTO evaluations VALUES (?,?,?)", (identity, now, encode(report)))
    return report


def public_summary(path: Path, *, now: float) -> dict:
    """Read-only projection; never create a DB from a dashboard request."""
    if not path.is_file():
        return {"mode": "PAPER", "experiments": []}
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=.2) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("""SELECT e.id,e.artifact,e.phase,e.last_error,e.last_success,e.end_at,
                a.payload AS artifact_payload,v.report FROM experiments e JOIN artifacts a ON e.artifact=a.digest
                LEFT JOIN evaluations v ON e.id=v.experiment ORDER BY e.created_at DESC,e.id LIMIT 12""").fetchall()
            result = []
            for row in rows:
                artifact = decode(row["artifact_payload"], limit=512 * 1024)
                report = decode(row["report"]) if row["report"] else {}
                observed = report.get("observation_at")
                result.append({"id": row["id"], "version": row["artifact"][:12],
                    "name": artifact["name"], "family": artifact["family"], "phase": row["phase"],
                    "last_error": row["last_error"], "last_success": row["last_success"],
                    "net_pnl_jpy": report.get("net_pnl_jpy"), "window_end": row["end_at"],
                    "stale": observed is None or now - observed > 7200,
                    "sample_count": report.get("sample_count", 0), "live_eligible": False})
        return {"mode": "PAPER", "experiments": result}
    except (sqlite3.Error, ValueError, KeyError, TypeError):
        return {"mode": "PAPER", "experiments": [], "error": "lab_snapshot_unavailable"}
