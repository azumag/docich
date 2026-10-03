"""自軍のたまごは「白兵で勝てそうなら温存」— HP と兵士数で判定する。

- 兵士は戦闘フィールドのスプライトを数える (味方が左・敵が右)。
- 側ごとに重なりを見つけたらその側は None (読めない=推測しない)。
- 合戦力が敵の7割を超えていて、自軍HPも最大の7割を超えている時だけ温存。
- 片側の兵士が読めない戦闘では HP 比較へ退ける (推測しない)。
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.hanjuku_screen import _field_soldiers, parse
from docich import hanjuku_policy as p
from test_hanjuku_chart_bot import Canvas
from test_hanjuku_survival import (battle, memory, wounded_egg_memory,
                                   wounded_egg_screen)


# 実測で上位に入る背景4色 (band 内の最頻4色を背景として除く)。兵士色は
# 5番目以下に置かないと背景扱いされて数えられないので、帯域は4色で埋める。
BAND_TONES = ((16, 72, 57), (41, 97, 164), (24, 48, 106), (123, 141, 148))
SOLDIER = (255, 80, 80)


def _paint_band(canvas):
    for y in range(40, 150):
        for x in range(256):
            canvas.put(x, y, BAND_TONES[x // 64])


def _bodies(canvas, bodies):
    for x, y, w, h in bodies:
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                canvas.put(xx, yy, SOLDIER)


ALLIES = ((20, 60, 10, 10), (40, 60, 10, 10), (20, 84, 10, 10),
          (40, 84, 10, 10), (20, 108, 10, 10), (40, 108, 10, 10))
ENEMIES = ((160, 60, 10, 10),)


def field_frame(bodies):
    c = Canvas((238, 238, 238))
    _paint_band(c)
    _bodies(c, bodies)
    return c.frame()


def test_field_soldier_sprites_are_counted_allies_left_and_enemies_right():
    # HPパネルは「敵が左」だが、フィールドは味方が左 (実測: 勝利フレームで
    # 右が空、敗戦フレームで左が空)。返り値は (味方, 敵)。
    assert _field_soldiers(field_frame(ALLIES + ENEMIES)) == (6, 1)
    assert _field_soldiers(field_frame(ENEMIES)) == (0, 1)
    assert _field_soldiers(field_frame(ALLIES)) == (6, 0)
    # 1体ぶん未満 (面積55未満) は兵士ではない。
    assert _field_soldiers(field_frame(((20, 60, 5, 5),))) == (0, 0)
    # 帯域の左端に接する成分は壁なので数えない。
    assert _field_soldiers(field_frame(((0, 60, 10, 10),))) == (0, 0)


def test_overlapping_soldiers_make_that_side_unknown_not_a_guess():
    # 20x20=400 は「2体以上が重なった」帯 (面積261..900)。その側は None。
    assert _field_soldiers(field_frame(((20, 60, 20, 20),) + ENEMIES)) == (None, 1)
    assert _field_soldiers(field_frame(ALLIES + ((160, 60, 20, 20),))) == (6, None)


def test_soldiers_are_read_only_off_a_real_battle_frame():
    c = Canvas((238, 238, 238))
    _paint_band(c)
    _bodies(c, ALLIES + ENEMIES)
    c.text(16, 176, 'ミント', (32, 32, 32))
    c.text(96, 176, '32', (32, 32, 32))
    c.text(144, 176, 'どうし', (32, 32, 32))
    c.text(224, 176, '90', (32, 32, 32))
    screen = parse(c.frame())
    assert screen.battle is not None
    assert screen.field_soldiers == (6, 1)
    # 通常のコマンドメニュー (battle なし) では前回値を上書きしない。
    assert parse(field_frame(ALLIES + ENEMIES)).battle is None
    assert parse(field_frame(ALLIES + ENEMIES)).field_soldiers is None


def test_battle_step_keeps_the_measured_counts_on_the_battle():
    mem = memory(hp=60, enemy=60)
    screen = battle(mem)
    screen.field_soldiers = (6, 1)
    p.battle_step(screen, mem)
    assert (mem['battle']['ally_soldiers'], mem['battle']['enemy_soldiers']) == (6, 1)
    # 読めない側は None のまま。以降の判定は HP 比較へ退ける。
    screen.field_soldiers = (6, None)
    p.battle_step(screen, mem)
    assert (mem['battle']['ally_soldiers'], mem['battle']['enemy_soldiers']) == (6, None)


def egg_memory(ally_hp, enemy_hp, soldiers):
    mem = wounded_egg_memory()
    mem['battle']['enemy_hp'] = enemy_hp
    screen = battle(mem)
    if soldiers is not None:
        screen.field_soldiers = soldiers
        p.battle_step(screen, mem)
    mem['battle']['ally_hp'] = ally_hp
    return mem


def egg_screen(hp):
    screen = wounded_egg_screen(hp)
    screen.text += 'たまごをつかう'
    return screen


def egg_need(mem, hp):
    """この局面で `_own_egg_needed` が下す判断と証拠。"""
    reading = p._egg_general_reading(egg_screen(hp), mem, mem['battle'])
    return p._own_egg_needed(mem, mem['battle'], reading)


def test_soldiers_can_flip_an_even_hp_fight_to_a_preserved_egg():
    # ヴィーナス60/82 vs 敵90 は HP だけだと 600 <= 630 で敵優位、温存できない。
    # 兵士6人=HP60 が加わって初めて 1200 > 700 になる (兵士1人=HP10)。
    needed, evidence = egg_need(egg_memory(60, 90, None), 60)
    assert needed is True and evidence['rule'] == 'hp_only'
    needed, evidence = egg_need(egg_memory(60, 90, (6, 1)), 60)
    assert needed is False and evidence['rule'] == 'soldier_force'
    assert (evidence['ally_force'], evidence['enemy_force']) == (120, 100)


def test_one_side_unknown_falls_back_to_hp_only_even_with_a_count():
    needed, evidence = egg_need(egg_memory(60, 90, (6, None)), 60)
    assert needed is True and evidence['rule'] == 'hp_only'
    assert evidence['ally_soldiers'] is None and evidence['enemy_soldiers'] is None


def test_a_wounded_general_uses_the_egg_even_when_the_enemy_hp_is_lower():
    # 30/82 は敵将軍29より残っているが、自軍は最大の7割を切っている。
    mem = egg_memory(30, 29, None)
    needed, evidence = egg_need(mem, 30)
    assert needed is True and evidence['rule'] == 'ally_wounded'
    assert evidence['ally_max_hp'] == 82 and evidence['healthy'] is False
    assert p.egg_battle_step(egg_screen(30), mem) == [p.pad('down')]
    assert mem['egg_needed'] is True
    assert any(r.get('strategy_variant') == 'egg_battle_use_egg'
               for r in mem['_records'])


def test_egg_battle_starts_by_preserving_the_egg_when_white_melee_is_enough():
    mem = egg_memory(60, 60, (6, 1))
    assert p.egg_battle_step(egg_screen(60), mem) == [p.pad('a')]
    assert mem['egg_action'] == 'attack' and mem['egg_needed'] is False
    assert any(r.get('strategy_variant') == 'egg_battle_attack' for r in mem['_records'])
    # 勝てそうなので g460 の退却ゲートは開かない (まず白兵で戦う)。
    assert not any(r['decision'] == 'battle_egg_retreat_attempt' for r in mem['_records'])


def test_a_later_bad_reading_escalates_to_the_egg_but_never_back():
    mem = egg_memory(60, 60, (6, 1))
    assert p.egg_battle_step(egg_screen(60), mem) == [p.pad('a')]
    assert mem['egg_action'] == 'attack'
    # 自軍が60→30に削られ、最大82の7割を切った。片側のHP優位は保っている。
    assert p.egg_battle_step(egg_screen(30), mem) == [p.pad('down')]
    assert mem['egg_action'] == 'use_egg' and mem['egg_needed'] is True
    assert any(r.get('strategy_variant') == 'egg_battle_use_egg' for r in mem['_records'])
    # 回復しても温存へは戻さない (昇格は一方向)。
    assert p.egg_battle_step(egg_screen(60), mem) in ([p.pad('down')], [p.pad('a')])
    assert mem['egg_action'] == 'use_egg'


def test_g460_retreat_gate_still_fires_when_the_egg_is_required():
    # 卵が要る局面で札も卵も無い (たまご行が読めない) なら、まず通常の
    # 退却メニューへ戻る。
    mem = egg_memory(30, 29, None)
    mem['battle']['egg_retreat_attempts'] = 0
    assert p.egg_battle_step(wounded_egg_screen(30), mem) == [p.pad('b')]
    assert mem['battle']['egg_retreat_attempts'] == 1
    # 防衛戦では退却しない。
    mem['battle'].update(side='defense', egg_retreat_tried=False)
    assert p.egg_battle_step(wounded_egg_screen(30), mem) == [p.pad('down')]


def test_strong_general_and_unknown_battle_always_take_the_egg():
    needed, evidence = p._own_egg_needed({}, 'not a battle')
    assert needed is True and evidence['rule'] == 'unknown_battle'
    needed, evidence = p._own_egg_needed({}, {'enemy': 'クイーン', 'ally': 'ヴィーナス',
                                              'ally_hp': 82, 'enemy_hp': 1})
    assert needed is True and evidence['strong_general'] is True
    assert evidence['strong_rule'] == 'boss'
