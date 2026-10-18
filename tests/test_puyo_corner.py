"""Offline puyo-puyo clone corner core contract (Issue #698).

No Twitch, game switch, VM or production writes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from docich import puyo_corner as pc


def field_from(rows: list[str]) -> list[list[str]]:
    """Build a field from ROWS_TOTAL strings of COLS cells (row 0 = hidden)."""
    assert len(rows) == pc.ROWS_TOTAL
    assert all(len(r) == pc.COLS for r in rows)
    return [list(r) for r in rows]


def blank(rows: dict[int, str]) -> list[str]:
    out = ["......"] * pc.ROWS_TOTAL
    for r, s in rows.items():
        out[r] = s
    return out


# ---------------------------------------------------------------------------
# field / chain / scoring primitives
# ---------------------------------------------------------------------------


def test_constants_are_the_documented_geometry():
    assert (pc.COLS, pc.ROWS, pc.HIDDEN_ROWS, pc.ROWS_TOTAL) == (6, 12, 1, 13)
    assert pc.COLORS == ("R", "G", "B", "Y")
    assert pc.SPAWN_ROW == 1 and pc.SPAWN_COL == 2


def test_find_groups_finds_four_connected():
    field = field_from(blank({12: "RRR.R.", 11: ".R...."}))
    groups = pc.find_groups(field)
    assert len(groups) == 1
    assert groups[0]["color"] == "R"
    assert len(groups[0]["cells"]) == 4  # L-shaped connected group


def test_find_groups_ignores_ojama_and_small_groups():
    field = field_from(blank({12: "RR.OO.", 11: "O....."}))
    assert pc.find_groups(field) == []  # 2 R (too small), ojama never group


def test_apply_gravity_settles_columns():
    field = field_from(blank({0: "R.....", 5: "G.....", 12: "B....."}))
    pc.apply_gravity(field)
    # relative order is kept: the bottom puyo stays at the bottom
    assert field[12][0] == "B" and field[11][0] == "G" and field[10][0] == "R"
    assert all(field[r][0] == pc.EMPTY for r in range(0, 10))
    assert all(field[r][c] == pc.EMPTY for r in range(pc.ROWS_TOTAL) for c in range(1, 6))


def test_resolve_single_chain_scores_zero_like_the_original():
    # a lone 1-chain of 4 puyos: chain power 0 + no bonuses -> score 0
    field = field_from(blank({11: "RR....", 12: "RR...."}))
    info = pc.resolve_field(field)
    assert info["chain"] == 1
    assert info["score"] == 0
    assert info["popped"] == 4
    assert field == pc._empty_field()
    assert info["all_clear"] is True


def test_resolve_two_chain_vertical_stack():
    # G square pops (chain 1), then the R pairs above fall and merge in col 0
    rows = {12: "R.....", 11: "R.....", 10: "GG....", 9: "GG....", 8: "RB....", 7: "RB...."}
    field = field_from(blank(rows))
    info = pc.resolve_field(field)
    assert info["chain"] == 2
    # step 1 (chain 1) scores 0; step 2: chain power 8 * 10 * 4 = 320
    assert info["score"] == 320
    assert [s["chain"] for s in info["steps"]] == [1, 2]
    assert info["steps"][1]["score"] == 320
    assert info["all_clear"] is False  # the B pair in col 1 remains


def test_resolve_three_chain():
    # R L-shape -> G L-shape -> B L-shape across three columns; all clear
    rows = {
        12: "RRR...", 11: "RGG...", 10: "GBB...", 9: "G.....", 8: "B.....", 7: "B.....",
    }
    field = field_from(blank(rows))
    info = pc.resolve_field(field)
    assert info["chain"] == 3
    # 0 (chain 1) + 8*10*4 (chain 2) + 16*10*4 (chain 3)
    assert [s["score"] for s in info["steps"]] == [0, 320, 640]
    assert info["score"] == 960
    assert info["all_clear"] is True


def test_resolve_group_bonus_for_five_puyos():
    # 5 connected R in the bottom row: group bonus 2 -> 2 * 10 * 5 = 100
    field = field_from(blank({12: "RRRRR."}))
    info = pc.resolve_field(field)
    assert info["chain"] == 1
    assert info["score"] == 100
    assert info["steps"][0]["group_sizes"] == [5]


def test_resolve_color_bonus_for_two_colors_same_step():
    # two groups (R and G) pop in the same step: color bonus 3 -> 3 * 10 * 8 = 240
    field = field_from(blank({11: "RRGG..", 12: "RRGG.."}))
    info = pc.resolve_field(field)
    assert info["chain"] == 1
    assert info["score"] == 240
    assert sorted(info["steps"][0]["colors"]) == ["G", "R"]


def test_resolve_all_clear_bonus_ready_adds_2100():
    field = field_from(blank({11: "RR....", 12: "RR...."}))
    info = pc.resolve_field(field, all_clear_bonus_ready=True)
    # (0 + 2100) * 10 * 4
    assert info["score"] == 2100 * 10 * 4
    assert info["all_clear"] is True


def test_resolve_ojama_adjacent_to_pop_is_cleared_and_not_scored():
    # 5 R + 1 adjacent ojama: puyos scored = 5, ojama cleared but unscored
    field = field_from(blank({12: "RRRRRO"}))
    info = pc.resolve_field(field)
    assert info["chain"] == 1
    assert info["popped"] == 5
    assert info["ojama_cleared"] == 1
    assert info["score"] == 100
    assert field == pc._empty_field()


def test_resolve_ojama_does_not_chain_through_ojama():
    # ojama touching a popping group pops, but ojama behind it does not
    field = field_from(blank({12: "RRRROO", 11: ".....O"}))
    info = pc.resolve_field(field)
    assert info["ojama_cleared"] == 1  # only the directly adjacent one
    assert field[12][5] == pc.OJAMA  # two cells away from the color group: stays
    assert field[11][5] == pc.OJAMA


def test_drop_ojama_fills_bottom_row_first_left_to_right():
    field = pc._empty_field()
    placed = pc.drop_ojama(field, 7)
    assert placed == 7
    assert "".join(field[12]) == "OOOOOO"
    assert "".join(field[11]) == "O....."


def test_drop_ojama_never_overwrites_puyos_and_honors_quota():
    field = field_from(blank({12: "RR..OO", 11: "......"}))
    placed = pc.drop_ojama(field, 30, max_drop=4)
    assert placed == 4
    # bottom row empty slots first (2), then the next row up (2)
    assert "".join(field[12]) == "RROOOO"
    assert "".join(field[11]) == "OO...."


# ---------------------------------------------------------------------------
# game: spawn / movement / rotation kicks
# ---------------------------------------------------------------------------


def test_new_game_spawns_vertical_pair_on_top_visible_row():
    state = pc.new_game(seed=1)
    assert state["phase"] == "PLAYING"
    assert state["pair"]["rot"] == pc.ROT_UP
    assert (state["pair"]["row"], state["pair"]["col"]) == (pc.SPAWN_ROW, pc.SPAWN_COL)
    a, ch = pc._cells(state["pair"])
    assert a == (1, 2) and ch == (0, 2)  # child sits in the hidden row
    assert state["next_pair"] is not None and len(state["next_pair"]) == 2
    assert state["field"][0][2] == pc.EMPTY


def test_pair_rng_is_deterministic_and_uniform_over_ordered_pairs():
    a = pc.new_game(seed=42)
    b = pc.new_game(seed=42)
    assert a["pair"] == b["pair"] and a["next_pair"] == b["next_pair"]
    seen = set()
    for seed in range(64):
        st = pc.new_game(seed=seed)
        seen.add((st["pair"]["axis"], st["pair"]["child"]))
    assert len(seen) == 16  # all 4x4 ordered pairs occur


def test_move_left_right_and_wall_noop():
    state = pc.new_game(seed=0)
    pc.apply_action(state, "left")
    assert state["pair"]["col"] == 1
    pc.apply_action(state, "left")
    assert state["pair"]["col"] == 0
    pc.apply_action(state, "left")  # blocked: silent no-op
    assert state["pair"]["col"] == 0
    for _ in range(10):
        pc.apply_action(state, "right")
    assert state["pair"]["col"] == pc.COLS - 1  # axis cannot pass the right wall


def test_rotate_wall_axis_slide_at_left_wall():
    state = pc.new_game(seed=0)
    pc.apply_action(state, "left")
    pc.apply_action(state, "left")  # axis col 0, rot UP
    assert pc.apply_action(state, "ccw")  # child would leave the field -> slide
    assert state["pair"]["rot"] == pc.ROT_LEFT
    assert state["pair"]["col"] == 1
    a, ch = pc._cells(state["pair"])
    assert (a, ch) == ((1, 1), (1, 0))


def test_rotate_wall_axis_slide_at_right_wall():
    state = pc.new_game(seed=0)
    for _ in range(3):
        pc.apply_action(state, "right")  # axis col 5, rot UP
    pc.apply_action(state, "cw")  # child would be at col 6 -> slide left
    assert state["pair"]["rot"] == pc.ROT_RIGHT
    assert state["pair"]["col"] == 4
    a, ch = pc._cells(state["pair"])
    assert (a, ch) == ((1, 4), (1, 5))


def test_rotate_floor_kick_on_hard_dropped_horizontal_pair():
    state = pc.new_game(seed=0)
    # craft a horizontal pair resting on the floor (as hard_drop would leave it)
    state["pair"] = {"axis": "R", "child": "R", "rot": pc.ROT_RIGHT, "row": 12, "col": 2}
    pc.apply_action(state, "cw")  # child would be below the floor -> kick up
    assert state["pair"]["rot"] == pc.ROT_DOWN
    assert (state["pair"]["row"], state["pair"]["col"]) == (11, 2)
    a, ch = pc._cells(state["pair"])
    assert (a, ch) == ((11, 2), (12, 2))


def test_rotate_blocked_by_occupied_cell_is_noop():
    state = pc.new_game(seed=0)
    state["pair"] = {"axis": "R", "child": "R", "rot": pc.ROT_RIGHT, "row": 12, "col": 2}
    state["field"][11][2] = "B"  # block the cell above the axis
    pc.apply_action(state, "ccw")  # child target (11,2) occupied: no kick applies
    assert state["pair"]["rot"] == pc.ROT_RIGHT
    assert (state["pair"]["row"], state["pair"]["col"]) == (12, 2)


def test_soft_drop_moves_one_row_and_hard_drop_locks():
    state = pc.new_game(seed=0)
    pc.apply_action(state, "soft_drop")
    assert state["pair"]["row"] == 2
    pc.apply_action(state, "hard_drop")
    assert state["pair"] is not None  # a fresh pair spawned after the lock
    assert state["field"][12][2] != pc.EMPTY  # locked puyos are on the floor
    assert state["score"] == 0 and state["chains_total"] == 0  # no chain yet


def test_step_gravity_locks_and_spawns_next():
    state = pc.new_game(seed=3)
    for _ in range(20):
        pc.step(state)
    assert state["ticks"] >= 12
    assert state["field"][12][2] != pc.EMPTY or state["field"][12][3] != pc.EMPTY


def test_game_level_all_clear_pending_is_set_and_consumed():
    state = pc.new_game(seed=0)
    # craft a 2x2 R group with an R/R pair stacked right above it in col 0
    state["field"][11][0] = "R"
    state["field"][12][0] = "R"
    state["field"][11][1] = "R"
    state["field"][12][1] = "R"
    state["pair"] = {"axis": "R", "child": "R", "rot": pc.ROT_UP, "row": 10, "col": 0}
    state["all_clear_pending"] = True
    pc.apply_action(state, "hard_drop")  # connected group of 6 R pops, all-clear consumed
    assert state["last_lock"]["chain"] == 1
    # (0 chain power + 2100 all-clear + 3 group bonus for 6) * 10 * 6
    assert state["last_lock"]["score"] == (2100 + 3) * 10 * 6
    assert state["all_clear_pending"] is True  # field emptied again: stays pending
    total = (2100 + 3) * 10 * 6  # 126180
    assert state["score"] == total
    assert state["attack_pending"] == total // pc.OJAMA_RATE
    assert state["carry"] == total % pc.OJAMA_RATE


def test_attack_uses_ojama_rate_with_carry():
    state = pc.new_game(seed=0)
    state["field"][12][0:5] = ["R"] * 5  # five R across the bottom row
    state["pair"] = {"axis": "R", "child": "R", "rot": pc.ROT_UP, "row": 11, "col": 5}
    pc.apply_action(state, "hard_drop")  # connects into a group of 7 R
    # group of 7: bonus 4 -> (0 + 4) * 10 * 7 = 280
    assert state["last_lock"]["score"] == 280
    assert state["score"] == 280
    assert state["attack_pending"] == 280 // pc.OJAMA_RATE  # 4
    assert state["carry"] == 280 % pc.OJAMA_RATE  # 0


def test_ojama_drops_after_own_chains_and_cannot_pop_this_lock():
    state = pc.new_game(seed=0)
    state["ojama_in"] = 3
    state["pair"] = {"axis": "R", "child": "R", "rot": pc.ROT_RIGHT, "row": 12, "col": 4}
    pc.apply_action(state, "hard_drop")  # 2 R at cols 4,5: no chain
    assert state["last_lock"]["ojama_dropped"] == 3
    assert state["ojama_in"] == 0
    assert state["field"][12][0] == pc.OJAMA  # ojama rest on the bottom row
    assert state["field"][12][4] == "R" and state["field"][12][5] == "R"


def test_spawn_blocked_means_game_over():
    state = pc.new_game(seed=0)
    state["field"][0][2] = "R"  # hidden spawn cell occupied
    state["pair"] = {"axis": "R", "child": "R", "rot": pc.ROT_RIGHT, "row": 12, "col": 4}
    pc.apply_action(state, "hard_drop")  # lock resolves, then spawn fails
    assert state["phase"] == "GAME_OVER"
    assert state["pair"] is None
    with pytest.raises(pc.PuyoCornerError):
        pc.apply_action(state, "left")


# ---------------------------------------------------------------------------
# match: two seats, ojama exchange, win/loss
# ---------------------------------------------------------------------------


def test_new_match_two_seats_with_derived_seed():
    match = pc.new_match(seed=7)
    assert match["phase"] == "PLAYING"
    assert set(match["games"].keys()) == {"A", "B"}
    assert match["games"]["A"]["seed"] == 7
    assert match["games"]["B"]["seed"] == pc._derive_seed(7, 1)
    assert match["games"]["A"]["seed"] != match["games"]["B"]["seed"]


def test_match_transfers_pending_attack_to_opponent_queue():
    match = pc.new_match(seed=0)
    match["games"]["A"]["attack_pending"] = 5
    pc.match_step(match)
    assert match["games"]["A"]["attack_pending"] == 0
    assert match["games"]["B"]["ojama_in"] == 5


def test_match_ojama_queue_cap_discards_overflow():
    match = pc.new_match(seed=0)
    match["games"]["A"]["attack_pending"] = pc.MAX_OJAMA_QUEUE + 100
    pc.match_step(match)
    assert match["games"]["B"]["ojama_in"] == pc.MAX_OJAMA_QUEUE


def test_match_ends_when_opponent_tops_out_via_ojama():
    match = pc.new_match(seed=0)
    b = match["games"]["B"]
    # fill B's field except the hidden row and the spawn axis row
    for r in range(2, pc.ROWS_TOTAL):
        for c in range(pc.COLS):
            b["field"][r][c] = pc.COLORS[(r + c) % 4]  # no 4-connected groups
    b["ojama_in"] = pc.MAX_OJAMA_DROP
    pc.match_apply_action(match, "B", "hard_drop")  # lock into the near-full field
    pc.match_step(match)
    assert b["phase"] == "GAME_OVER"
    assert match["phase"] == "ENDED"
    assert match["winner"] == "A"
    # ojama filled every empty cell it could before the spawn failed
    assert b["ojama_in"] == pc.MAX_OJAMA_DROP - 10


def test_simultaneous_death_is_a_draw():
    match = pc.new_match(seed=0)
    for side in ("A", "B"):
        g = match["games"][side]
        for r in range(2, pc.ROWS_TOTAL):
            for c in range(pc.COLS):
                g["field"][r][c] = pc.COLORS[(r + c) % 4]
        g["ojama_in"] = pc.MAX_OJAMA_DROP
    pc.match_apply_action(match, "A", "hard_drop")
    pc.match_apply_action(match, "B", "hard_drop")
    pc.match_step(match)
    assert match["phase"] == "ENDED"
    assert match["winner"] == "draw"


def test_run_greedy_match_terminates_deterministically():
    m1 = pc.run_greedy_match(pc.new_match(seed=20260918))
    m2 = pc.run_greedy_match(pc.new_match(seed=20260918))
    assert m1["phase"] == m2["phase"]
    assert m1["winner"] == m2["winner"]
    assert m1["games"]["A"]["score"] == m2["games"]["A"]["score"]
    assert m1["games"]["B"]["score"] == m2["games"]["B"]["score"]
    assert m1["phase"] == "ENDED"
    assert m1["winner"] in ("A", "B", "draw")
    # both seats actually played: chains happened
    total_chains = m1["games"]["A"]["chains_total"] + m1["games"]["B"]["chains_total"]
    assert total_chains > 0


def test_run_greedy_match_is_reasonably_lively():
    match = pc.run_greedy_match(pc.new_match(seed=1))
    a = match["games"]["A"]
    b = match["games"]["B"]
    # the greedy player must at least clear puyos and exchange attacks
    assert a["score"] > 0 or b["score"] > 0
    assert max(a["chains_total"], b["chains_total"]) >= 2


def test_greedy_policy_returns_valid_actions_only():
    state = pc.new_game(seed=11)
    seen = set()
    for _ in range(40):
        if state["phase"] != "PLAYING":
            break
        action = pc.greedy_policy(state)
        assert action in pc.ACTIONS
        seen.add(action)
        pc.apply_action(state, action)
        pc.step(state)
    assert seen  # policy produced usable controls


# ---------------------------------------------------------------------------
# fail-closed validation
# ---------------------------------------------------------------------------


def test_invalid_inputs_fail_closed():
    with pytest.raises(pc.PuyoCornerError):
        pc.new_game(seed=-1)
    with pytest.raises(pc.PuyoCornerError):
        pc.new_game(seed=True)  # bool is not an int here
    state = pc.new_game(seed=0)
    with pytest.raises(pc.PuyoCornerError):
        pc.apply_action(state, "jump")  # unknown action
    with pytest.raises(pc.PuyoCornerError):
        pc.apply_action(state, None)
    state["phase"] = "GAME_OVER"
    with pytest.raises(pc.PuyoCornerError):
        pc.apply_action(state, "left")
    with pytest.raises(pc.PuyoCornerError):
        pc.greedy_policy({"schema": pc.SCHEMA, "phase": "IDLE"})
    match = pc.new_match()
    with pytest.raises(pc.PuyoCornerError):
        pc.match_apply_action(match, "C", "left")


def test_validate_field_rejects_bad_shapes_and_cells():
    with pytest.raises(pc.PuyoCornerError):
        pc._validate_field([["R"] * 6] * 12)  # wrong row count
    with pytest.raises(pc.PuyoCornerError):
        pc._validate_field([["R"] * 5] * pc.ROWS_TOTAL)  # wrong col count
    with pytest.raises(pc.PuyoCornerError):
        pc._validate_field([["X"] * 6] * pc.ROWS_TOTAL)  # unknown cell


def test_chain_limit_fails_closed(monkeypatch: pytest.MonkeyPatch):
    field = field_from(blank({11: "RR....", 12: "RR...."}))
    monkeypatch.setattr(pc, "MAX_CHAIN_STEPS", 0)
    with pytest.raises(pc.PuyoCornerError):
        pc.resolve_field(field)


# ---------------------------------------------------------------------------
# rendering + persistence
# ---------------------------------------------------------------------------


def test_render_ascii_shape():
    state = pc.new_game(seed=0)
    text = pc.render_ascii(state)
    lines = text.split("\n")
    assert len(lines) == pc.ROWS_TOTAL
    assert all(len(line) == pc.COLS for line in lines)
    assert set(text) <= set(".RGBYO\n")


def test_game_state_is_json_serializable():
    state = pc.new_game(seed=5)
    pc.apply_action(state, "hard_drop")
    pc.step(state)
    blob = json.dumps(state, ensure_ascii=False)
    assert pc.SCHEMA == json.loads(blob)["schema"]


def test_save_load_game_roundtrip(tmp_path: Path):
    state = pc.new_game(seed=9)
    pc.apply_action(state, "hard_drop")
    path = tmp_path / "puyo_corner_state.json"
    pc.save_game(path, state)
    loaded = pc.load_game(path)
    assert loaded["seed"] == 9
    assert loaded["field"] == state["field"]
    assert loaded["score"] == state["score"]
    assert loaded["pair"] == state["pair"]
    assert loaded["next_pair"] == state["next_pair"]


def test_load_game_corrupt_file_yields_fresh_state(tmp_path: Path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    state = pc.load_game(path)
    assert state["phase"] == "PLAYING"
    assert state["score"] == 0
    path.write_text(json.dumps({"schema": 99}), encoding="utf-8")
    assert pc.load_game(path)["schema"] == pc.SCHEMA
    path.write_text(json.dumps({"schema": pc.SCHEMA, "phase": "PLAYING", "field": "bogus"}),
                    encoding="utf-8")
    assert pc.load_game(path)["field"] == pc._empty_field()


def test_save_load_match_roundtrip(tmp_path: Path):
    match = pc.new_match(seed=12)
    pc.match_apply_action(match, "A", "hard_drop")
    path = tmp_path / "puyo_corner_match.json"
    pc.save_match(path, match)
    loaded = pc.load_match(path)
    assert loaded["seed"] == 12
    assert loaded["games"]["A"]["field"] == match["games"]["A"]["field"]
    assert loaded["winner"] is None


def test_with_lock_runs_fn_and_creates_lock_file(tmp_path: Path):
    def add(x, y):
        return x + y

    assert pc.with_lock(tmp_path, add, 2, 3) == 5
    assert (tmp_path / pc.LOCK_FILE).exists()
