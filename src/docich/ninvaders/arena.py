"""Headless evaluator for nInvaders policies against the REAL game (tmux).

The play loop uses exactly the code the live player uses (``frame.parse`` +
``sandbox.PolicyProcess``) at the same tick rate, so an evaluated policy
behaves in production the way it did here.  A match ends when the title screen
returns after play (the game draws "GAME OVER" as block letters, never as
text); a policy that is still alive at ``max_seconds`` is a valid, censored
result — surviving is not a failure.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import frame as F
from .nudge import Nudge
from .sandbox import PolicyProcess

TICK_S = 0.1
VALID_ENDS = ("title", "timeout")


class ArenaError(RuntimeError):
    """Infrastructure problem (no tmux / no game binary)."""


def _tmux(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux", *args], capture_output=True, text=True)


def resolve_binary() -> list[str]:
    raw = os.environ.get("DOCICH_NINVADERS_BIN", "").strip()
    if raw:
        return shlex.split(raw)
    for cand in (shutil.which("ninvaders"), "/usr/games/ninvaders", "/usr/local/games/ninvaders"):
        if cand and Path(cand).exists():
            return [cand]
    raise ArenaError("ninvaders 実行ファイルが見つかりません (DOCICH_NINVADERS_BIN で指定可)")


def play_match(policy_path, *, binary: list[str] | None = None, tick_s: float = TICK_S,
               max_seconds: float = 240.0, boot_s: float = 1.5, start_timeout_s: float = 10.0,
               policy_kwargs: dict | None = None) -> dict:
    """Play one match; never raises for game/policy trouble (see result["end"])."""
    binary = binary or resolve_binary()
    if shutil.which("tmux") is None:
        raise ArenaError("tmux が見つかりません")
    session = f"nva-{os.getpid()}-{threading.get_ident() % 100000}-{int(time.time() * 1000) % 100000}"
    created = _tmux("new-session", "-d", "-x", "80", "-y", "24", "-s", session, shlex.join(binary))
    if created.returncode != 0:
        raise ArenaError(f"評価用セッションの起動に失敗しました: {created.stderr.strip()[:160]}")
    policy = None
    out: dict = {"end": "error", "score": None, "ticks": 0, "seconds": 0.0, "level": 1,
                 "lives_min": None, "cause": None, "last_frames": [], "policy": {}}
    try:
        time.sleep(boot_s)
        t0 = time.monotonic()
        started = False
        while time.monotonic() - t0 < start_timeout_s:
            cap = _tmux("capture-pane", "-p", "-t", session)
            if cap.returncode != 0:
                out["end"] = "session-lost"
                return out
            kind = F.classify(cap.stdout)
            if kind == "play":
                started = True
                break
            if kind == "title":
                _tmux("send-keys", "-t", session, "Space")
            time.sleep(0.3)
        if not started:
            out["end"] = "no-start"
            return out
        out["score"] = 0  # a game always starts at 0; keeps an instant timeout a valid result
        policy = PolicyProcess(policy_path, **(policy_kwargs or {}))
        nudge = Nudge()
        play_t0 = time.monotonic()
        seen_play = True  # the start loop above only exits on a play frame
        ticks = 0
        recent: list[str] = []
        last_obs: dict | None = None
        while True:
            loop_t = time.monotonic()
            if loop_t - play_t0 > max_seconds:
                out["end"] = "timeout"
                break
            cap = _tmux("capture-pane", "-p", "-t", session)
            if cap.returncode != 0:
                out["end"] = "session-lost"
                break
            obs = F.parse(cap.stdout, ticks)
            kind = obs["kind"]
            if kind in ("play", "gameover") and obs["score"] is not None:
                out["score"] = max(out["score"] or 0, obs["score"])
                out["level"] = max(out["level"], obs["level"] or 1)
            if kind == "play":
                seen_play = True
                last_obs = obs
                if obs["lives"] is not None:
                    out["lives_min"] = obs["lives"] if out["lives_min"] is None else min(out["lives_min"], obs["lives"])
                recent = (recent + [cap.stdout])[-3:]
                policy_keys = policy.tick(F.obs_for_policy(obs))
                keys = nudge.keys(obs) or policy_keys
                if keys:
                    _tmux("send-keys", "-t", session, *keys)
                ticks += 1
            elif kind == "title" and seen_play:
                out["end"] = "title"
                break
            spare = tick_s - (time.monotonic() - loop_t)
            if spare > 0:
                time.sleep(spare)
        out["ticks"] = ticks
        out["seconds"] = round(time.monotonic() - play_t0, 1)
        out["last_frames"] = recent[-2:]
        if last_obs is not None:
            deepest = max((y for _, y in last_obs["aliens"]), default=0)
            out["cause"] = "invasion" if deepest >= 19 else "bombs"
        out["policy"] = policy.stats()
        return out
    finally:
        if policy is not None:
            policy.close()
        # "=" forces an exact session-name match: without it, tmux falls back to a
        # PREFIX match when the session already died and could kill a parallel match.
        _tmux("kill-session", "-t", f"={session}")


def _safe_match(policy_path, kwargs) -> dict:
    try:
        return play_match(policy_path, **kwargs)
    except ArenaError:
        raise
    except Exception as exc:  # noqa: BLE001 - one bad match must not sink the batch
        return {"end": "error", "score": None, "error": f"{type(exc).__name__}: {str(exc)[:160]}",
                "ticks": 0, "seconds": 0.0, "policy": {}}


def summarize(matches: list[dict]) -> dict:
    valid = [m for m in matches if m.get("end") in VALID_ENDS and isinstance(m.get("score"), int)]
    scores = [m["score"] for m in valid]
    ticks = sum(int(m.get("ticks") or 0) for m in matches)
    faults = sum(int((m.get("policy") or {}).get("timeouts", 0)) + int((m.get("policy") or {}).get("errors", 0))
                 for m in matches)
    return {
        "n": len(matches), "played": len(valid), "incomplete": len(matches) - len(valid),
        "censored": sum(1 for m in valid if m.get("end") == "timeout"),
        "scores": scores,
        "mean": statistics.fmean(scores) if scores else 0.0,
        "median": statistics.median(scores) if scores else 0.0,
        "stdev": statistics.pstdev(scores) if len(scores) > 1 else 0.0,
        "min": min(scores) if scores else 0, "max": max(scores) if scores else 0,
        "policy_faults": faults, "policy_fault_rate": (faults / ticks) if ticks else 0.0,
    }


def evaluate(policy_path, matches: int, *, parallel: int = 4, **kwargs) -> dict:
    """Play ``matches`` matches (``parallel`` at a time) and summarize."""
    matches = max(1, int(matches))
    with ThreadPoolExecutor(max_workers=max(1, min(int(parallel), matches))) as pool:
        results = list(pool.map(lambda _i: _safe_match(policy_path, kwargs), range(matches)))
    return {"summary": summarize(results), "matches": results}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="docich.ninvaders.arena",
                                 description="nInvaders ポリシーを実ゲームで評価する (トークン不要)")
    ap.add_argument("--policy", required=True)
    ap.add_argument("--matches", type=int, default=4)
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--max-seconds", type=float, default=240.0)
    ap.add_argument("--tick", type=float, default=TICK_S)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    result = evaluate(args.policy, args.matches, parallel=args.parallel,
                      max_seconds=args.max_seconds, tick_s=args.tick)
    for m in result["matches"]:
        m["last_frames"] = f"<{len(m.get('last_frames') or [])} frames>"
    print(json.dumps(result["summary"], ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
