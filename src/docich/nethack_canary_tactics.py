"""Catalog-driven canary tactical policy (P6).

The action table is data: ``config/nethack-canary-actions.json``.  This module
holds only the reviewed key effects (``REVIEWED_EFFECTS``); the catalog is the
single source of truth mapping each action id to its effect.  A catalog change
(enable/disable, priority order, different preconditions, or a new action id
reusing a reviewed effect) therefore changes canary behaviour, which is what
lets a candidate be compared and promoted by
:mod:`docich.nethack_promotion_gate`.

Only the canary worker imports this module.  Production NetHack
(``src/docich/agent/brains.py``) keeps using :mod:`docich.nethack_policy`.
"""
from __future__ import annotations

import os
from pathlib import Path

from .actions import Action
from .nethack_action_spec import (
    REVIEWED_EFFECTS,
    ActionContext,
    ActionSpec,
    keys_match,
    load_action_catalog,
    precondition,
)
from .nethack_canary_rules import (
    DIRECTION_KEY,
    attackable_neighbors,
    door_key,
    food_item,
    openable_neighbors,
)
from .nethack_exploration import NethackExplorer
from .nethack_observation import NethackObservation
from .nethack_policy import NethackLayeredPolicy, PolicyDecision

CATALOG_ENV = "DOCICH_CANARY_CATALOG"
_CATALOG_RELATIVE = Path("config") / "nethack-canary-actions.json"

# Reviewed key effects this policy can execute.  A catalog candidate may use
# any of these effects under a new or existing action id; the policy never
# hard-codes action ids.
SUPPORTED_EFFECTS = frozenset(REVIEWED_EFFECTS)

# Deprecated P6f alias: the set of action ids shipped in the reviewed catalog.
# New code should use SUPPORTED_EFFECTS.  Kept so existing imports keep
# working during the migration.
SUPPORTED_ACTION_IDS = frozenset(
    {
        "advance_message",
        "confirm_attack",
        "decline_prompt",
        "directional_travel",
        "eat_food",
        "rest_impaired",
        "attack_adjacent",
        "rest_low_hp",
        "open_door",
        "explore_step",
        "rest",
    }
)


def resolve_catalog_path() -> Path:
    """Resolve the reviewed catalog for the host checkout or the image."""
    override = os.environ.get(CATALOG_ENV, "").strip()
    if override:
        path = Path(override)
        if not path.is_file():
            raise ValueError(f"{CATALOG_ENV} does not exist: {override}")
        return path
    for parent in Path(__file__).resolve().parents:
        candidate = parent / _CATALOG_RELATIVE
        if candidate.is_file():
            return candidate
    raise ValueError("canary action catalog not found")


def load_default_catalog() -> tuple[ActionSpec, ...]:
    return load_action_catalog(resolve_catalog_path())


class CanaryTacticalPolicy:
    """Baseline canary policy driven by the declarative action catalog."""

    def __init__(
        self,
        explorer: NethackExplorer | None = None,
        specs: tuple[ActionSpec, ...] | None = None,
    ) -> None:
        self._base = NethackLayeredPolicy(explorer)
        self._specs = specs if specs is not None else load_default_catalog()
        self._ordered = tuple(sorted(self._specs, key=lambda spec: spec.priority))
        # (dungeon_level, x, y) of doors whose open attempt did not change the
        # tile; they are never retried so the canary cannot loop on a lock.
        self._failed_doors: set[tuple[int, int, int]] = set()

    # --- effects (reviewed key producers, dispatched by spec.effect) --------

    def _run_effect(
        self, spec: ActionSpec, obs, inventory, ctx: ActionContext
    ) -> tuple[str, ...] | None:
        """Produce keys for a spec via its declarative effect.

        Returns None when the effect cannot resolve right now (no adjacent
        monster, no safe step, no food), so the policy falls through to the
        next spec.  The policy never branches on the action id.
        """
        if spec.effect not in SUPPORTED_EFFECTS:
            return None
        if spec.effect == "keys":
            return tuple(spec.key_pattern)
        if spec.effect == "attack_direction":
            neighbors = attackable_neighbors(obs)
            if not neighbors:
                return None
            dx, dy, _glyph = neighbors[0]
            return (DIRECTION_KEY[(dx, dy)],)
        if spec.effect == "open_door":
            for dx, dy, _glyph in openable_neighbors(obs):
                if door_key(obs, dx, dy) not in ctx.failed_doors:
                    self._failed_doors.add(door_key(obs, dx, dy))
                    return ("o", DIRECTION_KEY[(dx, dy)])
            return None
        if spec.effect == "eat_item":
            item = food_item(inventory)
            return ("e", item.letter) if item is not None else None
        if spec.effect == "explore_step":
            step = self._base.explorer.plan_step(obs)
            return (step.key,) if step is not None else None
        if spec.effect == "directional_travel":
            step = self._base.explorer.plan_step(_without_prompt(obs))
            return (step.key,) if step is not None else None
        return None

    # --- decision ---------------------------------------------------------

    def decide(
        self,
        obs: NethackObservation,
        *,
        inventory: tuple = (),
    ) -> PolicyDecision:
        ctx = ActionContext(
            observation=obs,
            inventory=inventory,
            failed_doors=frozenset(self._failed_doors),
            explorer=self._base.explorer,
        )
        for spec in self._ordered:
            if not spec.enabled:
                continue
            if not all(precondition(name, ctx) for name in spec.preconditions):
                continue
            keys = self._run_effect(spec, obs, inventory, ctx)
            if keys is None:
                continue
            if not keys_match(spec.key_pattern, keys):
                raise RuntimeError(
                    f"canary action {spec.id!r} produced keys {keys!r} outside {spec.key_pattern!r}"
                )
            return PolicyDecision(
                layer="tactical",
                intent=spec.id,
                reason=spec.description or spec.id,
                actions=tuple(Action(type="text", text=key) for key in keys),
            )
        # No reviewed action matches (for example a selection prompt or a
        # severe status condition): defer to the P3b base policy, which returns
        # an LLM/observation decision and never a canary key.
        return self._base.decide(obs)

    def assert_safe(self, decision: PolicyDecision) -> None:
        """Re-check a decision against this policy's catalog allowlist."""
        assert_canary_safe(decision, self._specs)


def _without_prompt(obs: NethackObservation) -> NethackObservation:
    from dataclasses import replace

    return replace(obs, prompt="none")


def assert_canary_safe(decision: PolicyDecision, specs: tuple[ActionSpec, ...]) -> None:
    """Allow only keys declared by the catalog spec for this intent."""
    if not decision.actions:
        return
    by_id = {spec.id: spec for spec in specs}
    spec = by_id.get(decision.intent)
    if spec is None:
        raise RuntimeError(f"canary action {decision.intent!r} is not in the catalog")
    if not all(action.type == "text" for action in decision.actions):
        raise RuntimeError("canary policy attempted a non-text action")
    keys = tuple(action.text for action in decision.actions)
    if not keys_match(spec.key_pattern, keys):
        raise RuntimeError(
            f"canary action {spec.id!r} keys {keys!r} do not match {spec.key_pattern!r}"
        )
