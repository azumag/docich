"""Periodic improvement loop for resolver strategies.

Token-free by default: candidates are parameter perturbations of the current
strategy, evaluated by playing real headless matches (docich.resolver.runner).
A candidate is promoted only when its mean score beats the current strategy by
``--margin-pct``; the previous strategy is archived under
``<state_dir>/resolver/history/`` and the loop appends every cycle to
``<state_dir>/resolver/improve_log.jsonl``.

Switch-aware: when a game-switch canonical is present the loop only improves
while that game is the active runtime, and otherwise sleeps (a finished
game's improvement loop must not keep running for another game).

LLM-proposed candidates are an intentional future extension: the policy
structure is fixed, so parameter search covers the space at zero token cost.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

from ..adapters.cli_game import cli_cols, cli_command_list, cli_rows
from ..config import load_game, load_global
from . import read_strategy, resolver_policy, strategy_path
from .runner import resolve_command, run_match


def perturb(strategy: dict, rng: random.Random) -> dict:
    """Mutate one numeric weight by a random factor (token-free candidate)."""
    keys = [
        k
        for k, v in strategy.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    if not keys:
        return dict(strategy)
    out = dict(strategy)
    key = rng.choice(keys)
    out[key] = round(max(0.05, float(strategy[key]) * rng.uniform(0.6, 1.6)), 4)
    return out


def evaluate(
    policy,
    strategy: dict,
    g,
    game_name: str,
    matches: int,
    *,
    interval_ms: int = 60,
    max_turns: int = 4000,
) -> dict:
    game = load_game(g, game_name)
    cmd = resolve_command(cli_command_list(game))
    results = []
    for _ in range(matches):
        try:
            results.append(
                run_match(
                    cmd,
                    cli_cols(game),
                    cli_rows(game),
                    lambda text, st=strategy: policy(text, st),
                    interval_s=interval_ms / 1000,
                    max_turns=max_turns,
                )
            )
        except Exception as exc:  # 一試合の失敗でサイクルを落とさない
            results.append({"score": None, "turns": None, "error": str(exc)})
    scores = [r["score"] for r in results if isinstance(r.get("score"), int)]
    mean = sum(scores) / len(scores) if scores else 0.0
    return {"matches": results, "mean_score": mean, "played": len(scores)}


def improve_once(
    g,
    game_name: str,
    *,
    matches: int = 3,
    candidates: int = 4,
    margin_pct: float = 10.0,
    seed=None,
) -> dict:
    rng = random.Random(seed)
    policy = resolver_policy(game_name)
    s_file = strategy_path(g.state_dir, game_name)
    base = read_strategy(s_file)
    base_ev = evaluate(policy, base, g, game_name, matches)
    trials = [
        {
            "kind": "baseline",
            "mean_score": base_ev["mean_score"],
            "scores": [m.get("score") for m in base_ev["matches"]],
            "strategy": base,
        }
    ]
    best_score, best_strategy = base_ev["mean_score"], base
    for _ in range(candidates):
        cand = perturb(base, rng)
        ev = evaluate(policy, cand, g, game_name, matches)
        trials.append(
            {
                "kind": "candidate",
                "mean_score": ev["mean_score"],
                "scores": [m.get("score") for m in ev["matches"]],
                "changed": {k: cand[k] for k in cand if cand.get(k) != base.get(k)},
                "strategy": cand,
            }
        )
        if ev["mean_score"] > best_score * (1 + margin_pct / 100.0):
            best_score, best_strategy = ev["mean_score"], cand
    promoted = best_strategy is not base
    if promoted:
        _promote(s_file, base, best_strategy)
    summary = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "game": game_name,
        "baseline": base_ev["mean_score"],
        "best": best_score,
        "promoted": promoted,
        "trials": trials,
    }
    _append_log(g.state_dir, game_name, summary)
    return summary


def _promote(s_file: Path, old: dict, new: dict) -> None:
    s_file.parent.mkdir(parents=True, exist_ok=True)
    history = s_file.parent / "history"
    history.mkdir(exist_ok=True)
    if s_file.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (history / f"{stamp}.json").write_text(
            json.dumps(old, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    s_file.write_text(
        json.dumps(new, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _append_log(state_dir, game_name: str, summary: dict) -> None:
    log_dir = Path(state_dir) / "resolver"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "improve_log.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")


def active_game(g) -> str | None:
    """Best-effort canonical active game (None when unknown)."""
    try:
        from ..game_switch import GameSwitchStore

        state, _ = GameSwitchStore(Path(g.state_dir)).canonical.load()
        active = state.get("active")
        return active.get("game") if isinstance(active, dict) else None
    except Exception:
        return None


def run_daemon(
    g,
    game_name: str,
    *,
    cycle_s: float,
    matches: int,
    candidates: int,
    margin_pct: float,
) -> None:
    print(
        f"[improve] game={game_name} cycle={cycle_s:.0f}s matches={matches} "
        f"candidates={candidates}",
        flush=True,
    )
    while True:
        started = time.monotonic()
        current = active_game(g)
        if current is not None and current != game_name:
            print(f"[improve] skip: active game is {current!r}", flush=True)
        else:
            try:
                s = improve_once(
                    g,
                    game_name,
                    matches=matches,
                    candidates=candidates,
                    margin_pct=margin_pct,
                )
                print(
                    f"[improve] baseline={s['baseline']:.1f} best={s['best']:.1f} "
                    f"promoted={s['promoted']}",
                    flush=True,
                )
            except Exception as exc:  # 1サイクルの失敗でデーモンを落とさない
                print(f"[improve] error: {exc}", flush=True)
        elapsed = time.monotonic() - started
        time.sleep(max(cycle_s - elapsed, 60.0))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="docich.resolver.improve", description="リゾルバ戦略の定期改善 (トークン不要)"
    )
    ap.add_argument("game")
    ap.add_argument("--config", default="config/docich.toml")
    ap.add_argument("--daemon", action="store_true")
    ap.add_argument("--cycle-min", type=float, default=60.0)
    ap.add_argument("--matches", type=int, default=3)
    ap.add_argument("--candidates", type=int, default=4)
    ap.add_argument("--margin-pct", type=float, default=10.0)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args(argv)
    # src/docich/resolver/improve.py -> parents[3] = repo root (cli_game.py と同じ)
    repo_root = Path(__file__).resolve().parents[3]
    g = load_global(repo_root, Path(args.config))
    if args.daemon:
        run_daemon(
            g,
            args.game,
            cycle_s=args.cycle_min * 60,
            matches=args.matches,
            candidates=args.candidates,
            margin_pct=args.margin_pct,
        )
        return 0
    summary = improve_once(
        g,
        args.game,
        matches=args.matches,
        candidates=args.candidates,
        margin_pct=args.margin_pct,
        seed=args.seed,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
