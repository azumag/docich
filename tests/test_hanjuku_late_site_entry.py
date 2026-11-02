"""A late 四季王 entry must bind after its step was sent again.

g1075 kept four 4-B site steps overdue for hours. A re-sent step replaces
sorties[step]; the general from the previous launch can still be marching
and enter later. Without a retired record his entry matched nothing, so the
site fight never reached boss_victories and the bot kept relaunching.
"""
import pytest

from docich import hanjuku_policy as policy
from docich.hanjuku.strategy import orders as O
from docich.hanjuku.strategy.campaign import _enter_chapter
from docich.hanjuku_screen import Screen, classify_text

SITE = '四季王城(南西)'
OLD = {'general': 'アルテミス', 'source': 'ミヒラギアン', 'status': 'overdue',
       'target': SITE, 'tick': 100}
NEW = {'general': 'ブカティーニ', 'source': 'アルカ', 'status': 'en_route',
       'target': SITE, 'tick': 1400}


def entry(general):
    screen = Screen(lines=[], hand=None, text=f'{general}しょうぐんがボスじょうにせめこんだ!!')
    screen.kind = classify_text(screen)
    return screen


def memory(tick, *, current=None, retired=None):
    return {'chapter': 4, 'tick': tick, 'orders': {'4-B1': 'launched'},
            'sorties': {'4-B1': dict(current or NEW)},
            'retired_sorties': {'4-B1': [dict(item) for item in (retired or [OLD])]},
            'boss_relaunch': {'4-B1': 1}}


def test_a_late_entry_binds_the_retired_attempt_and_keeps_the_new_one():
    mem = memory(1500)
    assert policy.message_step(entry('アルテミス'), mem) == [policy.pad('a')]
    assert mem['attack'] == {'general': 'アルテミス', 'castle': SITE, 'side': 'attack',
                             'step': '4-B1', 'entry_evidence': 'measured_boss_entry'}
    assert mem['sorties']['4-B1']['general'] == 'アルテミス'
    assert mem['sorties']['4-B1']['status'] == 'arrived'
    assert mem['sorties']['4-B1']['target'] == SITE
    assert [row['general'] for row in mem['retired_sorties']['4-B1']] == ['ブカティーニ']
    assert not mem.get('boss_relaunch')
    assert any(record.get('decision') == 'attack_observed'
               and record.get('chart_step') == '4-B1' for record in mem['_records'])


def test_the_current_attempt_still_binds_without_touching_the_retired_one():
    mem = memory(1500)
    assert policy.message_step(entry('ブカティーニ'), mem) == [policy.pad('a')]
    assert mem['attack']['general'] == 'ブカティーニ'
    assert mem['sorties']['4-B1']['general'] == 'ブカティーニ'
    assert [row['general'] for row in mem['retired_sorties']['4-B1']] == ['アルテミス']


def test_an_entry_outside_the_site_window_is_not_bound():
    mem = memory(100 + O.SITE_LATE_ENTRY_TICKS + 1)
    assert policy.message_step(entry('アルテミス'), mem) == []
    assert mem.get('attack') is None
    assert mem['sorties']['4-B1']['general'] == 'ブカティーニ'
    assert mem['retired_sorties']['4-B1'][0]['general'] == 'アルテミス'
    assert mem['retired_sorties']['4-B1'][0]['status'] == 'overdue'


def test_only_unentered_boss_attempts_are_retired():
    mem = {'retired_sorties': {}}
    O._retire_unentered_sortie(mem, '4-B1', OLD)
    assert mem['retired_sorties']['4-B1'][0]['general'] == 'アルテミス'
    O._retire_unentered_sortie(mem, '4-B1', {**OLD, 'status': 'arrived'})
    assert len(mem['retired_sorties']['4-B1']) == 1
    O._retire_unentered_sortie(mem, '4-C1', {**OLD, 'target': 'アルカ'})
    assert '4-C1' not in mem['retired_sorties']


def test_retired_attempts_are_bounded_per_step():
    mem = {'retired_sorties': {}}
    for tick in range(1, 6):
        O._retire_unentered_sortie(mem, '4-B1', {**OLD, 'tick': tick})
    assert [row['tick'] for row in mem['retired_sorties']['4-B1']] == [3, 4, 5]


def test_a_retired_attempt_without_a_tick_is_not_a_candidate():
    mem = memory(1500)
    mem['retired_sorties']['4-B1'][0]['tick'] = None
    assert O._match_sortie(mem, SITE, 'アルテミス') == (None, 'none')


def test_a_malformed_retired_ledger_is_ignored():
    mem = memory(1500)
    mem['retired_sorties'] = ['DO-NOT-USE']
    assert policy.message_step(entry('アルテミス'), mem) == []
    assert mem.get('attack') is None
    assert O._retire_unentered_sortie(mem, '4-B1', OLD) is None
    assert mem['retired_sorties']['4-B1'][0]['general'] == 'アルテミス'


def test_a_chapter_change_clears_retired_attempts():
    mem = memory(1500)
    _enter_chapter(mem, 5, reason='test')
    assert 'retired_sorties' not in mem
