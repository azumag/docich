"""Evidence gate for a Soren round that has already ended.

docich may cancel a live ``draining`` early only when the game itself proves
the round is over.  That proof must come from Soren, never from a raw
``game_state.json`` value: ``state == "STOP"`` alone is **not** terminal in
Soren's own predicate (``lib/game_terminal.py`` at the deployed Soren
``68f02445``), because a fresh STOP can still precede the founding counter
increment.

The gate mirrors the broker's own boundary acceptance
(``lib/game_lifecycle.py``), which is the authority for a switch: a live
runner is what keeps ``not terminal or runner_alive`` waiting, so

- while the runner is alive, only the runner-owned founding-boundary witness
  (``lib/game_founding_boundary.py``) hands the game over: a fresh board
  observation (<= 3s), a full quiet window (>= 300s), a matching board
  projection, ``stale_founding_stop`` plus its ``founding_boundary_token`` and
  attested ``founding_boundary_board``;
- once the runner is gone, Soren's shared terminal predicate
  (``game_terminal.is_terminal``: ``GAMEOVER``, or a non-founding STOP with
  ``makeSorenCount == 0`` quiet for >= 30s) is enough.

Every field is compared with ``is True`` / exact types, so a truthy string,
number or dict is never accepted, and the adapter additionally requires the
snapshot to belong to the current request and to be fresh.  This module
performs no I/O; the Soren adapter and its regression tests share the gate.
"""
from __future__ import annotations

from collections.abc import Mapping


def _founding_witness(snapshot: Mapping) -> bool:
    """The runner-owned founding-boundary attestation, or False."""

    if snapshot.get("stale_founding_stop") is not True:
        return False
    # The founding STOP handover is only proven together with the identity
    # token and the attested board it was computed from.
    token = snapshot.get("founding_boundary_token")
    if not isinstance(token, str) or not token:
        return False
    board = snapshot.get("founding_boundary_board")
    if not isinstance(board, Mapping):
        return False
    return board.get("state") == "STOP"


def soren_round_terminal(snapshot: object) -> bool:
    """True only when a Soren snapshot proves no boundary can still arrive.

    ``snapshot`` is the object Soren's lifecycle broker builds from its own
    evidence (``_read_game_snapshot``); anything else -- a raw state mapping, a
    missing or malformed shape -- is false.
    """

    if not isinstance(snapshot, Mapping):
        return False
    if snapshot.get("runner_alive"):
        return _founding_witness(snapshot)
    return snapshot.get("terminal") is True
