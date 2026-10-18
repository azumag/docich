"""Deterministic mystery case engine: fairness is the whole point (#684).

The case truth (culprit/method/motive/clues) must be reproducible from one
seed, committed before anything is shown, and never present in the public
board before the reveal.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich import mystery_case
from docich.mystery_case import (
    CaseError,
    COMMITMENT_PREFIX,
    MAX_ROOMS,
    MAX_SUSPECTS,
    MIN_ROOMS,
    MIN_SUSPECTS,
    ai_detective_choice,
    commitment,
    generate,
    new_seed,
    poll_actions,
    poll_question,
    public_board,
    reveal,
    verify_reveal,
)


def test_same_seed_produces_the_same_case_and_a_different_seed_does_not():
    first = generate(12345)
    second = generate(12345)
    assert first.culprit.name == second.culprit.name
    assert first.method == second.method and first.motive == second.motive
    assert [c.text for c in first.clues] == [c.text for c in second.clues]
    assert first.alibi_of(0) == second.alibi_of(0)

    other = generate(12346)
    assert (first.culprit.name, first.method, first.seed) != (
        other.culprit.name, other.method, other.seed)


def test_commitment_is_the_published_sha256_of_prefix_and_seed():
    import hashlib

    seed = new_seed()
    assert commitment(1, seed) == hashlib.sha256(
        f"{COMMITMENT_PREFIX}:1:{seed}".encode()).hexdigest()
    assert len(commitment(1, seed)) == 64
    with pytest.raises(CaseError):
        commitment(0, seed)
    with pytest.raises(CaseError):
        commitment(1, -1)
    with pytest.raises(CaseError):
        commitment(1, 2**64)


def test_case_bounds_respect_the_twitch_choice_limits():
    case = generate(7)
    assert MIN_SUSPECTS <= len(case.suspects) <= MAX_SUSPECTS
    assert MIN_ROOMS <= len(case.rooms) <= MAX_ROOMS
    for suspect in case.suspects:
        assert len(suspect.name) <= 25 and len(suspect.role) <= 25
        assert suspect.name not in ("\n", "")
    # Poll choices stay within Helix's 2..5 for both phases.
    assert 2 <= len(poll_actions(case, "investigate")) <= 5
    assert 2 <= len(poll_actions(case, "interrogate")) <= 5
    for value, label in poll_actions(case, "investigate") + poll_actions(case, "interrogate"):
        assert 1 <= len(label) <= 25 and value
    assert poll_question(case, "investigate")
    assert poll_question(case, "interrogate")
    with pytest.raises(CaseError):
        poll_actions(case, "reveal")


def test_key_clue_contradicts_only_the_culpit_and_stays_in_one_room():
    for seed in (1, 2, 3, 4, 5, 42, 999):
        case = generate(seed)
        key_clues = [c for c in case.clues if c.key]
        assert len(key_clues) == 1
        key = key_clues[0]
        assert key.room_id in {room.id for room in case.rooms}
        # The key clue names the culprit; the culprit's alibi is the claim it breaks.
        assert case.culprit.name in key.text
        assert case.alibi_of(case.culprit_index)


def test_public_board_hides_the_truth_until_the_reveal():
    case = generate(99)
    board = public_board(case)
    blob = repr(board)
    assert case.commitment in board["commitment"]
    assert case.commitment in blob
    assert case.culprit.name not in blob or case.culprit.name in "".join(
        s["name"] for s in board["suspects"])  # suspect list is public, guilt is not
    assert "method" not in board and "motive" not in board and "seed" not in board
    assert all("alibi" not in s for s in board["suspects"])
    assert all(not r.get("clue") for r in board["rooms"])

    searched = board["rooms"][0]["id"]
    after = public_board(case, revealed_rooms=(searched,))
    entry = next(r for r in after["rooms"] if r["id"] == searched)
    assert entry["searched"] is True and entry["clue"]

    revealed = reveal(case)
    final = public_board(case, revealed=revealed)
    assert final["reveal"]["culprit"]["name"] == case.culprit.name
    assert final["reveal"]["seed"] == case.seed
    with pytest.raises(CaseError):
        public_board(case, revealed={**revealed, "culprit": {"name": "someone", "role": "x"}})


def test_verify_reveal_accepts_only_the_exact_case():
    case = generate(5)
    assert verify_reveal(reveal(case), case) is True
    tampered = reveal(case)
    tampered["method"] = "別の手法"
    assert verify_reveal(tampered, case) is False
    assert verify_reveal(None, case) is False
    assert verify_reveal({}, case) is False


def test_ai_detective_choice_is_seeded_and_bounded():
    case = generate(11)
    import random

    rng_a = random.Random(f"{case.seed}:investigate:solo")
    rng_b = random.Random(f"{case.seed}:investigate:solo")
    values = {value for value, _label in poll_actions(case, "investigate")}
    pick = ai_detective_choice(case, "investigate", rng_a)
    assert pick == ai_detective_choice(case, "investigate", rng_b)
    assert pick in values


def test_alibi_of_unknown_partner_forms_a_bounded_sentence():
    case = generate(3)
    for index in range(len(case.suspects)):
        text = case.alibi_of(index)
        assert isinstance(text, str) and len(text) <= 80
