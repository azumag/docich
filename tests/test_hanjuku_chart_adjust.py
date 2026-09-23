"""Off-chart detection and runtime-adjusted chart adoption (issue #1085)."""
from pathlib import Path
import copy
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_chart as chart
from docich import hanjuku_chart_adjust as adjust
from docich import hanjuku_policy as policy
from docich.hanjuku_pixels import Frame
from docich.hanjuku_screen import Screen

FRAME = Frame(256, 224, bytes(256 * 224 * 3))
BASE_ORDERS = copy.deepcopy(chart.CHAPTER_1_ORDERS)


def map_screen():
    value = Screen(lines=[], hand=None, text='')
    value.kind = 'map'
    return value


def stuck_memory():
    """The live g328 situation: every order launched, スペンソニア not captured."""
    return {'chapter': 1, 'variant': 'chart', 'gold': 120, 'month': '1-7',
            'captured': ['キカンドン', 'ナキューメラ', 'ゴーメン', 'カストーラ'],
            'orders': {o['step']: 'launched' for o in chart.CHAPTER_1_ORDERS if o['step'] != '1-B1'},
            '_records': []}


def adjusted_doc(rid, **overrides):
    doc = {'schema': 1, 'chapter': 1, 'request_id': rid, 'source': 'test',
           'orders': [{'step': 'J1', 'general': 'ココット', 'source': 'ゴーメン',
                       'target': 'スペンソニア', 'cards': ['ダイチスイム'], 'after': None,
                       'note': 'スペンソニア再攻撃'},
                      {'step': 'J2', 'general': chart.HERO, 'source': 'スペンソニア',
                       'target': 'けっかい', 'cards': ['クースカン', 'ノリウツール'],
                       'after': ['captured', 'スペンソニア']}],
           'purchases': {'month': [1, 8], 'cards': [['イッテツーン', 3]], 'soldiers': 10}}
    doc.update(overrides)
    return doc


def decisions(mem, kind):
    return [r for r in mem['_records'] if r['decision'] == kind]


def test_off_chart_records_one_request_per_situation_and_holds():
    mem = stuck_memory()
    assert policy.map_step(map_screen(), mem, FRAME) == []
    [request] = decisions(mem, 'chart_adjust_request')
    assert request['off_chart_reason'] == 'orders_locked'
    assert request['blocked'] == [{'step': '1-B1', 'after': ['all_captured']}]
    assert request['request_id'] == adjust.request_id(mem) == mem['chart_adjust']['request_id']
    assert mem.get('active') is None
    # Holding on the same situation does not spam requests.
    assert policy.map_step(map_screen(), mem, FRAME) == []
    assert len(decisions(mem, 'chart_adjust_request')) == 1
    # A new capture is a new situation: 1-B1 becomes ready, no request needed.
    mem['captured'].append('スペンソニア')
    mem['captured'].append('ジョンリギ')
    policy.map_step(map_screen(), mem, FRAME)
    assert mem['active'] == '1-B1'


def test_unavailable_and_exhausted_charts_are_distinguished():
    mem = {'chapter': 2, 'orders': {}, '_records': []}
    policy.map_step(map_screen(), mem, FRAME)
    assert decisions(mem, 'chart_adjust_request')[0]['off_chart_reason'] == 'chart_unavailable'
    mem = stuck_memory()
    mem['orders']['1-B1'] = 'launched'
    policy.map_step(map_screen(), mem, FRAME)
    assert decisions(mem, 'chart_adjust_request')[0]['off_chart_reason'] == 'orders_exhausted'


def test_matching_adjusted_chart_is_adopted_as_an_independent_order_list():
    mem = stuck_memory()
    policy.map_step(map_screen(), mem, FRAME)
    rid = mem['chart_adjust']['request_id']
    # A stale answer (other request) is ignored.
    mem['_adjusted'] = adjust.validate(adjusted_doc('0' * 16))
    policy.map_step(map_screen(), mem, FRAME)
    assert mem.get('active') is None and not decisions(mem, 'chart_adjust_applied')
    mem['_adjusted'] = adjust.validate(adjusted_doc(rid))
    policy.map_step(map_screen(), mem, FRAME)
    [applied] = decisions(mem, 'chart_adjust_applied')
    assert applied['steps'] == ['J1', 'J2'] and mem['variant'] == 'chart_adjusted'
    assert mem['active'] == 'J1'
    [start] = decisions(mem, 'order_start')
    assert start['target'] == 'スペンソニア' and start['strategy_variant'] == 'chart_adjusted'
    # The adopted list survives without the file (persisted in memory, JSON-safe).
    mem = json.loads(json.dumps({k: v for k, v in mem.items() if k != '_adjusted'}))
    assert policy._order(mem)['step'] == 'J1'
    mem['orders']['J1'] = 'launched'
    mem['active'] = None
    assert policy.next_order(mem) is None
    mem['captured'].append('スペンソニア')
    assert policy.next_order(mem)['step'] == 'J2'
    # The base chart is never mutated.
    assert chart.CHAPTER_1_ORDERS == BASE_ORDERS


def test_chapter_change_drops_adjusted_chart():
    from docich.hanjuku_screen import Screen as S
    mem = stuck_memory()
    mem['chart_adjust'] = {'request_id': 'x', 'orders': [{'step': 'J1'}]}
    screen = S(lines=[], hand=None, text='', header={'chapter': 2, 'year': 1, 'month': 1, 'gold': 0})
    policy.observe_events(screen, mem)
    assert 'chart_adjust' not in mem


@pytest.mark.parametrize('patch', [
    {'schema': 2},
    {'request_id': 'nothex'},
    {'chapter': 2},
    {'orders': []},
    {'orders': [{'step': '1-A1', 'general': 'x', 'source': 'ゴーメン', 'target': 'けっかい'}]},
    {'orders': [{'step': 'J1', 'general': 'x', 'source': 'ゴーメン', 'target': 'どこか'}]},
    {'orders': [{'step': 'J1', 'general': 'x', 'source': 'ゴーメン', 'target': 'けっかい',
                 'cards': ['up', 'a']}]},
    {'orders': [{'step': 'J1', 'general': 'x', 'source': 'ゴーメン', 'target': 'けっかい',
                 'after': ['whenever']}]},
    {'orders': [{'step': 'J1', 'general': 'x', 'source': 'ゴーメン', 'target': 'けっかい'}] * 2},
    {'purchases': {'month': [1, 13]}},
    {'purchases': {'month': [1, 8], 'cards': [['ハッキング', 1]]}},
    {'purchases': {'month': [1, 8], 'soldiers': -1}},
])
def test_invalid_adjusted_charts_are_rejected(patch):
    with pytest.raises(ValueError):
        adjust.validate(adjusted_doc('a' * 16, **patch))


def test_save_load_and_request_files_are_atomic_runtime_records(tmp_path):
    assert adjust.load(tmp_path) is None
    (tmp_path / adjust.ADJUSTED_FILE).write_text('{broken', encoding='utf-8')
    assert adjust.load(tmp_path) is None
    doc = adjusted_doc('b' * 16)
    adjust.save(tmp_path, doc)
    loaded = adjust.load(tmp_path)
    assert loaded['orders'][0]['cards'] == ('ダイチスイム',)
    assert loaded['purchases']['cards'] == (('イッテツーン', 3),)
    with pytest.raises(ValueError):
        adjust.save(tmp_path, {**doc, 'schema': 9})
    mem = stuck_memory()
    policy.map_step(map_screen(), mem, FRAME)
    [record] = decisions(mem, 'chart_adjust_request')
    identity = {'game': 'hanjuku-hero', 'runtime_id': 'r', 'generation': 1, 'lease_id': 'l'}
    adjust.write_request(tmp_path, record, identity)
    request = json.loads((tmp_path / adjust.REQUEST_FILE).read_text(encoding='utf-8'))
    assert request['request_id'] == record['request_id'] and request['runtime_id'] == 'r'
    history = [json.loads(line) for line in
               (tmp_path / f'{adjust.HISTORY_LOG}.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [h['event'] for h in history] == ['adjusted_chart_saved', 'adjust_requested']


def test_bot_entry_publishes_request_and_offers_saved_chart(tmp_path):
    import importlib.util
    path = Path(__file__).resolve().parents[1] / 'brains/hanjuku/bot.py'
    spec = importlib.util.spec_from_file_location('hanjuku_bot_adjust_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    meta = {'hanjuku': {'game': 'hanjuku-hero', 'runtime_id': 'g1', 'generation': 3, 'lease_id': 'l'}}
    assert module.publish_adjust_request(tmp_path, [{'decision': 'map'}], meta) is None
    assert not (tmp_path / adjust.REQUEST_FILE).exists()
    mem = stuck_memory()
    policy.map_step(map_screen(), mem, FRAME)
    request = module.publish_adjust_request(tmp_path, mem['_records'], meta)
    assert request['generation'] == 3 and request['off_chart_reason'] == 'orders_locked'
    assert 'chart_adjust_request' in module.INPUT_CONTEXT_DECISIONS
    # The worker answers; the bot offers the validated chart to decide().
    adjust.save(tmp_path, adjusted_doc(request['request_id']))
    mem['_adjusted'] = adjust.load(tmp_path)
    policy.map_step(map_screen(), mem, FRAME)
    assert mem['active'] == 'J1'


def test_decide_offers_adjusted_chart_without_persisting_it(monkeypatch):
    from docich import hanjuku_bot, hanjuku_screen
    monkeypatch.setattr(hanjuku_bot, 'classify', lambda frame: 'field')
    monkeypatch.setattr(hanjuku_screen, 'parse', lambda frame, **kwargs: map_screen())
    memory = stuck_memory()
    del memory['_records']
    actions, state = hanjuku_bot.decide(FRAME, {'policy': memory})
    rid = state['policy']['chart_adjust']['request_id']
    assert actions == [] and [r['decision'] for r in state['_records']] == ['chart_adjust_request']
    actions, state = hanjuku_bot.decide(FRAME, state, adjusted=adjust.validate(adjusted_doc(rid)))
    assert state['policy']['active'] == 'J1' and '_adjusted' not in state['policy']
