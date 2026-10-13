"""All-captured chapter 2 must retain the measured boss navigation order."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_chart as chart
from docich import hanjuku_policy as policy
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse

FIXTURES = Path(__file__).parent / 'fixtures' / 'hanjuku'
BOSS_DESCRIPTION = 'みずのひいたあとにしろがあらわれました!'


def ready_memory():
    return {'chapter': 2, 'month': '3-7', 'tick': 5000,
            'captured': [n for n in chart.castles(2)
                         if n not in (chart.home_castle(2), chart.boss_castle(2))],
            'garrison': {'スペランザ': ['ゼウス', chart.HERO]},
            'orders': {o['step']: 'done' for o in chart.orders(2)
                       if o['target'] != chart.boss_castle(2)}}


def test_all_ordinary_castles_captured_selects_the_base_boss_order():
    mem = ready_memory()
    order = policy.next_order(mem)
    assert order is not None and order['step'] == '2-B1'
    assert order['target'] == 'ボス'
    # Coordinate registration must preserve the existing strict kit guard.
    assert policy._strict_boss_cards(order, mem)
    assert policy._deploy_cards(order, mem) == list(order['cards'])


def test_boss_remains_gated_until_last_ordinary_castle_is_captured():
    mem = ready_memory()
    mem['captured'].remove('アウスパジア')
    boss = next(o for o in chart.orders(2) if o['step'] == '2-B1')
    assert not policy._ready(boss, mem)
    assert policy.next_order(mem) is None


def test_native_ring_confirms_the_measured_boss_cell():
    frame = read_png(FIXTURES / 'chapter-2-boss-y-focus.png')
    cursor = policy.world_cursor(frame)
    assert cursor == (128.0, 72.5)
    offset = policy.Y_JUMP_OFFSET[2]
    assert chart.castles(2)['ボス'] == tuple(
        int((cursor[i] - offset[i]) * 8) for i in range(2))
    mem = ready_memory()
    mem['y_jump'] = {'goal': 'ボス', 'mode': 'target', 'step': '2-B1',
                     'moves': 1, 'wait': 0, 'tick': mem['tick']}
    assert policy._y_jump_step(mem, frame) == [policy.pad('a')]
    assert mem['y_jumped']['2-B1'] == 'ボス'
    assert mem['cursor'] == list(chart.castles(2)['ボス'])


def test_native_boss_click_description_and_off_tower_control_differ():
    # Isolated saved-core input: tower ring (128,72.5), A opens this exact
    # description six times. Off-tower ring (132,74.5), A leaves the map.
    description = parse(read_png(FIXTURES / 'chapter-2-boss-description.png'))
    field = parse(read_png(FIXTURES / 'chapter-2-boss-cursor.png'))
    control = parse(read_png(FIXTURES / 'chapter-2-boss-off-tower.png'))
    assert description.kind == 'text' and description.text == BOSS_DESCRIPTION
    assert field.kind == control.kind == 'map'
    assert field.cursor == control.cursor == (135, 117)
    assert control.text != BOSS_DESCRIPTION


def test_removing_unmeasured_boss_still_gates_navigation(monkeypatch):
    castles = {k: v for k, v in chart.castles(2).items() if k != 'ボス'}
    monkeypatch.setitem(chart.CASTLES, 2, castles)
    assert all(o['target'] != 'ボス' for o in chart.orders(2))
    assert policy.next_order(ready_memory()) is None
