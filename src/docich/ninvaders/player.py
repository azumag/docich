"""Live in-play driver for nInvaders.

Launched by ``games/cli-wrappers/ninvaders_docich.sh`` next to the game (the
wrapper keeps owning title start, score recording and the match cap).  It runs
the SAME frame parser, sandbox and tick rate the evaluator uses, against the
policy the improvement loop last promoted (``PolicyStore.current()``), so what
was measured is what plays.

* the policy is (re)selected when a match starts, never mid-match (hot-swap at
  the match boundary);
* if the policy cannot run (rejected source, worker dead) it plays the fixed
  sweep instead — the corner must never stall on a bad promotion;
* it never sends keys outside a live match; the wrapper starts matches.
"""
from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import frame as F
from .nudge import Nudge
from .sandbox import PolicyProcess, PolicyRejected
from .store import PolicyStore

TICK_S = 0.1


def _log(msg: str) -> None:
    print(f"[ninvaders-player] {msg}", file=sys.stderr, flush=True)


def _capture(pane: str) -> str | None:
    try:
        r = subprocess.run(["tmux", "capture-pane", "-p", "-t", pane],
                           capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _send(pane: str, keys: list[str]) -> None:
    if not keys:
        return
    try:
        subprocess.run(["tmux", "send-keys", "-t", pane, *keys], capture_output=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        pass


def _sweep(state: dict) -> list[str]:
    n = state.get("n", 0)
    state["n"] = n + 1
    return ["Right" if (n // 8) % 2 == 0 else "Left", "Space"]


def open_policy(store: PolicyStore) -> tuple[PolicyProcess | None, dict]:
    """Current promoted policy, else the tracked baseline, else None (sweep)."""
    tried = []
    for entry in (store.current(), store.baseline_entry()):
        if entry["path"] in tried:
            continue
        tried.append(entry["path"])
        try:
            return PolicyProcess(entry["path"]), entry
        except (PolicyRejected, OSError) as exc:
            _log(f"policy {entry['sha']} unusable: {exc}")
    return None, {"sha": "sweep", "origin": "fallback"}


def run(pane: str, store: PolicyStore, *, tick_s: float = TICK_S, stop=lambda: False) -> None:
    runner: PolicyProcess | None = None
    entry: dict = {}
    sweep_state: dict = {}
    nudge = Nudge()
    in_match = False
    ticks = 0
    best = 0
    try:
        while not stop():
            t0 = time.monotonic()
            text = _capture(pane)
            if text is None:
                time.sleep(0.5)
                continue
            obs = F.parse(text, ticks)
            kind = obs["kind"]
            if kind in ("play", "gameover") and obs["score"] is not None:
                best = max(best, obs["score"])
            if kind == "play":
                if not in_match:
                    in_match, ticks, sweep_state, best = True, 0, {}, 0
                    nudge = Nudge()
                    runner, entry = open_policy(store)
                    _log(f"match start policy={entry['sha']} origin={entry['origin']}")
                keys = runner.tick(F.obs_for_policy(obs)) if runner and not runner.dead else _sweep(sweep_state)
                keys = nudge.keys(obs) or keys
                if runner and runner.dead:
                    _log(f"policy {entry['sha']} died ({runner.last_error}); sweeping")
                    runner.close()
                    runner = None
                _send(pane, keys)
                ticks += 1
            elif kind == "title" and in_match:
                if runner is not None:
                    _log(f"match end policy={entry['sha']} score={best} ticks={ticks} stats={runner.stats()}")
                    runner.close()
                    runner = None
                in_match = False
            spare = tick_s - (time.monotonic() - t0)
            if spare > 0:
                time.sleep(spare)
    finally:
        if runner is not None:
            runner.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="docich.ninvaders.player")
    ap.add_argument("--pane", required=True, help="tmux pane running the game")
    ap.add_argument("--policy-dir", required=True, help="<state_dir>/resolver/ninvaders")
    ap.add_argument("--tick", type=float, default=TICK_S)
    args = ap.parse_args(argv)
    flag = {"stop": False}

    def _term(_sig, _frm):
        flag["stop"] = True

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    run(args.pane, PolicyStore(Path(args.policy_dir)), tick_s=args.tick, stop=lambda: flag["stop"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
