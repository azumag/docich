"""Evidence gate for a Soren round that has already ended.

docich may cancel a live ``draining`` early only when the game itself proves
the round is over.  That proof must come from Soren, never from a raw
``game_state.json`` value: ``state == "STOP"`` alone is **not** terminal in
Soren's own predicate (``lib/game_terminal.py`` at the deployed Soren
``68f02445``), because a fresh STOP can still precede the founding counter
increment.  Exactly two Soren-issued proofs are accepted.

``terminal``
    Soren's shared terminal predicate (``game_terminal.is_terminal``):
    ``GAMEOVER``, or a STOP that is non-founding (no ``tmp/markers/.soviet_created``
    marker and ``makeSorenCount == 0``) and has been quiet for >= 30s
    (``STOP_QUIET_SECONDS``).

``stale_founding_stop``
    The runner-owned founding-boundary witness (``lib/game_founding_boundary.py``)
    for a founding game that ended in STOP: a live runner process, a fresh
    board observation (<= 3s), a full quiet window (>= 300s), and a board
    projection that matches both the observation and the current
    ``game_state.json``.  ``founding_boundary_token`` binds that identity and
    ``founding_boundary_board`` is the attested projection.

Every field is compared with ``is True`` and exact types, so a truthy string,
number or dict is never accepted.  This module performs no I/O; the Soren
adapter and its regression tests share the same gate.
"""
from __future__ import annotations

from collections.abc import Mapping


def soren_round_terminal(snapshot: object) -> bool:
    """True only for a Soren snapshot that proves the round already ended.

    ``snapshot`` is the ``snapshot`` object Soren's lifecycle broker builds
    from its own evidence (``_read_game_snapshot``).  Anything else -- a raw
    state mapping, a missing or malformed shape -- is false.
    """

    if not isinstance(snapshot, Mapping):
        return False
    if snapshot.get("terminal") is True:
        # GAMEOVER, or a quiet non-founding STOP: Soren's own predicate.
        return True
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
