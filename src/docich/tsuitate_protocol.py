"""Offline guards for Tsuitate PlayerView and outgoing move intents.

This module does not connect, authenticate, join a queue, resign, or operate a
corner. A future transport must bind callbacks to its connection generation.
Only an authoritative view may advance the board; a successful move ACK cannot.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any

COLORS = frozenset({"sente", "gote"})
HAND_ROLES = frozenset({"pawn", "lance", "knight", "silver", "gold", "bishop", "rook"})
PIECE_ROLES = HAND_ROLES | {"king", "tokin", "promotedlance", "promotedknight", "promotedsilver", "horse", "dragon"}
SQUARE = re.compile(r"[1-9][a-i]\Z")
MOVE = re.compile(r"(?:[1-9][a-i][1-9][a-i]\+?|[PLNSGBR]\*[1-9][a-i])\Z")
MAX_SAFE_INTEGER = 2**53 - 1


class ProtocolError(ValueError):
    """Fixed diagnostic code; never include raw server content in errors."""


def _integer(value: Any, code: str, *, minimum: int = 0, maximum: int = MAX_SAFE_INTEGER) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ProtocolError(code)
    return value


def _color(value: Any) -> str:
    if not isinstance(value, str) or value not in COLORS:
        raise ProtocolError("invalid_color")
    return value


def _clock_number(value: Any) -> int | float:
    if type(value) not in (int, float):
        raise ProtocolError("invalid_clocks")
    try:
        valid = math.isfinite(value) and 0 <= value <= MAX_SAFE_INTEGER
    except OverflowError:
        valid = False
    if not valid:
        raise ProtocolError("invalid_clocks")
    return value


def _game_id(value: Any) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= 128
            or not value.isprintable() or value != value.strip()):
        raise ProtocolError("invalid_game_id")
    return value


@dataclass(frozen=True)
class PlayerView:
    game_id: str
    color: str
    pieces: tuple[tuple[str, str], ...]
    hand: tuple[tuple[str, int], ...]
    turn: str
    move_number: int
    clocks: tuple[int | float, int | float, str | None, int | float]
    fouls: tuple[int, int]
    checks: tuple[bool, bool]
    status: str

    @classmethod
    def parse(cls, raw: Any) -> PlayerView:
        if not isinstance(raw, dict):
            raise ProtocolError("invalid_view")
        game_id, color, turn = _game_id(raw.get("gameId")), _color(raw.get("yourColor")), _color(raw.get("turn"))
        move_number = _integer(raw.get("moveNumber"), "invalid_move_number", minimum=1)
        status = raw.get("status")
        if status not in ("playing", "ended"):
            raise ProtocolError("invalid_status")
        pieces = raw.get("yourPieces")
        if not isinstance(pieces, list) or len(pieces) > 40:
            raise ProtocolError("invalid_pieces")
        parsed_pieces: list[tuple[str, str]] = []
        seen: set[str] = set()
        for piece in pieces:
            if not isinstance(piece, dict):
                raise ProtocolError("invalid_piece")
            square, role = piece.get("square"), piece.get("role")
            if (not isinstance(square, str) or not SQUARE.fullmatch(square)
                    or not isinstance(role, str) or role not in PIECE_ROLES or square in seen):
                raise ProtocolError("invalid_piece")
            seen.add(square)
            parsed_pieces.append((square, role))
        hand = raw.get("yourHand")
        if not isinstance(hand, dict) or any(key not in HAND_ROLES for key in hand):
            raise ProtocolError("invalid_hand")
        parsed_hand = tuple(sorted((key, _integer(value, "invalid_hand", maximum=40)) for key, value in hand.items()))
        if len(parsed_pieces) + sum(count for _, count in parsed_hand) > 40:
            raise ProtocolError("invalid_piece_total")
        clock = raw.get("clocks")
        if not isinstance(clock, dict):
            raise ProtocolError("invalid_clocks")
        running = clock.get("running")
        if (status == "playing" and running != turn) or (status == "ended" and running is not None):
            raise ProtocolError("invalid_running_clock")
        clocks = (
            _clock_number(clock.get("senteMs")),
            _clock_number(clock.get("goteMs")),
            running,
            _clock_number(clock.get("serverTime")),
        )
        fouls = raw.get("fouls")
        if not isinstance(fouls, dict):
            raise ProtocolError("invalid_fouls")
        foul_counts = (_integer(fouls.get("you"), "invalid_fouls", maximum=10),
                       _integer(fouls.get("opponent"), "invalid_fouls", maximum=10))
        checks = raw.get("youInCheck"), raw.get("opponentInCheck")
        if any(type(value) is not bool for value in checks):
            raise ProtocolError("invalid_checks")
        return cls(game_id, color, tuple(sorted(parsed_pieces)), parsed_hand, turn,
                   move_number, clocks, foul_counts, checks, status)

    @property
    def key(self) -> tuple[str, str, int]:
        return self.game_id, self.color, self.move_number

    def public_dict(self) -> dict[str, Any]:
        """New allow-listed projection, never the original payload or extras."""
        return {
            "gameId": self.game_id, "yourColor": self.color,
            "yourPieces": [{"square": s, "role": r} for s, r in self.pieces],
            "yourHand": dict(self.hand), "turn": self.turn, "moveNumber": self.move_number,
            "clocks": dict(zip(("senteMs", "goteMs", "running", "serverTime"), self.clocks)),
            "fouls": {"you": self.fouls[0], "opponent": self.fouls[1]},
            "youInCheck": self.checks[0], "opponentInCheck": self.checks[1], "status": self.status,
        }


@dataclass(frozen=True)
class MoveIntent:
    sequence: int
    generation: int
    position: tuple[str, str, int]
    usi: str

    def payload(self) -> dict[str, str]:
        return {"gameId": self.position[0], "usi": self.usi}


class MoveGate:
    """Single-flight local intent gate, not a server-side exactly-once claim.

    On timeout/disconnect an ambiguous intent stays blocked until an advanced
    view or a higher foul count proves an outcome. The live transport/recovery
    policy is intentionally not implemented here; no move is blindly resent.
    """

    def __init__(self) -> None:
        self.generation = 0
        self.connected = False
        self.needs_sync = True
        self.quiescing = False
        self.view: PlayerView | None = None
        self.pending: MoveIntent | None = None
        self._sequence = 0
        self._acked_sequence = 0
        self._attempted: set[str] = set()
        self._foul_floor = 0

    def new_connection(self) -> int:
        self.generation += 1
        self.connected = True
        self.needs_sync = True
        return self.generation

    def disconnect(self, generation: int) -> None:
        if generation == self.generation:
            self.connected = False
            self.needs_sync = True

    def accept_view(self, raw: Any, generation: int, *, synchronized: bool = False) -> bool:
        if generation != self.generation or not self.connected:
            return False
        try:
            return self._accept_current_view(raw, synchronized=synchronized)
        except ProtocolError:
            # A corrupt current observation must not leave the previous board
            # actionable. Preserve evidence, but require authoritative resync.
            self.needs_sync = True
            raise

    def _accept_current_view(self, raw: Any, *, synchronized: bool) -> bool:
        if raw is None:
            if not synchronized or (self.view and self.view.status == "playing") or self.pending:
                self.needs_sync = True
                return False
            self.needs_sync = False
            return True
        view = PlayerView.parse(raw)
        old = self.view
        same_game = old is not None and old.game_id == view.game_id
        if old and not same_game and old.status == "playing":
            raise ProtocolError("unexpected_game")
        if same_game:
            if view.color != old.color:
                raise ProtocolError("changed_color")
            if view.move_number < old.move_number or any(a < b for a, b in zip(view.fouls, old.fouls)):
                return False
            if old.status == "ended" and view.status != "ended":
                return False
            if (view.status == "playing" and view.move_number == old.move_number
                    and (view.pieces, view.hand, view.turn) != (old.pieces, old.hand, old.turn)):
                raise ProtocolError("conflicting_position")
        advanced = old is None or not same_game or view.move_number > old.move_number
        foul_proved = same_game and view.fouls[0] > old.fouls[0]
        if self.needs_sync and not synchronized and not (same_game and advanced):
            return False
        if same_game and view.fouls[0] < self._foul_floor:
            return False
        if advanced:
            self.pending = None
            self._attempted.clear()
            self._foul_floor = view.fouls[0]
        elif foul_proved or view.status == "ended":
            self.pending = None
        self.view = view
        self.needs_sync = False
        return True

    def prepare_move(self, usi: str) -> MoveIntent:
        view = self.view
        if (not self.connected or self.needs_sync or view is None or view.status != "playing"
                or view.turn != view.color or self.pending is not None or view.fouls[0] >= 10):
            raise ProtocolError("move_not_ready")
        if not isinstance(usi, str) or not MOVE.fullmatch(usi):
            raise ProtocolError("invalid_usi")
        if "*" not in usi and usi[:2] == usi[2:4]:
            raise ProtocolError("invalid_usi")
        if usi in self._attempted:
            raise ProtocolError("already_attempted")
        self._sequence += 1
        intent = MoveIntent(self._sequence, self.generation, view.key, usi)
        self._attempted.add(usi)
        self.pending = intent
        return intent

    def acknowledge(self, intent: MoveIntent, raw: Any) -> bool:
        if (not self.connected or intent.generation != self.generation
                or intent != self.pending or intent.sequence == self._acked_sequence):
            return False
        if not isinstance(raw, dict) or type(raw.get("ok")) is not bool:
            raise ProtocolError("invalid_ack")
        if not raw["ok"]:
            if raw.get("reason") == "foul":
                count = _integer(raw.get("foulCount"), "invalid_ack", minimum=1, maximum=10)
                if self.view is None or count <= self.view.fouls[0]:
                    raise ProtocolError("invalid_ack")
                self._foul_floor = count
            elif raw.get("reason") != "error":
                raise ProtocolError("invalid_ack")
            self.pending = None
        self._acked_sequence = intent.sequence
        self.needs_sync = True
        return True

    def timeout(self, intent: MoveIntent) -> None:
        if intent == self.pending:
            self.needs_sync = True

    def request_quiesce(self) -> None:
        # Quiesce stops *future* matchmaking, never the active game's inputs.
        self.quiescing = True

    @property
    def can_join_queue(self) -> bool:
        return (self.connected and not self.needs_sync and not self.quiescing
                and self.pending is None and (self.view is None or self.view.status == "ended"))
