"""A menu permission survives its real decoded, unpaid introduction."""
import json

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_bot import decide
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas, month_canvas
from test_hanjuku_recovery_before_recruit import memory


def intro(gold):
    canvas = Canvas()
    canvas.rgb = bytearray(month_canvas(gold, on='しょうぐんぼしゅう').rgb)
    canvas.text(8, 183, 'ども!しょうぐんえんごかいのものです。')
    canvas.text(8, 199, 'しょうぐんのぼしゅうでございますね?')
    return canvas.frame()


def open_recruit(gold):
    mem = memory(gold, uses=2)
    mem['captured'] = ['キカンドン', 'ジョンリギ']
    shop = p._plan(mem, {'year': 1, 'month': 7, 'gold': gold})
    # This month's recovery has already been checked and skipped. Without
    # this flag adoption correctly asks for a recovery check first.
    shop.update(egg='skipped', recruit='pending', egg_before_recruit=True)
    actions, state = decide(month_canvas(gold, on='しょうぐんぼしゅう'), {'policy': mem})
    assert actions == [p.pad('a')]
    assert state['policy']['shop']['recruit_priority'] is True
    assert state['policy']['shop']['recruit'] == 'opened'
    return json.loads(json.dumps(state))


@pytest.mark.parametrize('gold', [130, 180, 300])
@pytest.mark.parametrize('tracking', ['tracked', 'lost'])
def test_skipped_positive_eggs_allow_unpaid_intro_after_menu_and_json(gold, tracking):
    state = open_recruit(gold)
    if tracking == 'lost':
        state['policy'].pop('month_sub')
        state['policy']['shop']['recruit'] = 'unverified'
    frame = intro(gold)
    assert parse(frame).kind == 'month_menu'
    assert p._month_dialog_body(parse(frame)) == p.RECRUIT_INTRO
    actions, state = decide(frame, state)
    assert actions == [p.pad('a')]
    assert state['policy']['shop']['recruit'] == 'opened'
    assert not state['policy']['month_sub'].get('aborted')
    assert 'recruit_paid_gold' not in state['policy']['month_sub']


@pytest.mark.parametrize('change', ['empty', 'unknown', 'recheck', 'cash'])
@pytest.mark.parametrize('tracking', ['tracked', 'lost'])
def test_unpaid_intro_rechecks_resources_before_any_fee(change, tracking):
    state = open_recruit(180)
    mem, gold = state['policy'], 180
    if tracking == 'lost':
        mem.pop('month_sub')
        mem['shop']['recruit'] = 'unverified'
    if change == 'empty':
        mem['egg_uses']['ゼウス'] = 0
    if change == 'unknown':
        mem['egg_uses'].pop('ゼウス')
    if change == 'recheck':
        mem['egg_recheck'] = ['ゼウス']
    if change == 'cash':
        gold = 79
        mem['captured'] = []  # the ordinary spare-cash gate applies
    actions, state = decide(intro(gold), state)
    assert actions == [p.pad('b')]
    assert state['policy']['shop']['recruit'].startswith('deferred_')
    assert 'recruit_paid_gold' not in state['policy'].get('month_sub', {})
