"""Egg-safe melee contracts; synthetic observations, no ROM or live input."""
from dataclasses import asdict
from pathlib import Path
import csv
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_policy as policy, hanjuku_reference as reference
from docich.hanjuku_egg_reference import (GENERAL_DEBUT, GENERAL_EGGS, GENERAL_HP,
                                          enemy_egg_triggers, general_debut_chapter, general_max_hp)
from docich.hanjuku_screen import Battle, Screen

MASH = [action for _ in range(policy.POWER_TAPS)
        for action in (policy.pad('a', 3), {'type': 'wait', 'ms': 50})]


def panel(enemy='クミン', enemy_hp=27, ally_hp=90):
    return Screen([], None, '', kind='battle', battle=Battle(enemy, enemy_hp, 'どうし', ally_hp))


def memory(*, side='attack', chapter=1, step=None):
    return {'chapter': chapter, 'attack': {'side': side, 'general': 'どうし',
                                          'castle': None, 'step': step}}


def enter(screen, mem):
    assert policy.battle_step(screen, mem) == []
    return policy.battle_step(screen, mem)


def test_actual_zero_based_card_numbers_and_opening_threshold():
    ids = [reference.CARD_IDS[n] for n in ('ブラッキー', 'クースカン', 'ファバード')]
    assert ids == [2, 13, 31]
    assert sum(ids) == 46
    assert not reference.enemy_egg_likely(ids)
    assert not reference.enemy_egg_likely([31, 15, 1])  # 47
    assert reference.enemy_egg_likely([31, 17])        # 48, two cards
    assert reference.enemy_egg_likely([31, 17, 1])
    assert set(reference.CARD_IDS) <= set(reference.CARDS)
    # owner 2026-10-03: CARDS は gcgx/wikiwiki の全32札を保持する。
    assert set(reference.CARDS) == set(reference.ALL_CARD_IDS)


def test_static_sfc_roster_matches_local_canonical_source_when_available():
    source = Path(__file__).resolve().parents[1] / 'games/hanjuku-sfc-speedrun/data/char.csv'
    if not source.exists():
        pytest.skip('optional strategy submodule not initialized')
    with source.open() as f:
        rows = [r for r in csv.DictReader(f) if int(r['ID']) < 128]
    expected = {r['名前']: (r['エッグ'] != '－', int(r['思考'])) for r in rows}
    assert GENERAL_EGGS == expected
    assert len(expected) == 128
    assert GENERAL_HP == {r['名前']: int(r['HP']) for r in rows}
    assert GENERAL_DEBUT == {r['名前']: int(r['話']) for r in rows if int(r['話']) > 0}
    assert general_max_hp('クイーン') == 70 and general_max_hp('キッシュ') == 26
    assert general_max_hp('しゅじんこう') == 90
    assert general_debut_chapter('ピオーネ') == 2 and general_debut_chapter('クイーン') == 1
    assert general_debut_chapter('ヒュドラ') is None
    assert enemy_egg_triggers('アルベルト').has_egg is None  # not an SFC roster row
    assert general_max_hp('アルベルト') is None       # fail-closed outside the roster


@pytest.mark.parametrize('name,expected', [
    ('ミント', (False, False, False, False, False)),
    ('ミモザ', (True, False, False, False, False)),
    ('オレガノ', (True, True, True, True, False)),
    ('キッシュ', (True, True, False, True, True)),
    ('クイーン', (True, True, True, True, True)),
    ('不明', (None, None, None, None, None)),
])
def test_trigger_capabilities(name, expected):
    assert tuple(asdict(enemy_egg_triggers(name)).values()) == expected


@pytest.mark.parametrize('enemy', ['だいじん', 'ミント', 'キッシュ', 'ミモザ'])
def test_known_no_clash_risk_preserves_exact_released_mash(enemy):
    mem = memory()
    assert enter(panel(enemy), mem) == MASH
    assert policy.battle_step(panel(enemy), mem) == MASH
    rec = mem['_records'][-1]
    assert rec['decision'] == 'battle_melee'
    assert rec['melee_control_mode'] == 'power_mash'
    assert rec['a_frames_sent'] == 12
    assert rec['chapter'] == 1 and rec['enemy'] == enemy
    assert rec['enemy_hp'] == 27 and rec['ally_hp'] == 90


def test_an_hp_gated_chart_card_keeps_its_gate_despite_egg_risk():
    mem = memory(step='1-A2')
    assert enter(panel('ガルバンゾー', 30), mem) == MASH
    assert not mem['battle'].get('card_flow')
    assert policy.battle_step(panel('ガルバンゾー', 24), mem) == [policy.pad('b')]
    assert mem['battle']['card_flow']['card'] == 'フットバース'
    assert mem['battle'].get('strategy_variant') != 'egg_denial_timing'


def test_an_egg_risk_hold_is_bounded_and_then_engages():
    mem = memory(side='defense')         # after the actual rescue alternatives are exhausted
    policy.battle_step(panel('クミン'), mem)          # first reading: wait for a stable one
    assert policy.battle_step(panel('クミン'), mem) == [policy.pad('b')]
    mem['battle']['survival'] = {'exhausted': True}
    for _ in range(policy.MELEE_HOLD_LIMIT):
        assert policy.battle_step(panel('クミン'), mem) == []
    assert policy.battle_step(panel('クミン'), mem) == MASH
    assert mem['_records'][-1]['melee_control_mode'] == 'power_mash'
    assert '保留上限' in mem['_records'][-1]['reason']


def test_hp_bleed_budget_cuts_the_hold_before_the_count_limit():
    # g530 09:52 defense: the count bound alone let 90 bleed to 73, and the
    # forced push after it fought the summoned モーグリ at 73 and died at its
    # last 4 HP. The budget stops the hold at 81 instead and latches.
    mem = memory(side='defense')
    policy.battle_step(panel('クミン'), mem)          # first reading: wait for a stable one
    assert policy.battle_step(panel('クミン'), mem) == [policy.pad('b')]
    mem['battle']['survival'] = {'exhausted': True}
    assert max(policy.MELEE_HP_HOLD_FLOOR,
               90 // policy.MELEE_HP_HOLD_DIVISOR) == 9
    # A bleed under the budget leaves the count bound alone in charge.
    for hp in (90, 86, 82):
        assert policy.battle_step(panel('クミン', 27, hp), mem) == []
    # 90 - 81 reaches the budget while the count limit is five holds away.
    assert policy.battle_step(panel('クミン', 27, 81), mem) == MASH
    rec = mem['_records'][-1]
    assert rec['melee_control_mode'] == 'power_mash'
    assert (rec['hold_hp_budget'], rec['hold_hp_bled']) == (9, 9)
    assert 'HP劣化' in rec['reason']
    # The cut latches for the rest of the fight: a recovered reading mashes.
    assert policy.battle_step(panel('クミン', 27, 90), mem) == MASH
    assert '打ち切り後' in mem['_records'][-1]['reason']


def test_hp_bleed_budget_floor_gives_a_tiny_opening_hp_real_room_to_hold():
    mem = memory(side='defense')
    policy.battle_step(panel('クミン', 27, 20), mem)
    assert policy.battle_step(panel('クミン', 27, 20), mem) == [policy.pad('b')]
    mem['battle']['survival'] = {'exhausted': True}
    # 20 // 10 would be 1; the floor allows max(3, ...) of wear first.
    assert policy.battle_step(panel('クミン', 27, 20), mem) == []
    assert policy.battle_step(panel('クミン', 27, 18), mem) == []
    assert policy.battle_step(panel('クミン', 27, 17), mem) == MASH
    rec = mem['_records'][-1]
    assert (rec['hold_hp_budget'], rec['hold_hp_bled']) == (3, 3)
    assert 'HP劣化' in rec['reason']


def test_charted_boss_kit_waits_for_a_measured_clash():
    mem = memory(step='1-B1')
    assert enter(panel('クイーン', 70), mem) == MASH
    assert not mem['battle'].get('card_flow')
    assert not mem['battle'].get('clashed')
    assert policy.battle_step(panel('クイーン', 68), mem) == [policy.pad('b')]
    assert mem['battle']['clashed'] is True
    assert mem['battle']['card_flow']['card'] == 'クースカン'
    assert mem['battle'].get('strategy_variant') != 'clash_kit_open_timing'
    assert enter(panel('クイーン'), memory()) == []



@pytest.mark.parametrize('enemy,chapter', [('クミン', 1), ('クイーン', 1), ('オレガノ', 10), ('不明', 10)])
def test_risk_or_unknown_enemy_avoids_assist_pulses_until_resources_are_checked(enemy, chapter):
    mem = memory(chapter=chapter)
    if enemy in ('クミン', 'オレガノ'):
        assert enter(panel(enemy), mem) == [policy.pad('b')]
        assert mem['_records'][-1]['decision'] == 'battle_survival'
        return  # a known unarmed egg fight now checks resources before contact
    assert enter(panel(enemy), mem) == []
    for _ in range(3):
        assert policy.battle_step(panel(enemy), mem) == []
    rec = mem['_records'][-1]
    assert rec['melee_control_mode'] == 'egg_safe_hold'
    assert rec['a_frames_sent'] == 0
    assert rec['egg_risk_flags']['clash_position'] is not False


@pytest.mark.parametrize('side', ['defense', None])
def test_defense_override_and_unknown_context_cannot_grant_normal_type_two_mash(side):
    mem = memory(side=side)
    assert enter(panel('キッシュ'), mem) == ([policy.pad('b')] if side == 'defense' else [])
    if side == 'defense':
        # The override also requires a resource check before contact. Once
        # melee is reached it still cannot grant the ordinary type-2 mash.
        assert policy._melee_step(mem, mem['battle']) == []
    flags = mem['_records'][-1]['egg_risk_flags']
    assert flags['clash_position'] is (True if side == 'defense' else None)
    assert flags['wall_mod4'] is (False if side == 'defense' else None)
    # Thought override cannot give an egg to an eggless general.
    assert enter(panel('ミント'), memory(side=side)) == MASH


def test_captured_castle_context_applies_defense_override():
    mem = memory()
    mem['attack']['castle'] = 'キカンドン'
    mem['captured'] = ['キカンドン']
    assert enter(panel('キッシュ'), mem) == [policy.pad('b')]
    assert policy._melee_step(mem, mem['battle']) == []
    assert mem['_records'][-1]['egg_risk_flags']['clash_position'] is True


def test_due_card_precedes_risky_melee_hold():
    mem = memory(step='1-V2')
    mem['attack']['general'] = 'ヴィーナス'
    screen = panel('ガルバンゾー', 60)
    screen.battle.ally = 'ヴィーナス'
    assert enter(screen, mem) == [policy.pad('b')]
    assert mem['battle']['card_flow']['card'] == 'フットバース'
    assert not any(r['decision'] == 'battle_melee' for r in mem['_records'])


@pytest.mark.parametrize('enemy_hp,ally_hp', [(0, 90), (27, 0), (None, 90), (27, None)])
def test_zero_or_partial_hp_never_inputs_even_against_eggless_enemy(enemy_hp, ally_hp):
    mem = memory()
    assert enter(panel('ミント'), mem) == MASH
    assert policy.battle_step(panel('ミント', enemy_hp, ally_hp), mem) == []
    if enemy_hp is None or ally_hp is None:
        assert mem['battle']['enemy_hp'] == 27 and mem['battle']['ally_hp'] == 90


def test_partial_reading_breaks_consecutive_entry_and_faded_names_hold():
    mem = memory()
    assert policy.battle_step(panel('ミント'), mem) == []
    assert policy.battle_step(panel('ミント', None), mem) == []
    assert policy.battle_step(panel('ミント'), mem) == []
    assert policy.battle_step(panel('ミント'), mem) == MASH
    assert policy.battle_step(panel('ミン�'), mem) == []
    assert policy.battle_step(panel('ミン'), mem) == []


@pytest.mark.parametrize('enemy', ['ミント', 'クミン'])
def test_melee_observability_survives_existing_schema_one_persistence(tmp_path, enemy):
    import importlib.util
    import json
    path = Path(__file__).resolve().parents[1] / 'brains/hanjuku/bot.py'
    spec = importlib.util.spec_from_file_location('egg_risk_persistence', path)
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    mem = memory(side='defense')
    assert policy.battle_step(panel(enemy), mem) == []
    policy.battle_step(panel(enemy), mem)  # stable observation creates the battle
    mem['battle']['survival'] = {'exhausted': True}
    actions = policy.battle_step(panel(enemy), mem)
    rec = mem['_records'][-1]
    state = {'step': 2, 'screen_kind': 'battle', 'phase': 'battle', 'policy': mem}
    entry.persist(tmp_path, state, [rec], {'hanjuku': {'game': 'hanjuku-hero',
                  'runtime_id': 'g1-test', 'generation': 1, 'lease_id': 'test'}},
                  actions=actions, frame_sha256='a' * 64)
    plan, decision = map(json.loads, (tmp_path / 'hanjuku_decisions.jsonl').read_text().splitlines())
    assert decision['schema'] == 1
    assert decision['decision_id'] == plan['decision_id']
    for field in ('egg_risk_flags', 'melee_control_mode', 'chapter', 'enemy',
                  'enemy_hp', 'ally_hp', 'a_frames_sent'):
        assert decision[field] == rec[field]
    assert plan['dispatch_status'] == 'planned_not_yet_sent'


def test_missing_panel_never_mashes_or_mutates_last_clear_hp():
    mem = memory()
    assert enter(panel('ミント'), mem) == MASH
    assert policy.battle_step(Screen([], None, '', kind='battle'), mem) == []
    assert mem['battle']['enemy_hp'] == 27


def test_parser_to_policy_partial_or_black_fade_holds(monkeypatch):
    from test_hanjuku_chart_bot import Canvas
    from docich import hanjuku_bot
    from docich.hanjuku_screen import parse
    c = Canvas((238, 238, 238))
    c.text(24, 176, 'ミント', color=(32, 32, 32))
    c.text(96, 176, '27', color=(32, 32, 32))
    c.text(152, 176, 'どうし', color=(32, 32, 32))
    c.text(224, 176, '90', color=(32, 32, 32))
    assert parse(c.frame()).battle == Battle('ミント', 27, 'どうし', 90)
    mem = memory()
    assert enter(parse(c.frame()), mem) == MASH
    # Hold the broad scene classifier at battle to test its unreadable-panel
    # fallback as well as policy's name/HP guards.
    monkeypatch.setattr(hanjuku_bot, 'classify', lambda frame: 'battle')
    partial = Canvas((238, 238, 238))
    partial.text(24, 176, 'ミント', color=(32, 32, 32))
    partial.text(152, 176, 'どうし', color=(32, 32, 32))
    partial.text(224, 176, '90', color=(32, 32, 32))
    for frame in (partial.frame(), Canvas((0, 0, 0)).frame()):
        assert parse(frame).battle is None
        actions, _ = hanjuku_bot.decide(frame, {'policy': mem})
        assert actions == []
