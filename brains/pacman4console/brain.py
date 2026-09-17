#!/usr/bin/env python3
"""Stateless, stdlib-only pacman4console CommandBrain; wrapper owns transitions.

Verified against Debian's pacman4console_1.3.orig.tar.gz pacman.c:
GetInput accepts arrow keys; DrawWindow renders C, ./ *, & and coloured BLANK
walls. The maze is 28x29 at pane (1,1); lives are outside it (row 30).
https://deb.debian.org/debian/pool/main/p/pacman4console/
Debian patches/levels installs maps in /usr/share/pacman4console/Levels.
Read those maps, never treat every blank as passable. Without a matching map,
only visibly occupied food cells are known passable (degraded baseline).
BFS heads toward food, avoiding visible ghosts. Custom maps/shifted windows
are not supported. Real-game timing and installed version remain unverified.
"""
from __future__ import annotations

from collections import deque
import json
import math
import os
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
WEIGHTS_PATH = Path(os.environ.get(
    "DOCICH_BRAIN_WEIGHTS", str(REPO_ROOT / "run/brain/pacman4console/weights.json"),
))
DEFAULT_WEIGHTS = {"ghost_radius": 1.0}
LEVELS_DIR = Path(os.environ.get("DOCICH_PACMAN_LEVELS_DIR", "/usr/share/pacman4console/Levels"))
WIDTH, HEIGHT = 28, 29
DIRS = (("Up", 0, -1), ("Left", -1, 0), ("Down", 0, 1), ("Right", 1, 0))


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


def load_map(level: int) -> set | None:
    if not 1 <= level <= 9:
        return None
    try:
        tokens = (LEVELS_DIR / f"level{level:02d}.dat").read_text(encoding="utf-8").split()
        cells = [int(v) for v in tokens]
    except (OSError, ValueError):
        return None
    if len(cells) != WIDTH * HEIGHT + 1 or cells[-1] != level:
        return None
    if any(not 0 <= v <= 9 for v in cells[:-1]):
        return None
    return {(i % WIDTH, i // WIDTH) for i, v in enumerate(cells[:-1]) if v not in (1, 4)}


def neighbours(cell):
    x, y = cell
    for key, dx, dy in DIRS:
        # The game supports tunnels at both pairs of maze edges.
        yield key, ((x + dx) % WIDTH, (y + dy) % HEIGHT)


def decide(text: str, weights: dict) -> str | None:
    lower = text.lower()
    if any(marker in lower for marker in (
        "press any key", "game over", "main menu", "difficulty", "paused",
        "play again", "enter your name",
    )):
        return None
    lines = text.splitlines()
    if len(lines) < 32:
        return None
    status = lines[31]
    level = re.search(r"\bLevel:\s*([0-9]+)\b", status)
    if level is None or not re.search(r"\bScore:\s*[0-9]+\b", status):
        return None
    rows = [row[1:WIDTH + 1].ljust(WIDTH) for row in lines[1:HEIGHT + 1]]
    heads, food, ghosts = [], set(), set()
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch == "C":
                heads.append((x, y))
            elif ch in ".*":
                food.add((x, y))
            elif ch == "&" or ch.isdigit():
                ghosts.add((x, y))
    if len(heads) != 1:
        return None
    head = heads[0]
    visible = food | ghosts | {head}
    walkable = load_map(int(level[1]))
    if walkable is None or not visible <= walkable:
        # Missing/mismatched map: do not infer corridors through invisible walls.
        walkable = visible
    radius = int(max(0, min(4, weights["ghost_radius"])))
    danger = set(ghosts)
    frontier = set(ghosts)
    for _ in range(radius):
        frontier = {n for c in frontier for _, n in neighbours(c) if n in walkable} - danger
        danger.update(frontier)

    def route(blocked):
        seen = {head}
        queue = deque([(head, None)])
        while queue:
            cell, first = queue.popleft()
            if cell in food:
                return first
            for key, nxt in neighbours(cell):
                if nxt in walkable and nxt not in blocked and nxt not in seen:
                    seen.add(nxt)
                    queue.append((nxt, first or key))
        return None

    key = route(danger) or route(ghosts)
    if key:
        return key
    # Still supply a movement key between levels / when food is unreachable;
    # prefer a known corridor, but never send quit, pause, or transition keys.
    for blocked in (danger, ghosts):
        for key, nxt in neighbours(head):
            if nxt in walkable and nxt not in blocked:
                return key
    return "Right"


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
