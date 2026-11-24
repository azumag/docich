"""きりふだ・たいきゃくが灰色で おくのて だけが生きた箱は B で閉じない。

g1163 2026-11-13 09:1x (chapter 9, 城防衛の召喚戦) の実測: きりふだ y176 /
たいきゃく y192 が灰色、おくのて y208 だけが白で、カーソルは灰色の きりふだ
行にあった。光る行が おくのて だけなので ``hidden_battle_commands``
(3行すべて灰色) にも ``disabled_item_box`` (たまご+きりふだ の2行) にも当たらず、
通常の戦闘メニューとして B を送り続けた。B はこの箱を閉じないため、同じ画面の
まま 5時間 戦闘が進まなかった (decisions 4832 件が battle_menu+b)。

実画面 (fixture) では下方向が効いて おくのて 行へ移動でき、確定1回で戦闘が
再開した。この回帰は、その「下げて1回だけ確定する」有界手順を固定する。
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

from docich.hanjuku.flows import battle_menus
from docich.hanjuku.os_layer import screen as screen_mod
from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.pixels import read_png

FIXTURES = Path(__file__).parent / "fixtures" / "hanjuku"
DEAD_ROW = FIXTURES / "g1163-okunote-only-box.png"
OKUNOTE_ROW = FIXTURES / "g1163-okunote-only-box-cursor.png"


def _screen(path, phase=None):
    frame = read_png(path).resized(256, 224)
    parsed = screen_mod.parse(frame, phase=phase or classify(frame))
    return frame, parsed


def _battle(**extra):
    cur = {
        "ally": "グレナデン", "enemy": "ビシソワーズ", "side": "defense",
        "castle": "オオキク", "ally_hp": 0, "enemy_hp": 27,
        "egg_battle": True, "okunote_summon_exhausted": True,
        "okunote_summon_exhausted_confirm": True,
        "okunote_flow": {"ticks": 1},
    }
    cur.update(extra)
    return {"battle": cur, "_records": []}


class TestOkunoteOnlyBoxDetection:
    def test_the_measured_frame_is_read_as_the_okunote_only_box(self):
        _, parsed = _screen(DEAD_ROW)
        # The coarse phase is the generic 'event' fallback; the command box is
        # still identified from the light row and the knight cursor.
        assert classify(read_png(DEAD_ROW).resized(256, 224)) == "event"
        assert parsed.kind == "battle_menu"
        assert parsed.okunote_only_box is True
        assert parsed.menu_cursor == 176
        assert [r.spans() for r in parsed.menu_rows] == [[(176, "おくのて")]]
        # Both summoned monsters are readable, so the box is not a guess.
        assert [(r.name, r.hp, r.side) for r in parsed.egg_rows] == [
            ("ポワソン", 80, "ally"), ("ドラゴンフライ", 263, "enemy")]

    def test_the_cursor_row_after_two_downs_is_still_the_same_box(self):
        _, parsed = _screen(OKUNOTE_ROW)
        assert parsed.okunote_only_box is True
        assert parsed.menu_cursor == 208

    def test_a_three_row_grey_box_is_not_this_box(self):
        # hidden_battle_commands (たまご/きりふだ/たいきゃく の3行) has its own
        # bounded scroll path; it must not be captured by the new flag.
        fake = mock.Mock(kind="battle_menu", menu_cursor=208,
                         menu_rows=[screen_mod.TextLine(208, ((176, "おくのて"),))])
        grey = [screen_mod.TextLine(176, ((176, "たまごをつかう"),)),
                screen_mod.TextLine(192, ((176, "きりふだ"),)),
                screen_mod.TextLine(208, ((176, "たいきゃく"),))]
        with mock.patch.object(screen_mod, "read_lines", return_value=grey):
            assert screen_mod._okunote_only_box(mock.Mock(), fake) is False

    def test_a_lit_row_above_okunote_is_not_this_box(self):
        fake = mock.Mock(kind="battle_menu", menu_cursor=176,
                         menu_rows=[screen_mod.TextLine(192, ((176, "きりふだ"),)),
                                    screen_mod.TextLine(208, ((176, "おくのて"),))])
        grey = [screen_mod.TextLine(176, ((176, "たまごをつかう"),))]
        with mock.patch.object(screen_mod, "read_lines", return_value=grey):
            assert screen_mod._okunote_only_box(mock.Mock(), fake) is False


class TestOkunoteOnlyBoxStep:
    def test_the_dead_row_moves_to_the_only_live_row(self):
        frame, parsed = _screen(DEAD_ROW)
        parsed.frame_sha256 = frame.digest()
        mem = _battle()
        assert battle_menus.battle_menu_step(parsed, mem) == [
            battle_menus.pad("down")]
        assert parsed.okunote_only_box is True

    def test_the_live_row_is_confirmed_once(self):
        frame, parsed = _screen(OKUNOTE_ROW)
        parsed.frame_sha256 = frame.digest()
        mem = _battle()
        assert battle_menus.battle_menu_step(parsed, mem) == [
            battle_menus.pad("a")]
        records = [r for r in mem["_records"]
                   if r.get("decision") == "battle_okunote_only_box_confirm"]
        assert len(records) == 1
        assert records[0]["resulting_event"] == "irreversible_risk_accepted"
        assert records[0]["observed_metric"]["cursor_y"] == 208

    def test_the_same_frame_holds_instead_of_pressing_b(self):
        frame, parsed = _screen(OKUNOTE_ROW)
        parsed.frame_sha256 = frame.digest()
        mem = _battle()
        cur = mem["battle"]
        assert battle_menus._okunote_only_box_step(parsed, mem, cur) == [
            battle_menus.pad("a")]
        # The same picture means the A did not land: wait, never fall back to
        # the B that is measured to do nothing on this box.
        for _ in range(2):
            assert battle_menus._okunote_only_box_step(parsed, mem, cur) == []
        # A bounded resend is allowed, then the box is handed to normal judging.
        assert battle_menus._okunote_only_box_step(parsed, mem, cur) == [
            battle_menus.pad("a")]
        # The resend is also unacknowledged: hold the frame, never press B.
        for _ in range(2):
            assert battle_menus._okunote_only_box_step(parsed, mem, cur) == []
        assert battle_menus._okunote_only_box_step(parsed, mem, cur) is None

    def test_the_down_budget_is_bounded(self):
        frame, parsed = _screen(DEAD_ROW)
        parsed.frame_sha256 = frame.digest()
        mem = _battle()
        mem["battle"]["okunote_only_box_step"] = {"downs": 0, "confirms": 0,
                                                 "sha": None, "waits": 0}
        cur = mem["battle"]
        assert battle_menus._okunote_only_box_step(parsed, mem, cur) == [
            battle_menus.pad("down")]
        cur["okunote_only_box_step"]["downs"] = battle_menus.OKUNOTE_ONLY_BOX_DOWNS
        # The cursor never reached the live row: do not guess another row.
        assert battle_menus._okunote_only_box_step(parsed, mem, cur) is None
        assert any(r.get("decision") == "battle_okunote_only_box_unread"
                   for r in mem["_records"])

    def test_the_exhausted_okunote_path_uses_the_same_bounded_step(self):
        # The exhausted latch alone returned None (-> generic B). With the new
        # flag the flow reaches the live row even when the summon reading fails.
        frame, parsed = _screen(OKUNOTE_ROW)
        parsed.frame_sha256 = frame.digest()
        mem = _battle()
        with mock.patch.object(battle_menus, "okunote", create=True), \
                mock.patch("docich.hanjuku.flows.okunote.menu_pending",
                           return_value=None), \
                mock.patch("docich.hanjuku.flows.okunote.summon_reading",
                           return_value=None):
            actions = battle_menus.okunote_step(parsed, mem)
        assert actions == [battle_menus.pad("a")]
