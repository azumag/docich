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
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from ..adapters.cli_game import cli_cols, cli_command_list, cli_rows
from ..config import load_game, load_global
from . import resolver_policy, strategy_path
from . import gnurobots as gnurobots_resolver
from .runner import EvaluationCleanupError, _session_absent, resolve_command, run_match
from .lease import activity_lock

GNUROBOTS_BIN = os.environ.get("GNUROBOTS_BIN", "/usr/local/bin/gnurobots")
GNUROBOTS_MAP = os.environ.get("GNUROBOTS_MAP", "/usr/local/share/gnurobots/maps/small.map")
GNUROBOTS_RESOLVER = os.environ.get(
    "GNUROBOTS_RESOLVER", "/usr/local/share/gnurobots/resolver.scm"
)


class ImprovementLeaseLost(RuntimeError):
    """The active game/generation changed while an evaluation was running."""


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


def _run_match_gnurobots(script_text: str, *, interval_s: float = 0.25, max_s: float = 300.0, guard=None, session_hook=None) -> dict:
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
        if session_hook is not None:
            session_hook("add",session)
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
            if guard is not None:
                guard()
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
        if not _session_absent(_tmux(["has-session", "-t", session])):
            raise EvaluationCleanupError(f"評価用セッションの停止に失敗しました: {session}")
        if session_hook is not None:
            session_hook("remove",session)
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
    guard=None,
    session_hook=None,
) -> dict:
    script = gnurobots_resolver.render(strategy)
    results = []
    for _ in range(matches):
        if guard is not None:
            guard()
        try:
            results.append(_run_match_gnurobots(script, interval_s=interval_ms / 1000, max_s=max_s, guard=guard, session_hook=session_hook))
        except (ImprovementLeaseLost,EvaluationCleanupError):
            raise
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
    guard=None,
    session_hook=None,
) -> dict:
    game = load_game(g, game_name)
    cmd = resolve_command(cli_command_list(game))
    results = []
    for _ in range(matches):
        if guard is not None:
            guard()
        try:
            results.append(
                run_match(
                    cmd,
                    cli_cols(game),
                    cli_rows(game),
                    lambda text, st=strategy: policy(text, st),
                    interval_s=interval_ms / 1000,
                    max_turns=max_turns,
                    guard=guard,
                    session_hook=session_hook,
                )
            )
        except (ImprovementLeaseLost,EvaluationCleanupError):
            raise
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
    expected_identity=None,
    session_hook=None,
) -> dict:
    def guard():
        if expected_identity is not None and active_runtime_identity(g) != expected_identity:
            raise ImprovementLeaseLost("game switch changed the improvement lease")

    guard()
    rng = random.Random(seed)
    s_file = strategy_path(g.state_dir, game_name)
    base = read_strategy_for_game(game_name, s_file)
    if game_name == "gnurobots":
        base_ev = evaluate_gnurobots(base, matches, guard=guard, session_hook=session_hook)
    else:
        policy = resolver_policy(game_name)
        base_ev = evaluate(policy, base, g, game_name, matches, guard=guard, session_hook=session_hook)
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
        guard()
        cand = perturb(base, rng)
        if game_name == "gnurobots":
            ev = evaluate_gnurobots(cand, matches, guard=guard, session_hook=session_hook)
        else:
            ev = evaluate(policy, cand, g, game_name, matches, guard=guard, session_hook=session_hook)
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
    guard()
    if promoted:
        from ..game_switch import GameSwitchStore
        store=GameSwitchStore(Path(g.state_dir))
        try:
            with store.lock(exclusive=False,blocking=False):
                state,_=store.canonical.load()
                active=state.get("active")
                current=(active.get("game"),active.get("generation"),active.get("lease_id")) if state.get("phase")=="ready" and isinstance(active,dict) else None
                if expected_identity is not None and current != expected_identity:
                    raise ImprovementLeaseLost("game switch changed the improvement lease")
                _promote(g, game_name, s_file, base, best_strategy)
        except ImprovementLeaseLost:
            raise
        except Exception as exc:
            from ..game_switch import GameSwitchBusyError
            if isinstance(exc,GameSwitchBusyError):
                raise ImprovementLeaseLost("game switch owns the improvement lease") from exc
            raise
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


def active_runtime_identity(g):
    """Return the ready runtime lease; unknown/draining state is never active."""
    try:
        from ..game_switch import GameSwitchStore

        state, _ = GameSwitchStore(Path(g.state_dir)).canonical.load()
        active = state.get("active")
        if state.get("phase") != "ready" or not isinstance(active, dict):
            return None
        game, generation, lease_id = active.get("game"), active.get("generation"), active.get("lease_id")
        if not isinstance(game, str) or not isinstance(generation, int) or not isinstance(lease_id, str):
            return None
        return game, generation, lease_id
    except Exception:
        return None


def active_game(g) -> str | None:
    identity = active_runtime_identity(g)
    return identity[0] if identity else None


def _claim_activity_marker(marker: Path, claim: dict) -> None:
  with activity_lock(marker.parents[2],claim["game"]):
    marker.parent.mkdir(parents=True,exist_ok=True)
    while True:
        try:
            fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        except FileExistsError:
            try:
                previous=json.loads(marker.read_text(encoding="utf-8"))
                old_pid=int(previous["pid"]); sessions=previous.get("sessions",[])
            except (OSError,ValueError,KeyError,TypeError) as exc:
                raise EvaluationCleanupError("既存の改善所有markerが不正です") from exc
            if not isinstance(sessions,list) or any(not isinstance(s,str) or not s.startswith(f"evalr-{old_pid}-") for s in sessions):
                raise EvaluationCleanupError("既存の改善session所有情報が不正です")
            try:
                os.kill(old_pid,0); owner_alive=True
            except ProcessLookupError:
                owner_alive=False
            except PermissionError as exc:
                raise EvaluationCleanupError("既存の改善ownerを確認できません") from exc
            if owner_alive:
                raise EvaluationCleanupError("既存の改善ownerが生存しています")
            for session in sessions:
                result=subprocess.run(["tmux","has-session","-t",session],capture_output=True,text=True)
                if not _session_absent(result):
                    raise EvaluationCleanupError(f"既存の評価sessionが残っています: {session}")
            marker.unlink()
            continue
        with os.fdopen(fd,"w",encoding="utf-8") as fh:
            json.dump(claim,fh,ensure_ascii=False,separators=(",",":"));fh.write("\n");fh.flush();os.fsync(fh.fileno())
        return


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
        identity = active_runtime_identity(g)
        if identity is None or identity[0] != game_name:
            print(f"[improve] skip: {game_name!r} is not the ready active game", flush=True)
            time.sleep(5.0)
            continue
        else:
            marker=Path(g.state_dir)/"resolver"/"active"/f"{game_name}.json"
            from ..game_switch import atomic_write_json
            marker_state={"pid":os.getpid(),"game":game_name,"generation":identity[1],"lease_id":identity[2],"sessions":[]}
            try:
                _claim_activity_marker(marker,marker_state)
            except EvaluationCleanupError as exc:
                print(f"[improve] blocked by previous cleanup: {exc}",flush=True)
                return
            def session_hook(action,session):
                with activity_lock(Path(g.state_dir),game_name):
                    sessions=marker_state["sessions"]
                    if action=="add" and session not in sessions: sessions.append(session)
                    if action=="remove" and session in sessions: sessions.remove(session)
                    atomic_write_json(marker,marker_state)
            lease_lost=False
            cleanup_failed=False
            try:
                s = improve_once(
                    g,
                    game_name,
                    matches=matches,
                    candidates=candidates,
                    margin_pct=margin_pct,
                    expected_identity=identity,
                    session_hook=session_hook,
                )
                print(
                    f"[improve] baseline={s['baseline']:.1f} best={s['best']:.1f} "
                    f"promoted={s['promoted']}",
                    flush=True,
                )
            except ImprovementLeaseLost:
                lease_lost=True
                print("[improve] cancelled: game switch changed the active lease", flush=True)
            except EvaluationCleanupError as exc:
                cleanup_failed=True
                marker_state["cleanup_error"]=str(exc)[:300]
                with activity_lock(Path(g.state_dir),game_name): atomic_write_json(marker,marker_state)
                print(f"[improve] fatal cleanup error: {exc}",flush=True)
            except Exception as exc:  # 1サイクルの失敗でデーモンを落とさない
                print(f"[improve] error: {exc}", flush=True)
            finally:
                if not cleanup_failed:
                    with activity_lock(Path(g.state_dir),game_name):
                        try: marker.unlink()
                        except FileNotFoundError: pass
            if cleanup_failed:
                return
            if lease_lost:
                time.sleep(1.0)
                continue
        elapsed = time.monotonic() - started
        remaining=max(cycle_s - elapsed, 60.0)
        while remaining > 0:
            time.sleep(min(5.0, remaining))
            remaining -= min(5.0, remaining)


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
