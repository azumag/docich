"""Periodic improvement loop for resolver strategies.

Token-free by default: candidates are parameter perturbations of the current
strategy, evaluated by playing real headless matches (docich.resolver.runner).
A candidate is promoted only when its mean score beats the current strategy by
``--margin-pct``; the previous strategy is archived under
``<state_dir>/resolver/history/`` and the loop appends every cycle to
``<state_dir>/resolver/improve_log.jsonl``.

Per-game evaluation:
- ``robots`` (BSD robots): the deterministic pane-parsing policy drives the
  match, keys are chosen every iteration.
- ``gnurobots``: the game self-plays a rendered Scheme program
  (docich.resolver.gnurobots.render); a match is the program running to its
  end and the STATISTICS block is the result.  A promotion re-renders the
  live ``/usr/local/share/gnurobots/resolver.scm`` so the match-loop wrapper
  picks it up at the next match start.

Switch-aware: when a game-switch canonical is present the loop only improves
while that game is the active runtime, and otherwise sleeps (a finished
game's improvement loop must not keep running for another game).

LLM-proposed candidates are an intentional future extension: the policy
structure is fixed, so parameter search covers the space at zero token cost.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path

from ..adapters.cli_game import cli_cols, cli_command_list, cli_rows
from ..config import load_game, load_global
from . import resolver_policy, strategy_path
from . import gnurobots as gnurobots_resolver
from .runner import resolve_command, run_match

GNUROBOTS_BIN = os.environ.get("GNUROBOTS_BIN", "/usr/local/bin/gnurobots")
GNUROBOTS_MAP = os.environ.get("GNUROBOTS_MAP", "/usr/local/share/gnurobots/maps/small.map")
GNUROBOTS_RESOLVER = os.environ.get(
    "GNUROBOTS_RESOLVER", "/usr/local/share/gnurobots/resolver.scm"
)


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


def _run_match_gnurobots(script_text: str, *, interval_s: float = 0.25, max_s: float = 300.0) -> dict:
    """Play one gnurobots match: the rendered script runs to its end.

    The game process exits when the program finishes or the robot dies; the
    STATISTICS block in the final pane capture is the result.
    """
    import shlex
    import subprocess

    fd, script_path = tempfile.mkstemp(suffix=".scm", prefix="grb-resolver-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(script_text)
    fd2, log_path = tempfile.mkstemp(suffix=".log", prefix="grb-run-")
    os.close(fd2)
    session = f"evalr-{os.getpid()}-{int(time.time() * 1000) % 1000000}"

    def _tmux(args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(["tmux", *args], capture_output=True, text=True)

    try:
        _tmux(["kill-session", "-t", session])
        # The game prints STATISTICS to stdout at exit.  Redirect it into a
        # log file: when the game process is the session command, its exit
        # closes the whole session, so a tmux capture of the final screen
        # taken after the fact comes back empty.  The trailing sleep keeps
        # the pane alive a moment so the last screen is still snapshotted.
        created = _tmux(
            [
                "new-session", "-d", "-x", "80", "-y", "24", "-s", session,
                "sh",
                "-c",
                f"{GNUROBOTS_BIN} -f {GNUROBOTS_MAP} {shlex.quote(script_path)} "
                f"> {shlex.quote(log_path)} 2>&1; echo EXIT:$?; sleep 2",
            ]
        )
        if created.returncode != 0:
            raise RuntimeError(f"評価用セッションの起動に失敗しました: {created.stderr.strip()}")
        start = time.monotonic()
        dead = False
        last_text = ""
        while time.monotonic() - start < max_s:
            time.sleep(interval_s)
            listed = _tmux(["list-panes", "-t", session, "-F", "#{pane_dead}"])
            if listed.returncode != 0 or not listed.stdout.strip():
                # the game IS the session command: its exit closes the session
                dead = True
                break
            dead = "1" in listed.stdout.split()
            text = _tmux(["capture-pane", "-p", "-t", session]).stdout
            if text:
                last_text = text
            if dead:
                break
        try:
            with open(log_path, encoding="utf-8", errors="replace") as fh:
                stats = gnurobots_resolver.parse_statistics(fh.read())
        except OSError:
            stats = {"score": None, "energy": None, "shields": None}
        if stats["score"] is None:
            # Fallback: the last live screen may still show the status line.
            stats = gnurobots_resolver.parse_statistics(last_text)
        return {
            "score": stats["score"],
            "energy": stats["energy"],
            "shields": stats["shields"],
            "timed_out": not dead,
            "seconds": round(time.monotonic() - start, 1),
        }
    finally:
        _tmux(["kill-session", "-t", session])
        for junk in (script_path, log_path):
            try:
                os.unlink(junk)
            except OSError:
                pass


def evaluate_gnurobots(
    strategy: dict,
    matches: int,
    *,
    interval_ms: int = 250,
    max_s: float = 300.0,
) -> dict:
    script = gnurobots_resolver.render(strategy)
    results = []
    for _ in range(matches):
        try:
            results.append(_run_match_gnurobots(script, interval_s=interval_ms / 1000, max_s=max_s))
        except Exception as exc:  # 一試合の失敗でサイクルを落とさない
            results.append({"score": None, "turns": None, "error": str(exc)})
    scores = [r["score"] for r in results if isinstance(r.get("score"), int)]
    mean = sum(scores) / len(scores) if scores else 0.0
    return {"matches": results, "mean_score": mean, "played": len(scores)}


def _game_defaults(game_name: str) -> dict:
    if game_name == "gnurobots":
        return dict(gnurobots_resolver.DEFAULT_STRATEGY)
    return _robots_defaults()


def _robots_defaults() -> dict:
    from .robots import DEFAULT_STRATEGY as ROBOTS_DEFAULTS

    return dict(ROBOTS_DEFAULTS)


def read_strategy_for_game(game_name: str, path: Path) -> dict:
    """Current strategy merged over the game's own defaults."""
    st = _game_defaults(game_name)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return st
    if isinstance(data, dict):
        st.update({k: v for k, v in data.items() if k in st})
    return st


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
    s_file = strategy_path(g.state_dir, game_name)
    base = read_strategy_for_game(game_name, s_file)
    if game_name == "gnurobots":
        base_ev = evaluate_gnurobots(base, matches)
    else:
        policy = resolver_policy(game_name)
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
        if game_name == "gnurobots":
            ev = evaluate_gnurobots(cand, matches)
        else:
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
        _promote(g, game_name, s_file, base, best_strategy)
    from . import scorelog

    summary = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "game": game_name,
        "baseline": base_ev["mean_score"],
        "best": best_score,
        "best_strategy_key": scorelog.strategy_key(best_strategy),
        "promoted": promoted,
        "trials": trials,
    }
    _append_log(g.state_dir, game_name, summary)
    return summary


def _promote(g, game_name: str, s_file: Path, old: dict, new: dict) -> None:
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
    if game_name == "gnurobots":
        # Live hot-swap: the match-loop wrapper re-reads the script at the
        # next match start.
        Path(GNUROBOTS_RESOLVER).write_text(gnurobots_resolver.render(new), encoding="utf-8")


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
