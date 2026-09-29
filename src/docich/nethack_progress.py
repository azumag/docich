"""Production action arbitration for NetHack waits and visible doors (#490).

Safety is a bounded command contract, not a promise that the hero survives.
A normal bump may fight a hostile creature or displace a pet. It is allowed
only after visible retreat fails; door opening is limited to a verified
adjacent target and a matching prompt on the next frame. Force-fight and
affirmative confirmations are never generated. See
``docs/games/nethack-progress-contract.md``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .actions import Action
from .nethack_exploration import (
    CARDINAL,
    DIRECTIONS,
    MOVE_KEYS,
    OPEN_DOOR_GLYPHS,
    _glyph,
    move_target,
    visible_safe_step,
)
from .nethack_observation import NethackObservation
from .nethack_policy import (
    NethackLayeredPolicy, PolicyDecision, STEP_OUT_INTENTS, _has_any,
    _visible_creature_contact, creature_glyph, decline_prompt,
    rest_action_for_hold, step_out_of_hold, turn_ready,
)


DoorKey = tuple[int, int, int]
MAX_UNCHANGED_DOOR_CAPTURES = 3


@dataclass(frozen=True)
class _DoorTarget:
    key: DoorKey
    player: tuple[int, int]
    direction: str


_OPEN_DOOR_DIRECTION_PROMPT = re.compile(
    r"In what direction\?(?:\s*\[[^\r\n]*\])?", re.IGNORECASE
)


def _is_open_door_direction_prompt(obs: NethackObservation) -> bool:
    return (
        obs.prompt == "direction"
        and _OPEN_DOOR_DIRECTION_PROMPT.fullmatch(obs.message.strip()) is not None
    )


def _door_action_ready(obs: NethackObservation, *, direction_prompt: bool = False) -> bool:
    allowed_prompts = {"direction"} if direction_prompt else {"none"}
    return (
        obs.prompt in allowed_prompts
        and obs.player is not None
        and obs.vitals.dungeon_level is not None
        and obs.vitals.hp is not None
        and obs.vitals.hp > 0
        and obs.vitals.hp_max is not None
        and obs.vitals.hp_max > 0
        and "Fainted" not in obs.conditions
        and not _has_any(
            obs,
            NethackLayeredPolicy._SEVERE_CONDITIONS
            | NethackLayeredPolicy._MOVEMENT_IMPAIRING,
        )
        and not _visible_creature_contact(obs)
    )


def _door_target_at(obs: NethackObservation, dx: int, dy: int, direction: str) -> _DoorTarget | None:
    if obs.player is None or obs.vitals.dungeon_level is None:
        return None
    x, y = obs.player[0] + dx, obs.player[1] + dy
    if _glyph(obs, (x, y)) != "+":
        return None
    return _DoorTarget((obs.vitals.dungeon_level, x, y), obs.player, direction)


def _next_unattempted_door(
    obs: NethackObservation, attempted_doors: set[DoorKey] | frozenset[DoorKey]
) -> _DoorTarget | None:
    if not _door_action_ready(obs):
        return None
    for dx, dy, direction in CARDINAL:
        target = _door_target_at(obs, dx, dy, direction)
        if target is not None and target.key not in attempted_doors:
            return target
    return None


def _door_target_matches(obs: NethackObservation, target: _DoorTarget | None) -> bool:
    player = obs.player
    if (
        target is None
        or not _is_open_door_direction_prompt(obs)
        or not _door_action_ready(obs, direction_prompt=True)
        or player is None
        or player != target.player
        or obs.vitals.dungeon_level != target.key[0]
    ):
        return False
    for dx, dy, direction in CARDINAL:
        if direction == target.direction:
            return (player[0] + dx, player[1] + dy) == target.key[1:] and _glyph(
                obs, target.key[1:]
            ) == "+"
    return False


def gameplay_ready(obs: NethackObservation) -> bool:
    """Complete gameplay frame with no condition tier that makes a move unsafe.

    Movement and contact keep this gate (owner decision 2026-09-23): only the
    ``.`` wait became unconditional.
    """
    return (
        obs.prompt == "none" and obs.player is not None
        and obs.vitals.dungeon_level is not None
        and obs.vitals.hp is not None and obs.vitals.hp > 0
        and obs.vitals.hp_max is not None and obs.vitals.hp_max > 0
        and not _has_any(obs, NethackLayeredPolicy._SEVERE_CONDITIONS | NethackLayeredPolicy._FOOD_EMERGENCY)
    )


def movement_ready(obs: NethackObservation) -> bool:
    return gameplay_ready(obs) and not _has_any(obs, NethackLayeredPolicy._MOVEMENT_IMPAIRING)


def assert_production_safe(
    decision: PolicyDecision,
    obs: NethackObservation,
    *,
    attempted_doors: set[DoorKey] | frozenset[DoorKey] = frozenset(),
    pending_door: _DoorTarget | None = None,
    opened_doors: set[DoorKey] | frozenset[DoorKey] = frozenset(),
) -> None:
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
        allowed = movement_ready(obs) and visible_safe_step(obs, key, opened_doors)
    elif intent == "bump_creature":
        target = move_target(obs, key)
        allowed = (
            movement_ready(obs) and target is not None
            and creature_glyph(_glyph(obs, target))
            # I is an invisible-monster marker, not a visible creature.
            and _glyph(obs, target) != "I"
        )
    elif intent == "rest_turn":
        # Any complete frame may spend one wait turn (owner decision
        # 2026-09-23); turn_ready is what keeps `.` from answering a prompt.
        allowed = turn_ready(obs) and key == "."
    elif intent == "open_door_start":
        allowed = key == "o" and _next_unattempted_door(obs, attempted_doors) is not None
    elif intent == "open_door_direction":
        allowed = (
            pending_door is not None
            and key == pending_door.direction
            and _door_target_matches(obs, pending_door)
        )
    if not allowed:
        raise RuntimeError("production action violates its observed context")


class NethackProgressResolver:
    """Resolve reviewed waits and door prompts without changing policy intent.

    Memory is local to a brain/runtime, bounded by the current visible scene.
    Rejected movement/bump edges expire when map, player or depth changes;
    changing messages or a turn counter alone must not re-arm a peaceful attack.
    """

    def __init__(self) -> None:
        self._scene = None
        self._pending: tuple[NethackObservation, str, str] | None = None
        self._failed: dict[str, int] = {}
        self._answered_prompt: str | None = None
        self._attempted_doors: set[DoorKey] = set()
        self._pending_door: _DoorTarget | None = None
        self._pending_door_raw_text: str | None = None
        self._pending_door_unchanged_captures = 0
        self._door_result_pending: _DoorTarget | None = None
        self._door_result_raw_text: str | None = None
        self._door_result_unchanged_captures = 0

    def _pending_door_capture_unchanged(self, obs: NethackObservation) -> bool:
        target = self._pending_door
        return (
            target is not None
            and self._pending_door_raw_text is not None
            and obs.prompt == "none"
            and obs.raw_text == self._pending_door_raw_text
            and obs.vitals.dungeon_level == target.key[0]
            and obs.player == target.player
        )

    def _clear_pending_door(self) -> None:
        self._pending_door = None
        self._pending_door_raw_text = None
        self._pending_door_unchanged_captures = 0

    def _door_result_capture_unchanged(self, obs: NethackObservation) -> bool:
        target = self._door_result_pending
        return (
            target is not None
            and self._door_result_raw_text is not None
            and obs.prompt == "none"
            and obs.raw_text == self._door_result_raw_text
            and obs.vitals.dungeon_level == target.key[0]
            and obs.player == target.player
            and _glyph(obs, target.key[1:]) == "+"
        )

    def _clear_door_result(self) -> None:
        self._door_result_pending = None
        self._door_result_raw_text = None
        self._door_result_unchanged_captures = 0

    def observe(self, obs: NethackObservation, explorer) -> None:
        if obs.raw_text != self._answered_prompt:
            self._answered_prompt = None
        if self._pending_door is not None and obs.prompt == "none":
            target = self._pending_door
            if self._pending_door_capture_unchanged(obs):
                # tmux send_keys acknowledges transport only. A repeated copy
                # of the pre-command frame is not evidence that NetHack
                # rejected `o`; retain the target so a delayed direction
                # prompt can still be answered safely.
                self._pending_door_unchanged_captures = min(
                    self._pending_door_unchanged_captures + 1,
                    MAX_UNCHANGED_DOOR_CAPTURES,
                )
            else:
                if obs.vitals.dungeon_level == target.key[0] and obs.player == target.player:
                    glyph = _glyph(obs, target.key[1:])
                    if glyph in OPEN_DOOR_GLYPHS:
                        explorer.mark_opened_door(target.key)
                    elif glyph == "+":
                        explorer.mark_failed_door(target.key)
                self._clear_pending_door()
        if self._door_result_pending is not None and obs.prompt == "none":
            target = self._door_result_pending
            if self._door_result_capture_unchanged(obs):
                # Direction-key transport may return one or more copies of the
                # pre-open gameplay frame before NetHack publishes the result.
                # That old '+' is not evidence that the open command failed.
                self._door_result_unchanged_captures = min(
                    self._door_result_unchanged_captures + 1,
                    MAX_UNCHANGED_DOOR_CAPTURES,
                )
            else:
                self._clear_door_result()
                if obs.vitals.dungeon_level == target.key[0] and obs.player == target.player:
                    glyph = _glyph(obs, target.key[1:])
                    if glyph in OPEN_DOOR_GLYPHS:
                        explorer.mark_opened_door(target.key)
                    elif glyph == "+":
                        explorer.mark_failed_door(target.key)
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
        self.assert_action_safe(result, obs, explorer)
        return result

    def assert_action_safe(
        self, decision: PolicyDecision, obs: NethackObservation, explorer
    ) -> None:
        assert_production_safe(
            decision,
            obs,
            attempted_doors=self._attempted_doors,
            pending_door=self._pending_door,
            opened_doors=getattr(explorer, "opened_doors", frozenset()),
        )

    def sent(self, decision: PolicyDecision, obs: NethackObservation) -> None:
        """Record only after fresh validation and adapter.act returned normally.

        This acknowledges transport, not game acceptance or turn advancement.
        resolve() alone must never spend retry budgets or create pending edges.
        """
        if decision.actions:
            if decision.intent == "open_door_start":
                target = _next_unattempted_door(obs, self._attempted_doors)
                if target is None:
                    raise RuntimeError("open door action lost its visible target")
                self._attempted_doors.add(target.key)
                self._pending_door = target
                self._pending_door_raw_text = obs.raw_text
                self._pending_door_unchanged_captures = 0
                return
            if decision.intent == "open_door_direction":
                target = self._pending_door
                if not _door_target_matches(obs, target):
                    raise RuntimeError("open door direction lost its prompt context")
                self._door_result_pending = target
                self._door_result_raw_text = self._pending_door_raw_text
                self._door_result_unchanged_captures = 0
                self._clear_pending_door()
                return
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
        # This is a fail-closed observation state, not a gameplay choice:
        # complete gameplay frames are resolved to an explicit ``.`` below.
        return PolicyDecision("strategic", "progress_blocked", reason, requires_llm=True)

    def _resolve(self, decision: PolicyDecision, obs: NethackObservation, explorer) -> PolicyDecision:
        decline = decline_prompt(obs)
        if decline == "decline_attack":
            decision = self._action(decline, "decline an observed attack confirmation", "n")
        if (
            decision.intent in {"decline_save", "decline_attack", "advance_message"}
            and self._answered_prompt == obs.raw_text
        ):
            return self._hold("prompt already answered; waiting for a new frame")
        if self._pending_door is not None:
            if obs.prompt == "direction":
                if _door_target_matches(obs, self._pending_door):
                    return self._action(
                        "open_door_direction",
                        "answer the direction prompt for the same visible closed door",
                        self._pending_door.direction,
                    )
                return self._hold("direction prompt no longer matches the attempted door")
            if obs.prompt == "more":
                return decision
            if obs.prompt != "none":
                return self._hold("waiting for the attempted door response; unexpected prompt")
            if self._pending_door_capture_unchanged(obs):
                wait_reason = (
                    "door response remains pending after repeated unchanged captures"
                    if self._pending_door_unchanged_captures >= MAX_UNCHANGED_DOOR_CAPTURES
                    else "door response is pending; waiting for a changed frame"
                )
                return self._hold(wait_reason)
            # observe() normally records this failed command. Keep a defensive
            # clear here for direct resolver callers that skipped observe().
            target = self._pending_door
            if (
                obs.vitals.dungeon_level == target.key[0]
                and obs.player == target.player
                and _glyph(obs, target.key[1:]) == "+"
            ):
                explorer.mark_failed_door(target.key)
            self._clear_pending_door()
        if self._door_result_pending is not None:
            # A door result can itself require paging. Advance each fresh More
            # frame once while retaining the pending result for reconciliation.
            if obs.prompt == "more" and decision.intent == "advance_message":
                return decision
            # Do not send another gameplay key while a successfully transported
            # direction command still lacks a fresh result frame.
            if self._door_result_capture_unchanged(obs) or obs.prompt != "none":
                return self._hold("door result is pending; waiting for a fresh frame")
            return self._hold("door result is pending; awaiting observation reconciliation")
        if decision.intent in {
            "explore_step",
            "exploration_blocked",
            "seek_food",
            "hold_low_hp",
            "survival_emergency",
            "food_emergency",
        }:
            door = _next_unattempted_door(obs, self._attempted_doors)
            if door is not None:
                return self._action(
                    "open_door_start",
                    f"open visible adjacent door at {door.key[1:]}",
                    "o",
                )
        if decision.actions:
            return decision
        if not turn_ready(obs):
            return decision

        contact = _visible_creature_contact(obs)
        # Prefer an explicit rest command whenever there is no visible contact
        # and the policy has a reviewed safe hold. Severe status and food
        # emergencies remain fail-closed until a recovery action is reviewed;
        # Hungry exploration gets one chance to find visible terrain first and
        # is also not allowed to burn nutrition through this fallback.
        if not contact and decision.intent != "seek_food" and "Hungry" not in obs.conditions:
            rest = rest_action_for_hold(decision, obs)
            if rest is not None:
                return self._action("rest_turn", "one explicit wait turn without visible contact", rest.text)

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
        rest = rest_action_for_hold(decision, obs)
        if rest is not None:
            return self._action("rest_turn", "no reviewed progress action; spend one explicit wait turn", rest.text)
        return self._hold("prompt, unknown screen or player position is not a gameplay turn")
