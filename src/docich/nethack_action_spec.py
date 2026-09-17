"""Declarative canary action catalog and machine verification (P6).

Capability additions (new action types) should be gated by an automatic
canary check instead of by forbidding them.  This module makes an action a
piece of data:

    {id, risk_class, effect, preconditions, key_pattern, postconditions}

and provides the predicates needed to verify a recorded action trace
(before frame -> keys -> after frame) without any human in the loop.

The catalog is descriptive in P6a: it does not yet drive the policy.  A
consistency test keeps the catalog and the canary policy from drifting, and
the verifier is what a later promotion gate will call.

In P6g each action also declares its key ``effect`` (reviewed vocabulary in
``REVIEWED_EFFECTS``).  The policy dispatches on the effect, so a new action
id that reuses a reviewed effect needs no code change.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from .nethack_canary_rules import (
    DIRECTION_KEY,
    attackable_neighbors,
    door_key,
    food_item,
    openable_neighbors,
)
from .nethack_exploration import NethackExplorer
from .nethack_inventory import VisibleInventoryItem
from .nethack_observation import NethackObservation, Vitals, normalize_tty

CATALOG_SCHEMA_VERSION = 1

# Reviewed risk classes.  A new class must be added here deliberately.
REVIEWED_RISK_CLASSES = frozenset(
    {"message", "prompt", "movement", "combat", "door", "item", "rest"}
)

# Reviewed key-effect vocabulary (P6g).  The catalog is the single source of
# truth for which key effect each action uses; the policy dispatches on
# ``effect`` instead of hard-coding handlers per action id.  A new id that
# uses one of these reviewed effects needs no code change.
REVIEWED_EFFECTS = frozenset(
    {
        "keys",
        "attack_direction",
        "open_door",
        "eat_item",
        "explore_step",
        "directional_travel",
    }
)

# The key_pattern contract per effect.  ``None`` means "any literal-only
# pattern" (no placeholder resolution); otherwise the exact pattern required.
EFFECT_KEY_PATTERNS: dict[str, tuple[str, ...] | None] = {
    "keys": None,
    "attack_direction": ("{direction}",),
    "open_door": ("o", "{direction}"),
    "eat_item": ("e", "{item_letter}"),
    "explore_step": ("{direction}",),
    "directional_travel": ("{direction}",),
}

# Placeholders allowed in a key pattern.  ``{direction}`` stands for one of the
# eight vi movement keys; ``{item_letter}`` stands for one inventory letter.
KNOWN_PLACEHOLDERS = frozenset({"direction", "item_letter"})

_DIRECTION_KEYS = frozenset(DIRECTION_KEY.values())
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
    # Declarative key effect (P6g): which reviewed key-producing behaviour
    # this action uses.  Read from the catalog like enabled/priority.
    effect: str = "keys"
    # Catalog-driven policy fields: a disabled spec is never selected, and
    # lower priority numbers are tried first.
    enabled: bool = True
    priority: int = 100

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "risk_class": self.risk_class,
            "effect": self.effect,
            "preconditions": list(self.preconditions),
            "key_pattern": list(self.key_pattern),
            "postconditions": list(self.postconditions),
            "description": self.description,
            "enabled": self.enabled,
            "priority": self.priority,
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
        return bool(attackable_neighbors(obs))
    if head == "no_adjacent_attackable":
        return not attackable_neighbors(obs)
    if head == "adjacent_closed_door":
        return any(
            door_key(obs, dx, dy) not in ctx.failed_doors
            for dx, dy, _glyph in openable_neighbors(obs)
        )
    if head == "inventory_food":
        return food_item(ctx.inventory) is not None
    if head == "safe_step":
        # A direction prompt blocks plan_step, but the observable map is the
        # same; evaluate as if no prompt were up.
        return ctx.explorer.plan_step(replace(obs, prompt="none")) is not None
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
        enabled = item.get("enabled", True)
        priority = item.get("priority", 100)
        if type(enabled) is not bool:
            raise ValueError(f"enabled must be a boolean: {enabled!r}")
        if type(priority) is not int or isinstance(priority, bool):
            raise ValueError(f"priority must be an integer: {priority!r}")
        return ActionSpec(
            id=str(item["id"]),
            risk_class=str(item["risk_class"]),
            effect=str(item.get("effect", "keys")),
            preconditions=tuple(str(v) for v in item["preconditions"]),
            key_pattern=tuple(str(v) for v in item["key_pattern"]),
            postconditions=tuple(str(v) for v in item["postconditions"]),
            description=str(item.get("description", "")),
            enabled=enabled,
            priority=priority,
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
        if spec.effect not in REVIEWED_EFFECTS:
            raise ValueError(f"{spec.id}: unreviewed effect {spec.effect!r}")
        if not spec.key_pattern:
            raise ValueError(f"{spec.id}: empty key_pattern")
        for token in spec.key_pattern:
            if _TOKEN_RE.match(token):
                if token[1:-1] not in KNOWN_PLACEHOLDERS:
                    raise ValueError(f"{spec.id}: unknown placeholder {token!r}")
            elif len(token) != 1:
                raise ValueError(f"{spec.id}: key token must be one character: {token!r}")
        _validate_effect_contract(spec)
        for name in spec.preconditions:
            # Raises ValueError for an unknown name.
            precondition(name, _NullContext())
        for name in spec.postconditions:
            postcondition(name, _NULL_OBS, _NULL_OBS)


def _validate_effect_contract(spec: ActionSpec) -> None:
    """Require the key_pattern to match the declared effect's contract."""
    required = EFFECT_KEY_PATTERNS.get(spec.effect)
    if required is None:
        # The "keys" effect emits the literal pattern with no placeholder
        # resolution, so placeholders are not allowed here.
        for token in spec.key_pattern:
            if _TOKEN_RE.match(token):
                raise ValueError(
                    f"{spec.id}: effect 'keys' must not use placeholders: {token!r}"
                )
        return
    if tuple(spec.key_pattern) != tuple(required):
        raise ValueError(
            f"{spec.id}: effect {spec.effect!r} requires key_pattern "
            f"{list(required)!r}, got {list(spec.key_pattern)!r}"
        )


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


def parse_action_catalog(
    raw: object,
    *,
    allowed_action_ids: frozenset[str] | None = None,
    allowed_effects: frozenset[str] | None = None,
) -> tuple[ActionSpec, ...]:
    """Validate an in-memory catalog object (used by the P6 proposer too).

    The reviewed boundary is the effect/predicate/placeholder vocabulary
    (``REVIEWED_EFFECTS`` / precondition+postcondition names /
    ``KNOWN_PLACEHOLDERS``), enforced by :func:`validate_action_catalog`, so a
    catalog may introduce new action ids.  ``allowed_action_ids`` is kept for
    backward compatibility (P6f callers); new callers should pass
    ``allowed_effects`` to bound the effects a proposer may use.
    """
    if not isinstance(raw, dict) or raw.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise ValueError("action catalog schema_version is invalid")
    entries = raw.get("actions")
    if not isinstance(entries, list):
        raise ValueError("action catalog must contain an actions list")
    specs = tuple(_spec_from_dict(item) for item in entries)
    validate_action_catalog(specs)
    if allowed_action_ids is not None:
        unknown = sorted({spec.id for spec in specs if spec.id not in allowed_action_ids})
        if unknown:
            raise ValueError(f"catalog references actions outside the allowed set: {unknown}")
    if allowed_effects is not None:
        unknown_effects = sorted({spec.effect for spec in specs if spec.effect not in allowed_effects})
        if unknown_effects:
            raise ValueError(f"catalog references effects outside the allowed set: {unknown_effects}")
    return specs


def load_action_catalog(path: Path) -> tuple[ActionSpec, ...]:
    return parse_action_catalog(json.loads(Path(path).read_text(encoding="utf-8")))


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

