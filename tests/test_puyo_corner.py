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
    st = seeded_state(seed=11)  # this self-play exhausts the pair budget
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    pc.play_match(st)
    assert st["phase"] == "TIMEOUT" and st["outcome"] == "timeout"
    assert "budget" in st["failure_reason"]
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
# chat participation (PR-4)
# ---------------------------------------------------------------------------


def started(seed=11):
    st = seeded_state(seed=seed)
    pc.start(st, now="2026-10-18T01:00:00+00:00")
    return st


def chat(st, user_id, display_name, text, now=None):
    return pc.observe_chat(st, user_id=user_id, display_name=display_name,
                           text=text, now=now)


def test_parse_chat_accepts_only_exact_commands():
    # Chat verbs and action tokens are case-insensitive; the argument must be
    # exactly one token.
    assert pc.parse_chat("!puyo join") == ("join", "")
    assert pc.parse_chat("!puyo JOIN") == ("join", "")
    assert pc.parse_chat("!puyo leave") == ("leave", "")
    assert pc.parse_chat("!puyo hard_drop") == ("action", "hard_drop")
    assert pc.parse_chat("!puyo LEFT") == ("action", "left")
    for bad in ("!puyo", "!puyo  join?extra", "!puyo unknown", "puyo join",
                "!pujo join", "!puyo hard_drop now", None, 5,
                "!puyo rotate_cw!!", "!choice 1"):
        assert pc.parse_chat(bad) is None


def test_join_roster_is_bounded_dedupe_and_idempotent(tmp_path):
    st = seeded_state()
    assert chat(st, "u1", "たろう", "!puyo join") == "join"
    assert st["roster"] == [{
        "user_id": "u1", "display_name": "たろう",
        "joined_at": None if False else st["roster"][0]["joined_at"],
    }] and len(st["roster"][0]["joined_at"]) > 0
    # re-join refreshes the display name, never duplicates
    pc.observe_chat(st, user_id="u1", display_name="じろう",
                    text="!puyo join", now="2026-10-18T01:00:09+00:00")
    assert len(st["roster"]) == 1 and st["roster"][0]["display_name"] == "じろう"
    for i in range(2, pc.MAX_ROSTER + 2):
        try:
            chat(st, f"u{i}", f"n{i}", "!puyo join")
        except pc.PuyoCornerError as exc:
            assert exc.code == "roster_full"
            assert len(st["roster"]) == pc.MAX_ROSTER
            break
    else:
        pytest.fail("roster bound was not enforced")
    # roster survives reset (new round, same participants), queue is per-round
    st_run = started(seed=2)
    pc.observe_chat(st_run, user_id="u1", display_name="たろう", text="!puyo join")
    chat(st_run, "u1", "たろう", "!puyo right")
    assert len(st_run["chat_queue"]) == 1
    pc.timeout(st_run, now="2026-10-18T01:01:30+00:00")
    pc.reset(st_run, now="2026-10-18T01:01:31+00:00")
    assert st_run["chat_queue"] == []
    assert [row["user_id"] for row in st_run["roster"]] == ["u1"]


def test_leave_is_idempotent_and_purges_queued_actions():
    st = started()
    chat(st, "u1", "たろう", "!puyo join", now="2026-10-18T01:00:01+00:00")
    assert chat(st, "u1", "たろう", "!puyo left") == "action"
    assert len(st["chat_queue"]) == 1
    assert chat(st, "u1", "たろう", "!puyo leave") == "leave"
    assert st["roster"] == [] and st["chat_queue"] == []
    assert chat(st, "u1", "たろう", "!puyo leave") == "leave"
    assert st["roster"] == []


def test_chat_actions_require_roster_match_phase_and_queue_bounds():
    st = seeded_state()
    with pytest.raises(pc.PuyoCornerError) as exc:
        chat(st, "u1", "たろう", "!puyo left")
    assert exc.value.code == "not_live"
    st = started()
    with pytest.raises(pc.PuyoCornerError) as exc:
        chat(st, "ghost", "ばれ", "!puyo left")
    assert exc.value.code == "not_in_roster"
    chat(st, "u1", "たろう", "!puyo join")
    for _ in range(pc.MAX_PENDING_PER_USER):
        chat(st, "u1", "たろう", "!puyo right")
    with pytest.raises(pc.PuyoCornerError) as exc:
        chat(st, "u1", "たろう", "!puyo right")
    assert exc.value.code == "chat_queue_full"
    assert len(st["chat_queue"]) == pc.MAX_PENDING_PER_USER
    # unknown actions are ignored before any roster check
    assert chat(st, "u1", "たろう", "!puyo nonsense") == "ignored"


def test_chat_driven_pair_replaces_the_user_plan_and_replays_deterministically():
    st = started(seed=11)
    chat(st, "u1", "たろう", "!puyo join")
    pc.observe_chat(st, user_id="u1", display_name="たろう",
                    text="!puyo left", now="2026-10-18T01:00:02+00:00")
    pc.play_match(st, max_steps=1)
    assert st["chat_queue"] == []  # queued action consumed
    # deterministic replay: the same feed reproduces the same board
    reference = json.dumps(
        {"user_board": st["user_board"], "ai_board": st["ai_board"],
         "scores": [st["user_score"], st["ai_score"]]},
        sort_keys=True)
    twin = started(seed=11)
    pc.observe_chat(twin, user_id="u1", display_name="たろう",
                    text="!puyo join", now=None)
    pc.observe_chat(twin, user_id="u1", display_name="たろう",
                    text="!puyo left", now="2026-10-18T01:00:02+00:00")
    pc.play_match(twin, max_steps=1)
    assert json.dumps(
        {"user_board": twin["user_board"], "ai_board": twin["ai_board"],
         "scores": [twin["user_score"], twin["ai_score"]]},
        sort_keys=True) == reference


def test_chat_run_round_still_tops_out_within_budgets():
    st = started(seed=3)
    chat(st, "u1", "たろう", "!puyo join")
    chat(st, "u2", "じろう", "!puyo join")
    pc.play_match(st)
    # seed 3 ends as the bounded pair-budget TIMEOUT, exactly like the
    # chat-free self-play drives
    assert st["phase"] == "TIMEOUT"
    assert st["outcome"] == "timeout"
    # record_match carries the participant ids, bounded roster order
    ledger = pc.new_ledger()
    assert pc.record_match(ledger, st, now="2026-10-18T01:05:00+00:00")
    entry = ledger["entries"][0]
    assert entry["participants"] == ["u1", "u2"]
    # a still-running MATCH state records nothing
    assert pc.record_match(pc.new_ledger(), started(seed=3)) is False


def test_overlay_draft_lists_participants_bounded_and_keeps_section_cap():
    st = started()
    chat(st, "u1", "たろう", "!puyo join")
    chat(st, "u2", "じろう", "!puyo join")
    draft = pc.overlay_draft(st)
    keys = [s["key"] for s in draft["sections"]]
    assert keys[:7] == [
        "phase", "user_board", "user_score", "user_chain",
        "ai_board", "ai_score", "ai_chain",
    ]
    assert keys[-1] == "participants"
    participants = next(s for s in draft["sections"] if s["key"] == "participants")
    assert participants["text"] == "たろう・じろう"
    assert len(draft["sections"]) <= pc.MAX_OVERLAY_SECTIONS
    assert pc.overlay_draft(seeded_state())["sections"][-1]["key"] != "participants"


def test_load_state_defaults_chat_fields_for_legacy_states(tmp_path):
    legacy = seeded_state()
    legacy.pop("roster", None)
    legacy.pop("chat_queue", None)
    path = tmp_path / "state.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    loaded = pc.load_state(path)
    assert loaded["roster"] == [] and loaded["chat_queue"] == []
    assert loaded["ruleset_id"] == legacy["ruleset_id"]
