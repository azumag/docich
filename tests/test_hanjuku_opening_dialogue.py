"""Synthetic replay of the reported opening phrase, not native-frame evidence."""
import pytest

from test_hanjuku_survival import battle, memory, menu
from test_hanjuku_chart_bot import Canvas
from docich import hanjuku_policy as p
from docich.hanjuku_bot import decide


@pytest.mark.parametrize('text', ['ここがおまえのはかばだ', 'ここが おまえの はかばだ', 'ここがおまえ'])
def test_visible_taunt_preserves_budget_then_opens_immediately(text):
    mem = memory()
    s = battle(mem)
    s.text = text
    for hp in (30, 29, 28, 27, 26, 25):
        s.battle.ally_hp = hp
        assert p.battle_step(s, mem) == [{'type': 'wait', 'ms': 50}]
        assert mem['battle']['ally_hp'] == hp
        assert 'survival' not in mem['battle']
        assert 'command_open_pending' not in mem['battle']
    s.text = ''
    assert p.battle_step(s, mem) == [p.pad('b')]
    assert mem['battle']['survival']['pending_opens'] == 1
    assert mem['_records'][-1]['decision'] == 'battle_command_open_requested'
    for _ in range(p.COMMAND_OPEN_WAITS):
        assert p.battle_step(s, mem) == [{'type': 'wait', 'ms': 50}]
    assert p.battle_menu_step(menu(), mem) == [p.pad('down')]


def test_hotload_pending_is_not_retried_or_reset_during_taunt():
    mem = memory()
    p.battle_step(battle(mem), mem)
    s = battle(mem)
    s.text = 'ここがおまえのはかばだ'
    for _ in range(8):
        assert p.battle_step(s, mem) == [{'type': 'wait', 'ms': 50}]
    assert mem['battle']['survival']['pending_opens'] == 1
    assert mem['battle']['command_open_pending']['waits'] == 0


def test_unrelated_text_does_not_delay_rescue():
    mem = memory()
    s = battle(mem)
    s.text = 'たたかいはつづく'
    assert p.battle_step(s, mem) == [p.pad('b')]


def test_egg_b_never_gets_human_open_marker():
    mem = memory()
    mem['battle']['egg_battle'] = True
    assert p._human_menu_open(mem['battle'], 'survival', mem) == [p.pad('b')]
    assert not mem.get('_records')


def test_synthetic_pixels_through_orchestrator_do_not_fallback_to_input():
    c = Canvas()
    for y in range(168, 200):
        for x in range(256):
            c.put(x, y, (238, 238, 238))
    for x, text in ((16, 'ミント'), (96, '50'), (144, 'どうし'), (224, '30')):
        c.text(x, 176, text, (32, 32, 32))
    c.text(24, 63, 'ここがおまえのはかばだ')
    state = {'policy': memory()}
    for _ in range(p.OPENING_DIALOGUE_WAIT_LIMIT):
        actions, state = decide(c.frame(), state)
        assert actions == [{'type': 'wait', 'ms': 50}]
    assert state['policy']['battle']['ally_hp'] == 30


def test_persistent_taunt_is_bounded_then_existing_judgment_resumes():
    limit = p.OPENING_DIALOGUE_WAIT_LIMIT
    baseline_mem = memory()
    baseline = battle(baseline_mem)
    baseline.text = 'たたかいはつづく'
    expected = [p.battle_step(baseline, baseline_mem), p.battle_step(baseline, baseline_mem)]

    mem = memory()
    s = battle(mem)
    s.text = 'ここがおまえのはかばだ'
    for _ in range(limit):
        assert p.battle_step(s, mem) == [{'type': 'wait', 'ms': 50}]
    assert mem['battle']['opening_dialogue_waits'] == limit
    assert sum(r['decision'] == 'battle_opening_dialogue_wait' for r in mem['_records']) == 1
    assert not any(r['decision'] == 'battle_opening_dialogue_exhausted' for r in mem['_records'])

    assert [p.battle_step(s, mem), p.battle_step(s, mem)] == expected
    exhausted = [r for r in mem['_records'] if r['decision'] == 'battle_opening_dialogue_exhausted']
    assert len(exhausted) == 1
    assert sum(r['decision'] == 'battle_opening_dialogue_wait' for r in mem['_records']) == 1


def test_taunt_absence_restarts_the_opening_dialogue_bound():
    limit = p.OPENING_DIALOGUE_WAIT_LIMIT
    mem = memory()
    s = battle(mem)
    s.text = 'ここがおまえのはかばだ'
    for _ in range(limit):
        p.battle_step(s, mem)
    s.text = ''
    p.battle_step(s, mem)
    assert 'opening_dialogue_waits' not in mem['battle']
    s.text = 'ここがおまえのはかばだ'
    assert p.battle_step(s, mem) == [{'type': 'wait', 'ms': 50}]
    assert mem['battle']['opening_dialogue_waits'] == 1
    assert sum(r['decision'] == 'battle_opening_dialogue_wait' for r in mem['_records']) == 2
