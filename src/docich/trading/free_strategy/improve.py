"""Daily improvement loop and LIVE promotion judgement for free PAPER strategies.

Owner decision 2026-11-27: free strategies run like the regular PAPER strategies -- a
new candidate every day while there is room, learning from the cost-inclusive results of
earlier candidates -- and a candidate that proves itself is promoted to LIVE.

``run_improve`` (``docich free-strategy improve``, the daily timer):

1. stops when ``free-strategies/improve.disabled`` exists;
2. retires risk-stopped candidates (``paused`` with ``drawdown_limit``): their deadline is
   moved to now, so the next worker cycle settles them and their observation slot frees up;
3. judges every candidate for promotion and writes ``free-strategies/promotion.json``;
4. generates one new candidate when a slot is free, with the bounded results of the
   previous candidates in the prompt (at most one provider request per UTC day, as before).

Promotion judgement (rule ``free-v1``): the JST day returns of a candidate's PAPER equity
(the last observation of each whole day against the previous day's) go through the same
e-process as rule v3 (``promotion_shadow.e_process``: E >= 5 with at least 5 day blocks,
0.2% per day null).  Only whole days after the candidate was created count.  A candidate
that is quarantined, or whose evaluation is stale, is never eligible.  When a free
strategy is already LIVE, the challenger must also beat that LIVE strategy's day returns
on the same days (0 on a day without a LIVE record).  The judgement only writes the
report and, for the best eligible candidate, starts its LIVE mirror (below).

LIVE mirror: the promoted artifact is kept running as one more PAPER experiment (365 days,
outside the two research slots, ``store.create(mirror=True)``).  Each worker cycle writes
its position weights to ``live_weights.json``; the LIVE worker follows them
(``trading/live/free_mirror.py``) and never runs the code.  A new candidate replaces the
mirror only when it beats the mirror's own day returns on the same days and the mirror is
at least ``MIRROR_MIN_HOLD_S`` (7 days) old.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Callable, Mapping

from .. import promotion_shadow as shadow
from .contract import StrategyError, decode
from .generation import generate, request_text
from .store import MAX_OBSERVED_EXPERIMENTS, LabStore

RULE_ID = "free-v1"
DISABLED_FILENAME = "improve.disabled"
PROMOTION_FILENAME = "promotion.json"
DEFAULT_DAYS = 7
MIRROR_DAYS = 365
MIRROR_CAPITAL = "10000"
MIRROR_MIN_HOLD_S = 7 * 86400
DEFAULT_BRIEF = ("暗号資産の現物を対象に、5分足の値動きと出来高から検証可能な仮説を1つ立てる。"
                 "往復の費用（約0.3%）を上回る値幅を狙い、取引が全く起きない設計は避ける。")
JST = dt.timezone(dt.timedelta(hours=9), "JST")
MAX_PRIOR = 8


def _day(epoch: float) -> str:
    return dt.datetime.fromtimestamp(float(epoch), tz=dt.timezone.utc).astimezone(JST).date().isoformat()


def equity_day_returns(store: LabStore, identity: str, *, capital: Decimal,
                       created_at: float, now: float) -> list[tuple[str, float]]:
    """``[(jst_day, return)]`` of whole days after creation, from the equity journal."""
    rows = store.db.execute("SELECT observed_at, equity FROM equity_observations WHERE experiment=? "
                            "ORDER BY observed_at", (identity,)).fetchall()
    first_day, today = _day(created_at), _day(now)
    closes: dict[str, Decimal] = {}
    for observed_at, equity in rows:
        closes[_day(observed_at)] = Decimal(equity)       # the last observation of the day wins
    previous = capital
    blocks = []
    for day in sorted(closes):
        value = closes[day]
        if first_day < day < today and previous > 0:
            blocks.append((day, float(value / previous - 1)))
        previous = value
    return blocks


def _results(store: LabStore) -> list[dict]:
    """Bounded cost-inclusive results of the latest candidates, newest first."""
    rows = store.db.execute("""SELECT e.id, e.phase, e.last_error, e.created_at, a.payload, v.report
        FROM experiments e JOIN artifacts a ON e.artifact=a.digest
        LEFT JOIN evaluations v ON e.id=v.experiment
        ORDER BY e.created_at DESC, e.id LIMIT ?""", (MAX_PRIOR,)).fetchall()
    results = []
    for identity, phase, last_error, created_at, payload, report in rows:
        artifact = decode(payload, limit=512 * 1024)
        if artifact.get("family") == "ops_smoke":
            continue
        data = decode(report) if report else {}
        results.append({"name": artifact["name"], "family": artifact["family"],
                        "thesis": artifact["thesis"][:200], "phase": phase,
                        "stop_reason": last_error, "net_pnl_jpy": data.get("net_pnl_jpy"),
                        "max_drawdown_fraction": data.get("max_drawdown_fraction"),
                        "fill_count": data.get("fill_count", 0)})
    return results


def retire_risk_stopped(store: LabStore, *, now: float) -> int:
    """End candidates paused by the drawdown stop now, so their slot frees up."""
    with store.transaction():
        cursor = store.db.execute("""UPDATE experiments SET end_at=?, revision=revision+1
            WHERE phase='paused' AND last_error='drawdown_limit' AND end_at>?""", (now, now))
    return cursor.rowcount


def judge_promotion(store: LabStore, *, now: float,
                    live_returns: Mapping[str, float] | None = None,
                    live_artifact: str | None = None) -> dict:
    """Judge every candidate; the best eligible one is ``candidate``.  Reads only."""
    rows = store.db.execute("""SELECT e.id, e.artifact, e.phase, e.created_at, e.policy, v.report
        FROM experiments e LEFT JOIN evaluations v ON e.id=v.experiment
        ORDER BY e.created_at, e.id""").fetchall()
    mirrors = store.mirror_ids()
    judged = []
    for identity, artifact, phase, created_at, policy, report in rows:
        if identity in mirrors:
            continue   # a LIVE mirror is the champion, not a challenger
        data = decode(report) if report else {}
        capital = Decimal(decode(policy)["capital_jpy"])
        blocks = equity_day_returns(store, identity, capital=capital,
                                    created_at=float(created_at), now=now)
        outcome = shadow.e_process(blocks) if blocks else {"blocks": 0, "e_value": 1.0,
                                                           "ruined": False, "promoted_day": None}
        reason = None
        if phase == "quarantined":
            reason = "quarantined"
        elif "valuation_stale" in (data.get("blockers") or []):
            reason = "valuation_stale"
        elif outcome["promoted_day"] is None:
            reason = "rule_not_met"
        elif artifact == live_artifact:
            reason = "already_live"
        mean = sum(value for _, value in blocks) / len(blocks) if blocks else 0.0
        live_mean = None
        if reason is None and live_returns is not None:
            live_mean = sum(live_returns.get(day, 0.0) for day, _ in blocks) / len(blocks)
            if not mean - live_mean > 0:
                reason = "not_better_than_live"
        judged.append({"experiment": identity, "artifact": artifact, "phase": phase,
                       "blocks": outcome["blocks"], "e_value": round(float(outcome["e_value"]), 4),
                       "mean_day_return": round(mean, 8),
                       "live_mean_day_return": None if live_mean is None else round(live_mean, 8),
                       "eligible": reason is None, "reason": reason})
    eligible = sorted((item for item in judged if item["eligible"]), key=lambda item: -item["e_value"])
    return {"schema_version": 1, "rule": RULE_ID, "judged_at": now,
            "threshold": shadow.THRESHOLD, "min_blocks": shadow.MIN_BLOCKS,
            "candidate": eligible[0] if eligible else None, "experiments": judged[-12:]}


def mirror_day_returns(store: LabStore, mirror: Mapping, *, now: float) -> dict[str, float]:
    exp = store.experiment(mirror["experiment"])
    return dict(equity_day_returns(store, mirror["experiment"],
                                   capital=Decimal(exp["policy"]["capital_jpy"]),
                                   created_at=float(exp["created_at"]), now=now))


def update_mirror(store: LabStore, report: Mapping, *, now: float) -> dict | None:
    """Start the LIVE mirror of the promoted candidate; ``None`` when nothing changes.

    The current mirror is replaced at most once per ``MIRROR_MIN_HOLD_S``; the judgement
    already required the challenger to beat it on the same days.
    """
    candidate = report.get("candidate")
    if not isinstance(candidate, Mapping):
        return None
    current = store.mirror()
    if current is not None:
        if current["artifact"] == candidate["artifact"] or now - current["promoted_at"] < MIRROR_MIN_HOLD_S:
            return None
        store.end_now(current["experiment"], now=now)
    identity = store.create(candidate["artifact"], now=now, capital=MIRROR_CAPITAL,
                            days=MIRROR_DAYS, mirror=True)
    return {"experiment": identity, "artifact": candidate["artifact"], "promoted_at": now,
            "from_experiment": candidate["experiment"],
            "replaced": None if current is None else current["experiment"]}


def write_report(directory: Path, payload: Mapping) -> None:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".promotion-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, directory / PROMOTION_FILENAME)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_report(trading_dir) -> dict | None:
    path = Path(trading_dir) / "free-strategies" / PROMOTION_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def run_improve(trading_dir, *, image: str, symbols: list[str], brief: str = DEFAULT_BRIEF,
                capital: str = "10000", days: int = DEFAULT_DAYS,
                now_fn: Callable[[], float] = time.time, text_fn=request_text,
                live_returns: Mapping[str, float] | None = None,
                live_artifact: str | None = None) -> dict:
    directory = Path(trading_dir) / "free-strategies"
    if (directory / DISABLED_FILENAME).exists():
        return {"mode": "PAPER", "status": "skipped", "reason": "disabled"}
    now = float(now_fn())
    store = LabStore(directory / "lab.sqlite3")
    try:
        retired = retire_risk_stopped(store, now=now)
        mirror = store.mirror()
        if mirror is not None and live_returns is None:
            live_returns = mirror_day_returns(store, mirror, now=now)
            live_artifact = mirror["artifact"]
        report = judge_promotion(store, now=now, live_returns=live_returns, live_artifact=live_artifact)
        report["mirror"] = update_mirror(store, report, now=now) or (
            None if mirror is None else {"experiment": mirror["experiment"], "artifact": mirror["artifact"],
                                         "promoted_at": mirror["promoted_at"]})
        write_report(directory, report)
        prior = _results(store)
        room = len(store.research_observed_ids()) < MAX_OBSERVED_EXPERIMENTS
    finally:
        store.close()
    summary = {"mode": "PAPER", "retired": retired,
               "mirror": None if report.get("mirror") is None else report["mirror"]["experiment"],
               "promotion_candidate": None if report["candidate"] is None
               else report["candidate"]["experiment"]}
    if not room:
        return {**summary, "status": "skipped", "reason": "experiment_capacity"}
    try:
        result = generate(Path(trading_dir), image=image, symbols=symbols, brief=brief,
                          capital=capital, days=days, now_fn=now_fn, text_fn=text_fn,
                          prior_results=prior)
    except StrategyError as exc:
        return {**summary, "status": "failed", "reason": str(exc)}
    return {**summary, **result}


__all__ = ["run_improve", "judge_promotion", "equity_day_returns", "retire_risk_stopped",
           "read_report", "RULE_ID", "PROMOTION_FILENAME", "DISABLED_FILENAME"]
