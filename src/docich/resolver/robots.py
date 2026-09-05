"""BSD robots resolver (token-free).

Board layout (from the pane capture)::

    +--------------------------------------------------+ Directions: ...
    |   ...  @  ...  +  ...  *  ...                    | Commands: ...
    +--------------------------------------------------+ Score: 0

Rules implemented (bsdgames robots):

- The player ``@`` moves one cell in 8 directions (``h j k l y u b n``),
  may wait one turn in place (``.``) or teleport to a random free cell
  (``t``).  ``w`` is bsdgames' "wait for end" fast-forward and is fatal
  unless the level clears exactly on that turn: the resolver never sends
  it (verified empirically on the production pane, 2026-09-05).
- Every turn each robot ``+`` simultaneously moves one cell toward the
  player (both axes independently).
- A robot stepping onto a robot or onto junk ``*`` dies into junk (score).
- A robot stepping onto the player kills the player (match over).
- Junk is impassable for the player and stepping onto a robot's cell is
  suicide.

Policy: never die.  Prefer moves that trigger robot collisions (each
collision removes robots from the board), then keep distance from robots
and keep escape options; wait when safe (converging robots collide on
their own); teleport only when no safe move exists.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

Cell = tuple[int, int]

DIRECTIONS: dict[str, Cell] = {
    "h": (-1, 0),
    "l": (1, 0),
    "k": (0, -1),
    "j": (0, 1),
    "y": (-1, -1),
    "u": (1, -1),
    "b": (-1, 1),
    "n": (1, 1),
}

DEFAULT_STRATEGY: dict[str, "float | bool"] = {
    "w_collision": 40.0,  # score per robot removed by a forced collision
    "w_dist": 5.0,        # score per cell of nearest-robot distance
    "w_near": 8.0,        # penalty per robot within danger_radius
    "danger_radius": 2.0,
    "w_mob": 2.5,         # score per safe escape option after the move
    "w_edge": 3.0,        # penalty per missing border cell of clearance
    "w_wait": 0.5,        # tie-break bonus for waiting (robots collide on their own)
    "w_junk": 2.0,        # score per junk cell adjacent to the landing cell (lure)
    "w_lure": 15.0,       # score when a robot's 3-step pursuit path crosses junk
    "teleport_when_trapped": True,
}

WAIT_KEY = "."  # single-turn wait ('w' is the fatal wait-for-end fast-forward)

_ROW_RE = re.compile(r"^\|(.*?)\|")
_SCORE_RE = re.compile(r"\bScore:\s*(\d+)")
_OVER_RE = re.compile(r"Another game|got you|quitter", re.IGNORECASE)


@dataclass
class Board:
    width: int
    height: int
    player: Cell
    robots: list[Cell]
    junk: list[Cell]
    score: int | None
    game_over: bool


def parse_board(text: str) -> Board | None:
    """Parse the pane text into a Board, or None when it is not a robots board.

    The right-hand panel (Directions/Commands/Legend/Score) is ignored; only
    the ``|...|`` rows are treated as board content.  A pane without exactly
    one ``@`` is not a playable board (presentation logs, transition frames).
    """
    rows: list[str] = []
    score = None
    for line in text.splitlines():
        m = _ROW_RE.match(line)
        if m:
            rows.append(m.group(1))
            continue
        if score is None:
            ms = _SCORE_RE.search(line)
            if ms:
                score = int(ms.group(1))
    if len(rows) < 3:
        return None
    width = max(len(row) for row in rows)
    if width < 5:
        return None
    player = None
    robots: list[Cell] = []
    junk: list[Cell] = []
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch == "@":
                if player is not None:
                    return None
                player = (x, y)
            elif ch == "+":
                robots.append((x, y))
            elif ch == "*":
                junk.append((x, y))
    if player is None:
        return None
    return Board(
        width=width,
        height=len(rows),
        player=player,
        robots=robots,
        junk=junk,
        score=score,
        game_over=bool(_OVER_RE.search(text)),
    )


def game_over(text: str) -> bool:
    """True when the pane shows the end-of-match prompt (raw-text check)."""
    return bool(_OVER_RE.search(text))


def score_from_text(text: str) -> int | None:
    m = _SCORE_RE.search(text)
    return int(m.group(1)) if m else None


def decide(text: str, strategy: dict | None = None) -> list[str]:
    """Return the keys to send for this pane text (empty = send nothing)."""
    st = dict(DEFAULT_STRATEGY)
    if strategy:
        st.update({k: v for k, v in strategy.items() if k in st})
    # The end-of-match prompt is checked on the raw text first: transition
    # frames (e.g. the "Teleport!" redraw) drop the '@' and do not parse.
    if game_over(text):
        return ["y"]
    board = parse_board(text)
    if board is None:
        return []
    if not board.robots:
        return ["."]  # level cleared: nudge the game to spawn the next wave
    key = _choose_move(board, st)
    if key is None and st["teleport_when_trapped"]:
        return ["t"]
    return [key] if key is not None else []


def _sign(v: int) -> int:
    return (v > 0) - (v < 0)


def _step_toward(robot: Cell, target: Cell) -> Cell:
    return (robot[0] + _sign(target[0] - robot[0]), robot[1] + _sign(target[1] - robot[1]))


def _manhattan(a: Cell, b: Cell) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _simulate(robots: list[Cell], junk: set[Cell], target: Cell) -> tuple[list[Cell], int, int]:
    """One simultaneous robot step toward ``target``.

    Returns (surviving robot positions, robots killed by collisions or junk,
    robots that land on ``target``).  Robots landing on ``target`` hit the
    player: they are lethal, never scored as collision kills.
    """
    counts: dict[Cell, int] = {}
    hits = 0
    for r in robots:
        p = _step_toward(r, target)
        if p == target:
            hits += 1
            continue
        counts[p] = counts.get(p, 0) + 1
    survivors: list[Cell] = []
    killed = 0
    for p, n in counts.items():
        if n >= 2 or p in junk:
            killed += n
        else:
            survivors.append(p)
    return survivors, killed, hits


def _lure_bonus(robots: list[Cell], junk: set[Cell], target: Cell, w_lure: float, horizon: int = 3) -> float:
    """Reward positions whose pursuit path leads robots onto junk.

    Two robots never collide with each other (parallel pursuit at constant
    separation), so the endgame depends on luring them onto junk heaps.  For
    each robot, walk up to ``horizon`` pursuit steps toward ``target``; the
    first step that lands on junk scores (earlier = better).  A robot that
    would reach the player itself within the horizon scores nothing (the
    per-turn safety checks own that case).
    """
    bonus = 0.0
    for r in robots:
        p = r
        for step in range(1, horizon + 1):
            p = _step_toward(p, target)
            if p == target:
                break
            if p in junk:
                bonus += w_lure * (horizon - step + 1) / horizon
                break
    return bonus


def _choose_move(board: Board, st: dict) -> str | None:
    junk = set(board.junk)
    robot_set = set(board.robots)
    player = board.player
    radius = int(st["danger_radius"])
    candidates: list[tuple[str, Cell]] = [(WAIT_KEY, player)]
    for key, (dx, dy) in DIRECTIONS.items():
        candidates.append((key, (player[0] + dx, player[1] + dy)))
    best_key: str | None = None
    best_score = float("-inf")
    for key, target in candidates:
        if not (0 <= target[0] < board.width and 0 <= target[1] < board.height):
            continue
        if target in junk or target in robot_set:
            continue
        survivors, killed, hits = _simulate(board.robots, junk, target)
        if hits:
            continue  # a robot steps onto this cell next turn: suicide
        score = st["w_collision"] * killed
        if survivors:
            dmin = min(_manhattan(target, r) for r in survivors)
            nclose = sum(1 for r in survivors if _manhattan(target, r) <= radius)
        else:
            dmin = 99
            nclose = 0
        score += st["w_dist"] * dmin
        score -= st["w_near"] * nclose
        mob = 0
        for dx, dy in DIRECTIONS.values():
            n = (target[0] + dx, target[1] + dy)
            if 0 <= n[0] < board.width and 0 <= n[1] < board.height and n not in junk:
                if all(_manhattan(n, r) > 1 for r in survivors):
                    mob += 1
        score += st["w_mob"] * mob
        jadj = sum(
            1
            for dx, dy in DIRECTIONS.values()
            if (target[0] + dx, target[1] + dy) in junk
        )
        score += st["w_junk"] * jadj
        score += _lure_bonus(board.robots, junk, target, st["w_lure"])
        clear = min(
            target[0], board.width - 1 - target[0], target[1], board.height - 1 - target[1]
        )
        if clear < 2:
            score -= st["w_edge"] * (2 - clear)
        if key == WAIT_KEY:
            score += st["w_wait"]
        if score > best_score:
            best_score = score
            best_key = key
    return best_key
