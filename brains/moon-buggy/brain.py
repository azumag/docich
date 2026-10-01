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

import hashlib
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
AB_ACTIVE_PATH = os.environ.get("DOCICH_MOON_BUGGY_AB_ACTIVE")
AB_EXPERIMENT_ID = os.environ.get("DOCICH_MOON_BUGGY_AB_EXPERIMENT_ID")
AB_MATCH_INDEX = os.environ.get("DOCICH_MOON_BUGGY_AB_MATCH_INDEX")
DEFAULT_WEIGHTS = {"laser_period": 7.0}


def load_weights() -> dict | None:
    if AB_ACTIVE_PATH:
        try:
            active_path = Path(AB_ACTIVE_PATH)
            if active_path.is_symlink():
                return None
            active = json.loads(active_path.read_text(encoding="utf-8"))
            data = active.get("weights") if isinstance(active, dict) else None
            expected_index = int(AB_MATCH_INDEX) if AB_MATCH_INDEX else -1
            if (not isinstance(active, dict)
                    or type(active.get("schema_version")) is not int
                    or active.get("schema_version") != 1
                    or active.get("arm") not in {"A", "B"} or not isinstance(data, dict)
                    or not AB_EXPERIMENT_ID
                    or active.get("experiment_id") != AB_EXPERIMENT_ID
                    or type(active.get("index")) is not int
                    or not 0 <= expected_index < len("ABBA")
                    or active.get("index") != expected_index
                    or active.get("arm") != "ABBA"[expected_index]
                    or active.get("match_id") != f"{AB_EXPERIMENT_ID}:{expected_index}"):
                return None
            period = data.get("laser_period")
            if (set(data) != {"laser_period"} or type(period) not in (int, float)
                    or not math.isfinite(period) or not 2 <= period <= 100):
                return None
            canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if active.get("weights_sha256") != digest:
                return None
            return {"laser_period": period}
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            return None

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
    if not isinstance(weights, dict):
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
