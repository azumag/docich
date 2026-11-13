"""#4052 両軍召喚戦で将軍HPが読めないとき、召喚獣HPを判断材料として保存する。

g1163 の2行HPパネルは味方召喚獣ポワソン80/敵ドラゴンフライ263で、味方行が将軍名
ではないため summon_reading は None を返す。将軍HP(ally_hp/enemy_hp)は unknown の
まま上書きせず、召喚獣HPだけを summon_* として保存し劣勢判定に使う。
"""
from __future__ import annotations

from pathlib import Path

from docich.hanjuku.flows import okunote
from docich.hanjuku.judgment.battle import _behind, _defender_summon_last_resort
from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse

FIXTURE = Path(__file__).parent / "fixtures/hanjuku" / "g1163-okunote-only-box.png"


def _screen():
    frame = read_png(FIXTURE).resized(256, 224)
    return parse(frame, phase=classify(frame))


def _battle(**extra):
    cur = {
        "ally": "グレナデン",
        "enemy": "ビシソワーズ",
        "side": "defense",
        "castle": "オオキク",
        "ally_hp": 0,
        "enemy_hp": 27,
        "start_ally_hp": 0,
        "start_enemy_hp": 27,
        "egg_battle": True,
        "card_summon_observed": True,
    }
    cur.update(extra)
    return cur


def test_real_pair_panel_stores_summon_hp_without_touching_generals():
    screen = _screen()
    assert [(r.name, r.hp, r.side) for r in screen.egg_rows] == [
        ("ポワソン", 80, "ally"),
        ("ドラゴンフライ", 263, "enemy"),
    ]
    mem = {}
    cur = _battle()
    assert okunote.summon_reading(screen, mem, cur) is None
    reading = okunote.summon_monster_panel(screen, cur)
    assert reading == {
        "ally_hp": 80,
        "enemy_monster": "ドラゴンフライ",
        "enemy_monster_hp": 263,
    }
    assert cur["summon_ally"] == "ポワソン"
    assert cur["summon_ally_hp"] == 80
    assert cur["summon_enemy"] == "ドラゴンフライ"
    assert cur["summon_enemy_hp"] == 263
    # 将軍HPは unknown のまま。0 を「読めた0HP」として保存しない。
    assert cur["ally_hp"] == 0
    assert cur["enemy_hp"] == 27


def test_summon_pair_behind_uses_monster_hp_when_generals_unread():
    screen = _screen()
    cur = _battle()
    reading = okunote.summon_monster_panel(screen, cur)
    assert reading is not None
    assert _behind(cur) is True
    # 優勢な召喚獣ペアなら劣勢判定しない。
    cur2 = _battle()
    assert okunote.summon_monster_panel(screen, cur2)
    cur2["summon_ally_hp"] = 263
    cur2["summon_enemy_hp"] = 80
    assert _behind(cur2) is False


def test_defender_summon_last_resort_can_use_monster_pair_reading():
    screen = _screen()
    # 召喚獣ペア読取を前提に、既存の最終救済判定が召喚獣HP差を根拠に成立する。
    cur = _battle(
        chapter=9,
        side="defense",
        castle="オオキク",
        ally="グレナデン",
        enemy="ビシソワーズ",
        ally_hp=0,
        enemy_hp=27,
        start_ally_hp=81,
        start_enemy_hp=27,
        okunote_only_observed=True,
        card_flow=None,
        planned_cards=[],
        egg_battle=True,
        card_summon_observed=True,
    )
    reading = okunote.summon_monster_panel(screen, cur)
    assert reading == {
        "ally_hp": 80,
        "enemy_monster": "ドラゴンフライ",
        "enemy_monster_hp": 263,
    }
    # 実フレームの召喚獣HPペア(80 vs 263)は劣勢で、同じ読取を使う救済判定とも整合する。
    assert _behind(cur) is True
    rescue_reading = dict(reading, ally_hp=40)
    assert _defender_summon_last_resort({"chapter": 9}, cur, rescue_reading) is True
    # 一撃で倒せる敵召喚獣HPでは救済を開かない既存規律は維持する。
    assert _defender_summon_last_resort(
        {"chapter": 9}, cur, dict(reading, enemy_monster_hp=4)
    ) is False


def test_non_summon_names_do_not_create_pair_state():
    screen = _screen()
    # 敵が召喚獣名でないパネルはペアとして保存しない。
    screen.egg_rows[1].name = "未確認"
    cur = _battle()
    assert okunote.summon_monster_panel(screen, cur) is None
    assert "summon_ally_hp" not in cur
    assert "summon_enemy_hp" not in cur
