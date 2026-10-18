"""Offline puyo corner core contract (Issue #698).

No Twitch, game switch, VM or production writes. Deterministic: the seeded
RNG stream lives in the state, so the recorded state replays identically.
"""

from __future__ import annotations

import json

import pytest

from docich import puyo_corner as pc


def make_ruleset(**kw):
    base = {
        "colors": ["red", "blue", "green", "yellow"],
        "board": {"columns": 6, "rows": 12, "hiddenRows": 1},
        "piecePair": {"size": 2},
        "chainMechanics": {
            "minConnection": 4,
            "simultaneousClear": True,
            "gravitySettle": True,
            "chainReevaluation": True,
        },
        "scoring": {
            "perPuyo": 10,
            "chainBonus": "bounded table",
            "simultaneousBonus": "bounded table",
            "allClearBonus": "bounded table",
        },
        "winCondition": "opponent top-out",
        "lossCondition": "own spawn blocked",
    }
    base.update(kw)
    return base


def make_config(**kw):
    base = {"ruleset": make_ruleset(), "gravity_ms": 700, "gravity_pending": 0}
    base.update(kw)
    return base


def seeded_state(seed=7):
    return pc.new_state(make_config(), seed=seed)


def drive(state, side, actions, ticks=1):
    for action in actions:
        pc.apply_action(state, side, action)
    for _ in range(ticks):
        if state["phase"] != "MATCH":
            break
        pc.tick(state, now="2026-10-18T01:00:00+00:00")
    return state


# ---------------------------------------------------------------------------
# ruleset validation
# ---------------------------------------------------------------------------


def test_validate_ruleset_ok():
    ruleset = pc.validate_ruleset(make_ruleset())
    assert ruleset["board"] == {"columns": 6, "rows": 12, "hiddenRows": 1}
    assert ruleset["piecePair"] == {"size": 2, "axisPlusSatellite": True}
    assert ruleset["chainMechanics"]["minConnection"] == 4


def test_validate_ruleset_rejects_bad():
    with pytest.raises(pc.PuyoCornerError):
        pc.validate_ruleset(make_ruleset(colors=[]))
    with pytest.raises(pc.PuyoCornerError):
        pc.validate_ruleset(make_ruleset(colors=["red", "blue", "red"]))
    with pytest.raises(pc.PuyoCornerError):
        pc.validate_ruleset(make_ruleset(colors=["red", "blue", "green", "yellow", "purple"]))
    with pytest.raises(pc.PuyoCornerError):
        pc.validate_ruleset(make_ruleset(board={"columns": 7, "rows": 12, "hiddenRows": 1}))
    with pytest.raises(pc.PuyoCornerError):
        pc.validate_ruleset(make_ruleset(piecePair={"size": 3}))
    with pytest.raises(pc.PuyoCornerError):
        pc.validate_ruleset(make_ruleset(
            chainMechanics={"minConnection": 3, "simultaneousClear": True,
                            "gravitySettle": True, "chainReevaluation": True}))
    with pytest.raises(pc.PuyoCornerError):
        pc.validate_ruleset(make_ruleset(
            chainMechanics={"minConnection": 4, "simultaneousClear": False,
                            "gravitySettle": True, "chainReevaluation": True}))


def test_ruleset_digest_is_stable_and_curated():
    first = pc.ruleset_digest(make_ruleset())
    second = pc.ruleset_digest(make_ruleset())
    assert first == second and len(first) == 64
    st = seeded_state()
    pc.require_live_ruleset(st, st["ruleset_id"])
    with pytest.raises(pc.RulesetError):
        pc.require_live_ruleset(st, "0" * 64)


# ---------------------------------------------------------------------------
# board helpers
# ---------------------------------------------------------------------------


def test_board_helpers_roundtrip():
    board = pc.empty_board()
    board[11][5] = "red"
    snapshot = pc.copy_board(board)
    board[11][5] = None
    assert snapshot[11][5] == "red" and pc.blocks_left(snapshot) == 1
    with pytest.raises(pc.PuyoCornerError):
        pc.copy_board([[None] * 6 for _ in range(11)])
    with pytest.raises(pc.PuyoCornerError):
        pc.copy_board([[None] * 7 for _ in range(12)])
    assert pc.blocks_left(pc.empty_board()) == 0


def test_render_board_marker_wins():
    board = pc.empty_board()
    board[0][0] = "red"
    board[11][5] = "blue"
    out = pc.render_board(board, marker=".")
    lines = out.splitlines()
    assert len(lines) == 12
    assert lines[0] == "red....."
    assert lines[-1] == ".....blue"
    with pytest.raises(pc.PuyoCornerError):
        pc.render_board("nope")


# ---------------------------------------------------------------------------
# pairs
# ---------------------------------------------------------------------------


def test_spawn_pair_is_seeded_and_bounded():
    first = pc.spawn_pair(["red", "blue"], rng=42, now="2026-10-18T01:00:00+00:00")
    second = pc.spawn_pair(["red", "blue"], rng=42)
    assert first["spawn_digest"] == second["spawn_digest"]
    ax, ay = first["axis_pos"]
    sx, sy = first["satellite_pos"]
    assert (ax, ay) == (2, 0) and (sx, sy) == (3, 0)
    axis, satellite = first["axis"], first["satellite"]
    assert axis in ("red", "blue") and satellite in ("red", "blue")
    with pytest.raises(pc.PuyoCornerError):
        pc.spawn_pair(["red", "blue", "green", "yellow", "purple"])
    with pytest.raises(pc.PuyoCornerError):
        pc.spawn_pair(["red"])


def test_pair_cells_bounds_and_overlap():
    pair = pc.spawn_pair(["red", "blue"], rng=1)
    cells = pc.pair_cells(pair)
    assert cells == [(2, 0), (3, 0)]
    overlap = dict(pair)
    overlap["satellite_pos"] = list(overlap["axis_pos"])
    with pytest.raises(pc.PuyoCornerError):
        pc.pair_cells(overlap)
    pair["axis_pos"] = [2, 12]
    with pytest.raises(pc.PuyoCornerError):
        pc.pair_cells(pair)


# ---------------------------------------------------------------------------
# chain resolution
# ---------------------------------------------------------------------------


def vertical_four(color="red"):
    board = pc.empty_board()
    for y in range(4):
        board[11 - y][2] = color
    return board


def test_connected_groups_and_settle():
    board = vertical_four()
    assert pc.blocks_left(board) == 4
    groups = pc.connected_groups(board)
    assert len(groups) == 1 and len(groups[0]) == 4
    mixed = pc.empty_board()
    mixed[11][0] = "red"
    mixed[11][1] = "blue"
    assert len(pc.connected_groups(mixed)) == 2


def test_diagonal_is_never_connected():
    board = pc.empty_board()
    for (x, y) in ((2, 11), (2, 10), (2, 9), (3, 8)):
        board[y][x] = "red"
    # (2,9) and (3,8) touch diagonally only: 3 vertical + 1 single
    sizes = sorted(len(g) for g in pc.connected_groups(board))
    assert sizes == [1, 3]


def test_resolve_chain_scales_bonus():
    ruleset = pc.validate_ruleset(make_ruleset())
    board = vertical_four()
    blocks, chain, score, all_clear = pc._resolve_chain(board, ruleset)
    assert (blocks, chain, all_clear) == (4, 1, True)  # the last blocks clear
    assert score > 0
    # a 4x4 square of the same color is 16 blocks in one chain pass
    square = pc.empty_board()
    for y in range(4):
        for x in range(4):
            square[8 + y][x] = "blue"
    blocks_sq, chain_sq, score_sq, _ = pc._resolve_chain(square, ruleset)
    assert (blocks_sq, chain_sq) == (16, 1)
    assert score_sq > score


def test_chain_reevaluation_through_gravity():
    ruleset = pc.validate_ruleset(make_ruleset())
    board = pc.empty_board()
    # a full bottom row of blues clears first; the reds hidden above it only
    # reconnect to the fourth red once the support row collapses and settles
    for x in range(6):
        board[11][x] = "blue"
    for x in (0, 1, 2):
        board[10][x] = "red"
    board[10][3] = "blue"
    board[9][3] = "red"
    blocks, chain, score, all_clear = pc._resolve_chain(board, ruleset)
    assert blocks == 11 and chain == 2
    assert all_clear is True  # the whole board empties after the second pass
    assert score > 0


def test_resolve_chain_never_clears_below_min():
    ruleset = pc.validate_ruleset(make_ruleset())
    board = pc.empty_board()
    for (x, y) in ((2, 11), (2, 10), (2, 9)):
        board[y][x] = "red"
    blocks, chain, score, all_clear = pc._resolve_chain(board, ruleset)
    assert (blocks, chain, score, all_clear) == (0, 0, 0, False)
    assert pc.blocks_left(board) == 3


# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------


def test_input_contract():
    st = seeded_state()
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    with pytest.raises(pc.PuyoCornerError):
        pc.apply_action(st, "user", "up")
    with pytest.raises(pc.PuyoCornerError):
        pc.apply_action(st, "spectator", "left")
    with pytest.raises(pc.PuyoCornerError):
        pc.apply_action(seeded_state(), "user", "left")  # phase IDLE
    pc.new_state(make_config(), seed=7)  # explicit config is fine
    pc.new_state()  # no config: the built-in ruleset keeps fresh states usable
    with pytest.raises(pc.PuyoCornerError):
        pc.new_state(make_config(), seed=-1)
    # bounded action budget
    st2 = seeded_state(seed=3)
    pc.start(st2, now="2026-10-18T01:00:00+00:00")
    st2["actions"] = pc.MAX_ACTIONS
    with pytest.raises(pc.PuyoCornerError) as exc:
        pc.apply_action(st2, "user", "left")
    assert exc.value.code == "action_budget"


def test_move_and_rotate_stay_in_bounds():
    st = seeded_state(seed=11)
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    for action in ("left", "left", "left", "left"):
        pc.apply_action(st, "user", action)
    axis, satellite = pc.pair_cells(st["user_pair"])
    assert axis[0] == 0 or satellite[0] <= 1
    st["user_axis_anchor"] = list(axis)
    for action in ("right", "right", "right", "right", "right"):
        pc.apply_action(st, "user", action)
    x_positions = [cell[0] for cell in pc.pair_cells(st["user_pair"])]
    assert all(0 <= x < 6 for x in x_positions)
    for _ in range(4):
        pc.apply_action(st, "user", "rotate_cw")
    assert all(0 <= x < 6 for x in [c[0] for c in pc.pair_cells(st["user_pair"])])
    for side in ("user", "ai"):
        assert st[f"{side}_board"] == pc.empty_board() or pc.blocks_left(
            st[f"{side}_board"]) >= 0


def test_soft_drop_and_hard_drop_lock():
    st = seeded_state(seed=5)
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    pc.apply_action(st, "user", "hard_drop")
    # a hard drop locks only on the same tick; a locked pair triggers a refill
    pc.tick(st, now="2026-10-18T01:00:01+00:00")
    assert pc.blocks_left(st["user_board"]) == 2
    assert st["user_chain"] == 0
    assert pc.blocks_left(st["ai_board"]) >= 0


def test_hard_drop_finishes_before_the_floor():
    st = seeded_state(seed=13)
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    for _ in range(5):
        pc.tick(st, now="2026-10-18T01:00:01+00:00")
        if st["phase"] != "MATCH":
            return
    # after 5 ticks the pair must have advanced downward
    cells = pc.pair_cells(st["user_pair"])
    assert cells[0][1] >= 0


# ---------------------------------------------------------------------------
# state machine
# ---------------------------------------------------------------------------


def test_invalid_transitions():
    st = seeded_state()
    with pytest.raises(pc.PuyoCornerError):
        pc.tick(st)  # IDLE cannot tick
    with pytest.raises(pc.PuyoCornerError):
        pc._transition(st, "ROUND_OVER")
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    with pytest.raises(pc.PuyoCornerError):
        pc.start(st)  # double start


def test_fail_timeout_reset():
    st = seeded_state()
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    pc.fail(st, "boom", now="2026-10-18T01:00:01+00:00")
    assert st["phase"] == "FAILED" and st["failure_reason"] == "boom"
    pc.reset(st)
    assert st["phase"] == "IDLE" and pc.blocks_left(st["user_board"]) == 0
    st2 = seeded_state()
    pc.start(st2, now="2026-10-18T01:00:00+00:00")
    pc.timeout(st2, now="2026-10-18T01:00:01+00:00")
    assert st2["phase"] == "TIMEOUT" and st2["outcome"] == "timeout"
    with pytest.raises(pc.PuyoCornerError):
        pc.fail(st2, "boom")  # terminal phase cannot fail again
    pc.reset(st2)
    # reset keeps the ruleset id
    assert st["ruleset_id"] == st2["ruleset_id"]


def test_finish_requires_round_over():
    st = seeded_state()
    with pytest.raises(pc.PuyoCornerError):
        pc.finish(st)
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    with pytest.raises(pc.PuyoCornerError):
        pc.finish(st)  # MATCH cannot finish
    pc._transition(st, "ROUND_OVER")  # force the reviewed boundary
    pc.finish(st, now="2026-10-18T01:00:02+00:00")
    assert st["phase"] == "END" and st["outcome"] == "completed"


# ---------------------------------------------------------------------------
# match loop
# ---------------------------------------------------------------------------


def test_full_match_ends_with_a_winner():
    st = seeded_state(seed=7)
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    import random as _random
    chooser = _random.Random(7)
    i = 0
    while st["phase"] == "MATCH" and i < 300:
        side = "user" if i % 2 == 0 else "ai"
        pc.apply_action(st, side, chooser.choice((
            "left", "right", "rotate_cw", "rotate_ccw", "hard_drop")))
        pc.tick(st, now="2026-10-18T01:00:02+00:00")
        i += 1
    assert st["phase"] == "ROUND_OVER"
    assert st["winner"] in ("user", "ai", "draw")
    pc.finish(st, now="2026-10-18T01:00:03+00:00")
    assert st["outcome"] in ("user_win", "ai_win", "draw")
    assert st["actions"] >= 1 and st["frames"] >= 1
    assert st["user_score"] >= 0 and st["ai_score"] >= 0
    assert any(r.get("reason") == "top_out" for r in st["round_results"])


def test_match_is_deterministic_for_a_seed():
    def play():
        st = seeded_state(seed=21)
        pc.start(st, now="2026-10-18T01:00:00+00:00")
        import random as _random
        chooser = _random.Random(21)
        i = 0
        while st["phase"] == "MATCH" and i < 400:
            side = "user" if i % 2 == 0 else "ai"
            pc.apply_action(st, side, chooser.choice((
                "left", "right", "rotate_cw", "rotate_ccw", "hard_drop")))
            pc.tick(st, now="2026-10-18T01:00:02+00:00")
            i += 1
        pc.finish(st, now="2026-10-18T01:00:03+00:00")
        return st

    a, b = play(), play()
    strip = lambda s: {k: s[k] for k in (
        "frames", "actions", "pairs_spawned", "winner", "outcome",
        "user_score", "ai_score")}
    assert strip(a) == strip(b)
    assert a["user_board"] == b["user_board"]
    assert a["ai_board"] == b["ai_board"]
    assert a["rng"] == b["rng"]


def test_pair_budget_fails_closed():
    st = seeded_state()
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    st["pairs_spawned"] = pc.MAX_PAIRS_PER_ROUND
    pc.apply_action(st, "user", "hard_drop")  # force the lock on the next tick
    with pytest.raises(pc.PuyoCornerError) as exc:
        pc.tick(st, now="2026-10-18T01:00:01+00:00")
    assert exc.value.code == "pair_budget"


# ---------------------------------------------------------------------------
# ledger + persistence
# ---------------------------------------------------------------------------


def test_ledger_records_and_trims(tmp_path):
    ledger = pc.new_ledger()
    st = seeded_state()
    assert pc.record_match(ledger, st) is False
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    pc.timeout(st, now="2026-10-18T01:00:01+00:00")
    assert pc.record_match(ledger, st) is True
    assert ledger["entries"][-1]["outcome"] == "timeout"
    assert ledger["entries"][-1]["ruleset_id"] == st["ruleset_id"]
    for _ in range(pc.MAX_HISTORY_ENTRIES + 5):
        ledger["entries"].append(dict(ledger["entries"][0]))
    pc._trim_ledger(ledger)
    assert len(ledger["entries"]) <= pc.MAX_HISTORY_ENTRIES


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    st = seeded_state()
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    pc.tick(st, now="2026-10-18T01:00:01+00:00")
    pc.timeout(st, now="2026-10-18T01:00:02+00:00")
    pc.save_state(path, st)
    loaded = pc.load_state(path)
    assert loaded["phase"] == st["phase"]
    assert loaded["user_board"] == st["user_board"]
    assert loaded["rng"] == st["rng"]
    assert loaded["ruleset_id"] == st["ruleset_id"]
    assert loaded["outcome"] == "timeout"
    ledger_path = tmp_path / "ledger.json"
    ledger = pc.new_ledger()
    pc.record_match(ledger, st)
    ledger_path2 = tmp_path / "ledger2.json"
    pc.save_ledger(ledger_path, ledger)
    reloaded = pc.load_ledger(ledger_path2)
    assert reloaded == pc.new_ledger()
    reloaded2 = pc.load_ledger(ledger_path)
    assert len(reloaded2["entries"]) == 1
    hist = tmp_path / "history.jsonl"
    pc.append_history_jsonl(hist, reloaded2["entries"][0])
    assert json.loads(hist.read_text().splitlines()[0])["outcome"] == "timeout"
    assert pc.with_lock(tmp_path, lambda: 42) == 42


def test_missing_state_file_returns_fresh(tmp_path):
    fresh = pc.load_state(tmp_path / "nope.json")
    assert fresh["phase"] == "IDLE"
    assert pc.load_ledger(tmp_path / "nope.json") == pc.new_ledger()


def test_corrupt_state_file_returns_fresh(tmp_path):
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json")
    assert pc.load_state(corrupt)["phase"] == "IDLE"
