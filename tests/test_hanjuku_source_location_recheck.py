"""A failed attack's unknown location may be re-read without assuming a spare."""
import json

from docich import hanjuku_policy as p
from docich.hanjuku_font import UNKNOWN
from test_hanjuku_retake_guard import generals, memory


def after_loss():
    mem = memory()
    mem.update(month='1-7', battle={
        'ally': 'ゼウス', 'ally_hp': 0, 'enemy_hp': 21, 'castle': 'ジョンリギ',
        'side': 'attack', 'step': None, 'away': 1})
    p.battle_end(mem, 'map')
    assert mem['general_location_unknown'] == ['ゼウス']
    assert mem['garrison']['アルマムーン'] == ['どうし']
    mem['active'] = 'I:retake:1'
    return mem


def source_retake(mem):
    return next((o for o in p.interim_candidates(mem).values()
                 if o['source'] == 'アルマムーン' and o['target'] == 'ジョンリギ'), None)


def select(mem, order):
    order = dict(order, step=order.get('step', 'I:retake:probe'), after=None)
    mem['active'] = order['step']
    mem.setdefault('launched_orders', {})[order['step']] = order
    mem.setdefault('orders', {})[order['step']] = 'pending'


def test_unknown_name_after_loss_can_be_reobserved_after_json_then_select_a_real_spare():
    mem = after_loss()
    actions = p.deploy_step(generals(['ゼウス' + UNKNOWN, 'どうし']), mem)
    assert actions == [p.pad('b'), p.pad('b')]
    assert mem['general_location_unknown'] == ['ゼウス']
    assert mem['garrison']['アルマムーン'] == ['ゼウス', 'どうし']
    mem = json.loads(json.dumps(mem))
    order = source_retake(mem)
    assert order is not None
    assert order['source_probe'] is True
    assert mem['general_location_unknown'] == ['ゼウス']
    select(mem, order)
    assert p.deploy_step(generals(['ゼウス', 'どうし']), mem) == [p.pad('a')]
    assert mem['general_location_unknown'] == []
    assert mem['garrison']['アルマムーン'] == ['ゼウス', 'どうし']
    assert not mem.get('sorties')


def test_a_real_single_defender_is_still_refuted_and_not_reopened():
    mem = after_loss()
    assert p.deploy_step(generals(['どうし']), mem) == [p.pad('b'), p.pad('b')]
    assert source_retake(mem) is None
    assert mem['unplaced_refuted_castles']['アルマムーン']['month'] == '1-7'


def test_repeated_unknown_rows_have_a_finite_json_persistent_budget():
    mem = after_loss()
    partial = generals(['ゼウス' + UNKNOWN, 'どうし'])
    for attempt in range(p.SOURCE_LOCATION_RECHECK_LIMIT):
        if attempt:
            order = source_retake(mem)
            assert order is not None
            select(mem, order)
        assert p.deploy_step(partial, mem) == [p.pad('b'), p.pad('b')]
        assert mem['source_location_rechecks']['アルマムーン']['attempts'] == attempt + 1
        assert 'アルマムーン' not in mem.get('unplaced_refuted_castles', {})
        assert mem['general_location_unknown'] == ['ゼウス']
        mem = json.loads(json.dumps(mem))
    assert source_retake(mem) is None
    stale = dict(mem['launched_orders']['I:retake:1'], source_probe=True)
    assert p._source_spare_missing(stale, mem, allow_probe=True)
    # Unplaced joiners and a different chart actor cannot bypass this bound.
    mem['house_eggs'] = {'カシュー': {'location': 'castle', 'month': '1-7'}}
    assert source_retake(mem) is None
    result = p._interim_source(mem, 'ジョンリギ', {'general': 'どうし', 'source': 'アルマムーン'},
                               p._owned(mem), set())
    assert result is None or result[0] != 'アルマムーン'
    mem['month'] = '1-8'
    assert source_retake(mem) is not None


def test_hotloaded_old_absence_record_does_not_prevent_unknown_location_probe():
    mem = after_loss()
    mem['garrison']['アルマムーン'] = ['ゼウス', 'どうし']
    old = {'castle': 'アルマムーン', 'month': '1-7', 'names': []}
    mem['unplaced_refuted'] = old.copy()
    mem['unplaced_refuted_castles'] = {'アルマムーン': old.copy()}
    order = source_retake(mem)
    assert order is not None and order['source_probe'] is True
    assert mem['unplaced_refuted_castles']['アルマムーン'] == old
    assert mem['general_location_unknown'] == ['ゼウス']
    select(mem, order)
    assert p.deploy_step(generals(['ゼウス', 'どうし']), mem) == [p.pad('a')]
    assert mem['general_location_unknown'] == []
    assert not mem.get('source_location_rechecks')


def test_actual_busy_general_cannot_make_an_unknown_only_shortage():
    mem = after_loss()
    mem['sorties'] = {'busy': {'general': 'どうし', 'target': 'ゴーメン',
                              'status': 'en_route', 'tick': 99}}
    assert p.deploy_step(generals(['ゼウス' + UNKNOWN, 'どうし']), mem) == [p.pad('b'), p.pad('b')]
    assert 'アルマムーン' not in mem.get('source_location_rechecks', {})
    assert source_retake(mem) is None
    assert mem['general_location_unknown'] == ['ゼウス']


def test_two_duplicate_names_are_not_a_second_defender_to_probe():
    mem = after_loss()
    assert p.deploy_step(generals(['どうし', 'どうし']), mem) == [p.pad('b'), p.pad('b')]
    assert not mem.get('source_location_rechecks')
    assert source_retake(mem) is None


def test_chapter_entry_discards_prior_location_probe_budget():
    mem = after_loss()
    p.deploy_step(generals(['ゼウス' + UNKNOWN, 'どうし']), mem)
    assert mem['source_location_rechecks']
    p._enter_chapter(mem, 2, evidence='test', reason='new chapter')
    assert 'source_location_rechecks' not in mem


def test_adjusted_pending_retake_keeps_next_order_reobservation_path():
    mem = after_loss()
    order = dict(mem['launched_orders']['I:retake:1'], step='A:recheck:J1')
    mem['chart_plan'] = {'orders': [order]}
    select(mem, order)
    assert p.deploy_step(generals(['ゼウス' + UNKNOWN, 'どうし']), mem) == [p.pad('b'), p.pad('b')]
    mem = json.loads(json.dumps(mem))
    reopened = p.next_order(mem)
    assert reopened is not None and reopened['step'] == order['step']
    assert mem['general_location_unknown'] == ['ゼウス']
    select(mem, reopened)
    assert p.deploy_step(generals(['ゼウス', 'どうし']), mem) == [p.pad('a')]
    assert mem['general_location_unknown'] == []
