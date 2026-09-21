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


def rotation_config(g) -> dict:
    if not getattr(g, "config_path", None) or not g.config_path.is_file():
        return {}
    raw = tomllib.loads(g.config_path.read_text()).get("corner_rotation", {})
    if not isinstance(raw, dict) or type(raw.get("enabled", False)) is not bool:
        raise CornerCatalogError("invalid corner_rotation configuration")
    return raw


def rotation_enabled(g) -> bool:
    return rotation_config(g).get("enabled", False)


def load_catalog(g) -> tuple[Corner, ...]:
    # Adapter names are an execution API, never a scheduler's selection policy.
    from .corner_adapters import ADAPTERS

    rows = rotation_config(g).get("corners", [])
    if not isinstance(rows, list):
        raise CornerCatalogError("corner_rotation.corners must be an array")
    result = []
    for row in rows:
        if not isinstance(row, dict) or set(row) - {
            "id", "adapter", "game", "enabled", "paused", "live_eligible"
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
        if any(type(value) is not bool for value in
               (item.enabled, item.paused, item.live_eligible)) or item.live_eligible:
            raise CornerCatalogError("corners must remain live_eligible=false")
        result.append(item)
    if len({c.id for c in result}) != len(result) or len({c.game for c in result}) != len(result):
        raise CornerCatalogError("duplicate corner id or game")
    return tuple(result)
