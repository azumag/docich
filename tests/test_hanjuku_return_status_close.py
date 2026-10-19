"""A status-close request is not proof that the unit menu is visible yet.

Native-glyph synthetic frames exercise public decide(); no live incident
frame or game input is claimed by these delayed-observation regressions.
"""
import json

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_bot import decide
from docich.hanjuku_screen import Screen, parse
from test_hanjuku_chart_bot import Canvas
from test_hanjuku_camp_proof_binding import opened
from test_hanjuku_house import status
from test_hanjuku_retreat_recheck import ID, MAP, FRAME, focus, memory, panel_over_map


def field_status(actor='ゼウス', hp=6):
    c = Canvas(); c.rgb = bytearray(panel_over_map().rgb)
    panel = status(actor, main=False, hp=hp)
    for y in range(16, 136):
        for x in range(8, 128):
            c.put(x, y, panel.pixel(x, y))
    return c.frame()


def closing(mode):
    actor = 'ゼウス' if mode == 'named' else 'ヴィーナス'
    mem = opened(mode, actor)
    mem['survey_run_identity'] = dict(ID)
    actions, state = decide(field_status(actor), {'policy': mem}, run_identity=ID)
    assert actions == [p.pad('b')]
    return actor, state


@pytest.mark.parametrize('mode', ['named', 'anonymous'])
def test_decide_delayed_status_close_keeps_owner_and_returns_without_reopening_status(mode):
    actor, state = closing(mode)
    queue = json.loads(json.dumps(state['policy']['retreat_rechecks']))
    proof = json.loads(json.dumps(state['policy']['recall']['named_status']))
    actions, state = decide(field_status(actor), state, run_identity=ID)
    assert actions == []
    assert state['policy'].get('recall'), 'one delayed B must not abandon the named UI'
    state = json.loads(json.dumps(state))
    for selected, expected in [(1, 'down'), (2, 'down'), (3, 'a')]:
        actions, state = decide(panel_over_map(selected=selected), state, run_identity=ID)
        assert actions == [p.pad(expected)]
    assert state['policy']['recall']['stage'] == 'dest'
    assert state['policy']['recall']['general'] == actor
    assert state['policy']['retreat_rechecks'] == queue
    assert state['policy']['recall']['named_status'] == proof
    assert not state['policy'].get('garrison')


@pytest.mark.parametrize('mode', ['named', 'anonymous'])
def test_decide_ignored_first_b_has_one_bounded_retry_then_accepts_actual_menu(mode):
    actor, state = closing(mode)
    for expected in ([], [p.pad('b')], []):
        actions, state = decide(field_status(actor), json.loads(json.dumps(state)), run_identity=ID)
        assert actions == expected
        assert state['policy'].get('recall')
    actions, state = decide(panel_over_map(selected=3), state, run_identity=ID)
    assert actions == [p.pad('a')]
    assert state['policy']['recall']['stage'] == 'dest'


@pytest.mark.parametrize('mode', ['named', 'anonymous'])
@pytest.mark.parametrize('kind', ['text', 'unknown'])
def test_close_wait_has_a_shared_finite_budget_across_json_and_fades(mode, kind):
    actor, state = closing(mode)
    mem = state['policy']; before = json.loads(json.dumps(mem['retreat_rechecks']))
    panel = parse(field_status(actor))
    for i in range(4):
        mem = json.loads(json.dumps(mem)); mem['tick'] += 1
        screen = Screen([], None, '', kind='unknown') if kind == 'unknown' and i % 2 else panel
        actions = p.camp_recall_step(screen, mem, FRAME)
        assert actions != [p.pad('a')]
        assert bool(mem.get('recall')) is (i < 3)
    for name, row in before.items():
        assert mem['retreat_rechecks'][name]['attempts'] == row['attempts']
        assert mem['retreat_rechecks'][name]['tick'] == row['tick']
    assert not mem.get('garrison')


def test_failed_closes_do_not_replenish_named_attempts_on_requeue():
    mem = memory(); created = mem['retreat_rechecks']['ゼウス']['tick']
    for attempt in range(1, 4):
        focus(mem)
        panel = parse(field_status())
        assert p.camp_recall_step(panel, mem, FRAME) == [p.pad('b')]
        for _ in range(4):
            mem = json.loads(json.dumps(mem)); mem['tick'] += 1
            assert p.camp_recall_step(panel, mem, FRAME) != [p.pad('a')]
        assert not mem.get('recall')
        assert mem['retreat_rechecks']['ゼウス']['attempts'] == attempt
        assert mem['retreat_rechecks']['ゼウス']['tick'] == created
        mem['tick'] += 30
    for _ in range(20):
        assert p.camp_recall_step(MAP, mem, FRAME) is None
        mem = json.loads(json.dumps(mem)); mem['tick'] += 1
    assert mem['retreat_rechecks']['ゼウス']['attempts'] == 3


@pytest.mark.parametrize('mode', ['named', 'anonymous'])
def test_another_actor_status_cannot_continue_the_old_close_owner(mode):
    actor, state = closing(mode)
    mem = state['policy']
    other = 'ヴィーナス' if actor == 'ゼウス' else 'ゼウス'
    assert p.camp_recall_step(parse(field_status(other)), mem, FRAME) != [p.pad('a')]
    assert not mem.get('recall')


@pytest.mark.parametrize('kind', ['yes_no', 'month_menu', 'battle_menu', 'card_select'])
def test_another_foreground_during_close_is_not_treated_as_the_unit_menu(kind):
    _, state = closing('named'); mem = state['policy']
    assert p.camp_recall_step(Screen([], None, '', kind=kind), mem, FRAME) is None
    assert not mem.get('recall')


@pytest.mark.parametrize('reads', [True, -1, 5, 1.0, '1', None, 'missing'])
def test_invalid_saved_close_budget_does_not_restore_permission(reads):
    _, state = closing('named'); mem = state['policy']
    mem['recall']['reads'] = reads
    if reads == 'missing':
        mem['recall'].pop('reads')
    assert p.camp_recall_step(parse(field_status()), mem, FRAME) != [p.pad('a')]
    assert not mem.get('recall')


@pytest.mark.parametrize('steps,retained', [(0, True), (2, False)])
def test_old_saved_menu_migrates_only_an_untouched_close_request(steps, retained):
    _, state = closing('named'); mem = state['policy']
    mem['recall'].update(stage='menu', steps=steps, reads=0)
    attempts = mem['retreat_rechecks']['ゼウス']['attempts']
    assert p.camp_recall_step(parse(field_status()), mem, FRAME) != [p.pad('a')]
    assert bool(mem.get('recall')) is retained
    assert mem['retreat_rechecks']['ゼウス']['attempts'] == attempts


def test_close_wait_hp_zero_invalidates_only_that_actor_without_arrival_claim():
    from docich.hanjuku.flows import camp_recheck
    _, state = closing('named'); mem = state['policy']
    camp_recheck.capture(mem, {'ally': 'ヴィーナス', 'side': 'attack', 'ally_hp': 6,
                              'hero_retreat': {'selected': 1}})
    assert p.camp_recall_step(parse(field_status(hp=0)), mem, FRAME) == [p.pad('b')]
    assert not mem.get('recall') and 'ゼウス' not in mem['retreat_rechecks']
    assert mem['retreat_rechecks']['ヴィーナス']['attempts'] == 0
    assert not mem.get('garrison') and not mem.get('recall_verification')


def test_invalid_proof_never_closes_an_unrelated_text_foreground():
    _, state = closing('named'); mem = state['policy']
    mem['recall'].pop('named_status')
    assert p.camp_recall_step(Screen([], None, 'べつのイベント', kind='text'), mem, FRAME) is None
    assert not mem.get('recall')


def test_house_return_owner_also_waits_for_the_actual_menu_after_b():
    from test_hanjuku_house import memory as house_memory
    mem = house_memory('field_status', returning=True)
    mem['survey_run_identity'] = dict(ID)
    state = {'policy': mem}
    for frame, expected in [(panel_over_map(selected=1), [p.pad('a')]),
                            (field_status(), [p.pad('b')]), (field_status(), []),
                            (panel_over_map(selected=3), [p.pad('a')])]:
        actions, state = decide(frame, json.loads(json.dumps(state)), run_identity=ID)
        assert actions == expected
    assert state['policy']['house']['phase'] == 'return_view'
