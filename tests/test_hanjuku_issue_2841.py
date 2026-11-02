"""Issue #2841: cancelled sorties must not be reissued unchanged."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_policy as p
from docich.hanjuku.os_layer.screen import Screen
from docich.hanjuku_font import TextLine
from docich.hanjuku.flows.deploy import _verify_sortie_source


def _screen(words=()):
    lines = [TextLine(47 + 16 * i, tuple((144 + 8 * j, c) for j, c in enumerate(w)))
             for i, w in enumerate(words)]
    return Screen(kind='general_list', text=''.join(words), lines=lines,
                  hand=(122, 41, 139, 53))


def test_hero_broken_sortie_is_not_reissued_while_egg_stays_broken():
    mem = {'chapter': 1, 'month': '1-7', 'tick': 100,
           'garrison': {'アルマムーン': ['どうし']},
           'orders': {'1-A1': 'pending'}, 'picked': [], 'active': '1-A1',
           'house_eggs': {'どうし': {'broken': True, 'month': '1-6'}}}
    order = {'step': '1-A1', 'general': 'どうし', 'source': 'アルマムーン',
             'target': 'ゴーメン', 'cards': [], 'after': None}
    p.deploy_step(Screen(lines=[], hand=None, text='', kind='castle_menu'), mem)
    assert mem['orders']['1-A1'] != 'launched'
    assert not p._ready(order, mem)


def test_sortie_cancelled_observed_is_not_reissued_while_general_stays():
    mem = {'chapter': 1, 'month': '1-7', 'tick': 100,
           'garrison': {'アルマムーン': ['ゼウス', 'どうし']},
           'orders': {'new': 'pending'}, 'picked': [], 'active': 'new',
           'sorties': {'new': {'general': 'ゼウス', 'source': 'アルマムーン',
                              'status': 'launched_unconfirmed', 'tick': 50}}}
    _verify_sortie_source(_screen(['ゼウス', 'どうし']), mem, {'step': 'new'})
    order = {'step': 'new', 'general': 'ゼウス', 'source': 'アルマムーン',
             'target': 'ゴーメン', 'cards': [], 'after': None}
    assert mem['orders']['new'] != 'launched'
    assert not p._ready(order, mem)


def test_source_defender_cancel_is_not_reissued_while_defender_state_stays():
    mem = {'chapter': 1, 'month': '1-7', 'tick': 100,
           'garrison': {'アルマムーン': ['ゼウス']},
           'orders': {'I:1': 'pending'}, 'picked': [], 'active': 'I:1',
           'general_location_unknown': []}
    order = {'step': 'I:1', 'general': 'ゼウス', 'source': 'アルマムーン',
             'target': 'ゴーメン', 'cards': [], 'after': None,
             'purpose': 'attack'}
    from docich.hanjuku.flows.deploy import _keep_sortie_defender
    screen = _screen(['ゼウス'])
    assert _keep_sortie_defender(screen, mem, order) is not None
    assert not p._ready(order, mem)
