"""Public-information NetHack TTY observation normalization.

This parser is intentionally conservative.  It only derives information that
is visibly present in the terminal frame and never attempts to inspect NetHack
process memory, map internals, unseen monsters, unidentified item identity, or
other hidden state.

The normalized contract is shared by the layered gameplay policy.  It is not
coupled to the spectator's graphical tile mapping.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


DEFAULT_COLS = 80
DEFAULT_ROWS = 24

_STATUS_PATTERNS = {
    "hp": re.compile(r"\bHP:\s*(-?\d+)\((\d+)\)", re.IGNORECASE),
    "power": re.compile(r"\bPw:\s*(-?\d+)\((\d+)\)", re.IGNORECASE),
    "ac": re.compile(r"\bAC:\s*(-?\d+)", re.IGNORECASE),
    "experience_level": re.compile(r"\b(?:Exp|Xp):\s*(\d+)", re.IGNORECASE),
    "dungeon_level": re.compile(r"\bDlvl:\s*(\d+)", re.IGNORECASE),
    "gold": re.compile(r"(?:\$|Gold):\s*(\d+)", re.IGNORECASE),
    "turn": re.compile(r"\bT:\s*(\d+)", re.IGNORECASE),
}

# Visible status conditions only.  Keep the canonical spelling in the output
# so downstream policy/config does not depend on capitalization from a port.
_CONDITIONS = (
    "Hungry",
    "Weak",
    "Fainting",
    "Fainted",
    "Starved",
    "Blind",
    "Conf",
    "Stun",
    "Hallu",
    "Sick",
    "FoodPois",
    "Ill",
    "Slime",
    "Strngl",
    "Deaf",
    "Lev",
    "Fly",
    "Ride",
)


@dataclass(frozen=True)
class Vitals:
    hp: int | None = None
    hp_max: int | None = None
    power: int | None = None
    power_max: int | None = None
    ac: int | None = None
    experience_level: int | None = None
    dungeon_level: int | None = None
    gold: int | None = None
    turn: int | None = None

    @property
    def hp_ratio(self) -> float | None:
        if self.hp is None or self.hp_max is None or self.hp_max <= 0:
            return None
        return max(0.0, min(1.0, self.hp / self.hp_max))


@dataclass(frozen=True)
class NethackObservation:
    raw_text: str
    message: str
    map_rows: tuple[str, ...]
    status_lines: tuple[str, ...]
    player: tuple[int, int] | None
    vitals: Vitals
    conditions: tuple[str, ...]
    prompt: str

    def local_map(self, radius: int = 2) -> tuple[str, ...]:
        """Return a visible square around the player, preserving raw glyphs."""
        if type(radius) is not int or radius < 0 or radius > 8:
            raise ValueError("radius must be an integer between 0 and 8")
        if self.player is None:
            return ()
        px, py = self.player
        result: list[str] = []
        for y in range(py - radius, py + radius + 1):
            chars: list[str] = []
            for x in range(px - radius, px + radius + 1):
                if y < 0 or y >= len(self.map_rows):
                    chars.append(" ")
                    continue
                row = self.map_rows[y]
                chars.append(row[x] if 0 <= x < len(row) else " ")
            result.append("".join(chars))
        return tuple(result)

    def visible_neighbors(self) -> tuple[str, ...]:
        """Return the eight raw visible glyphs around the player."""
        if self.player is None:
            return ()
        px, py = self.player
        values: list[str] = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                y, x = py + dy, px + dx
                if y < 0 or y >= len(self.map_rows):
                    values.append(" ")
                    continue
                row = self.map_rows[y]
                values.append(row[x] if 0 <= x < len(row) else " ")
        return tuple(values)

    def public_summary(self) -> dict[str, object]:
        """Small serializable summary suitable for policy/LLM input."""
        return {
            "message": self.message,
            "prompt": self.prompt,
            "player": list(self.player) if self.player is not None else None,
            "vitals": {
                "hp": self.vitals.hp,
                "hp_max": self.vitals.hp_max,
                "hp_ratio": self.vitals.hp_ratio,
                "power": self.vitals.power,
                "power_max": self.vitals.power_max,
                "ac": self.vitals.ac,
                "experience_level": self.vitals.experience_level,
                "dungeon_level": self.vitals.dungeon_level,
                "gold": self.vitals.gold,
                "turn": self.vitals.turn,
            },
            "conditions": list(self.conditions),
            "local_map": list(self.local_map()),
        }


def _prompt_kind(text: str, message: str) -> str:
    lower = text.lower()
    msg = message.lower()
    if "--more--" in lower:
        return "more"
    if "in what direction" in lower or "what direction" in msg:
        return "direction"
    if "(y/n)" in lower or "[yn" in lower or "yes or no" in lower:
        return "yes_no"
    if "what do you want to" in lower or "pick an object" in lower:
        return "selection"
    if "call a" in msg or "name an" in msg:
        return "text"
    return "none"


def _parse_vitals(status: str) -> Vitals:
    hp_match = _STATUS_PATTERNS["hp"].search(status)
    power_match = _STATUS_PATTERNS["power"].search(status)

    def one(name: str) -> int | None:
        match = _STATUS_PATTERNS[name].search(status)
        return int(match.group(1)) if match else None

    return Vitals(
        hp=int(hp_match.group(1)) if hp_match else None,
        hp_max=int(hp_match.group(2)) if hp_match else None,
        power=int(power_match.group(1)) if power_match else None,
        power_max=int(power_match.group(2)) if power_match else None,
        ac=one("ac"),
        experience_level=one("experience_level"),
        dungeon_level=one("dungeon_level"),
        gold=one("gold"),
        turn=one("turn"),
    )


def normalize_tty(
    text: str,
    *,
    cols: int = DEFAULT_COLS,
    rows: int = DEFAULT_ROWS,
) -> NethackObservation:
    """Normalize one classic terminal frame using visible information only."""
    if type(cols) is not int or cols < 1:
        raise ValueError("cols must be a positive integer")
    if type(rows) is not int or rows < 3:
        raise ValueError("rows must be an integer >= 3")

    raw_lines = text.splitlines()
    lines = [(line[:cols]).ljust(cols) for line in raw_lines[:rows]]
    while len(lines) < rows:
        lines.append(" " * cols)

    message = lines[0].rstrip()
    status_lines = tuple(line.rstrip() for line in lines[-2:] if line.rstrip())
    map_rows = tuple(lines[1:-2])

    players: list[tuple[int, int]] = []
    for y, row in enumerate(map_rows):
        for x, char in enumerate(row):
            if char == "@":
                players.append((x, y))
    # During menus/prompts '@' may be absent.  Multiple visible '@' glyphs are
    # ambiguous, so fail soft rather than guessing which one is the player.
    player = players[0] if len(players) == 1 else None

    status = " ".join(status_lines)
    status_lower = status.lower()
    conditions = tuple(
        condition
        for condition in _CONDITIONS
        if re.search(rf"(?<![A-Za-z]){re.escape(condition.lower())}(?![A-Za-z])", status_lower)
    )
    return NethackObservation(
        raw_text=text,
        message=message,
        map_rows=map_rows,
        status_lines=status_lines,
        player=player,
        vitals=_parse_vitals(status),
        conditions=conditions,
        prompt=_prompt_kind(text, message),
    )
