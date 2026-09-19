"""Production action arbitration for NetHack's turn-based holds (#490).

Safety is a bounded command contract, not a promise that the hero survives.
A normal bump may fight a hostile creature or displace a pet. It is allowed
only after visible retreat fails; force-fight and affirmative confirmations
are never generated. See docs/games/nethack-progress-contract.md.
"""
from __future__ import annotations

from .actions import Action
from .nethack_exploration import DIRECTIONS, MOVE_KEYS, _glyph, move_target, visible_safe_step
from .nethack_observation import NethackObservation
from .nethack_policy import (
    NethackLayeredPolicy, PolicyDecision, STEP_OUT_INTENTS, _has_any,
    _visible_creature_contact, creature_glyph, decline_prompt,
    rest_action_for_hold, step_out_of_hold,
)


def gameplay_ready(obs: NethackObservation) -> bool:
    return (
        obs.prompt == "none" and obs.player is not None
        and obs.vitals.dungeon_level is not None
        and obs.vitals.hp is not None and obs.vitals.hp > 0
        and obs.vitals.hp_max is not None and obs.vitals.hp_max > 0
        and not _has_any(obs, NethackLayeredPolicy._SEVERE_CONDITIONS | NethackLayeredPolicy._FOOD_EMERGENCY)
    )


def movement_ready(obs: NethackObservation) -> bool:
    return gameplay_ready(obs) and not _has_any(obs, NethackLayeredPolicy._MOVEMENT_IMPAIRING)


def assert_production_safe(decision: PolicyDecision, obs: NethackObservation) -> None:
    """Check the observation as well as key shape at the final action boundary.

    In particular, 'n' means southeast on the map and no at a confirmation.
    A syntactic key allowlist alone cannot distinguish these contexts.
    """
    if not decision.actions:
        return
    if len(decision.actions) != 1 or decision.actions[0].type != "text":
        raise RuntimeError("production action must be one literal text key")
    key = decision.actions[0].text
    intent = decision.intent
    allowed = False
    if intent == "advance_message":
        allowed = key == " " and obs.prompt == "more"
    elif intent in {"decline_save", "decline_attack"}:
        allowed = key == "n" and decline_prompt(obs) == intent
    elif intent in {"explore_step", "retreat_step"}:
        allowed = movement_ready(obs) and visible_safe_step(obs, key)
    elif intent == "bump_creature":
        target = move_target(obs, key)
        allowed = (
            movement_ready(obs) and target is not None
            and creature_glyph(_glyph(obs, target))
            # I is an invisible-monster marker, not a visible creature.
            and _glyph(obs, target) != "I"
        )
    elif intent == "rest_turn":
        allowed = (
            gameplay_ready(obs) and key == "." and not _visible_creature_contact(obs)
            and "Hungry" not in obs.conditions
        )
    if not allowed:
        raise RuntimeError("production action violates its observed context")


class NethackProgressResolver:
    """Resolve reviewed holds without changing advisory/shadow policy intent.

    Memory is local to a brain/runtime, bounded by the current visible scene.
    Rejected movement/bump edges expire when map, player or depth changes;
    changing messages or a turn counter alone must not re-arm a peaceful attack.
    """

    def __init__(self) -> None:
        self._scene = None
        self._pending: tuple[NethackObservation, str, str] | None = None
        self._failed: dict[str, int] = {}
        self._answered_prompt: str | None = None

    def observe(self, obs: NethackObservation, explorer) -> None:
        if obs.raw_text != self._answered_prompt:
            self._answered_prompt = None
        decline = decline_prompt(obs)
        if decline is not None:
            # An observed rejection is causal only if our preceding action
            # targeted this same position/depth. Never infer pet/hostile identity.
            if decline == "decline_attack" and self._pending is not None:
                before, key, _intent = self._pending
                if (obs.player, obs.vitals.dungeon_level) == (before.player, before.vitals.dungeon_level):
                    explorer.blocked_steps.add((before.player, key))
                self._pending = None
            return
        if obs.prompt != "none":
            return  # retain pending movement through a More page
        scene = (obs.vitals.dungeon_level, obs.player, obs.map_rows)
        if scene != self._scene:
            explorer.blocked_steps.clear()
            self._failed.clear()
            self._scene = scene
            self._pending = None
            return
        if self._pending is None:
            return
        before, key, intent = self._pending
        self._pending = None
        # Same position alone is not failed combat: a hit consumes a turn
        # without moving the hero. Prefer T; without T, identical frames give
        # no evidence of progress, so combat attempts must also be bounded.
        no_turn = (
            before.vitals.turn is not None and obs.vitals.turn == before.vitals.turn
        ) or (
            before.vitals.turn is None
            and obs.raw_text == before.raw_text
        )
        if no_turn:
            self._failed[key] = self._failed.get(key, 0) + 1
            if self._failed[key] >= 2:
                explorer.blocked_steps.add((obs.player, key))
        else:
            self._failed.pop(key, None)

    def resolve(self, decision: PolicyDecision, obs: NethackObservation, explorer) -> PolicyDecision:
        result = self._resolve(decision, obs, explorer)
        assert_production_safe(result, obs)
        return result

    def sent(self, decision: PolicyDecision, obs: NethackObservation) -> None:
        """Record only after fresh validation and adapter.act returned normally.

        This acknowledges transport, not game acceptance or turn advancement.
        resolve() alone must never spend retry budgets or create pending edges.
        """
        if decision.actions:
            key = decision.actions[0].text
            if decision.intent in {"decline_save", "decline_attack", "advance_message"}:
                self._answered_prompt = obs.raw_text
            elif key in MOVE_KEYS:
                self._pending = (obs, key, decision.intent)

    @staticmethod
    def _action(intent: str, reason: str, key: str) -> PolicyDecision:
        return PolicyDecision("tactical", intent, reason, (Action(type="text", text=key),))

    @staticmethod
    def _hold(reason: str) -> PolicyDecision:
        return PolicyDecision("strategic", "progress_blocked", reason, requires_llm=True)

    def _resolve(self, decision: PolicyDecision, obs: NethackObservation, explorer) -> PolicyDecision:
        decline = decline_prompt(obs)
        if decline == "decline_attack":
            decision = self._action(decline, "decline an observed attack confirmation", "n")
        if decision.actions:
            if decision.intent in {"decline_save", "decline_attack", "advance_message"} and self._answered_prompt == obs.raw_text:
                return self._hold("prompt already answered; waiting for a new frame")
            return decision
        if not gameplay_ready(obs) or decision.intent not in STEP_OUT_INTENTS:
            return decision

        contact = _visible_creature_contact(obs)
        # Low HP/impairment without contact benefits from one rest turn.
        # Hungry exploration must not be replaced by endless nutrition-burning
        # rest. It may travel, but does not guess food or item commands.
        if not contact and "Hungry" not in obs.conditions:
            rest = rest_action_for_hold(decision, obs)
            if rest is not None:
                return self._action("rest_turn", "one recovery turn without visible contact", rest.text)

        step = step_out_of_hold(decision, obs, explorer)
        if step is not None:
            return self._action("retreat_step", "visible terrain before any creature contact", step.text)

        if contact and movement_ready(obs):
            for _dx, _dy, key in DIRECTIONS:
                target = move_target(obs, key)
                glyph = _glyph(obs, target)
                if creature_glyph(glyph) and glyph != "I" and (obs.player, key) not in explorer.blocked_steps:
                    # Deliberate expansion from P3b: ordinary bump preserves
                    # NetHack's pet/peaceful handling. F+direction or y would
                    # bypass that protection and remain forbidden.
                    return self._action("bump_creature", "no visible retreat; one ordinary contact attempt", key)
        return self._hold("no reviewed progress action; prompt, impairment or rejected routes need context")
