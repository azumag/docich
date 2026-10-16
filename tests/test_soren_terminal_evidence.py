"""Evidence tests for the Soren round-terminal gate.

The owner's rule is that ``state == "STOP"`` alone must never justify
cancelling a live drain.  These tests pin exactly which Soren-issued evidence
does, mirroring the broker's own boundary acceptance
(``lib/game_lifecycle.py``): while the runner is alive only the runner-owned
founding witness hands the game over, and Soren's terminal predicate counts
once the runner is gone.  Truthy-but-wrong types are rejected everywhere.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.adapters.soren_terminal import soren_round_terminal  # noqa: E402

TOKEN = "3f2f0d0f7c8b4c1e9a1d2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f70"


def _snapshot(**overrides):
    snapshot = {
        "state": "STOP",
        "terminal": False,
        "stale_founding_stop": False,
        "founding_boundary_token": None,
        "founding_boundary_board": None,
        "founding_seen": True,
        "make_soren_count": 0,
        "score": 1976,
        "pieces": 41,
        "game_count": 5711,
        "runner_pid": 3605841,
        "runner_game": 5712,
        "runner_alive": True,
    }
    snapshot.update(overrides)
    return snapshot


def _witness(**overrides):
    base = {
        "stale_founding_stop": True,
        "founding_boundary_token": TOKEN,
        "founding_boundary_board": {
            "state": "STOP",
            "score": 1976,
            "makeSorenCount": 3,
            "pieces": 41,
        },
    }
    base.update(overrides)
    return _snapshot(**base)


def test_raw_stop_state_is_never_a_proof():
    """The owner's explicit rule: STOP alone must not unlock the drain."""

    assert soren_round_terminal({"state": "STOP"}) is False
    assert soren_round_terminal({"state": "STOP", "score": 1976}) is False
    # The incident state: founding marker present, makeSorenCount 0, runner up.
    assert soren_round_terminal(_snapshot()) is False
    assert soren_round_terminal(_snapshot(runner_alive=False)) is False


def test_terminal_only_counts_once_the_runner_is_gone():
    """A live runner keeps the broker waiting, so the gate must too."""

    assert soren_round_terminal(_snapshot(terminal=True, runner_alive=True)) is False
    assert soren_round_terminal(_snapshot(terminal=True, runner_alive=False)) is True
    assert soren_round_terminal(
        _snapshot(state="GAMEOVER", terminal=True, runner_alive=False)
    ) is True


def test_founding_witness_hands_over_while_the_runner_lives():
    assert soren_round_terminal(_witness()) is True
    assert soren_round_terminal(_witness(terminal=True)) is True


def test_founding_witness_is_not_a_proof_without_the_live_runner():
    """The broker only takes the witness together with a live runner."""

    assert soren_round_terminal(_witness(runner_alive=False)) is False


@pytest.mark.parametrize("value", ["true", "True", 1, 0, {"terminal": True}, [], None])
def test_terminal_must_be_a_strict_bool(value):
    assert soren_round_terminal(_snapshot(terminal=value, runner_alive=False)) is False


@pytest.mark.parametrize("value", ["true", 1, {"ok": True}, [], None])
def test_stale_founding_stop_must_be_a_strict_bool(value):
    assert soren_round_terminal(_witness(stale_founding_stop=value)) is False


@pytest.mark.parametrize("token", [None, "", 42, True, {"token": TOKEN}, [TOKEN]])
def test_founding_witness_needs_a_non_empty_string_token(token):
    assert soren_round_terminal(_witness(founding_boundary_token=token)) is False


@pytest.mark.parametrize("board", [None, "STOP", 1, [], {}])
def test_founding_witness_needs_the_attested_board_projection(board):
    assert soren_round_terminal(_witness(founding_boundary_board=board)) is False


@pytest.mark.parametrize("state", ["playing", "GAMEOVER", "UNKNOWN", "", None])
def test_founding_witness_board_must_be_stop(state):
    board = {"state": state, "score": 1976, "makeSorenCount": 3, "pieces": 41}
    assert soren_round_terminal(_witness(founding_boundary_board=board)) is False


@pytest.mark.parametrize("snapshot", [None, "snapshot", 1, [], ["terminal"]])
def test_non_mapping_snapshots_are_rejected(snapshot):
    assert soren_round_terminal(snapshot) is False


def test_missing_fields_are_rejected():
    assert soren_round_terminal({}) is False
    assert soren_round_terminal({"terminal": False}) is False
    assert soren_round_terminal({"stale_founding_stop": True}) is False
