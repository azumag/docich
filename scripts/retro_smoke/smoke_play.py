#!/usr/bin/env python3
"""Drive a real CLI retro game through docich's own adapter / brain path.

Runs inside the throwaway container from run.sh, against a *copy* of the repo
(/work).  It uses the repo's real CliGameAdapter (tmux pane), the real
CommandBrain (subprocess brain script) and the real agent loop iteration -- only
the outer coordinator (display / stream / switch lock) is left out.  Wrappers
write their scorelog to $<GAME>_SCORELOG, which run.sh points into /out.

usage: smoke_play.py <game> <seconds>

env:
  SMOKE_NULL_BRAIN=1   never press game keys (observe the wrapper alone, e.g. to
                       watch a game end by itself)
  SMOKE_INTERVAL_MS=N  override the brain/observe interval
  SMOKE_DUMP_ALL=1     also write every observed pane to panes_all.jsonl

Outputs in /out/<game>/: summary.json, snapshots.json (first panes of every
distinct screen kind + periodic + per-change), actions.jsonl, final_pane.txt.
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(os.environ.get("REPO", "/work"))
sys.path.insert(0, str(REPO / "src"))

from docich.adapters import make_adapter  # noqa: E402
from docich.agent import brains  # noqa: E402
from docich.agent.loop import _run_iteration  # noqa: E402
from docich.config import load_game, load_global  # noqa: E402

# Screen kinds worth keeping verbatim: menus, dialogs, game-over, name entry.
KEYWORDS = (
    "game over", "play again", "press any", "try again", "enter your name",
    "new game", "start game", "main menu", "retry", "high score", "highscore",
    "paused", "play!", "difficulty", "quit",
)

game_name = sys.argv[1]
seconds = float(sys.argv[2])
out_dir = Path(os.environ.get("OUT", "/out")) / game_name
out_dir.mkdir(parents=True, exist_ok=True)

g = load_global(REPO)
game = load_game(g, game_name)
adapter = make_adapter(g, game)


class NullBrain:
    """[agent] enabled=false games (the wrapper plays) or SMOKE_NULL_BRAIN=1."""

    def decide(self, obs):
        return []


use_brain = game.agent.enabled and os.environ.get("SMOKE_NULL_BRAIN") != "1"
brain = brains.build_brain(g, game) if use_brain else NullBrain()
interval_ms = (
    int(os.environ.get("SMOKE_INTERVAL_MS") or 0)
    or game.agent.interval_ms
    or g.agent.default_interval_ms
)
acts_log: list[dict] = []
dump_all: list[dict] = []


class RecAdapter:
    def __init__(self, inner):
        self.inner = inner
        self.empty_obs = 0
        self.pane_hashes: list[str] = []
        self.snapshots: list[dict] = []
        self.t0 = time.monotonic()
        self._next_snap = 0.0
        self._last_change_snap = -10.0
        self.kw_seen: dict[tuple, int] = {}

    def observe(self):
        obs = self.inner.observe()
        now = time.monotonic() - self.t0
        if os.environ.get("SMOKE_DUMP_ALL") == "1":
            dump_all.append({"t": round(now, 2), "pane": obs.text})
        if obs.text == "":
            self.empty_obs += 1
        digest = hashlib.md5(obs.text.encode()).hexdigest()
        changed = bool(self.pane_hashes) and self.pane_hashes[-1] != digest
        self.pane_hashes.append(digest)
        kw = tuple(k for k in KEYWORDS if k in obs.text.lower())
        # Keep the first 3 panes of each distinct keyword set so menu / dialog /
        # game-over screens survive even when the per-change budget is used up.
        kw_new = self.kw_seen.get(kw, 0) < 3
        if kw_new:
            self.kw_seen[kw] = self.kw_seen.get(kw, 0) + 1
        if kw_new or now >= self._next_snap or (
            changed and now - self._last_change_snap >= 1.0 and len(self.snapshots) < 120
        ):
            self.snapshots.append({"t": round(now, 1), "kw": list(kw), "pane": obs.text})
            if now >= self._next_snap:
                self._next_snap = now + 15.0
            if changed:
                self._last_change_snap = now
        return obs

    def act(self, action):
        acts_log.append({
            "t": round(time.monotonic() - self.t0, 2), "type": action.type,
            "text": action.text, "key": action.key, "keys": list(action.keys),
        })
        return self.inner.act(action)


class RecBrain:
    def __init__(self, inner):
        self.inner = inner
        self.cycles = 0
        self.cycles_with_actions = 0
        self.decide_ms: list[float] = []

    def decide(self, obs):
        t = time.monotonic()
        acts = self.inner.decide(obs)
        self.decide_ms.append((time.monotonic() - t) * 1000)
        self.cycles += 1
        if acts:
            self.cycles_with_actions += 1
        return acts


adapter.prepare()
rec_a, rec_b = RecAdapter(adapter), RecBrain(brain)
deadline = time.monotonic() + seconds
errors = 0
while time.monotonic() < deadline:
    t0 = time.monotonic()
    try:
        _run_iteration(rec_a, rec_b, interval_ms)
    except Exception as exc:  # mirror run_agent's catch-log-continue
        errors += 1
        print(f"[smoke] iteration error: {exc}", file=sys.stderr, flush=True)
    time.sleep(max(interval_ms / 1000 - (time.monotonic() - t0), 0))

alive = subprocess.run(
    ["tmux", "has-session", "-t", adapter._session()], capture_output=True
).returncode == 0
final = adapter.observe().text if alive else ""

counts = collections.Counter(
    (a["type"], a["text"] or a["key"] or ",".join(a["keys"])) for a in acts_log
)
summary = {
    "game": game_name,
    "seconds": seconds,
    "interval_ms": interval_ms,
    "cycles": rec_b.cycles,
    "cycles_with_actions": rec_b.cycles_with_actions,
    "actions_total": len(acts_log),
    "distinct_actions": {f"{k[0]}:{k[1]}": v for k, v in counts.most_common(12)},
    "empty_obs": rec_a.empty_obs,
    "pane_changes": sum(
        1 for a, b in zip(rec_a.pane_hashes, rec_a.pane_hashes[1:]) if a != b
    ),
    "iteration_errors": errors,
    "decide_ms_avg": round(sum(rec_b.decide_ms) / max(len(rec_b.decide_ms), 1), 1),
    "decide_ms_max": round(max(rec_b.decide_ms or [0]), 1),
    "session_alive_at_end": alive,
}
(out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
(out_dir / "snapshots.json").write_text(json.dumps(rec_a.snapshots, ensure_ascii=False, indent=1))
(out_dir / "actions.jsonl").write_text("\n".join(json.dumps(a) for a in acts_log))
(out_dir / "final_pane.txt").write_text(final)
if dump_all:
    (out_dir / "panes_all.jsonl").write_text("\n".join(json.dumps(d) for d in dump_all))
print(json.dumps(summary, ensure_ascii=False, indent=2))
