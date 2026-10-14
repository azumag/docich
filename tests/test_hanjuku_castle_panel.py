"""Synthetic regression for a lingering, already-verified castle status panel."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_policy as policy
from docich.hanjuku_screen import Screen


def setup_case():
    order = dict(step='F1', general='どうし', source='スペンソニア', target='けっかい',
                 cards=[], after=None, note='test')
    mem = dict(chapter=1, active='F1', castle_verified='F1', orders={}, picked=[],
               captured=['スペンソニア'], garrison={'スペンソニア': ['どうし', 'ガルバンゾー']},
               chart_plan={'request_id': 'test', 'orders': [order]}, _records=[])
    return order, mem


def status(name='スペンソニア'):
    return Screen(lines=[], hand=None, kind='castle_menu',
                  text=f'しゅつげき{name}じょうステータスしゅうにゅう22Gレベル1しょうぐん2めい')


def test_cached_verification_does_not_hide_a_still_open_status_panel():
    order, mem = setup_case()
    for _ in range(3):
        assert policy.deploy_step(status(), mem) == [policy.pad('b')]
    assert mem['castle_verified'] == 'F1'
    assert not any(r['decision'] == 'sortie_input' for r in mem['_records'])


def test_cached_verification_does_not_ignore_a_contradictory_castle():
    order, mem = setup_case()
    assert policy._check_source_castle(status('ゴーメン'), mem, order) == [
        policy.pad('b'), {'type': 'wait', 'ms': 500}, policy.pad('b')]
    assert mem.get('castle_verified') != 'F1'
    assert mem['source_miss']['F1'] == 1


def test_verified_plain_menu_keeps_existing_sortie_path():
    order, mem = setup_case()
    plain = Screen(lines=[], hand=None, kind='castle_menu', text='しゅつげきステータス')
    assert policy._check_source_castle(plain, mem, order) is None


def test_unclassified_status_does_not_create_a_new_verification():
    order, mem = setup_case()
    mem.pop('castle_verified')
    partial = Screen(lines=[], hand=None, kind='castle_menu', text='スペンソニアじょう')
    assert policy._check_source_castle(partial, mem, order) is None
    assert 'castle_verified' not in mem
