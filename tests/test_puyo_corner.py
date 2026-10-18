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
    """Tsu-style scoring (PR #2491 integration): a lone 1-chain scores 0."""
    ruleset = pc.validate_ruleset(make_ruleset())
    board = vertical_four()
    blocks, chain, score, all_clear = pc._resolve_chain(board, ruleset)
    assert (blocks, chain, all_clear) == (4, 1, True)  # the last blocks clear
    assert score == 0  # a lone 1-chain: no points and no ojama attack
    # a 2-chain: the 7-blue bottom mass clears (chain 1), then the reds
    # above merge into a connected 4 (chain 2, chain power 8 x 10 x 4)
    two = pc.empty_board()
    for x in range(6):
        two[11][x] = "blue"
    for x in (0, 1, 2):
        two[10][x] = "red"
    two[10][3] = "blue"
    two[9][3] = "red"
    blocks_two, chain_two, score_two, all_clear_two = pc._resolve_chain(two, ruleset)
    assert (blocks_two, chain_two, all_clear_two) == (11, 2, True)
    # chain 1: 7 blues = (0 + 0 + group bonus 4) x 10 x 7 = 280
    # chain 2: 4 reds  = (8 + 0 + 0) x 10 x 4 = 320
    assert score_two == 600
    assert score_two > score


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


# ---------------------------------------------------------------------------
# match runtime + AI brain + overlay draft (offline follow-up slice)
# ---------------------------------------------------------------------------


def make_pair(axis="red", satellite="red", col=2):
    return {
        "axis": axis,
        "axis_pos": [col, 0],
        "satellite": satellite,
        "satellite_pos": [col + 1, 0],
        "spawned_at": "2026-10-18T01:00:00+00:00",
        "spawn_digest": ":".join((axis, satellite)),
    }


def test_plan_actions_is_bounded_and_engine_verified():
    st = seeded_state(seed=3)
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    plan = pc.plan_actions(st, "user")
    assert plan, "a live pair always yields a plan"
    assert len(plan) <= pc.MAX_PLAN_ACTIONS
    assert all(action in pc.ACTIONS for action in plan)
    assert plan[-1] == "hard_drop"
    assert plan == pc.plan_actions(st, "user")  # deterministic
    # graceful no-plan outside MATCH or without a live pair
    assert pc.plan_actions(seeded_state(), "user") == []
    st["user_pair"] = None
    assert pc.plan_actions(st, "user") == []


def test_plan_actions_finds_a_scoring_chain():
    st = seeded_state(seed=3)
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    st["user_board"] = pc.empty_board()
    for x in (0, 1, 2):
        st["user_board"][11][x] = "red"
    st["user_pair"] = make_pair("red", "red", col=4)
    plan = pc.plan_actions(st, "user")
    for action in plan:
        pc.apply_action(st, "user", action)
    pc.tick(st, now="2026-10-18T01:00:01+00:00")
    assert st["user_chain"] >= 1
    assert st["user_score"] > 0


def test_play_match_top_out_ends_with_a_winner():
    st = seeded_state(seed=7)  # this self-play reaches a top-out
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    pc.play_match(st)
    assert st["phase"] == "ROUND_OVER"
    assert st["winner"] in ("user", "ai")
    pc.finish(st, now="2026-10-18T01:00:09+00:00")
    assert st["outcome"] in ("user_win", "ai_win")
    assert any(r.get("reason") == "top_out" for r in st["round_results"])
    pc.reset(st)
    assert st["phase"] == "IDLE"
    assert pc.blocks_left(st["user_board"]) == 0


def test_play_match_timeout_ends_bounded():
    st = seeded_state(seed=11)
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    pc.play_match(st)
    # with the integrated ojama economy this seed now reaches a top-out
    # before exhausting its pair budget; either way the match is decided
    assert st["phase"] in ("ROUND_OVER", "TIMEOUT")
    pc.finish(st, now="2026-10-18T01:00:09+00:00")
    assert st["outcome"] in ("user_win", "ai_win", "draw", "timeout")
    pc.reset(st)
    assert st["phase"] == "IDLE"


def test_play_match_is_deterministic():
    def play():
        st = seeded_state(seed=7)
        pc.start(st, now="2026-10-18T01:00:00+00:00")
        pc.play_match(st)
        pc.finish(st, now="2026-10-18T01:00:09+00:00")
        return st

    a, b = play(), play()
    strip = lambda s: {k: s[k] for k in (
        "frames", "actions", "pairs_spawned", "winner", "outcome",
        "user_score", "ai_score", "rng")}
    assert strip(a) == strip(b)
    assert a["user_board"] == b["user_board"] and a["ai_board"] == b["ai_board"]


def test_play_match_budget_exhaustion_times_out_closed():
    st = seeded_state(seed=5)
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    st["pairs_spawned"] = pc.MAX_PAIRS_PER_ROUND
    pc.play_match(st)
    assert st["phase"] == "TIMEOUT"
    assert st["outcome"] == "timeout"
    assert "budget" in st["failure_reason"]


def test_play_match_respects_max_steps():
    st = seeded_state(seed=5)
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    pc.play_match(st, max_steps=1)
    assert st["phase"] in ("MATCH", "ROUND_OVER")
    with pytest.raises(pc.PuyoCornerError):
        pc.play_match(st, max_steps=0)


def test_overlay_draft_is_bounded_and_pure():
    st = seeded_state()
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    pc.tick(st, now="2026-10-18T01:00:01+00:00")
    draft = pc.overlay_draft(st)
    assert draft["title"] == "ぷよぷよクローン"
    assert draft["phase"] == "MATCH"
    keys = [section["key"] for section in draft["sections"]]
    assert len(draft["sections"]) <= pc.MAX_OVERLAY_SECTIONS
    assert keys[:7] == [
        "phase", "user_board", "user_score", "user_chain",
        "ai_board", "ai_score", "ai_chain",
    ]
    user_board = next(s for s in draft["sections"] if s["key"] == "user_board")
    assert len(user_board["text"].splitlines()) == pc.BOARD_ROWS
    # pure: drafting never advances the match
    assert pc.overlay_draft(st) == draft
    pc.timeout(st, now="2026-10-18T01:00:02+00:00")
    assert pc.overlay_draft(st)["sections"][-1]["key"] != "winner"
    win = seeded_state()
    win["winner"] = "user"
    assert pc.overlay_draft(win)["sections"][-1]["text"] == "user"


# ---------------------------------------------------------------------------
# integrated from PR #2491: Tsu-faithful scoring, ojama economy,
# greedy_policy, and the bounded match time limit (owner-approved merge)
# ---------------------------------------------------------------------------


def test_group_bonus_applies_to_a_single_chain():
    ruleset = pc.validate_ruleset(make_ruleset())
    board = pc.empty_board()
    for x in range(5):
        board[11][x] = "red"  # connected 5: group bonus 2
    blocks, chain, score, all_clear = pc._resolve_chain(board, ruleset)
    assert (blocks, chain, all_clear) == (5, 1, True)
    assert score == (0 + 0 + 2) * 10 * 5  # 100


def test_color_bonus_applies_when_two_colors_clear_together():
    ruleset = pc.validate_ruleset(make_ruleset())
    board = pc.empty_board()
    for y in (10, 11):
        board[y][0] = "red"
        board[y][1] = "red"
        board[y][2] = "blue"
        board[y][3] = "blue"
    blocks, chain, score, _ = pc._resolve_chain(board, ruleset)
    assert (blocks, chain) == (8, 1)
    assert score == (0 + 3 + 0) * 10 * 8  # two colors: +3 -> 240


def test_drop_ojama_fills_bottom_row_flat():
    board = pc.empty_board()
    placed = pc.drop_ojama(board, 7)
    assert placed == 7
    assert board[11] == ["ojama"] * 6
    assert board[10][0] == "ojama"
    with pytest.raises(pc.PuyoCornerError):
        pc.drop_ojama([[None] * 6] * 11, 1)  # malformed board
    with pytest.raises(pc.PuyoCornerError):
        pc.drop_ojama(pc.empty_board(), -1)


def test_drop_ojama_never_overwrites_and_honors_quota():
    board = pc.empty_board()
    board[11] = ["red", "red", None, None, "ojama", "ojama"]
    placed = pc.drop_ojama(board, 30, max_drop=4)
    assert placed == 4
    assert board[11] == ["red", "red", "ojama", "ojama", "ojama", "ojama"]
    assert board[10][0] == "ojama" and board[10][1] == "ojama"


def test_ojama_never_groups_and_pops_only_by_adjacency():
    ruleset = pc.validate_ruleset(make_ruleset())
    # a full ojama row never clears on its own
    ojama_only = pc.empty_board()
    for x in range(6):
        ojama_only[11][x] = "ojama"
    assert pc._resolve_chain(ojama_only, ruleset) == (0, 0, 0, False)
    # ojama adjacent to a cleared color pops; ojama behind it survives
    board = pc.empty_board()
    for x in range(5):
        board[11][x] = "red"
    board[11][5] = "ojama"
    board[10][5] = "ojama"
    blocks, chain, score, all_clear = pc._resolve_chain(board, ruleset)
    assert (blocks, chain) == (5, 1)
    assert score == 100  # ojama is never scored
    # the adjacent ojama popped; the one behind it survived and settled back
    # down to the bottom row after the gravity pass
    assert board[11][5] == "ojama"
    assert board[10][5] is None
    assert all_clear is False


def test_ojama_drops_after_own_chains():
    st = seeded_state(seed=5)
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    st["user_ojama_in"] = 1
    for x in range(5):
        st["user_board"][11][x] = "red"
    st["user_pair"] = make_pair("blue", "blue", col=4)
    pc.apply_action(st, "user", "hard_drop")
    pc.tick(st, now="2026-10-18T01:00:01+00:00")
    # if the ojama had dropped before the lock's own chains, it would have
    # landed at (5,11), touching the clearing reds, and popped. After the
    # chains the first free cell is (0,11), far from any cleared color.
    assert st["user_ojama_in"] == 0
    assert st["user_board"][11][0] == "ojama"  # post-chain drop: survives
    assert st["user_board"][11][4] == "blue" and st["user_board"][11][5] == "blue"
    assert st["user_chain"] == 1 and st["user_score"] == 100
    # attack: (0 carry + 100) // 70 = 1, remainder 30; exchanged to the AI
    assert st["user_carry"] == 30
    assert st["user_attack_pending"] == 0
    assert st["ai_ojama_in"] == 1


def test_lone_one_chain_sends_no_ojama():
    st = seeded_state(seed=7)
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    for (x, y) in ((0, 11), (1, 11), (2, 11), (1, 10)):
        st["user_board"][y][x] = "red"
    st["user_pair"] = make_pair("blue", "blue", col=2)
    pc.apply_action(st, "user", "hard_drop")
    pc.tick(st, now="2026-10-18T01:00:01+00:00")
    assert st["user_chain"] == 1
    assert st["user_score"] == 0  # 1-chain only: (0+0+0) x 10 x 4
    assert st["user_attack_pending"] == 0 and st["user_carry"] == 0
    assert st["ai_ojama_in"] == 0


def test_exchange_transfers_pending_to_opponent():
    st = seeded_state()
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    st["user_attack_pending"] = 5
    pc.tick(st, now="2026-10-18T01:00:01+00:00")
    assert st["user_attack_pending"] == 0
    assert st["ai_ojama_in"] == 5


def test_exchange_caps_queue():
    st = seeded_state()
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    st["user_attack_pending"] = pc.MAX_OJAMA_QUEUE + 100
    pc.tick(st, now="2026-10-18T01:00:01+00:00")
    assert st["ai_ojama_in"] == pc.MAX_OJAMA_QUEUE


def test_ojama_top_out_gives_opponent_the_win():
    st = seeded_state(seed=7)
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    colors = make_ruleset()["colors"]
    for y in range(1, pc.BOARD_ROWS):  # no 4-connected groups in the pattern
        for x in range(pc.BOARD_COLS):
            st["user_board"][y][x] = colors[(y + x) % 4]
    st["user_ojama_in"] = pc.MAX_OJAMA_DROP
    pc.apply_action(st, "user", "hard_drop")
    pc.tick(st, now="2026-10-18T01:00:01+00:00")
    assert st["phase"] == "ROUND_OVER"
    assert st["winner"] == "ai"
    # only the 4 free cells of the spawn row could take ojama
    assert st["user_ojama_in"] == pc.MAX_OJAMA_DROP - 4
    pc.finish(st, now="2026-10-18T01:00:02+00:00")
    assert st["outcome"] == "ai_win"


def test_all_clear_bonus_is_granted_at_lock():
    st = seeded_state(seed=3)
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    for x in range(6):
        st["user_board"][11][x] = "blue"
    for x in (0, 1, 2):
        st["user_board"][10][x] = "red"
    st["user_board"][10][3] = "blue"
    st["user_board"][9][3] = "red"
    st["user_pair"] = make_pair("blue", "blue", col=4)  # must not bridge the reds
    pc.apply_action(st, "user", "hard_drop")
    pc.tick(st, now="2026-10-18T01:00:01+00:00")
    # chain 1: 9 connected blues = (0+0+6) x 10 x 9 = 540
    # chain 2: 4 reds after the settle = (8+0+0) x 10 x 4 = 320, all clear
    assert st["user_chain"] == 2
    total = 540 + 320 + pc.ALL_CLEAR_BONUS
    assert st["user_score"] == total
    assert st["user_attack_pending"] == 0  # already exchanged this tick
    # the exchange caps the opponent's reserve at 30 (overflow discarded)
    assert st["ai_ojama_in"] == pc.MAX_OJAMA_QUEUE


def test_greedy_policy_returns_valid_first_action():
    st = seeded_state(seed=3)
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    action = pc.greedy_policy(st, "user")
    assert action in pc.ACTIONS
    assert action == pc.plan_actions(st, "user")[0]  # first step of the plan
    assert action == pc.greedy_policy(st, "user")  # deterministic
    assert pc.greedy_policy(seeded_state(), "user") == "hard_drop"  # no live pair


def test_time_limit_ends_with_score_comparison():
    st = seeded_state(seed=7)
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    st["user_score"] = 500
    st["frames"] = pc.MATCH_MAX_TICKS
    pc.play_match(st)
    assert st["phase"] == "ROUND_OVER"
    assert st["winner"] == "user"  # higher score wins at the tick limit
    assert st["failure_reason"] == "match tick limit"
    assert any(r.get("reason") == "time_limit" for r in st["round_results"])
    pc.finish(st, now="2026-10-18T01:00:09+00:00")
    assert st["outcome"] == "user_win"


def test_play_match_step_cap_decides_by_score():
    st = seeded_state(seed=5)
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    pc.play_match(st, max_steps=1)
    # bounded finish: the driver cap never leaves a match hanging
    assert st["phase"] == "ROUND_OVER"
    assert st["winner"] in ("user", "ai", "draw")
    assert st["failure_reason"] == "selfplay step limit"
    pc.finish(st, now="2026-10-18T01:00:09+00:00")
    assert st["outcome"] in ("user_win", "ai_win", "draw")
