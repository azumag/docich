"""g684 1-7..1-10: the broken-egg sole defender was deferred every month."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

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
            'captured': ['カストーラ'], 'castle_ownership': {}, 'house': {'phase': 'castle_verify', 'chapter': 1, 'age': 0,
                                              'total': 0, 'general': 'ヴィーナス', 'source': 'カストーラ',
                                              'pending': [], 'seen': [], 'inspected': []}}


@pytest.mark.parametrize('general', ['ヴィーナス', 'ゼウス', 'クミン'])
@pytest.mark.parametrize('deferred', [None, '1-7', '1-6'])
def test_every_nonhero_sole_defender_repairs_immediately(general, deferred):
    mem = memory('1-7'); mem['house']['general'] = general
    if deferred: mem['repair_deferred'] = {general: deferred}
    assert house._repair_step(castle_status('カストーラ', [general]), mem, None) == [p.pad('b')]
    assert mem['house']['phase'] == 'castle_sortie'
    assert mem['house']['sole_defender'] is True
    assert any(r['decision'] == 'house_repair_sole_defender' for r in mem['_records'])
    assert not any(r['decision'] == 'house_general_deferred' for r in mem['_records'])


@pytest.mark.parametrize('general,owned', [('どうし', ['カストーラ']), ('どうし', [])])
@pytest.mark.parametrize('deferred', [None, '1-6'])
def test_hero_and_last_castle_remain_protected_even_after_old_deferral(general, owned, deferred):
    mem = memory('1-7'); mem['captured'] = owned
    source = 'カストーラ' if owned else 'アルマムーン'
    mem['house'].update(general=general, source=source)
    if deferred: mem['repair_deferred'] = {general: deferred}
    house._repair_step(castle_status(source, [general]), mem, None)
    assert mem['house']['phase'] != 'castle_sortie'
    assert any(r['decision'] == 'house_general_deferred' for r in mem['_records'])


@pytest.mark.parametrize('phase', ['castle_pick', 'castle_kit', 'castle_confirm', 'house_confirm'])
@pytest.mark.parametrize('general,last', [('どうし', False), ('どうし', True)])
def test_hotloaded_sole_permission_cannot_bypass_final_protection(phase, general, last):
    from test_hanjuku_retake_guard import generals
    mem = memory('1-7')
    mem['house'].update(phase=phase, general=general, source='アルマムーン', sole_defender=True)
    if last: mem['captured'] = []
    screen = generals([general]) if phase == 'castle_pick' else Screen([], None, '', kind='unknown')
    actions = house._repair_step(screen, mem, None)
    assert not any('a' in a.get('buttons', []) for a in actions)
    assert mem['house']['phase'] == 'close'


def test_two_generals_dispatch_as_before_even_at_the_last_castle():
    mem = memory('1-7'); mem['captured'] = []
    mem['house']['source'] = 'アルマムーン'
    house._repair_step(castle_status('アルマムーン', ['ヴィーナス', 'ゼウス']), mem, None)
    assert mem['house']['phase'] == 'castle_sortie'
    assert not mem['house'].get('sole_defender')


@pytest.mark.parametrize('general', ['ヴィーナス', 'ゼウス'])
def test_actual_single_general_list_can_select_repair_without_old_permission(general):
    from test_hanjuku_retake_guard import generals
    mem = memory('1-7'); mem['house'].update(phase='castle_pick', general=general)
    assert house._repair_step(generals([general]), mem, None) == [p.pad('a')]
    assert mem['house']['phase'] == 'castle_kit' and mem['house']['sole_defender'] is True


@pytest.mark.parametrize('general,should_cancel', [('どうし', True), ('ヴィーナス', False)])
def test_losing_other_castles_after_selection_cancels_the_repair(general, should_cancel):
    from test_hanjuku_retake_guard import generals
    mem = memory('1-7'); mem['house'].update(phase='castle_pick', source='アルマムーン')
    assert house._repair_step(generals([general]), mem, None) == [p.pad('a')]
    mem['captured'] = []
    actions = house._repair_step(Screen([], None, '', kind='card_select'), mem, None)
    if should_cancel:
        assert actions == []
        assert mem['house']['phase'] == 'close'
    else:
        assert mem['house']['phase'] == 'castle_kit'


def test_next_field_general_does_not_inherit_the_previous_castle_guard():
    mem = memory('1-7')
    mem['house'].update(sole_defender=True, pending=['ゼウス'])
    house._next_general(mem)
    assert mem['house']['general'] == 'ゼウス' and mem['house']['phase'] == 'find_field'
    assert mem['house']['source'] is None and not mem['house'].get('sole_defender')


def test_broken_venus_repairs_even_as_last_castle_sole_defender():
    mem = memory('1-7')
    mem['captured'] = []
    mem['house'].update(general='ヴィーナス', source='アルマムーン')
    assert house._repair_step(castle_status('アルマムーン', ['ヴィーナス']), mem, None) == [p.pad('b')]
    assert mem['house']['phase'] == 'castle_sortie'
    assert mem['house'].get('sole_defender') is True
    assert any(r['decision'] == 'house_repair_sole_defender' for r in mem['_records'])
    assert not any(r['decision'] == 'house_general_deferred' for r in mem['_records'])
