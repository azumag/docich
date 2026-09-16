"""Strict public-only shadow observation comparison for NetHack (P4a).

A structured source can write one JSON snapshot for comparison with the live
TTY normalization. The shadow source is never consulted by gameplay policy;
unknown fields are rejected so hidden engine state cannot quietly enter the
normalized public contract.
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import GameConfig, GlobalConfig
from .nethack_observation import NethackObservation, Vitals


SCHEMA_VERSION = 1
_ALLOWED_ROOT = frozenset({"schema_version", "source", "captured_at", "public"})
_ALLOWED_PUBLIC = frozenset({"message", "map_rows", "player", "vitals", "conditions", "prompt"})
_ALLOWED_VITALS = frozenset(
    {"hp", "hp_max", "power", "power_max", "ac", "experience_level", "dungeon_level", "gold", "turn"}
)
_ALLOWED_PROMPTS = frozenset({"none", "more", "direction", "yes_no", "selection", "text"})
_ALLOWED_CONDITIONS = frozenset(
    {
        "Hungry", "Weak", "Fainting", "Fainted", "Starved", "Blind", "Conf", "Stun",
        "Hallu", "Sick", "FoodPois", "Ill", "Slime", "Strngl", "Deaf", "Lev", "Fly", "Ride",
    }
)


ShadowStatus = Literal["disabled", "missing", "stale", "invalid", "match", "mismatch"]


@dataclass(frozen=True)
class ShadowPublicSnapshot:
    source: str
    captured_at: float
    message: str
    map_rows: tuple[str, ...]
    player: tuple[int, int] | None
    vitals: Vitals
    conditions: tuple[str, ...]
    prompt: str

    def to_observation(self) -> NethackObservation:
        return NethackObservation(
            raw_text="",
            message=self.message,
            map_rows=self.map_rows,
            status_lines=(),
            player=self.player,
            vitals=self.vitals,
            conditions=self.conditions,
            prompt=self.prompt,
        )


@dataclass(frozen=True)
class ShadowComparison:
    source: str
    matches: bool
    mismatches: tuple[str, ...]
    map_cells_compared: int
    map_cells_mismatched: int


@dataclass(frozen=True)
class ShadowOutcome:
    status: ShadowStatus
    comparison: ShadowComparison | None = None
    error: str | None = None


@dataclass(frozen=True)
class NethackShadowConfig:
    enabled: bool = False
    path: str = ""
    max_age_s: float = 5.0
    max_bytes: int = 131_072


def _exact_keys(raw: dict[str, object], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(raw) - set(allowed))
    if unknown:
        raise ValueError(f"{where} contains non-public/unknown fields: {unknown}")


def _optional_int(raw: dict[str, object], key: str) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"vitals.{key} must be int or null")
    return value


def parse_shadow_snapshot(data: str | bytes | dict[str, object]) -> ShadowPublicSnapshot:
    if isinstance(data, (bytes, bytearray)):
        data = bytes(data).decode("utf-8")
    if isinstance(data, str):
        try:
            raw = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"shadow snapshot is not valid JSON: {exc}") from exc
    else:
        raw = data
    if not isinstance(raw, dict):
        raise ValueError("shadow snapshot must be an object")
    _exact_keys(raw, _ALLOWED_ROOT, "shadow root")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported shadow schema_version")

    source = raw.get("source")
    if not isinstance(source, str) or not source.strip() or len(source) > 80:
        raise ValueError("shadow source must be a short non-empty string")
    captured_at = raw.get("captured_at")
    if isinstance(captured_at, bool) or not isinstance(captured_at, (int, float)):
        raise ValueError("captured_at must be a finite non-negative number")
    captured_at_value = float(captured_at)
    if not math.isfinite(captured_at_value) or captured_at_value < 0:
        raise ValueError("captured_at must be a finite non-negative number")

    public = raw.get("public")
    if not isinstance(public, dict):
        raise ValueError("shadow public must be an object")
    _exact_keys(public, _ALLOWED_PUBLIC, "shadow public")

    message = public.get("message", "")
    if not isinstance(message, str) or len(message) > 1024:
        raise ValueError("public.message must be a string <=1024 chars")

    rows_raw = public.get("map_rows", [])
    if not isinstance(rows_raw, list) or len(rows_raw) > 64:
        raise ValueError("public.map_rows must be a list with at most 64 rows")
    map_rows: list[str] = []
    for row in rows_raw:
        if not isinstance(row, str) or len(row) > 160:
            raise ValueError("each public.map_rows entry must be a string <=160 chars")
        map_rows.append(row)

    player_raw = public.get("player")
    player: tuple[int, int] | None
    if player_raw is None:
        player = None
    elif (
        isinstance(player_raw, list)
        and len(player_raw) == 2
        and all(type(value) is int for value in player_raw)
        and all(value >= 0 for value in player_raw)
    ):
        player = (player_raw[0], player_raw[1])
    else:
        raise ValueError("public.player must be [x,y] non-negative ints or null")

    vitals_raw = public.get("vitals", {})
    if not isinstance(vitals_raw, dict):
        raise ValueError("public.vitals must be an object")
    _exact_keys(vitals_raw, _ALLOWED_VITALS, "public.vitals")
    vitals = Vitals(
        hp=_optional_int(vitals_raw, "hp"),
        hp_max=_optional_int(vitals_raw, "hp_max"),
        power=_optional_int(vitals_raw, "power"),
        power_max=_optional_int(vitals_raw, "power_max"),
        ac=_optional_int(vitals_raw, "ac"),
        experience_level=_optional_int(vitals_raw, "experience_level"),
        dungeon_level=_optional_int(vitals_raw, "dungeon_level"),
        gold=_optional_int(vitals_raw, "gold"),
        turn=_optional_int(vitals_raw, "turn"),
    )

    conditions_raw = public.get("conditions", [])
    if not isinstance(conditions_raw, list) or not all(isinstance(v, str) for v in conditions_raw):
        raise ValueError("public.conditions must be a string list")
    if any(value not in _ALLOWED_CONDITIONS for value in conditions_raw):
        raise ValueError("public.conditions contains a non-public/unknown condition")
    conditions = tuple(dict.fromkeys(conditions_raw))

    prompt = public.get("prompt", "none")
    if not isinstance(prompt, str) or prompt not in _ALLOWED_PROMPTS:
        raise ValueError("public.prompt is invalid")

    return ShadowPublicSnapshot(
        source=source.strip(),
        captured_at=captured_at_value,
        message=message,
        map_rows=tuple(map_rows),
        player=player,
        vitals=vitals,
        conditions=conditions,
        prompt=prompt,
    )


def compare_public_observations(
    tty: NethackObservation,
    shadow: ShadowPublicSnapshot,
) -> ShadowComparison:
    other = shadow.to_observation()
    mismatches: list[str] = []

    if tty.message != other.message:
        mismatches.append("message")
    if tty.prompt != other.prompt:
        mismatches.append("prompt")
    if tty.player != other.player:
        mismatches.append("player")
    if set(tty.conditions) != set(other.conditions):
        mismatches.append("conditions")

    for field in (
        "hp", "hp_max", "power", "power_max", "ac", "experience_level",
        "dungeon_level", "gold", "turn",
    ):
        if getattr(tty.vitals, field) != getattr(other.vitals, field):
            mismatches.append(f"vitals.{field}")

    if len(tty.map_rows) != len(other.map_rows):
        mismatches.append("map_rows.length")
    map_cells_compared = 0
    map_cells_mismatched = 0
    for y in range(min(len(tty.map_rows), len(other.map_rows))):
        left, right = tty.map_rows[y], other.map_rows[y]
        if len(left) != len(right):
            mismatches.append(f"map_rows[{y}].length")
        for x in range(min(len(left), len(right))):
            map_cells_compared += 1
            if left[x] != right[x]:
                map_cells_mismatched += 1
    if map_cells_mismatched:
        mismatches.append("map_cells")

    return ShadowComparison(
        source=shadow.source,
        matches=not mismatches,
        mismatches=tuple(mismatches),
        map_cells_compared=map_cells_compared,
        map_cells_mismatched=map_cells_mismatched,
    )


def load_shadow_config(game: GameConfig) -> NethackShadowConfig:
    raw = game.raw if isinstance(game.raw, dict) else {}
    nethack = raw.get("nethack", {})
    section = nethack.get("shadow", {}) if isinstance(nethack, dict) else {}
    if section is None:
        section = {}
    if not isinstance(section, dict):
        raise ValueError("[nethack.shadow] must be a table")

    enabled = section.get("enabled", False)
    if type(enabled) is not bool:
        raise ValueError("shadow enabled must be boolean")
    path = section.get("path", "")
    if not isinstance(path, str) or len(path) > 1024:
        raise ValueError("shadow path must be a string")
    max_age_s = section.get("max_age_s", 5.0)
    if isinstance(max_age_s, bool) or not isinstance(max_age_s, (int, float)) or not 0.1 <= float(max_age_s) <= 300:
        raise ValueError("shadow max_age_s must be between 0.1 and 300")
    max_bytes = section.get("max_bytes", 131_072)
    if type(max_bytes) is not int or not 1024 <= max_bytes <= 1_048_576:
        raise ValueError("shadow max_bytes is out of range")
    return NethackShadowConfig(
        enabled=enabled,
        path=path.strip(),
        max_age_s=float(max_age_s),
        max_bytes=max_bytes,
    )


class NethackShadowController:
    """Read, validate, compare and log a shadow snapshot without affecting play."""

    def __init__(
        self,
        g: GlobalConfig,
        game: GameConfig,
        *,
        config: NethackShadowConfig | None = None,
        wall_time=time.time,
    ) -> None:
        self.config = config or load_shadow_config(game)
        self._wall_time = wall_time
        state_dir = Path(getattr(g, "state_dir", ".")).expanduser()
        if self.config.path:
            configured = Path(self.config.path).expanduser()
            if not configured.is_absolute():
                configured = state_dir / configured
        else:
            configured = state_dir / "nethack" / "shadow" / "latest.json"
        self.snapshot_path = configured
        self.log_path = state_dir / "nethack" / "shadow" / "comparisons.jsonl"

    def _log(self, payload: dict[str, object]) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            pass

    def compare(self, tty: NethackObservation) -> ShadowOutcome:
        if not self.config.enabled:
            return ShadowOutcome(status="disabled")
        try:
            stat = self.snapshot_path.stat()
        except FileNotFoundError:
            return ShadowOutcome(status="missing")
        except OSError as exc:
            return ShadowOutcome(status="invalid", error=str(exc)[:200])
        if stat.st_size > self.config.max_bytes:
            return ShadowOutcome(status="invalid", error="shadow snapshot exceeds size limit")
        try:
            snapshot = parse_shadow_snapshot(self.snapshot_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            outcome = ShadowOutcome(status="invalid", error=str(exc)[:240])
            self._log({"schema_version": 1, "ts": self._wall_time(), "status": outcome.status, "error": outcome.error})
            return outcome

        age = self._wall_time() - snapshot.captured_at
        if age > self.config.max_age_s or age < -5.0:
            outcome = ShadowOutcome(status="stale", error=f"shadow age {age:.3f}s outside accepted window")
            self._log(
                {
                    "schema_version": 1,
                    "ts": self._wall_time(),
                    "status": outcome.status,
                    "source": snapshot.source,
                    "age_s": age,
                }
            )
            return outcome

        comparison = compare_public_observations(tty, snapshot)
        status: ShadowStatus = "match" if comparison.matches else "mismatch"
        outcome = ShadowOutcome(status=status, comparison=comparison)
        self._log(
            {
                "schema_version": 1,
                "ts": self._wall_time(),
                "status": status,
                "source": comparison.source,
                "mismatches": list(comparison.mismatches),
                "map_cells_compared": comparison.map_cells_compared,
                "map_cells_mismatched": comparison.map_cells_mismatched,
                "policy_effect": "none",
            }
        )
        return outcome
