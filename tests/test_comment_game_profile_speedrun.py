"""Speedrun comment profile: measured timer facts, rules memo and the launcher.

The speedrun fixtures are produced by the real RTA state machine
(``new_timer`` -> ``start_attempt`` -> ``confirm_split`` -> ``finish``/``dnf``)
and written by ``store``; no timer document is hand-written.  The original
``hanjuku-hero`` profile is pinned to its pre-change snapshot.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.comment import game_profile  # noqa: E402
from docich.hanjuku_speedrun.rta import store, timer  # noqa: E402

SPEEDRUN = game_profile.SPEEDRUN
BASE_MS = 1_700_000_000_000
RUNTIME = "g1300-abcdef12"
LAUNCHER = ROOT / "bin" / "docich-comment-profile"
ENABLED = '[hanjuku.chart_adjust]\nenabled = true\nagents = "a:b"\n'
DISABLED = '[hanjuku.chart_adjust]\nenabled = false\nagents = "a:b"\n'


# ---------------------------------------------------------------------------
# real RTA fixtures
# ---------------------------------------------------------------------------


def _state(tmp_path) -> Path:
    state = tmp_path / "run-soren-live"
    state.mkdir()
    return state


def _write_switch(state, *, game=SPEEDRUN, runtime=RUNTIME):
    (state / "game_switch.json").write_text(json.dumps({
        "schema_version": 1,
        "phase": "ready",
        "active": {"game": game, "adapter": "retroarch", "runtime_id": runtime},
    }), encoding="utf-8")


def _save(state, current, records, *, runtime=RUNTIME):
    runtime_dir = state / "runtimes" / runtime
    runtime_dir.mkdir(parents=True, exist_ok=True)
    store.save_timer(runtime_dir, current)
    store.save_records(runtime_dir, records)


def _finished_attempt(records, start_ms, segment_ms, *, runtime=RUNTIME):
    current = timer.new_timer(runtime, records, start_ms)
    current, records = timer.start_attempt(current, records, start_ms)
    now = start_ms
    for index in range(timer.SEGMENT_COUNT):
        now += segment_ms
        current, records = timer.confirm_split(
            current, records, now, index, "chapter_end_save")
    current, records = timer.finish(current, records, now)
    return current, records, now


def _running_attempt(records, start_ms, segment_times, *, runtime=RUNTIME):
    current = timer.new_timer(runtime, records, start_ms)
    current, records = timer.start_attempt(current, records, start_ms)
    now = start_ms
    for index, segment_ms in enumerate(segment_times):
        now += segment_ms
        current, records = timer.confirm_split(
            current, records, now, index, "chapter_end_save")
    return current, records, now


def _profile(state, now):
    profile = game_profile.build(SPEEDRUN, state_dir=state, now=now)
    assert profile is not None and profile["game"] == SPEEDRUN
    assert len(profile["game_state"]) <= 300
    return profile


def _assert_no_facts(text):
    assert text == game_profile.SPEEDRUN_NO_INFO
    assert re.search(r"\d", text) is None


# ---------------------------------------------------------------------------
# game_state from the real timer documents
# ---------------------------------------------------------------------------


def test_pending_attempt_reports_start_before_and_the_measured_pb(tmp_path):
    state = _state(tmp_path)
    _write_switch(state)
    _, records, _ = _finished_attempt(timer.default_records(BASE_MS), BASE_MS, 60_000)
    pending = timer.new_timer(RUNTIME, records, BASE_MS + 20_000_000)
    _save(state, pending, records)

    text = _profile(state, (BASE_MS + 20_001_000) / 1000)["game_state"]
    assert "試走の開始前" in text
    assert "PB 0:12:00" in text
    assert "経過" not in text
    assert "計測情報なし" not in text


def test_running_attempt_with_pb_reports_elapsed_split_and_deltas(tmp_path):
    state = _state(tmp_path)
    _write_switch(state)
    _, records, _ = _finished_attempt(timer.default_records(BASE_MS), BASE_MS, 60_000)
    start = BASE_MS + 10_000_000
    current, records, _ = _running_attempt(records, start, [50_000, 65_000])
    _save(state, current, records)

    text = _profile(state, (start + 120_000) / 1000)["game_state"]
    assert "第3話を走行中" in text
    assert "経過 0:02:00" in text
    assert "PB 0:12:00" in text
    assert "第2話終了時点でPB比 −0:05" in text
    assert "直前の第2話は 0:01:05、PB時の区間より遅い" in text
    assert "PB比 +0:05" in text
    assert "Sum of Best 0:11:50" in text


def test_running_attempt_without_pb_says_there_is_no_pb(tmp_path):
    state = _state(tmp_path)
    _write_switch(state)
    current, records, _ = _running_attempt(
        timer.default_records(BASE_MS), BASE_MS, [50_000])
    _save(state, current, records)

    text = _profile(state, (BASE_MS + 60_000) / 1000)["game_state"]
    assert "第2話を走行中" in text
    assert "直前の第1話は 0:00:50、区間ベスト更新" in text
    assert "PBはまだ無い" in text
    assert "PB比" not in text
    assert "Sum of Best" not in text


def test_finished_attempt_that_updates_the_pb_is_reported(tmp_path):
    state = _state(tmp_path)
    _write_switch(state)
    _, records, _ = _finished_attempt(timer.default_records(BASE_MS), BASE_MS, 60_000)
    current, records, now = _finished_attempt(records, BASE_MS + 1_000_000, 55_000)
    _save(state, current, records)

    text = _profile(state, (now + 1000) / 1000)["game_state"]
    assert "完走してPB更新" in text
    assert "記録 0:11:00" in text
    assert "PB 0:11:00" in text
    assert "区間ベスト更新" in text
    assert "Sum of Best 0:11:00" in text


def test_finished_attempt_slower_than_the_pb_is_not_called_a_pb(tmp_path):
    state = _state(tmp_path)
    _write_switch(state)
    _, records, _ = _finished_attempt(timer.default_records(BASE_MS), BASE_MS, 60_000)
    current, records, now = _finished_attempt(records, BASE_MS + 1_000_000, 65_000)
    _save(state, current, records)

    text = _profile(state, (now + 1000) / 1000)["game_state"]
    assert "完走したがPBより遅い" in text
    assert "記録 0:13:00" in text
    assert "PB 0:12:00" in text
    assert "PB更新" not in text


@pytest.mark.parametrize("reason, label", [
    ("game_over", "ゲームオーバー"),
    ("stalled", "画面停滞"),
    ("stopped", "運用停止"),
])
def test_dnf_attempt_reports_the_measured_reason(tmp_path, reason, label):
    state = _state(tmp_path)
    _write_switch(state)
    current, records, now = _running_attempt(
        timer.default_records(BASE_MS), BASE_MS, [50_000])
    current, records = timer.dnf(current, records, now + 60_000, reason)
    _save(state, current, records)

    text = _profile(state, (now + 61_000) / 1000)["game_state"]
    assert f"{label}で打ち切り" in text
    assert "経過 0:01:50" in text
    assert "PBはまだ無い" in text


def test_a_merged_split_is_named_as_a_range_and_never_claims_a_segment_best(tmp_path):
    state = _state(tmp_path)
    _write_switch(state)
    _, records, _ = _finished_attempt(timer.default_records(BASE_MS), BASE_MS, 60_000)
    start = BASE_MS + 10_000_000
    current = timer.new_timer(RUNTIME, records, start)
    current, records = timer.start_attempt(current, records, start)
    # One observation covers chapters 1-2: a merged split.
    current, records = timer.confirm_split(
        current, records, start + 130_000, 1, "chapter_end_save")
    _save(state, current, records)

    text = _profile(state, (start + 140_000) / 1000)["game_state"]
    assert "第1〜2話" in text
    assert "区間ベスト更新" not in text


def test_blue_verdict_is_a_fast_segment_that_is_not_the_best(tmp_path):
    state = _state(tmp_path)
    _write_switch(state)
    _, records, _ = _finished_attempt(timer.default_records(BASE_MS), BASE_MS, 60_000)
    # A DNF improves chapter 1 to 40s without touching the PB.
    first, records, now = _running_attempt(records, BASE_MS + 1_000_000, [40_000])
    first, records = timer.dnf(first, records, now, "game_over")
    start = BASE_MS + 2_000_000
    current, records, _ = _running_attempt(records, start, [50_000])
    _save(state, current, records)

    text = _profile(state, (start + 60_000) / 1000)["game_state"]
    assert "PB時の区間より速い" in text
    assert "PB比 −0:10" in text


def test_white_without_pb_is_reported_as_no_comparison(tmp_path):
    state = _state(tmp_path)
    _write_switch(state)
    first, records, now = _running_attempt(
        timer.default_records(BASE_MS), BASE_MS, [50_000])
    first, records = timer.dnf(first, records, now, "game_over")
    start = BASE_MS + 1_000_000
    current, records, _ = _running_attempt(records, start, [60_000])
    _save(state, current, records)

    text = _profile(state, (start + 70_000) / 1000)["game_state"]
    assert "直前の第1話は 0:01:00、比較なし" in text
    assert "PB時の区間より" not in text
    assert "PBはまだ無い" in text


def test_speedrun_profile_text_has_no_template_or_shell_metacharacters(tmp_path):
    state = _state(tmp_path)
    _write_switch(state)
    _, records, _ = _finished_attempt(timer.default_records(BASE_MS), BASE_MS, 60_000)
    start = BASE_MS + 10_000_000
    current, records, _ = _running_attempt(records, start, [65_000])
    _save(state, current, records)

    profile = _profile(state, (start + 70_000) / 1000)
    texts = [profile["game_state"], profile["ui_memo"], profile["game_rules"],
             profile["game_rules_ja"], *profile["ops_lines"],
             *(pair[1] for pair in profile["persona_replace"])]
    for text in texts:
        for ch in "$`\\<>\x7f":
            assert ch not in text


# ---------------------------------------------------------------------------
# no measurable facts: nothing numeric may be invented
# ---------------------------------------------------------------------------


def test_missing_timer_is_no_information(tmp_path):
    state = _state(tmp_path)
    _write_switch(state)
    _assert_no_facts(_profile(state, BASE_MS / 1000)["game_state"])


def test_corrupt_timer_is_no_information_and_is_not_quarantined(tmp_path):
    state = _state(tmp_path)
    _write_switch(state)
    runtime_dir = state / "runtimes" / RUNTIME
    runtime_dir.mkdir(parents=True)
    path = store.timer_path(runtime_dir)
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")

    _assert_no_facts(_profile(state, BASE_MS / 1000)["game_state"])
    assert path.read_text(encoding="utf-8") == "{broken"
    assert not list(path.parent.glob("timer.json.corrupt-*"))


def test_stale_running_heartbeat_is_no_information(tmp_path):
    state = _state(tmp_path)
    _write_switch(state)
    current, records, heartbeat = _running_attempt(
        timer.default_records(BASE_MS), BASE_MS, [50_000])
    _save(state, current, records)

    _assert_no_facts(_profile(state, (heartbeat + 31_000) / 1000)["game_state"])
    # One second inside the bound is still shown.
    fresh = _profile(state, (heartbeat + 29_000) / 1000)["game_state"]
    assert "第2話を走行中" in fresh


def test_timer_for_another_runtime_is_no_information(tmp_path):
    state = _state(tmp_path)
    _write_switch(state, runtime="g999-ffffffff")
    current, records, _ = _running_attempt(
        timer.default_records(BASE_MS), BASE_MS, [50_000])
    _save(state, current, records)

    _assert_no_facts(_profile(state, (BASE_MS + 60_000) / 1000)["game_state"])


def test_active_other_game_is_no_information(tmp_path):
    state = _state(tmp_path)
    _write_switch(state, game="hanjuku-hero")
    current, records, _ = _running_attempt(
        timer.default_records(BASE_MS), BASE_MS, [50_000])
    _save(state, current, records)

    _assert_no_facts(_profile(state, (BASE_MS + 60_000) / 1000)["game_state"])


@pytest.mark.parametrize("switch", [None, {}, {"active": None}, {"active": "hanjuku-speedrun"}])
def test_missing_or_non_object_active_game_is_no_information(tmp_path, switch):
    state = _state(tmp_path)
    if switch is not None:
        (state / "game_switch.json").write_text(json.dumps(switch), encoding="utf-8")
    current, records, _ = _running_attempt(
        timer.default_records(BASE_MS), BASE_MS, [50_000])
    _save(state, current, records)

    _assert_no_facts(_profile(state, (BASE_MS + 60_000) / 1000)["game_state"])


def test_corrupt_records_falls_back_to_the_timer_comparison(tmp_path):
    state = _state(tmp_path)
    _write_switch(state)
    _, records, _ = _finished_attempt(timer.default_records(BASE_MS), BASE_MS, 60_000)
    start = BASE_MS + 10_000_000
    current, records, _ = _running_attempt(records, start, [65_000])
    _save(state, current, records)
    records_path = store.records_path(state / "runtimes" / RUNTIME)
    records_path.write_text("{broken", encoding="utf-8")

    text = _profile(state, (start + 70_000) / 1000)["game_state"]
    assert "PB 0:12:00" in text
    assert "PB比" in text
    assert records_path.read_text(encoding="utf-8") == "{broken"


def test_no_state_dir_is_no_information():
    _assert_no_facts(game_profile.build(SPEEDRUN, state_dir=None)["game_state"])


# ---------------------------------------------------------------------------
# original corner must not change
# ---------------------------------------------------------------------------


def _config(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_original_hanjuku_profile_is_byte_for_byte_unchanged(tmp_path):
    config = _config(tmp_path, "hanjuku-hero.toml", ENABLED)
    profile = game_profile.build("hanjuku-hero", config=config, state_dir=tmp_path)
    assert set(profile) == {"schema", "game", "persona_replace", "game_state",
                            "ui_memo", "game_rules", "game_rules_ja", "ops_lines"}
    assert profile["schema"] == 1 and profile["game"] == "hanjuku-hero"
    assert profile["persona_replace"] == [list(pair) for pair in game_profile.PERSONA_REPLACE]
    assert profile["game_state"] == (
        "現在のメイン画面: hanjuku-hero — 半熟英雄(SFCエミュレータ)。"
        "ソ連ゲームのスコア・盤面・ピース・建国統計はこのゲームの状況ではありません。"
        "現在の戦闘・勝敗・操作の情報はこのメモにはありません。推測して補わないでください。")
    assert profile["ui_memo"] == ""
    assert profile["game_rules"] == (
        "【Current Game Rules (半熟英雄)】\n"
        "There is no rules memo for the current game. Never explain it with the Soviet game's rules "
        "(countries, merging, score). If a viewer asks about its rules and the memo has none, "
        "say you do not have that information instead of guessing.")
    assert profile["game_rules_ja"] == (
        "\t【現在のゲームの基本ルール（半熟英雄）】\n"
        "\t※ 現在のゲームの基本ルールのメモはありません。ソ連ゲームの国・併合・得点のルールで説明しないこと。\n"
        "\t※ ルールを聞かれても、メモにないことは推測で答えず、分からないと伝えること。")
    assert profile["ops_lines"] == [
        *game_profile.HANJUKU_HOW_IT_WORKS, game_profile.HANJUKU_LLM_ON]


def test_original_hanjuku_state_does_not_read_the_speedrun_timer(tmp_path, monkeypatch):
    state = _state(tmp_path)
    _write_switch(state)
    current, records, _ = _running_attempt(
        timer.default_records(BASE_MS), BASE_MS, [50_000])
    _save(state, current, records)

    def forbidden():
        raise AssertionError("speedrun profile read from the hanjuku-hero path")

    monkeypatch.setattr(game_profile, "speedrun_state", forbidden)
    profile = game_profile.build("hanjuku-hero", state_dir=state)
    assert "半熟英雄(SFCエミュレータ)" in profile["game_state"]


# ---------------------------------------------------------------------------
# rules, ops lines and config selection
# ---------------------------------------------------------------------------


def test_speedrun_rules_match_the_design_and_have_no_metacharacters():
    profile = game_profile.build(SPEEDRUN, state_dir=None)
    en, ja = profile["game_rules"], profile["game_rules_ja"]
    for needle in ("12-chapter", "chapter-end save", "めでたし", "PB", "segment best",
                   "Sum of Best", "yellow", "blue", "red", "game over",
                   "stalled", "about 1 second", "Soviet game's rules"):
        assert needle in en, needle
    for needle in ("12話", "章末セーブ確認", "めでたし", "PB", "区間ベスト", "Sum of Best",
                   "黄=区間ベスト更新", "青=区間ベストではないがPB時の区間より速い",
                   "赤=PB時の区間より遅い", "ゲームオーバー", "画面停滞", "最初から走り直す",
                   "約1秒", "ソ連ゲーム", "推測で答えず"):
        assert needle in ja, needle
    for text in (en, ja):
        assert len(text) <= 1500
        assert not any(ch in text for ch in "$`\\<>\x7f")
    assert all(line.startswith("\t") for line in ja.split("\n"))


def test_speedrun_rules_do_not_reuse_the_soviet_game_rules():
    profile = game_profile.build(SPEEDRUN, state_dir=None)
    en, ja = profile["game_rules"], profile["game_rules_ja"]
    assert "Never explain this with the Soviet game's rules" in en
    assert "ソ連ゲームの国・併合・得点のルールで説明しない" in ja
    for text in (en, ja):
        assert "16" not in text and "アルメニア" not in text and "Armenia" not in text


def test_speedrun_ops_lines_put_the_measuring_corner_note_after_the_intro(tmp_path):
    config = _config(tmp_path, "hanjuku-speedrun.toml", ENABLED)
    profile = game_profile.build(SPEEDRUN, config=config)
    expected = game_profile.hanjuku_how_it_works(config)
    assert profile["ops_lines"] == [
        expected[0], game_profile.SPEEDRUN_OPS_LINE, *expected[1:]]
    assert profile["ops_lines"][-1] == game_profile.HANJUKU_LLM_ON


def test_speedrun_blurb_and_per_game_config_selection(tmp_path):
    from docich.comment.sorengame_context import (
        GAME_BLURBS,
        SorenGameContextProvider,
    )

    assert GAME_BLURBS[SPEEDRUN] == game_profile.SPEEDRUN_BLURB
    original = _config(tmp_path, "original.toml", ENABLED)
    speedrun = _config(tmp_path, "speedrun.toml", DISABLED)
    provider = SorenGameContextProvider(tmp_path, {
        "DOCICH_HANJUKU_CONFIG_FILE": str(original),
        "DOCICH_HANJUKU_SPEEDRUN_CONFIG_FILE": str(speedrun),
    })
    speedrun_profile = provider._game_profile(SPEEDRUN, state_dir=tmp_path)
    original_profile = provider._game_profile("hanjuku-hero", state_dir=tmp_path)
    assert speedrun_profile["ops_lines"][-1] == game_profile.HANJUKU_LLM_OFF
    assert original_profile["ops_lines"][-1] == game_profile.HANJUKU_LLM_ON


def test_launcher_emits_the_same_speedrun_profile(tmp_path):
    import time

    state = tmp_path / "state"
    state.mkdir()
    _write_switch(state)
    # The launcher reads the real clock, so the heartbeat must be fresh now.
    start = int(time.time() * 1000) - 60_000
    current, records, _ = _running_attempt(
        timer.default_records(start), start, [50_000])
    _save(state, current, records)
    config = _config(tmp_path, "hanjuku-speedrun.toml", ENABLED)

    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env.update({
        "DOCICH_STATE_DIR": str(state),
        "DOCICH_HANJUKU_SPEEDRUN_CONFIG_FILE": str(config),
    })
    result = subprocess.run([str(LAUNCHER), SPEEDRUN], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    profile = json.loads(result.stdout)
    expected = game_profile.build(SPEEDRUN, config=config, state_dir=state,
                                  now=None)
    assert profile["game"] == SPEEDRUN
    assert "第2話を走行中" in profile["game_state"]
    assert profile["ops_lines"] == expected["ops_lines"]
    assert profile["game_rules"] == expected["game_rules"]


def test_speedrun_is_a_reviewed_active_game_kind():
    from docich.comment_classifier import active_game

    assert SPEEDRUN in active_game.GAME_KINDS
    assert active_game.hint(SPEEDRUN, "action") == {
        "game_active": True, "active_game_kind": SPEEDRUN,
        "interaction_kind": "action"}
