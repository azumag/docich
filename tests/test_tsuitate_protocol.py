"""Hermetic protocol tests: no socket, credential, queue, or live opponent."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from docich.tsuitate_protocol import MoveGate, PlayerView, ProtocolError


def view(**changes):
    value = {
        "gameId": "game-1", "yourColor": "sente",
        "yourPieces": [{"square": "5i", "role": "king"}, {"square": "7g", "role": "pawn"}],
        "yourHand": {"pawn": 1}, "turn": "sente", "moveNumber": 1,
        "clocks": {"senteMs": 300000, "goteMs": 300000, "running": "sente", "serverTime": 1000},
        "fouls": {"you": 0, "opponent": 0}, "youInCheck": False,
        "opponentInCheck": False, "status": "playing",
    }
    value.update(changes)
    return value


def ready():
    gate = MoveGate()
    generation = gate.new_connection()
    assert gate.accept_view(view(), generation, synchronized=True)
    return gate, generation


def advanced(number=2, turn="gote"):
    value = view(moveNumber=number, turn=turn)
    value["clocks"]["running"] = turn
    value["yourPieces"][1]["square"] = "7f"
    return value


def test_view_is_detached_and_public_projection_drops_unknown_secrets():
    raw = view()
    raw.update(token="tsb_do_not_publish", opponent={"username": "hidden"}, opponentPieces=["hidden"])
    parsed = PlayerView.parse(raw)
    raw["yourPieces"][0]["square"] = "1a"
    raw["yourHand"]["pawn"] = 20
    public = parsed.public_dict()
    assert public["yourHand"] == {"pawn": 1}
    assert {p["square"] for p in public["yourPieces"]} == {"5i", "7g"}
    assert "hidden" not in json.dumps(public) and "tsb_" not in json.dumps(public)
    public["yourHand"]["pawn"] = 30
    assert parsed.public_dict()["yourHand"] == {"pawn": 1}


@pytest.mark.parametrize("field,value", [
    ("gameId", ""), ("gameId", "bad\nvalue"), ("yourColor", "other"),
    ("turn", True), ("moveNumber", True), ("moveNumber", "1"), ("moveNumber", 0),
    ("youInCheck", 1), ("opponentInCheck", None), ("status", "unknown"),
    ("yourHand", {"king": 1}), ("yourHand", {"pawn": True}),
    ("yourPieces", [{"square": "0a", "role": "pawn"}]),
    ("yourPieces", [{"square": "5i", "role": "secret"}]),
    ("yourPieces", [{"square": "5i", "role": "king"}, {"square": "5i", "role": "pawn"}]),
    ("fouls", {"you": 11, "opponent": 0}),
])
def test_malformed_view_is_rejected_without_echoing_payload(field, value):
    with pytest.raises(ProtocolError) as error:
        PlayerView.parse(view(**{field: value}))
    assert str(error.value).startswith("invalid_")


@pytest.mark.parametrize("bad", [True, None, "3", -1, float("nan"), float("inf"), 10**400])
def test_invalid_clock_cannot_become_a_playable_view(bad):
    raw = view()
    raw["clocks"]["senteMs"] = bad
    with pytest.raises(ProtocolError, match="invalid_clocks"):
        PlayerView.parse(raw)


def test_clock_fraction_and_zero_are_valid_numbers():
    raw = view()
    raw["clocks"]["senteMs"] = 0
    raw["clocks"]["goteMs"] = 1000.5
    assert PlayerView.parse(raw).clocks[:2] == (0, 1000.5)


def test_only_fresh_own_turn_can_create_one_pending_intent():
    gate = MoveGate()
    with pytest.raises(ProtocolError, match="move_not_ready"):
        gate.prepare_move("7g7f")
    generation = gate.new_connection()
    assert not gate.accept_view(view(), generation)
    assert gate.accept_view(view(), generation, synchronized=True)
    intent = gate.prepare_move("7g7f")
    assert intent.payload() == {"gameId": "game-1", "usi": "7g7f"}
    assert gate.accept_view(view(), generation)  # duplicate state is harmless
    with pytest.raises(ProtocolError, match="move_not_ready"):
        gate.prepare_move("P*5e")
    assert gate.acknowledge(intent, {"ok": True})
    assert not gate.acknowledge(intent, {"ok": True})
    assert gate.view.move_number == 1  # ACK is not a reconstructed board
    assert gate.accept_view(advanced(), generation)
    assert gate.pending is None
    with pytest.raises(ProtocolError, match="move_not_ready"):
        gate.prepare_move("P*5e")


def test_state_before_ack_ignores_late_success_callback():
    gate, gen = ready()
    intent = gate.prepare_move("7g7f")
    assert gate.accept_view(advanced(), gen)
    assert not gate.acknowledge(intent, {"ok": True})
    assert gate.view.move_number == 2


def test_foul_ack_requires_fresh_count_and_never_repeats_the_move():
    gate, gen = ready()
    intent = gate.prepare_move("7g7f")
    assert gate.acknowledge(intent, {"ok": False, "reason": "foul", "foulCount": 1})
    assert not gate.accept_view(view(), gen, synchronized=True)
    assert gate.accept_view(view(fouls={"you": 1, "opponent": 0}), gen, synchronized=True)
    with pytest.raises(ProtocolError, match="already_attempted"):
        gate.prepare_move("7g7f")
    assert gate.prepare_move("P*5e").usi == "P*5e"


def test_foul_view_before_ack_does_not_reopen_a_new_pending_move():
    gate, gen = ready()
    first = gate.prepare_move("7g7f")
    assert gate.accept_view(view(fouls={"you": 1, "opponent": 0}), gen)
    second = gate.prepare_move("P*5e")
    assert not gate.acknowledge(first, {"ok": False, "reason": "foul", "foulCount": 1})
    assert gate.pending == second


def test_reconnect_and_timeout_do_not_guess_or_resend_ambiguous_moves():
    gate, gen = ready()
    intent = gate.prepare_move("7g7f")
    gate.timeout(intent)
    assert gate.accept_view(view(), gen, synchronized=True)
    assert gate.pending == intent
    gate.disconnect(gen)
    new_gen = gate.new_connection()
    assert not gate.accept_view(advanced(), gen, synchronized=True)
    assert not gate.acknowledge(intent, {"ok": True})
    assert gate.accept_view(view(), new_gen, synchronized=True)
    with pytest.raises(ProtocolError, match="move_not_ready"):
        gate.prepare_move("P*5e")
    assert gate.accept_view(advanced(), new_gen, synchronized=True)
    assert gate.pending is None


def test_stale_or_conflicting_views_do_not_replace_current_state():
    gate, gen = ready()
    gate.accept_view(advanced(), gen)
    before = gate.view
    assert not gate.accept_view(view(), gen, synchronized=True)
    bad = advanced()
    bad["yourPieces"][1]["square"] = "7e"
    with pytest.raises(ProtocolError, match="conflicting_position"):
        gate.accept_view(bad, gen, synchronized=True)
    with pytest.raises(ProtocolError, match="unexpected_game"):
        gate.accept_view(view(gameId="another"), gen, synchronized=True)
    assert gate.view == before


@pytest.mark.parametrize("raw", [{"ok": 1}, {"ok": False, "reason": "other"}, {"ok": False, "reason": "foul", "foulCount": True}])
def test_invalid_ack_preserves_pending(raw):
    gate, gen = ready()
    intent = gate.prepare_move("7g7f")
    with pytest.raises(ProtocolError, match="invalid_ack"):
        gate.acknowledge(intent, raw)
    assert gate.pending == intent


def test_quiesce_blocks_future_queue_but_not_active_gameplay():
    gate, gen = ready()
    gate.request_quiesce()
    assert not gate.can_join_queue
    intent = gate.prepare_move("7g7f")
    end = advanced()
    end["status"] = "ended"
    end["clocks"]["running"] = None
    assert gate.accept_view(end, gen)
    assert not gate.can_join_queue
    assert not gate.acknowledge(intent, {"ok": True})
    assert not gate.accept_view(advanced(3, "sente"), gen, synchronized=True)
    with pytest.raises(ProtocolError, match="move_not_ready"):
        gate.prepare_move("P*5e")


def test_missing_active_game_is_not_end_evidence_and_tenth_foul_blocks():
    gate, gen = ready()
    assert not gate.accept_view(None, gen, synchronized=True)
    assert gate.accept_view(view(fouls={"you": 10, "opponent": 0}), gen, synchronized=True)
    with pytest.raises(ProtocolError, match="move_not_ready"):
        gate.prepare_move("7g7f")


@pytest.mark.parametrize("bad", [view(moveNumber=True), view(gameId="other"), None])
def test_invalid_current_observation_blocks_previous_good_board_until_sync(bad):
    gate, gen = ready()
    previous = gate.view
    if bad is None:
        assert not gate.accept_view(bad, gen, synchronized=True)
    else:
        with pytest.raises(ProtocolError):
            gate.accept_view(bad, gen)
    assert gate.view == previous
    assert gate.needs_sync
    with pytest.raises(ProtocolError, match="move_not_ready"):
        gate.prepare_move("7g7f")
    assert not gate.accept_view(view(), gen)
    assert gate.accept_view(view(), gen, synchronized=True)
    assert gate.prepare_move("7g7f").position == previous.key


def test_malformed_stale_connection_cannot_poison_current_board():
    gate, gen = ready()
    assert not gate.accept_view({"invalid": True}, gen - 1)
    assert not gate.needs_sync
    assert gate.prepare_move("7g7f")


@pytest.mark.parametrize("usi", ["", "7g7g", "0a1b", "P*0a", "7g7f++", "7g7f\n", "K*5e", None])
def test_usi_is_strict_syntax_not_arbitrary_payload(usi):
    gate, _ = ready()
    with pytest.raises(ProtocolError, match="invalid_usi"):
        gate.prepare_move(usi)
    assert gate.pending is None
