"""Native-layout synthetic observations, not a live Angelin-use receipt."""
from copy import deepcopy

import pytest

from docich import hanjuku_house as house, hanjuku_policy as p
from docich.hanjuku_egg_stock import observed_uses
from docich.hanjuku_font import TextLine, UNKNOWN
from docich.hanjuku_screen import parse
from test_hanjuku_house import status
from test_hanjuku_chart_bot import sortie_canvas
from test_hanjuku_defense_read_model import project, receipts, CONTEXT
from test_hanjuku_retreat_egg_status import open_status
from test_hanjuku_retreat_recheck import memory as camp_memory, FRAME


@pytest.mark.parametrize('uses', range(6))
def test_observed_count_accepts_zero_through_five(uses):
    assert observed_uses(uses) == uses
    assert type(observed_uses(uses)) is int


@pytest.mark.parametrize('value', [None, True, False, -1, 6, 999, '0', '5', 5.0,
                                   float('nan'), float('inf'), [], {}])
def test_invalid_count_is_unknown_not_a_recovery_or_zero(value):
    assert observed_uses(value) is None


@pytest.mark.parametrize('main', [True, False])
@pytest.mark.parametrize('egg', ['エラベルエッグ', 'スーパーエッグ', 'ワンダーエッグ',
                                 'カラフルエッグ', 'イビルエッグ'])
def test_native_status_accepts_five_without_changing_egg_type(main, egg):
    info = house.general_status(parse(status('ゼウス', egg + '5', main=main)))
    assert info is not None
    assert info['general'] == 'ゼウス'
    assert info['egg'] == egg and info['uses'] == 5
    assert info['broken'] is False


@pytest.mark.parametrize('main', [True, False])
@pytest.mark.parametrize('body', ['エラベルエッグ6', 'エラベルエッグ9',
                                  'エラベルエッグ14', 'エラベルエッグ15',
                                  'エラベルエッグ05', 'エラベルエッグ'])
def test_status_never_splits_a_bad_count_into_an_egg_name_and_last_digit(main, body):
    assert house.general_status(parse(status('ゼウス', body, main=main))) is None


@pytest.mark.parametrize('body,broken', [('なし', False), ('こわれている', True)])
def test_eggless_and_broken_remain_distinct_from_an_observed_zero(body, broken):
    info = house.general_status(parse(status('ゼウス', body)))
    assert info['uses'] is None and info['egg'] is None
    assert info['broken'] is broken


def test_unknown_glyph_does_not_unlock_a_cached_zero():
    mem = {'chapter': 1, 'month': '1-6', 'tick': 10, 'egg_uses': {'ゼウス': 0}}
    screen = parse(status('ゼウス', 'エラベルエッグ5'))
    row = next(row for row in screen.lines if row.y == 119)
    screen.lines[screen.lines.index(row)] = TextLine(row.y, tuple(
        (x, UNKNOWN if x == 112 else ch) for x, ch in row.cells))
    before = deepcopy(mem)
    assert house._observe_status(screen, mem) is None
    assert mem == before


def test_house_observation_replaces_old_zero_and_releases_only_its_recovery_cost():
    mem = {'chapter': 1, 'month': '1-6', 'tick': 10, 'gold': 250,
           'egg_uses': {'ゼウス': 0, 'ヴィーナス': 2},
           'egg_types': {'ゼウス': 'スーパーエッグ', 'ヴィーナス': 'スーパーエッグ'}}
    assert p._egg_measured_cost(mem) == 100
    info = house._observe_status(parse(status('ゼウス', 'スーパーエッグ5')), mem)
    assert info is not None and mem['egg_uses'] == {'ゼウス': 5, 'ヴィーナス': 2}
    assert mem['house_eggs']['ゼウス']['uses'] == 5
    assert p._egg_measured_cost(mem) == 50
    assert mem['gold'] == 250  # reading is not a payment or an Angelin use
    assert [r['decision'] for r in mem['_records']] == ['egg_seen']


@pytest.mark.parametrize('mode,actor', [('named', 'ゼウス'), ('anonymous', 'ヴィーナス'),
                                        ('anonymous_blocked', 'ゼウス')])
def test_named_camp_five_replaces_zero_without_claiming_return(mode, actor):
    mem = camp_memory()
    mem['egg_uses'] = {'ゼウス': 0, 'ヴィーナス': 0}
    mem['egg_recheck'] = [actor]
    before_sorties = deepcopy(mem['sorties'])
    open_status(mem, mode)
    screen = parse(status(actor, 'エラベルエッグ5', main=False, hp=6))
    assert p.camp_recall_step(screen, mem, FRAME) == [p.pad('b')]
    assert mem['egg_uses'][actor] == 5
    other = 'ヴィーナス' if actor == 'ゼウス' else 'ゼウス'
    assert mem['egg_uses'][other] == 0
    assert actor not in mem['egg_recheck']
    assert not p._own_egg_empty(mem, {'ally': actor})
    assert mem['sorties'] == before_sorties
    assert not mem.get('garrison') and not mem.get('house_eggs')
    assert not any(r.get('decision') == 'egg_recover' for r in mem.get('_records', []))


@pytest.mark.parametrize('bad_scope', ['actor', 'main_panel', 'identity'])
def test_five_count_does_not_bypass_camp_owner_or_panel_guards(bad_scope):
    mem = camp_memory()
    mem['egg_uses'] = {'ゼウス': 0, 'ヴィーナス': 0}
    open_status(mem, 'named')
    actor = 'ヴィーナス' if bad_scope == 'actor' else 'ゼウス'
    if bad_scope == 'identity':
        mem['_run_identity'] = {**mem['_run_identity'], 'generation': 99}
    screen = parse(status(actor, 'エラベルエッグ5', main=bad_scope == 'main_panel', hp=6))
    p.camp_recall_step(screen, mem, FRAME)
    assert mem['egg_uses'] == {'ゼウス': 0, 'ヴィーナス': 0}


@pytest.mark.parametrize('uses', range(6))
def test_sortie_observation_accepts_real_single_digit_counts(uses):
    screen = sortie_canvas('ゼウス', uses)
    assert p._egg_row(screen) == ('ゼウス', 'エラベルエッグ', uses)


@pytest.mark.parametrize('uses', [6, 9, 14, 15, '05', '5x', '?'])
def test_sortie_rejects_out_of_range_or_multidigit_counts(uses):
    assert p._egg_row(sortie_canvas('ゼウス', uses)) is None


def test_sortie_five_replaces_zero_in_the_battle_availability_gate():
    mem = {'chapter': 1, 'egg_uses': {'ゼウス': 0}}
    assert p._own_egg_empty(mem, {'ally': 'ゼウス'})
    p.observe_events(sortie_canvas('ゼウス', 5), mem)
    assert mem['egg_uses']['ゼウス'] == 5
    assert not p._own_egg_empty(mem, {'ally': 'ゼウス'})


@pytest.mark.parametrize('uses', range(6))
def test_defense_model_preserves_all_valid_egg_counts(uses):
    result = project(*receipts(hp=90, uses=uses))
    assert result['hero_egg_uses'] == uses
    assert result['hero_protection_needed'] is (True if uses == 0 else None)
    assert result['hero_defense_tail'] is None and result['reorder_actions'] == []


@pytest.mark.parametrize('uses', [None, True, False, -1, 6, '5', 5.0])
def test_defense_model_does_not_invent_valid_count_from_bad_values(uses):
    result = project(*receipts(hp=90, uses=uses))
    assert result['hero_egg_uses'] is None and result['reorder_actions'] == []


def test_five_does_not_suppress_a_low_hp_hero_warning():
    result = project(*receipts(hp=3, uses=5))
    assert result['hero_egg_uses'] == 5 and result['hero_protection_needed'] is True
    assert result['reorder_actions'] == []


def test_five_does_not_make_a_stale_hero_receipt_current():
    result = project(*receipts(hp=90, uses=5), context={**CONTEXT, 'tick': 116})
    assert result['hero_egg_uses'] is None and result['hero_protection_needed'] is None


@pytest.mark.parametrize('uses,depleted', [(3, True), (4, False), (5, False)])
def test_normal_monthly_recovery_target_stays_four_not_five(uses, depleted):
    mem = {'egg_uses': {'ゼウス': uses}, 'egg_types': {'ゼウス': 'スーパーエッグ'}}
    assert ('ゼウス' in p._depleted_eggs(mem)) is depleted
    assert p._egg_measured_cost(mem) == (50 if depleted else 0)


@pytest.mark.parametrize('egg', ['いっぱつエッグ', 'キングエッグ'])
def test_one_use_egg_recovery_target_stays_one(egg):
    mem = {'egg_uses': {'ゼウス': 1}, 'egg_types': {'ゼウス': egg}}
    assert not p._depleted_eggs(mem)
    mem['egg_uses']['ゼウス'] = 0
    assert p._depleted_eggs(mem) == {'ゼウス'}
