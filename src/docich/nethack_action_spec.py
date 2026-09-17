"""Declarative canary action catalog and machine verification (P6).

Capability additions (new action types) should be gated by an automatic
canary check instead of by forbidding them.  This module makes an action a
piece of data:

    {id, risk_class, preconditions, key_pattern, postconditions}

and provides the predicates needed to verify a recorded action trace
(before frame -> keys -> after frame) without any human in the loop.

The catalog is descriptive in P6a: it does not yet drive the policy.  A
consistency test keeps the catalog and the canary policy from drifting, and
the verifier is what a later promotion gate will call.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .nethack_canary_tactics import (
    _DIRECTION_KEY,
    _attackable_neighbors,
    _food_item,
    _openable_neighbors,
)
from .nethack_exploration import NethackExplorer
from .nethack_inventory import VisibleInventoryItem
from .nethack_observation import NethackObservation, Vitals, normalize_tty

CATALOG_SCHEMA_VERSION = 1

# Reviewed risk classes.  A new class must be added here deliberately.
REVIEWED_RISK_CLASSES = frozenset(
    {"message", "prompt", "movement", "combat", "door", "item", "rest"}
)

# Placeholders allowed in a key pattern.  ``{direction}`` stands for one of the
# eight vi movement keys; ``{item_letter}`` stands for one inventory letter.
KNOWN_PLACEHOLDERS = frozenset({"direction", "item_letter"})

_DIRECTION_KEYS = frozenset(_DIRECTION_KEY.values())
_LETTER_RE = re.compile(r"^[A-Za-z]$")
_TOKEN_RE = re.compile(r"^\{[a-z_]+\}$")

STATUS_VERIFIED = "verified"
STATUS_PRECONDITION = "precondition_not_met"
STATUS_KEYS = "keys_mismatch"
STATUS_POSTCONDITION = "postcondition_not_met"


@dataclass(frozen=True)
class ActionSpec:
    id: str
    risk_class: str
    preconditions: tuple[str, ...]
    key_pattern: tuple[str, ...]
    postconditions: tuple[str, ...]
    description: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "risk_class": self.risk_class,
            "preconditions": list(self.preconditions),
            "key_pattern": list(self.key_pattern),
            "postconditions": list(self.postconditions),
            "description": self.description,
        }


@dataclass(frozen=True)
class SpecVerification:
    spec_id: str
    status: str
    detail: str

    @property
    def verified(self) -> bool:
        return self.status == STATUS_VERIFIED

    def to_dict(self) -> dict[str, object]:
        return {"spec_id": self.spec_id, "status": self.status, "detail": self.detail}


@dataclass
class ActionContext:
    observation: NethackObservation
    inventory: tuple[VisibleInventoryItem, ...] = ()
    failed_doors: frozenset[tuple[int, int, int]] = frozenset()
    explorer: NethackExplorer = field(default_factory=NethackExplorer)


# --- preconditions -----------------------------------------------------------


def _door_key(obs: NethackObservation, dx: int, dy: int) -> tuple[int, int, int]:
    depth = obs.vitals.dungeon_level
    px, py = obs.player if obs.player is not None else (0, 0)
    return (depth if depth is not None else -1, px + dx, py + dy)


def precondition(name: str, ctx: ActionContext) -> bool:
    """Evaluate one declarative precondition name against a visible frame."""
    obs = ctx.observation
    head, _, arg = name.partition(":")
    if head == "prompt":
        return obs.prompt == arg
    if head == "message_contains":
        return arg.lower() in obs.raw_text.lower()
    if head == "condition":
        return arg in obs.conditions
    if head == "condition_any":
        return any(item in obs.conditions for item in arg.split("|") if item)
    if head == "hp_ratio_at_most":
        ratio = obs.vitals.hp_ratio
        return ratio is not None and ratio <= float(arg)
    if head == "player_visible":
        return obs.player is not None
    if head == "player_absent":
        return obs.player is None
    if head == "adjacent_attackable":
        return bool(_attackable_neighbors(obs))
    if head == "no_adjacent_attackable":
        return not _attackable_neighbors(obs)
    if head == "adjacent_closed_door":
        return any(
            _door_key(obs, dx, dy) not in ctx.failed_doors
            for dx, dy, _glyph in _openable_neighbors(obs)
        )
    if head == "inventory_food":
        return _food_item(ctx.inventory) is not None
    if head == "safe_step":
        return ctx.explorer.plan_step(obs) is not None
    raise ValueError(f"unknown precondition {name!r}")


# --- postconditions ----------------------------------------------------------


def postcondition(name: str, before: NethackObservation, after: NethackObservation) -> bool:
    """Evaluate one declarative postcondition name over before/after frames."""
    head, _, arg = name.partition(":")
    if name == "always":
        return True
    if head == "prompt_is":
        return after.prompt == arg
    if head == "prompt_not":
        return after.prompt != arg
    if head == "condition_cleared":
        return arg in before.conditions and arg not in after.conditions
    if head == "message_changed":
        return before.raw_text != after.raw_text
    if head == "turn_advanced":
        return (
            before.vitals.turn is not None
            and after.vitals.turn is not None
            and after.vitals.turn > before.vitals.turn
        )
    if head == "screen_changed":
        return before.raw_text != after.raw_text
    raise ValueError(f"unknown postcondition {name!r}")


# --- key pattern -------------------------------------------------------------


def _placeholder_ok(placeholder: str, token: str) -> bool:
    if placeholder == "direction":
        return token in _DIRECTION_KEYS
    if placeholder == "item_letter":
        return bool(_LETTER_RE.match(token))
    return False


def keys_match(key_pattern: tuple[str, ...], keys: tuple[str, ...]) -> bool:
    if len(key_pattern) != len(keys):
        return False
    for pattern, key in zip(key_pattern, keys):
        if _TOKEN_RE.match(pattern):
            if not _placeholder_ok(pattern[1:-1], key):
                return False
        elif pattern != key:
            return False
    return True


# --- catalog -----------------------------------------------------------------


def _spec_from_dict(item: object) -> ActionSpec:
    if not isinstance(item, dict):
        raise ValueError("action catalog entry must be an object")
    try:
        return ActionSpec(
            id=str(item["id"]),
            risk_class=str(item["risk_class"]),
            preconditions=tuple(str(v) for v in item["preconditions"]),
            key_pattern=tuple(str(v) for v in item["key_pattern"]),
            postconditions=tuple(str(v) for v in item["postconditions"]),
            description=str(item.get("description", "")),
        )
    except (KeyError, TypeError) as exc:
        raise ValueError(f"invalid action catalog entry: {item!r}") from exc


def validate_action_catalog(specs: tuple[ActionSpec, ...]) -> None:
    seen: set[str] = set()
    if not specs:
        raise ValueError("action catalog is empty")
    for spec in specs:
        if not spec.id or spec.id in seen:
            raise ValueError(f"duplicate or empty action id {spec.id!r}")
        seen.add(spec.id)
        if spec.risk_class not in REVIEWED_RISK_CLASSES:
            raise ValueError(f"{spec.id}: unreviewed risk_class {spec.risk_class!r}")
        if not spec.key_pattern:
            raise ValueError(f"{spec.id}: empty key_pattern")
        for token in spec.key_pattern:
            if _TOKEN_RE.match(token):
                if token[1:-1] not in KNOWN_PLACEHOLDERS:
                    raise ValueError(f"{spec.id}: unknown placeholder {token!r}")
            elif len(token) != 1:
                raise ValueError(f"{spec.id}: key token must be one character: {token!r}")
        for name in spec.preconditions:
            # Raises ValueError for an unknown name.
            precondition(name, _NullContext())
        for name in spec.postconditions:
            postcondition(name, _NULL_OBS, _NULL_OBS)


def _null_observation() -> NethackObservation:
    return NethackObservation(
        raw_text="",
        message="",
        map_rows=(),
        status_lines=(),
        player=None,
        vitals=Vitals(),
        conditions=(),
        prompt="none",
    )


_NULL_OBS = _null_observation()


class _NullContext:
    observation = _NULL_OBS
    inventory: tuple[VisibleInventoryItem, ...] = ()
    failed_doors: frozenset[tuple[int, int, int]] = frozenset()
    explorer = NethackExplorer()


def load_action_catalog(path: Path) -> tuple[ActionSpec, ...]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise ValueError("action catalog schema_version is invalid")
    entries = raw.get("actions")
    if not isinstance(entries, list):
        raise ValueError("action catalog must contain an actions list")
    specs = tuple(_spec_from_dict(item) for item in entries)
    validate_action_catalog(specs)
    return specs


def spec_by_id(specs: tuple[ActionSpec, ...], spec_id: str) -> ActionSpec:
    for spec in specs:
        if spec.id == spec_id:
            return spec
    raise KeyError(spec_id)


def verify_action_spec(
    spec: ActionSpec,
    *,
    before: NethackObservation,
    after: NethackObservation,
    keys: tuple[str, ...],
    inventory: tuple[VisibleInventoryItem, ...] = (),
    failed_doors: frozenset[tuple[int, int, int]] = frozenset(),
    explorer: NethackExplorer | None = None,
) -> SpecVerification:
    """Verify one recorded action trace against its declarative spec."""
    ctx = ActionContext(
        observation=before,
        inventory=inventory,
        failed_doors=failed_doors,
        explorer=explorer or NethackExplorer(),
    )
    for name in spec.preconditions:
        if not precondition(name, ctx):
            return SpecVerification(spec.id, STATUS_PRECONDITION, f"precondition {name!r} not met")
    if not keys_match(spec.key_pattern, keys):
        return SpecVerification(
            spec.id, STATUS_KEYS, f"keys {keys!r} do not match pattern {spec.key_pattern!r}"
        )
    for name in spec.postconditions:
        if not postcondition(name, before, after):
            return SpecVerification(
                spec.id, STATUS_POSTCONDITION, f"postcondition {name!r} not met"
            )
    return SpecVerification(spec.id, STATUS_VERIFIED, "ok")


def _inventory_from_trace(raw: object) -> tuple[VisibleInventoryItem, ...]:
    if not isinstance(raw, list):
        return ()
    items: list[VisibleInventoryItem] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            items.append(
                VisibleInventoryItem(
                    letter=str(entry["letter"]),
                    description=str(entry["description"]),
                    quantity=entry.get("quantity"),
                    buc=str(entry.get("buc", "unknown")),
                    equipped=bool(entry.get("equipped", False)),
                    unpaid=bool(entry.get("unpaid", False)),
                    category_hint=str(entry.get("category_hint", "unknown")),
                )
            )
        except KeyError:
            continue
    return tuple(items)


def verify_trace_file(
    path: Path,
    specs: tuple[ActionSpec, ...],
    *,
    cols: int = 80,
    rows: int = 24,
) -> tuple[SpecVerification, ...]:
    """Verify every recorded canary action trace line against the catalog."""
    by_id = {spec.id: spec for spec in specs}
    results: list[SpecVerification] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            results.append(SpecVerification("<unparsable>", STATUS_KEYS, "trace line is not JSON"))
            continue
        if not isinstance(record, dict):
            results.append(SpecVerification("<unparsable>", STATUS_KEYS, "trace line is not an object"))
            continue
        intent = record.get("intent")
        spec = by_id.get(str(intent))
        if spec is None:
            results.append(
                SpecVerification(str(intent), STATUS_PRECONDITION, "intent is not in the catalog")
            )
            continue
        before_raw = record.get("before")
        after_raw = record.get("after")
        keys = record.get("keys")
        if not isinstance(before_raw, str) or not isinstance(after_raw, str) or not isinstance(keys, list):
            results.append(SpecVerification(spec.id, STATUS_KEYS, "trace line is missing a frame or keys"))
            continue
        results.append(
            verify_action_spec(
                spec,
                before=normalize_tty(before_raw, cols=cols, rows=rows),
                after=normalize_tty(after_raw, cols=cols, rows=rows),
                keys=tuple(str(key) for key in keys),
                inventory=_inventory_from_trace(record.get("inventory")),
            )
        )
    return tuple(results)

