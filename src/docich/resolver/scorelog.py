"""Stable identifiers for resolver strategies stored in improvement logs,
plus the per-game score history backing the broadcast score-stats panel.

Basic display rule for every game on the stream: the overlay shows a score
histogram, a score history, and a strategy ranking.  This module owns the
data side:

- live match scores are appended to ``<state_dir>/scores/<game>.jsonl``
  (one JSON object per match end; producers: the resolver brain at
  game-over, the gnurobots binary's own scorelog append, game wrappers);
- strategy evaluations already live in
  ``<state_dir>/resolver/improve_log.jsonl`` (docich.resolver.improve);
  strategy_ranking() folds them into a per-strategy ranking keyed by the
  stable strategy key below.

summary() returns the three views; render_text() formats them as the text
section show_status.sh embeds in the broadcast sidebar.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from pathlib import Path

SCORES_DIRNAME = "scores"
IMPROVE_LOG_NAME = "improve_log.jsonl"


def strategy_key(strategy: Mapping[str, object]) -> str:
    """Return a deterministic content key for a resolver strategy."""
    payload = json.dumps(
        dict(strategy),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def scores_path(state_dir, game: str) -> Path:
    return Path(state_dir) / SCORES_DIRNAME / f"{game}.jsonl"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def record(state_dir, game: str, score: int, *, source: str = "live", extra: dict | None = None) -> dict:
    """Append one match-end score record (best effort, never raises)."""
    rec = {"ts": _now_iso(), "game": game, "score": int(score), "source": source}
    if extra:
        rec.update(extra)
    try:
        path = scores_path(state_dir, game)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return rec


def load_history(state_dir, game: str, limit: int = 20) -> list[dict]:
    """The most recent ``limit`` score records (oldest first)."""
    path = scores_path(state_dir, game)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:]:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and isinstance(item.get("score"), int):
            out.append(item)
    return out[-limit:]


def histogram(scores: list[int], buckets: int = 6) -> list[dict]:
    """Equal-width score buckets as [{"lo", "hi", "count"}] (empty -> [])."""
    clean = [int(s) for s in scores if isinstance(s, int)]
    if not clean or buckets < 1:
        return []
    lo, hi = min(clean), max(clean)
    if hi == lo:
        return [{"lo": lo, "hi": hi, "count": len(clean)}]
    width = (hi - lo) / buckets
    out = []
    for i in range(buckets):
        b_lo = lo + width * i
        b_hi = lo + width * (i + 1)
        inclusive_hi = i == buckets - 1
        count = sum(
            1
            for s in clean
            if b_lo <= s and (s <= b_hi if inclusive_hi else s < b_hi)
        )
        out.append({"lo": int(b_lo), "hi": int(b_hi), "count": count})
    return out


def strategy_ranking(state_dir, game: str, top: int = 5) -> list[dict]:
    """Fold the improve log into a per-strategy ranking for this game."""
    path = Path(state_dir) / "resolver" / IMPROVE_LOG_NAME
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    agg: dict[str, dict] = {}
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("game") != game:
            continue
        for trial in entry.get("trials", []) or []:
            strategy = trial.get("strategy")
            if not isinstance(strategy, dict):
                continue
            mean = trial.get("mean_score")
            if not isinstance(mean, (int, float)):
                continue
            key = strategy_key(strategy)
            slot = agg.setdefault(
                key, {"key": key, "strategy": strategy, "best": mean, "runs": 0, "wins": 0}
            )
            slot["runs"] += 1
            if mean > slot["best"]:
                slot["best"] = mean
        best_key = entry.get("best_strategy_key")
        if best_key and best_key in agg and entry.get("promoted"):
            agg[best_key]["wins"] += 1
    ranked = sorted(agg.values(), key=lambda s: s["best"], reverse=True)
    return ranked[:top]


def summary(state_dir, game: str, *, history_limit: int = 20, rank_top: int = 5) -> dict:
    history = load_history(state_dir, game, history_limit)
    return {
        "game": game,
        "history": history,
        "histogram": histogram([h["score"] for h in history]),
        "ranking": strategy_ranking(state_dir, game, rank_top),
    }


def render_text(summary: dict, *, width: int = 46, history_lines: int = 6) -> str:
    """Text section for the show_status dashboard / overlay sidebar."""
    out = ["SCORE STATS"]
    hist = summary.get("histogram") or []
    if hist:
        peak = max((h["count"] for h in hist), default=0) or 1
        out.append("ヒストグラム")
        for h in hist:
            bar = "#" * max(1, int(h["count"] * 18 / peak)) if h["count"] else "-"
            out.append(f" {h['lo']:>5}-{h['hi']:<5} {bar} {h['count']}")
    else:
        out.append("ヒストグラム: データなし")
    history = summary.get("history") or []
    if history:
        out.append("スコア履歴(新しい順)")
        recent = list(reversed(history))[:history_lines]
        line = " ".join(f"#{i + 1}:{h['score']}" for i, h in enumerate(recent))
        for chunk in [line[i:i + width] for i in range(0, len(line), width)]:
            out.append(" " + chunk)
        if len(history) > len(recent):
            out.append(f" ...ほか{len(history) - len(recent)}件")
    else:
        out.append("スコア履歴: データなし")
    ranking = summary.get("ranking") or []
    if ranking:
        out.append("戦略ランキング")
        for i, s in enumerate(ranking, 1):
            out.append(f" {i}. {s['key'][:8]} avg {int(s['best'])} win{s['wins']} x{s['runs']}")
    else:
        out.append("戦略ランキング: データなし")
    return "\n".join(out)


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="docich.resolver.scorelog")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("--game", required=True)
    rec.add_argument("--score", type=int, required=True)
    rec.add_argument("--source", default="manual")
    rec.add_argument("--state-dir", default=None)
    summ = sub.add_parser("summary")
    summ.add_argument("--game", required=True)
    summ.add_argument("--state-dir", default=None)
    summ.add_argument("--text", action="store_true")
    args = ap.parse_args(argv)

    state_dir = args.state_dir or Path("run")
    if args.cmd == "record":
        print(json.dumps(record(state_dir, args.game, args.score, source=args.source), ensure_ascii=False))
        return 0
    data = summary(state_dir, args.game)
    if args.text:
        print(render_text(data))
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
