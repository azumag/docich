"""Conservative visible-map exploration planner for NetHack (P3b).

The planner uses only the normalized visible TTY map.  It never treats a
monster, item, visible trap, closed door, or blank/unknown cell as passable.
It issues at most one movement key per observation, preferring cardinal routes
and considering diagonals when no cardinal step remains.

This is intentionally less ambitious than human play: getting stuck at a door
is preferable to silently adding unreviewed door/item/combat semantics.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .nethack_observation import NethackObservation


CARDINAL = (
    (0, -1, "k"),
    (0, 1, "j"),
    (-1, 0, "h"),
    (1, 0, "l"),
)
DIAGONAL = ((-1, -1, "y"), (1, -1, "u"), (-1, 1, "b"), (1, 1, "n"))
DIRECTIONS = CARDINAL + DIAGONAL
MOVE_KEYS = frozenset(key for _dx, _dy, key in DIRECTIONS)
PASSABLE = frozenset({".", "#", "<", ">"})


@dataclass
class LevelMemory:
    visits: dict[tuple[int, int], int] = field(default_factory=dict)
    seen: dict[tuple[int, int], str] = field(default_factory=dict)


class NethackExplorationMemory:
    """Visited/visible memory keyed by visible dungeon level."""

    def __init__(self) -> None:
        self.levels: dict[int, LevelMemory] = {}

    def level(self, dungeon_level: int) -> LevelMemory:
        return self.levels.setdefault(dungeon_level, LevelMemory())

    def update(self, obs: NethackObservation) -> LevelMemory | None:
        depth = obs.vitals.dungeon_level
        if depth is None:
            return None
        memory = self.level(depth)
        for y, row in enumerate(obs.map_rows):
            for x, glyph in enumerate(row):
                if glyph != " ":
                    memory.seen[(x, y)] = glyph
        if obs.player is not None:
            memory.visits[obs.player] = memory.visits.get(obs.player, 0) + 1
        return memory


def _glyph(obs: NethackObservation, pos: tuple[int, int]) -> str:
    x, y = pos
    if y < 0 or y >= len(obs.map_rows):
        return " "
    row = obs.map_rows[y]
    if x < 0 or x >= len(row):
        return " "
    return row[x]


def _passable(obs: NethackObservation, pos: tuple[int, int]) -> bool:
    if pos == obs.player:
        return True
    return _glyph(obs, pos) in PASSABLE


def move_target(obs: NethackObservation, key: str) -> tuple[int, int] | None:
    if obs.player is None:
        return None
    for dx, dy, candidate in DIRECTIONS:
        if key == candidate:
            return obs.player[0] + dx, obs.player[1] + dy
    return None


def visible_safe_step(obs: NethackObservation, key: str) -> bool:
    target = move_target(obs, key)
    return obs.prompt == "none" and target is not None and _glyph(obs, target) in PASSABLE


def _frontier(obs: NethackObservation, pos: tuple[int, int]) -> bool:
    """True when a safe cell borders something not yet visible/passable.

    A closed door is a useful frontier even though P3b will not open it; the
    planner can approach it and then safely stop for a later policy layer.
    """
    for dx, dy, _key in CARDINAL:
        glyph = _glyph(obs, (pos[0] + dx, pos[1] + dy))
        if glyph in {" ", "+"}:
            return True
    return False


@dataclass(frozen=True)
class ExplorationStep:
    key: str
    source: tuple[int, int]
    target: tuple[int, int]
    target_glyph: str
    reason: str


class NethackExplorer:
    """One-step BFS explorer over currently visible safe terrain."""

    def __init__(self, memory: NethackExplorationMemory | None = None) -> None:
        self.memory = memory or NethackExplorationMemory()
        # Updated only by the production resolver from returned action plans.
        # A visible floor does not prove a diagonal doorway/squeeze is legal.
        self.blocked_steps: set[tuple[tuple[int, int], str]] = set()

    def _neighbors(self, obs: NethackObservation, pos: tuple[int, int], directions):
        for dx, dy, key in directions:
            nxt = (pos[0] + dx, pos[1] + dy)
            if _passable(obs, nxt) and (pos, key) not in self.blocked_steps:
                yield nxt, key

    def plan_step(self, obs: NethackObservation) -> ExplorationStep | None:
        memory = self.memory.update(obs)
        if memory is None or obs.player is None or obs.prompt != "none":
            return None

        return self._plan_step(obs, memory, CARDINAL) or self._plan_step(obs, memory, DIRECTIONS)

    def _plan_step(self, obs: NethackObservation, memory: LevelMemory, directions) -> ExplorationStep | None:
        start = obs.player
        assert start is not None
        queue = deque([start])
        parent: dict[tuple[int, int], tuple[tuple[int, int], str] | None] = {start: None}
        distance: dict[tuple[int, int], int] = {start: 0}
        candidates: list[tuple[int, int]] = []

        while queue:
            pos = queue.popleft()
            if pos != start and _frontier(obs, pos):
                candidates.append(pos)
            for nxt, key in self._neighbors(obs, pos, directions):
                if nxt in parent:
                    continue
                parent[nxt] = (pos, key)
                distance[nxt] = distance[pos] + 1
                queue.append(nxt)

        if candidates:
            # Prefer least-visited frontier, then shortest route, then stable
            # coordinate ordering for deterministic tests/replays.
            target = min(
                candidates,
                key=lambda p: (memory.visits.get(p, 0), distance[p], p[1], p[0]),
            )
            reason = "visible frontier with lowest visit count"
        else:
            direct = [nxt for nxt, _key in self._neighbors(obs, start, directions)]
            if not direct:
                return None
            target = min(
                direct,
                key=lambda p: (memory.visits.get(p, 0), p[1], p[0]),
            )
            reason = "no visible frontier; choose least-visited safe neighbor"

        # Walk backward to obtain the first key on the BFS path.
        cursor = target
        first_key: str | None = None
        first_target: tuple[int, int] | None = None
        while cursor != start:
            edge = parent.get(cursor)
            if edge is None:
                # direct fallback candidates are guaranteed adjacent.
                dx = cursor[0] - start[0]
                dy = cursor[1] - start[1]
                match = next((key for ex, ey, key in directions if (ex, ey) == (dx, dy)), None)
                first_key = match
                first_target = cursor
                break
            prev, key = edge
            first_key = key
            first_target = cursor
            if prev == start:
                break
            cursor = prev

        if first_key is None or first_target is None:
            return None
        glyph = _glyph(obs, first_target)
        if glyph not in PASSABLE:
            # Final fail-closed check: never let a stale parent map move onto a
            # creature, item, trap, door or blank cell.
            return None
        return ExplorationStep(
            key=first_key,
            source=start,
            target=first_target,
            target_glyph=glyph,
            reason=reason,
        )
