"""Normalize CLI NetHack terminal state for the read-only spectator UI."""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

COLS = 80
ROWS = 24
SNAPSHOT_SCHEMA_VERSION = 1

_STATUS_HINT_RE = re.compile(r"(?:Dlvl|HP|Pw|AC):")
_DLVL_RE = re.compile(r"\bDlvl:([^\s]+)")
_GOLD_RE = re.compile(r"(?:\$|Gold):(-?\d+)")
_HP_RE = re.compile(r"\bHP:(-?\d+)\((-?\d+)\)")
_PW_RE = re.compile(r"\bPw:(-?\d+)\((-?\d+)\)")
_AC_RE = re.compile(r"\bAC:(-?\d+)")
_EXP_RE = re.compile(r"\bExp:(\d+)(?:/(\d+))?")
_TURN_RE = re.compile(r"\bT:(\d+)")


def _normalize_terminal(text: str) -> list[str]:
    rows = text.replace("\r", "").split("\n")
    if rows and rows[-1] == "":
        rows.pop()
    if len(rows) < ROWS:
        rows = [""] * (ROWS - len(rows)) + rows
    elif len(rows) > ROWS:
        rows = rows[-ROWS:]
    return [row[:COLS].ljust(COLS) for row in rows]


def _parse_int(match: re.Match[str] | None, group: int = 1) -> int | None:
    if match is None:
        return None
    try:
        return int(match.group(group), 10)
    except (TypeError, ValueError):
        return None


def _status_start(lines: list[str]) -> int | None:
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if "HP:" in line and ("Dlvl:" in line or "AC:" in line or "Pw:" in line):
            return max(0, index - 1)
    for index in range(len(lines) - 1, -1, -1):
        if _STATUS_HINT_RE.search(lines[index]):
            return max(0, index - 1)
    return None


def _parse_status(lines: list[str]) -> dict[str, Any]:
    text = " ".join(line.strip() for line in lines if line.strip())
    dlvl = _DLVL_RE.search(text)
    hp = _HP_RE.search(text)
    pw = _PW_RE.search(text)
    exp = _EXP_RE.search(text)
    return {
        "dlvl": dlvl.group(1) if dlvl else None,
        "gold": _parse_int(_GOLD_RE.search(text)),
        "hp": _parse_int(hp),
        "max_hp": _parse_int(hp, 2),
        "pw": _parse_int(pw),
        "max_pw": _parse_int(pw, 2),
        "ac": _parse_int(_AC_RE.search(text)),
        "exp_level": _parse_int(exp),
        "exp_points": _parse_int(exp, 2),
        "turn": _parse_int(_TURN_RE.search(text)),
        "raw": [line.rstrip() for line in lines],
    }


def parse_terminal_snapshot(text: str) -> dict[str, Any]:
    """Return conservative map/HUD state; menus remain raw terminal mode."""
    lines = _normalize_terminal(text)
    status_start = _status_start(lines)
    if status_start is None:
        map_rows: list[str] = []
    else:
        map_rows = [line[:COLS] for line in lines[1:max(1, status_start)]]

    player: dict[str, int] | None = None
    for y, row in enumerate(map_rows):
        x = row.find("@")
        if x >= 0:
            player = {"x": x, "y": y}
            break

    status_lines = lines[status_start:] if status_start is not None else []
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "mode": "map" if map_rows else "terminal",
        "message": lines[0].strip(),
        "map": map_rows,
        "player": player,
        "status": _parse_status(status_lines),
        "terminal": [line.rstrip() for line in lines],
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None


def read_run_summary(state_dir: Path) -> dict[str, Any] | None:
    """Expose only non-sensitive presentation fields from durable run state."""
    root = state_dir / "nethack"
    current = _read_json(root / "current.json")
    if current is None:
        return None
    run_id = current.get("run_id")
    if not isinstance(run_id, str):
        return None
    try:
        if str(uuid.UUID(run_id)) != run_id:
            return None
    except (ValueError, TypeError, AttributeError):
        return None
    run = _read_json(root / "runs" / f"{run_id}.json")
    if run is None or run.get("run_id") != run_id:
        return None
    fields = (
        "run_id",
        "expedition",
        "status",
        "score",
        "turns",
        "max_depth",
        "role",
        "race",
        "alignment",
        "got_amulet",
    )
    return {field: run.get(field) for field in fields}
