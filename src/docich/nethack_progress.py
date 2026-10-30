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
from .nethack_food import production_food_item
from .nethack_inventory import parse_visible_inventory
from .nethack_observation import NethackObservation
from .nethack_policy import (
    DESCEND_COMMAND, MAX_PRAY_DUNGEON_LEVEL, NethackLayeredPolicy, PRAY_RECOVERY_STEPS,
    PolicyDecision, SEVERE_RECOVERY_COMMAND, STEP_OUT_INTENTS, _has_any,
    _visible_creature_contact, creature_glyph, decline_prompt,
    rest_action_for_hold, step_out_of_hold, turn_ready,
)


DoorKey = tuple[int, int, int]
MAX_UNCHANGED_DOOR_CAPTURES = 3
# Acked answers (More / decline) re-sent while the same prompt frame persists.
MAX_ANSWERED_PROMPT_RESENDS = 3
# #2806: a key sent to probe the inventory or answer the eat prompt is given
# this many unchanged captures before the pending flow is abandoned.
MAX_UNCHANGED_INVENTORY_CAPTURES = 3
# A probe that found no safe food is not repeated every turn (viewing
# inventory is free but the listing rarely changes); after an eaten item the
# meal and the hunger state need a few turns to settle before re-probing.
INVENTORY_REPROBE_TURNS = 30
EAT_REPROBE_TURNS = 10

# "What do you want to eat? [ab or ?*]" — the only prompt whose bracket may be
# answered with a probed food letter.
_EAT_PROMPT_RE = re.compile(
    r"What do you want to eat\?\s*\[([a-zA-Z$]+)\s*(?:or\s+\?\*)?\]")
_EMPTY_INVENTORY_RE = re.compile(
    r"empty-handed|not carrying anything", re.IGNORECASE)


def _eat_prompt_letters(obs: NethackObservation) -> frozenset[str] | None:
    """Only a whole, unanswered top-line eat prompt exposes answer letters.

    Mirroring ``decline_prompt``: a wrapped or already-answered line, and any
    prompt that is not exactly this question, stay fail-closed.
    """
    raw_lines = obs.raw_text.splitlines()
    if not raw_lines or raw_lines[0].strip() != obs.message.strip():
        return None
    match = _EAT_PROMPT_RE.fullmatch(obs.message.strip())
    return frozenset(match.group(1)) if match else None


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
    down_stairs: set[tuple[int, int]] | frozenset[tuple[int, int]] = frozenset(),
    pending_eat: str | None = None,
    inventory_pending: bool = False,
) -> None:
    """Check the observation as well as key shape at the final action boundary.

    In particular, 'n' means southeast on the map and no at a confirmation.
    A syntactic key allowlist alone cannot distinguish these contexts.
    """
    if not decision.actions:
        return
    if len(decision.actions) != 1:
        raise RuntimeError("production action must be one literal key")
    intent = decision.intent
    if intent == "close_inventory":
        # Escape is the cancel default for the inventory menu and for an eat
        # prompt whose bracket lost the probed letter. It is only produced
        # while a bounded inventory flow is pending, and never answers an
        # arbitrary prompt (the pending state, not the frame, authorizes it).
        if (decision.actions[0].type == "special"
                and decision.actions[0].key == "Escape" and inventory_pending):
            return
        raise RuntimeError("production action violates its observed context")
    if decision.actions[0].type != "text":
        raise RuntimeError("production action must be one literal text key")
    key = decision.actions[0].text
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
    elif intent == "status_recovery":
        # Reviewed prayer recovery (#759): exactly one `#` on a complete frame
        # with a visible severe status and a non-Gehennom dungeon level.  An
        # adjacent creature deliberately does not change the treatment.
        level = obs.vitals.dungeon_level
        allowed = (
            key == SEVERE_RECOVERY_COMMAND
            and turn_ready(obs)
            and level is not None
            and level <= MAX_PRAY_DUNGEON_LEVEL
        )
    elif intent == "descend_stairs":
        # The hero glyph hides the terrain, so the staircase is proven by the
        # explorer's remembered tile for this depth (#2666). A prompt frame is
        # never allowed to receive '>' because it could answer a question.
        allowed = (
            key == DESCEND_COMMAND
            and turn_ready(obs)
            and obs.player is not None
            and obs.player in down_stairs
        )
    elif intent == "open_door_start":
        allowed = key == "o" and _next_unattempted_door(obs, attempted_doors) is not None
    elif intent == "open_door_direction":
        allowed = (
            pending_door is not None
            and key == pending_door.direction
            and _door_target_matches(obs, pending_door)
        )
    elif intent == "open_inventory":
        # 'i' is read-only and turn-free; hunger tiers may also include the
        # food-emergency set, for which eating matters most (#2806).
        allowed = (
            key == "i"
            and turn_ready(obs)
            and _has_any(obs, {"Hungry"} | NethackLayeredPolicy._FOOD_EMERGENCY)
        )
    elif intent == "eat_start":
        # 'e' may only follow a completed probe that recorded a safe letter.
        allowed = key == "e" and turn_ready(obs) and pending_eat is not None
    elif intent == "eat_select":
        # The letter must be both the probed safe item and currently offered
        # inside the visible eat prompt's bracket.
        letters = _eat_prompt_letters(obs)
        allowed = (
            pending_eat is not None
            and key == pending_eat
            and letters is not None
            and key in letters
        )
    elif intent == "food_recovery":
        # A meal in progress may only ever spend one wait turn; contact and
        # step fallbacks stay out so the occupation is never interrupted.
        allowed = turn_ready(obs) and key == "."
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
        self._answered_prompt_resends = 0
        self._attempted_doors: set[DoorKey] = set()
        self._pending_door: _DoorTarget | None = None
        self._pending_door_raw_text: str | None = None
        self._pending_door_unchanged_captures = 0
        self._door_result_pending: _DoorTarget | None = None
        self._door_result_raw_text: str | None = None
        self._door_result_unchanged_captures = 0
        self._pray_step: int | None = None
        self._pray_expected: str | None = None
        self._pray_spent = False
        # #2806 bounded inventory/eat flow. ``_inv_stage`` walks the reviewed
        # sequence: "open" (i sent, awaiting listing) -> "close" (Escape sent,
        # awaiting the gameplay frame) -> "eat" (e sent, awaiting the eat
        # prompt) -> "eaten" (letter sent, awaiting the result frame).
        self._inv_stage: str | None = None
        self._inv_raw_text: str | None = None
        self._inv_unchanged = 0
        self._food_letter: str | None = None
        self._inv_no_food = False
        self._inv_turn: int | None = None
        self._inv_block_turns = 0
        self._meal_pending = False

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

    def _clear_inventory(self) -> None:
        """Abandon a pending inventory/eat flow; never guess a later key."""
        self._inv_stage = None
        self._inv_raw_text = None
        self._inv_unchanged = 0
        self._food_letter = None

    def observe(self, obs: NethackObservation, explorer) -> None:
        if turn_ready(obs) and not _has_any(obs, NethackLayeredPolicy._SEVERE_CONDITIONS):
            # Only a *complete* severe-free frame is reviewed evidence that the
            # prayer episode ended (cured or otherwise resolved).  A frame
            # whose status block is missing (a pager, a partial capture) also
            # shows no condition; treating that as improvement would re-arm the
            # budget and let the same status be prayed for again.
            self._pray_spent = False
        if obs.raw_text != self._answered_prompt:
            self._answered_prompt = None
            self._answered_prompt_resends = 0
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
        if self._inv_stage is not None and self._inv_raw_text is not None:
            # Same frame after an acked probe/eat key: transport ack is not
            # game acceptance. Bound the wait, then _resolve abandons the flow.
            if obs.raw_text == self._inv_raw_text:
                self._inv_unchanged = min(
                    self._inv_unchanged + 1, MAX_UNCHANGED_INVENTORY_CAPTURES)
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
            # Keep the prayer continuation (``_pray_step`` / ``_pray_expected``)
            # and ``_pray_spent``: an unrelated map change (a distant cell, a
            # rejected fresh capture) must not abandon a half-typed extended
            # command or re-arm the one-prayer budget.  The step is dropped
            # only when its expected prompt is gone (checked in ``_resolve``),
            # and the budget only by a complete severe-free frame.
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
        if decision.intent == "status_recovery":
            if len(decision.actions) != 1 or decision.actions[0].type != "text":
                raise RuntimeError("production action must be one literal text key")
            key = decision.actions[0].text
            if self._pray_expected is not None:
                if key != self._pray_expected:
                    raise RuntimeError("reviewed prayer continuation key is unexpected")
                return
            level = obs.vitals.dungeon_level
            if (
                key != SEVERE_RECOVERY_COMMAND
                or not turn_ready(obs)
                or level is None
                or level > MAX_PRAY_DUNGEON_LEVEL
                or not _has_any(obs, NethackLayeredPolicy._SEVERE_CONDITIONS)
            ):
                raise RuntimeError("production action violates its observed context")
            return
        assert_production_safe(
            decision,
            obs,
            attempted_doors=self._attempted_doors,
            pending_door=self._pending_door,
            opened_doors=getattr(explorer, "opened_doors", frozenset()),
            down_stairs=explorer.memory.down_stairs_at(obs.vitals.dungeon_level),
            pending_eat=self._food_letter,
            inventory_pending=self._inv_stage is not None,
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
            if decision.intent == "open_inventory":
                self._inv_stage = "open"
                self._inv_raw_text = obs.raw_text
                self._inv_unchanged = 0
                self._inv_turn = obs.vitals.turn
                self._meal_pending = False
                return
            if decision.intent == "close_inventory":
                if self._inv_stage not in {"open", "eat"}:
                    raise RuntimeError("inventory close sent outside a pending flow")
                if self._inv_stage == "open":
                    self._inv_stage = "close"
                else:
                    self._inv_stage = "abort_eat"
                self._inv_raw_text = obs.raw_text
                self._inv_unchanged = 0
                return
            if decision.intent == "eat_start":
                if self._inv_stage != "close" or self._food_letter is None:
                    raise RuntimeError("eat command sent without a probed safe letter")
                self._inv_stage = "eat"
                self._inv_raw_text = obs.raw_text
                self._inv_unchanged = 0
                return
            if decision.intent == "eat_select":
                if self._inv_stage != "eat" or self._food_letter is None:
                    raise RuntimeError("eat answer sent without a pending eat prompt")
                self._inv_stage = "eaten"
                self._inv_raw_text = obs.raw_text
                self._inv_unchanged = 0
                return
            key = decision.actions[0].text
            if decision.intent == "status_recovery":
                if key == SEVERE_RECOVERY_COMMAND and self._pray_expected is None:
                    # The extended command has started; the reviewed word and
                    # the paranoid_confirmation answer follow one key per
                    # frame.  Spend the single-per-episode prayer budget now.
                    self._pray_step = 0
                    self._pray_spent = True
                elif self._pray_expected is not None and key == self._pray_expected:
                    # Transport acked: consume the key we peeked in ``_resolve``.
                    self._pray_expected = None
                    if self._pray_step is not None:
                        self._pray_step += 1
                        if self._pray_step >= len(PRAY_RECOVERY_STEPS):
                            self._pray_step = None
                return
            if decision.intent in {"decline_save", "decline_attack", "advance_message"}:
                # Count only acknowledged re-sends of the same unchanged frame.
                if self._answered_prompt == obs.raw_text:
                    self._answered_prompt_resends += 1
                else:
                    self._answered_prompt_resends = 0
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

    @staticmethod
    def _special(intent: str, reason: str, key: str) -> PolicyDecision:
        return PolicyDecision("tactical", intent, reason, (Action(type="special", key=key),))

    def _inventory_probe_due(self, obs: NethackObservation) -> bool:
        if self._inv_turn is None or obs.vitals.turn is None:
            return self._inv_turn is None
        return obs.vitals.turn - self._inv_turn >= self._inv_block_turns

    def _cooldown_probe(self, obs: NethackObservation, turns: int) -> None:
        """Pause probing after an abandoned flow so a weird frame cannot
        re-open the menu every single capture."""
        if obs.vitals.turn is not None:
            self._inv_turn = obs.vitals.turn
        self._inv_block_turns = turns

    def _resolve_inventory(
        self, decision: PolicyDecision, obs: NethackObservation
    ) -> PolicyDecision | None:
        """Advance the bounded inventory/eat flow, or ``None`` once released.

        Every stage verifies the frame it expected; a changed or unexpected
        screen abandons the flow rather than guessing the next key. Sending
        updates the stage via ``sent`` -- resolve() only proposes the next key.
        """
        stage = self._inv_stage
        if obs.prompt == "more":
            # A pager (including the inventory menu's own --More--) is answered
            # by the existing reviewed answer, never by our pending key.
            return decision
        if obs.raw_text == self._inv_raw_text:
            if self._inv_unchanged >= MAX_UNCHANGED_INVENTORY_CAPTURES:
                self._cooldown_probe(obs, INVENTORY_REPROBE_TURNS)
                self._clear_inventory()
                return self._hold(
                    "inventory/eat flow produced no changed frame; abandoned")
            return self._hold("waiting for a fresh frame after the inventory key")
        if stage == "open":
            items = parse_visible_inventory(obs.raw_text)
            if items or _EMPTY_INVENTORY_RE.search(obs.raw_text):
                food = production_food_item(items)
                self._food_letter = food.letter if food else None
                self._inv_no_food = food is None
                self._inv_block_turns = INVENTORY_REPROBE_TURNS
                # The listing itself was the expected frame: close the menu.
                return self._special(
                    "close_inventory",
                    "close the inventory listing after probing", "Escape")
            # Frame changed without a listing (menu closed early, other
            # screen): abandon, never guess.
            self._cooldown_probe(obs, MAX_UNCHANGED_INVENTORY_CAPTURES * 2)
            self._clear_inventory()
            return None
        if stage == "close":
            # The frame changed after Escape. A probed safe letter may only be
            # spent on a complete gameplay frame; the stage itself advances
            # only in sent(), so a rejected plan replans this same key.
            if self._food_letter is None or not turn_ready(obs):
                self._cooldown_probe(obs, MAX_UNCHANGED_INVENTORY_CAPTURES * 2)
                self._clear_inventory()
                return None
            return self._action(
                "eat_start", "open the eat prompt for the probed safe food", "e")
        if stage == "eat":
            letters = _eat_prompt_letters(obs)
            if letters is not None:
                letter = self._food_letter
                if letter is None or letter not in letters:
                    # The prompt does not offer the probed item: cancel instead
                    # of answering with any letter.
                    return self._special(
                        "close_inventory",
                        "abort the eat prompt; probed letter not offered",
                        "Escape")
                return self._action(
                    "eat_select",
                    "answer the eat prompt with the probed safe food letter",
                    letter)
            self._cooldown_probe(obs, MAX_UNCHANGED_INVENTORY_CAPTURES * 2)
            self._clear_inventory()
            return None
        if stage == "abort_eat":
            # Any changed frame means the prompt closed; nothing was eaten.
            self._cooldown_probe(obs, MAX_UNCHANGED_INVENTORY_CAPTURES * 2)
            self._clear_inventory()
            return None
        if stage == "eaten":
            # The result frame is here; hunger improvement (or its absence) is
            # re-observed by the normal decision cycle, not guessed. The meal
            # is an occupation that movement would interrupt.
            self._inv_stage = None
            self._food_letter = None
            self._inv_no_food = False
            self._meal_pending = True
            self._inv_block_turns = EAT_REPROBE_TURNS
            if obs.vitals.turn is not None:
                self._inv_turn = obs.vitals.turn
            return None
        return None

    def _resolve(self, decision: PolicyDecision, obs: NethackObservation, explorer) -> PolicyDecision:
        # A reviewed prayer command spans several single-character inputs
        # ('#' then the word `pray` + Enter, then the paranoid_confirmation
        # answer).  While it is in progress, finish it before considering a new
        # policy decision so the game never receives a half-typed command.
        if self._pray_step is not None:
            if self._pray_step >= len(PRAY_RECOVERY_STEPS):
                self._pray_step = None
                self._pray_expected = None
            else:
                key, predicate = PRAY_RECOVERY_STEPS[self._pray_step]
                if predicate(obs):
                    # Peek only: the key is consumed once the send is acked
                    # (``sent``).  A rejected/discarded plan keeps it so the
                    # next replanning re-sends the same key instead of skipping.
                    self._pray_expected = key
                    return self._action("status_recovery", "continue the reviewed prayer recovery", key)
                # The prompt this key expected is gone or changed: abandon the
                # half-typed command and fail closed rather than inventing
                # further keystrokes.
                self._pray_step = None
                self._pray_expected = None
        decline = decline_prompt(obs)
        if decline == "decline_attack":
            decision = self._action(decline, "decline an observed attack confirmation", "n")
        if (
            decision.intent in {"decline_save", "decline_attack", "advance_message"}
            and self._answered_prompt == obs.raw_text
        ):
            # The game may drop one acked key without changing the frame.
            # Re-send the same reviewed answer a bounded number of times; the
            # stall guard remains the final backstop once the budget is spent.
            if self._answered_prompt_resends < MAX_ANSWERED_PROMPT_RESENDS:
                return decision
            return self._hold(
                f"prompt already answered; re-sent {self._answered_prompt_resends} times, "
                "waiting for a new frame"
            )
        if decision.intent == "status_recovery" and self._pray_spent:
            return self._hold("prayer recovery already spent for this severe episode")
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
        if self._inv_stage is not None:
            handled = self._resolve_inventory(decision, obs)
            if handled is not None:
                return handled
        # #2806: a Hungry tier first proves what the visible inventory holds.
        # Viewing is free and read-only; eating itself is gated by the pending
        # flow and the final-context check, never by a guessed letter.
        hungry = decision.intent in {"seek_food", "food_emergency"}
        if (hungry and self._food_letter is None and turn_ready(obs)
                and self._inventory_probe_due(obs)):
            return self._action(
                "open_inventory",
                "probe the visible inventory before deciding on food",
                "i",
            )
        if hungry and self._meal_pending and not self._inventory_probe_due(obs):
            # A meal is an occupation: movement would interrupt it and waste
            # the food. Spend explicit wait turns -- never the generic
            # contact/bump fallback -- until hunger visibly settles or the
            # re-probe window opens.
            return PolicyDecision(
                "midlevel", "food_recovery",
                "ate a probed item; waiting for the meal and hunger to settle",
                (Action(type="text", text="."),),
            )
        if hungry and self._inv_no_food and not self._inventory_probe_due(obs):
            # Probed, nothing safe: keep searching as an explicit goal instead
            # of silently wandering while hungry (fail-closed diagnosis).
            decision = PolicyDecision(
                "midlevel", "food_acquisition",
                "visible inventory holds no safe food; keep looking for food",
            )
        if decision.intent in {
            "explore_step",
            "exploration_blocked",
            "seek_food",
            "food_acquisition",
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
        # prompt_decision requires LLM context and carries no reviewed action;
        # do not invent a fallback rest/step keystroke (keeps brain in sync
        # with the base policy's direct call).
        if decision.intent == "prompt_decision":
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
