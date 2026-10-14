"""Trusted parser for the nInvaders 80x24 tmux pane.

Glyph semantics below were measured on the real game (2026-09-19, 0.1 s
polling) — they are NOT the ones the first bot assumed:

* ``!``  the player's own missile: rises from the cannon, ~25 rows/s, one at a
         time.  It is never a threat.
* ``:``  an alien bomb: falls ~8 rows/s and is the only thing that kills.
* ``#``  destructible barriers (own missiles fired from under one are wasted).
* ``/-^-\\`` the cannon (5 wide, ``^`` is the centre) on the row above the
         status line; the status line is ``Level: NN Score: NNNNNNN Lives: ...``.
* aliens are 3 chars wide and animate between two frames
  (``,^,``/``.-.``, ``_O-``/``-O_``, ``-o-``/``/o\\``).
* a match ends with a "GAME OVER" screen drawn as giant ``#`` block letters
  (rows ~7-17, ~2 s, the status line stays) and then returns to the title
  screen (``Press SPACE to start``).  The literal text "Game Over" never
  appears, so text matching cannot detect the end of a match.

Everything here is stdlib-only and never raises: policy code (untrusted) only
ever sees the dict returned by :func:`parse`.
"""
from __future__ import annotations

import re

COLS = 80
ROWS = 24
TITLE_MARK = "Press SPACE to start"
CANNON = "/-^-\\"
STATUS_RE = re.compile(r"Level:\s*(\d+)\s+Score:\s*(\d+)")
UFO_RE = re.compile(r"<[ o]{2,4}>")
ALIEN_CHARS = frozenset(",^._-/\\Oo")


BANNER_MIN_HASHES = 15  # '#' cells above row 15; barriers never sit that high


def _is_gameover(lines: list[str]) -> bool:
    return sum(line.count("#") for line in lines[:15]) >= BANNER_MIN_HASHES


def classify(text: str) -> str:
    """'title' | 'gameover' | 'play' | 'other' (transition/unknown)."""
    if not isinstance(text, str):
        return "other"
    if TITLE_MARK in text:
        return "title"
    if STATUS_RE.search(text):
        return "gameover" if _is_gameover(text.splitlines()) else "play"
    return "other"


def _status_row(lines: list[str]) -> int | None:
    for y in range(len(lines) - 1, -1, -1):
        if STATUS_RE.search(lines[y]):
            return y
    return None


def _absorb_shots(line: str) -> str:
    """A bomb/missile drawn inside an alien glyph must not split the alien."""
    chars = list(line)
    for x, ch in enumerate(chars):
        if ch in ":!":
            left = line[x - 1] if x > 0 else " "
            right = line[x + 1] if x + 1 < len(line) else " "
            if left in ALIEN_CHARS or right in ALIEN_CHARS:
                chars[x] = "O"
    return "".join(chars)


def _alien_runs(line: str, y: int) -> list[tuple[int, int]]:
    line = _absorb_shots(line)
    out: list[tuple[int, int]] = []
    x = 0
    n = len(line)
    while x < n:
        if line[x] in ALIEN_CHARS:
            start = x
            while x < n and line[x] in ALIEN_CHARS:
                x += 1
            length = x - start
            if length >= 2:
                count = max(1, round(length / 3))
                for k in range(count):
                    cx = start + int((k + 0.5) * length / count)
                    out.append((cx, y))
        else:
            x += 1
    return out


def parse(text: str, tick: int = 0) -> dict:
    """Structured view of one pane capture.  Always returns a full dict."""
    obs: dict = {
        "tick": int(tick), "kind": classify(text), "cols": COLS, "rows": ROWS,
        "player": None, "player_hit": False, "bombs": [], "missiles": [],
        "aliens": [], "barriers": [], "ufo": None,
        "score": None, "level": None, "lives": None, "text": text if isinstance(text, str) else "",
    }
    if not isinstance(text, str):
        return obs
    try:
        lines = text.splitlines()
        status_y = _status_row(lines)
        if status_y is not None:
            m = STATUS_RE.search(lines[status_y])
            obs["level"], obs["score"] = int(m.group(1)), int(m.group(2))
            tail = lines[status_y].split("Lives:", 1)
            obs["lives"] = tail[1].count("/-\\") + 1 if len(tail) == 2 else None
        if obs["kind"] != "play":
            return obs
        limit = status_y if status_y is not None else len(lines)
        player = None
        for y in range(limit - 1, -1, -1):
            idx = lines[y].find(CANNON)
            if idx >= 0:
                player = (idx + 2, y)
                break
        obs["player"] = player
        obs["player_hit"] = obs["kind"] == "play" and player is None
        for y in range(limit):
            line = lines[y]
            for x, ch in enumerate(line):
                if ch == ":":
                    obs["bombs"].append((x, y))
                elif ch == "!":
                    obs["missiles"].append((x, y))
                elif ch == "#" and len(obs["barriers"]) < 400:
                    obs["barriers"].append((x, y))
        alien_stop = player[1] if player else limit
        for y in range(alien_stop):
            line = lines[y]
            m = UFO_RE.search(line) if y <= 2 else None
            if m:
                obs["ufo"] = (m.start() + 2, y)
                line = line[: m.start()] + " " * (m.end() - m.start()) + line[m.end():]
            obs["aliens"].extend(_alien_runs(line, y))
    except Exception:  # noqa: BLE001 - parsing must never break the play loop
        pass
    return obs


def obs_for_policy(obs: dict) -> dict:
    """JSON-safe copy handed to (untrusted) policy code."""
    out = dict(obs)
    for key in ("player", "ufo"):
        if out.get(key) is not None:
            out[key] = list(out[key])
    for key in ("bombs", "missiles", "aliens", "barriers"):
        out[key] = [list(p) for p in out[key]]
    return out
