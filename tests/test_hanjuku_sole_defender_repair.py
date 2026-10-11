"""g684 1-7..1-10: the broken-egg sole defender was deferred every month."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import hanjuku_house as house
from docich import hanjuku_policy as p
from docich.hanjuku_font import TextLine
from docich.hanjuku_screen import Screen


def castle_status(castle, names):
    lines = [TextLine(95 + 16 * i, tuple((144 + 8 * j, ch) for j, ch in enumerate(n)))
             for i, n in enumerate(names)]
    text = f'{castle}じょうステータスしゅうにゅう しょうぐん{len(names)}めい' + ''.join(names)
    return Screen(lines=lines, hand=None, text=text, kind='text')


def memory(month):
    return {'chapter': 1, 'month': month, 'gold': 250, 'tick': 500,
            'castle_ownership': {}, 'house': {'phase': 'castle_verify', 'chapter': 1, 'age': 0,
                                              'total': 0, 'general': 'ヴィーナス', 'source': 'カストーラ',
                                              'pending': [], 'seen': [], 'inspected': []}}


def test_first_month_defers_the_sole_defender():
    mem = memory('1-7')
    assert house._repair_step(castle_status('カストーラ', ['ヴィーナス']), mem, None) == [p.pad('b')]
    assert any(r['decision'] == 'house_general_deferred' for r in mem['_records'])
    assert mem['repair_deferred'] == {'ヴィーナス': '1-7'}


def test_a_later_month_sends_the_sole_defender_to_repair():
    mem = memory('1-8')
    mem['repair_deferred'] = {'ヴィーナス': '1-7'}
    assert house._repair_step(castle_status('カストーラ', ['ヴィーナス']), mem, None) == [p.pad('b')]
    assert mem['house']['phase'] == 'castle_sortie'
    assert mem['house']['sole_defender'] is True
    assert any(r['decision'] == 'house_repair_sole_defender' for r in mem['_records'])
    assert not any(r['decision'] == 'house_general_deferred' for r in mem['_records'])


def test_same_month_repeat_still_defers():
    mem = memory('1-7')
    mem['repair_deferred'] = {'ヴィーナス': '1-7'}
    house._repair_step(castle_status('カストーラ', ['ヴィーナス']), mem, None)
    assert mem['house']['phase'] != 'castle_sortie'


def test_two_generals_dispatch_as_before():
    mem = memory('1-7')
    house._repair_step(castle_status('カストーラ', ['ヴィーナス', 'ゼウス']), mem, None)
    assert mem['house']['phase'] == 'castle_sortie'
    assert not mem['house'].get('sole_defender')

