#!/usr/bin/env python3
"""Stateless, stdlib-only bastet CommandBrain; transitions belong to the wrapper.

Default controls verified in Debian bookworm bastet(6): Enter hard-drops,
Down soft-drops; Space ROTATES (it is not a hard drop).
https://manpages.debian.org/bookworm/bastet/bastet.6.en.html
Ui.cpp DrawDot uses coloured spaces, so plain capture-pane cannot locate blocks.
This deliberately modest policy places the current piece without pretending to
solve that invisible board. Custom ~/.bastetrc keybindings are not supported.
Weights are numeric and reloaded on each fresh invocation for live hot-swap.
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
    "DOCICH_BRAIN_WEIGHTS", str(REPO_ROOT / "run/brain/bastet/weights.json"),
))
DEFAULT_WEIGHTS = {"hard_drop": 1.0}


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
    # Dialogs can leave the previous score visible underneath. Test them first.
    if any(marker in lower for marker in (
        "try again!", "play!", "difficulty", "game over", "main menu",
        "enter your name", "high score", "get ready", "starting level",
        "to start", "press any key", "resume", "paused", "key you wish",
    )):
        return None
    if not re.search(r"\bScore:\s*[0-9]+\b", text):
        return None
    return "Enter" if weights["hard_drop"] >= 0.5 else "Down"


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
