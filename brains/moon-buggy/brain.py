#!/usr/bin/env python3
"""Stateless moon-buggy CommandBrain: repeated jumps with periodic laser fire.

Controls verified in Debian bookworm moon-buggy(6): Space/j jumps; a/l fires.
https://manpages.debian.org/bookworm/moon-buggy/moon-buggy.6.en.html
There is no verified Up/Down acceleration binding, so none is sent. Scrolling
is automatic; a jump pressed in the air is ignored. This is a baseline, not
crater tracking: a score-derived laser cadence is deterministic and needs no
persistent state. Collision avoidance and timing still require real-game tests.
Transitions belong exclusively to the wrapper. Only stdlib, no token usage.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
WEIGHTS_PATH = Path(os.environ.get(
    "DOCICH_BRAIN_WEIGHTS", str(REPO_ROOT / "run/brain/moon-buggy/weights.json"),
))
DEFAULT_WEIGHTS = {"laser_period": 7.0}


def load_weights() -> dict:
    weights = dict(DEFAULT_WEIGHTS)
    try:
        data = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for key in weights:
                value = data.get(key)
                if type(value) in (int, float) and math.isfinite(value):
                    weights[key] = value
    except (OSError, ValueError, OverflowError, RecursionError):
        pass
    return weights


def decide(text: str, weights: dict) -> str | None:
    lower = text.lower()
    if any(marker in lower for marker in (
        "start game", "new game", "game over", "main menu", "difficulty",
        "press any key", "enter your name", "high score", "paused",
    )):
        return None
    score = re.search(r"\bscore:\s*([0-9]+)\b", lower)
    if score is None or not re.search(r"\blevel:\s*[0-9]+\b", lower):
        return None
    period = int(max(2, min(100, weights["laser_period"])))
    # A minority of score values fire, all other cycles attempt a jump. The
    # game keeps moving even when the score (and therefore action) is unchanged.
    return "l" if int(score[1]) % period == period - 1 else "Space"


def main() -> int:
    code = 0
    key = None
    try:
        obs = json.load(sys.stdin)
        if not isinstance(obs, dict) or not isinstance(obs.get("text"), str):
            code = 2
        else:
            key = decide(obs["text"], load_weights())
    except (ValueError, OSError, RecursionError):
        code = 2
    actions = [] if key is None else [{"type": "key", "keys": [key]}]
    print(json.dumps({"actions": actions}), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
