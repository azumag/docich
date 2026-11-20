"""Never confirm an attack onto a friendly-held boss cell.

Live g1288 2026-11-21: the wave generals stand on the boss track, and the
hero's attack on the boss's cell hit them instead (wins 24 with ten wave
generals lost, the panel naming トレビス/ポセイドン/コリアンダー). The boss
itself kept summoning; our own sorties were the real damage dealer.
"""
from types import SimpleNamespace

from docich.hanjuku.flows import boss_pursuit as bp
from docich.hanjuku.flows import map as M
from docich.hanjuku.os_layer.screen import Frame

BOSS = (800, 600)


def memory(**extra):
    mem = {
        'chapter': 9,
        'tick': 10000,
        'boss_cell': {'chapter': 9, 'cell': list(BOSS), 'tick': 9990, 'readings': 2},
        bp.LIST_KEY: {'ミント': {'step': 'A', 'launched': 9500}},
    }
    mem.update(extra)
    return mem


def test_a_general_on_the_boss_cell_blocks_the_attack():
    mem = memory()
    assert bp.friend_on_boss_cell(mem) is False
    mem[bp.LIST_KEY]['ミント']['ambush_cell'] = [808, 604]
    assert bp.friend_on_boss_cell(mem) is True
    mem[bp.LIST_KEY]['ミント']['ambush_cell'] = [700, 600]
    mem[bp.LIST_KEY]['ミント']['unit_world'] = [805, 598]
    assert bp.friend_on_boss_cell(mem) is True


def test_the_boss_target_check_refuses_a_friendly_held_cell():
    mem = memory()
    mem[bp.LIST_KEY]['ミント']['ambush_cell'] = [804, 602]
    frame = Frame(256, 224, b'\x00' * (256 * 224 * 3))
    screen = SimpleNamespace(kind='map_target', marker=None, cursor=(100, 100),
                             text='', hand=None, selected=None)
    order = {'step': 'A', 'target': 'ボス', 'source': 'イデ！'}
    assert M._target_roof_under_marker(screen, mem, frame, order) is False
    assert [r for r in mem.get('_records', [])
            if r['decision'] == 'boss_target_friendly_held']
