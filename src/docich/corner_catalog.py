"""Canonical, flat production corner catalog (execution belongs to adapters)."""
from __future__ import annotations

from dataclasses import dataclass
import tomllib

from .naming import validate_game_name


class CornerCatalogError(ValueError):
    pass


@dataclass(frozen=True)
class Corner:
    id: str
    adapter: str
    game: str
    enabled: bool = True
    paused: bool = False
    live_eligible: bool = False
    # None inherits retro_corner.target_matches (legacy default: 3).
    target_matches: int | None = None


# Dispatch policies. "interval" keeps the 24h/N cadence; "queue" fires the next
# eligible corner as soon as the shared slot is free (cooldown-limited).
SCHEDULE_MODES = ("interval", "queue")
DEFAULT_COOLDOWN_HOURS = 24.0
MAX_COOLDOWN_HOURS = 24 * 30


def rotation_config(g) -> dict:
    if not getattr(g, "config_path", None) or not g.config_path.is_file():
        return {}
    raw = tomllib.loads(g.config_path.read_text()).get("corner_rotation", {})
    if not isinstance(raw, dict) or type(raw.get("enabled", False)) is not bool:
        raise CornerCatalogError("invalid corner_rotation configuration")
    mode = raw.get("schedule_mode", "interval")
    if mode not in SCHEDULE_MODES:
        raise CornerCatalogError("corner_rotation.schedule_mode must be 'interval' or 'queue'")
    cooldown = raw.get("cooldown_hours", DEFAULT_COOLDOWN_HOURS)
    if (type(cooldown) not in (int, float) or isinstance(cooldown, bool)
            or not 0 < float(cooldown) <= MAX_COOLDOWN_HOURS):
        raise CornerCatalogError("corner_rotation.cooldown_hours must be a positive number")
    return raw


def rotation_enabled(g) -> bool:
    return rotation_config(g).get("enabled", False)


def schedule_mode(g) -> str:
    return rotation_config(g).get("schedule_mode", "interval")


def cooldown_seconds(g) -> float:
    return float(rotation_config(g).get("cooldown_hours", DEFAULT_COOLDOWN_HOURS)) * 3600.0


def load_catalog(g) -> tuple[Corner, ...]:
    # Adapter names are an execution API, never a scheduler's selection policy.
    from .corner_adapters import ADAPTERS

    rows = rotation_config(g).get("corners", [])
    if not isinstance(rows, list):
        raise CornerCatalogError("corner_rotation.corners must be an array")
    result = []
    for row in rows:
        if not isinstance(row, dict) or set(row) - {
            "id", "adapter", "game", "enabled", "paused", "live_eligible", "target_matches"
        }:
            raise CornerCatalogError("invalid corner catalog entry")
        try:
            item = Corner(**row)
            validate_game_name(item.id)
            validate_game_name(item.game)
        except (TypeError, ValueError) as exc:
            raise CornerCatalogError("invalid corner identity") from exc
        if item.adapter not in ADAPTERS:
            raise CornerCatalogError("unsupported corner adapter")
        if item.target_matches is not None and (
            item.adapter != "game" or type(item.target_matches) is not int
            or not 1 <= item.target_matches <= 100
        ):
            raise CornerCatalogError("target_matches requires game adapter and integer 1-100")
        if any(type(value) is not bool for value in
               (item.enabled, item.paused, item.live_eligible)) or item.live_eligible:
            raise CornerCatalogError("corners must remain live_eligible=false")
        result.append(item)
    if len({c.id for c in result}) != len(result) or len({c.game for c in result}) != len(result):
        raise CornerCatalogError("duplicate corner id or game")
    return tuple(result)
