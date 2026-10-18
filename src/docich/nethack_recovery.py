"""Reviewed recovery actions for severe NetHack status emergencies (#759).

This module is the single reviewed table for severe status recovery.  The
production surface intentionally stays minimal: one explicit prayer command.
Every other in-game remedy needs evidence the public TTY frame cannot provide,
so those remain outside the surface instead of being guessed.

Evidence base (NetHack 3.6.7, verified against the released sources):

- ``src/pray.c`` ``in_trouble()`` orders the major troubles ``Stoned`` (first),
  ``Slimed``, ``Strangled`` (also destroys a worn amulet of strangulation) and
  ``Sick``; ``fix_worst_trouble()``/``pleased()`` cure the worst one on a
  successful prayer and may cure further ones.
- ``src/botl.c`` displays the fatal set as `` Stone`` / `` Slime`` /
  `` Strngl`` / `` FoodPois`` / `` TermIll``.  (``Ill`` is not a 3.6.7 tty
  token; the observation vocabulary keeps it as a fail-closed alias.)
- Without a cure the conditions are fatal on a timer: food poisoning dies in
  10-19 turns (``src/eat.c``), illness in ``20..19+Con`` turns (``src/mhitu.c``
  ``diseasemu``), sliming ends the hero in about nine turns (green slime touch)
  and stoning/strangling run their own countdown dialogues (``src/timeout.c``).
  Waiting (``.``) therefore never resolves these states; it only spends the
  remaining turns.
- ``dopray()`` is one key (``p``), runs ``nomul(-3)`` and ends with
  ``You finish your prayer.``  Typed-ahead keys interrupt the freeze, so the
  resolver must not send gameplay keys while the prayer response is pending.
- ``prayer_done()`` in Gehennom answers ``<god> can't help you`` and rolls
  ``angrygods()``; hidden standing (``ublesscnt``/``Luck``/``ugangr``/
  alignment) is not observable.  The reviewed gate therefore prays only well
  above the Gehennom depth boundary and only once per severe episode.  The
  residual risk of one angry roll per episode is accepted against the certain
  death the untreated condition otherwise guarantees.

Item, spell and accessory cures (potions, unicorn horn, eucalyptus leaf, cure
sickness spell, polymorph, removing a strangulation amulet with ``R``) all
need a confirmed inventory observation or a selection prompt contract.  The
issue requires not guessing item keys without confirmed inventory, so none of
them are on this surface; they are separate reviewed contracts if ever added.
"""
from __future__ import annotations

from .actions import Action
from .nethack_observation import NethackObservation
from .nethack_policy import NethackLayeredPolicy, PolicyDecision, _has_any, turn_ready

RECOVERY_INTENT = "recovery_pray"

# dopray() consumes exactly one key and answers with its own messages; there
# is no prompt and no follow-up input for the reviewed prayer.
PRAY_KEY = "p"

# Prayer is offered only while the hero is provably outside Gehennom.  The
# main dungeon stays far above this bound, and unknown or deeper levels stay
# fail-closed because in Gehennom a prayer never cures and always risks the
# god's anger (pray.c prayer_done() Inhell branch).
PRAY_SAFE_MAX_DUNGEON_LEVEL = 20

# The observable fatal set (#759).  Mirrors NethackLayeredPolicy._SEVERE_
# CONDITIONS; kept as an explicit frozenset so the recovery contract states
# exactly which conditions this table covers.
SEVERE_RECOVERY_CONDITIONS = frozenset(NethackLayeredPolicy._SEVERE_CONDITIONS)


def pray_frame_ready(obs: NethackObservation) -> bool:
    """Frame-level evidence gate for the reviewed prayer recovery.

    Requires a complete gameplay frame (no prompt, unique player, positive
    HP, known dungeon level), a dungeon level that rules out Gehennom, and a
    visible severe status condition.  Deliberately independent of adjacent
    creatures: the issue forbids changing the treatment on adjacency alone,
    and the condition itself is lethal on a timer.
    """
    return (
        turn_ready(obs)
        and obs.vitals.dungeon_level is not None
        and obs.vitals.dungeon_level <= PRAY_SAFE_MAX_DUNGEON_LEVEL
        and _has_any(obs, SEVERE_RECOVERY_CONDITIONS)
    )


def pray_recovery_action(
    decision: PolicyDecision | None, obs: NethackObservation
) -> Action | None:
    """The one reviewed prayer action for a fail-closed status emergency.

    The policy decision itself stays action-free (``status_emergency``,
    ``requires_llm=True``); the resolver substitutes this action exactly the
    way it substitutes ``rest_turn``.  Returns ``None`` (fail-closed) for any
    other intent, any decision that already carries actions, or any frame
    that fails the evidence gate.
    """
    if decision is None or decision.intent != "status_emergency" or decision.actions:
        return None
    if not pray_frame_ready(obs):
        return None
    return Action(type="text", text=PRAY_KEY)


def assert_pray_safe(decision: PolicyDecision, obs: NethackObservation) -> None:
    """Production boundary guard: recovery_pray is exactly one ``p`` in context."""
    if (
        decision.intent != RECOVERY_INTENT
        or len(decision.actions) != 1
        or decision.actions[0].type != "text"
        or decision.actions[0].text != PRAY_KEY
        or not pray_frame_ready(obs)
    ):
        raise RuntimeError(
            "recovery pray attempted an action outside the reviewed safe surface"
        )
