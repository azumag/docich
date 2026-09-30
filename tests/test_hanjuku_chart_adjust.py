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
    mem = {'chapter': 3, 'orders': {}, '_records': []}
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
    j1, j2 = adjust.execution_step(rid, 'J1'), adjust.execution_step(rid, 'J2')
    # A stale answer (other request) is ignored.
    mem['_adjusted'] = adjust.validate(adjusted_doc('0' * 16))
    policy.map_step(map_screen(), mem, FRAME)
    assert mem.get('active') is None and not decisions(mem, 'chart_adjust_applied')
    mem['_adjusted'] = adjust.validate(adjusted_doc(rid))
    policy.map_step(map_screen(), mem, FRAME)
    [applied] = decisions(mem, 'chart_adjust_applied')
    assert applied['steps'] == [j1, j2] and applied['local_steps'] == ['J1', 'J2']
    assert mem['variant'] == 'chart_adjusted' and mem['active'] == j1
    [start] = decisions(mem, 'order_start')
    assert start['target'] == 'スペンソニア' and start['strategy_variant'] == 'chart_adjusted'
    # The adopted list survives without the file (persisted in memory, JSON-safe).
    mem = json.loads(json.dumps({k: v for k, v in mem.items() if k != '_adjusted'}))
    mem['_records'] = []
    assert policy._order(mem)['step'] == j1
    # Real flow: J1 launches, then plain map frames while waiting for the capture.
    policy._finish_order(mem, 'launched')
    for _ in range(3):
        assert policy.map_step(map_screen(), mem, FRAME) == []
    # A new request may be issued, but the adopted plan and purchases survive it.
    assert len(decisions(mem, 'chart_adjust_request')) == 1
    assert [o['step'] for o in mem['chart_plan']['orders']] == [j1, j2]
    assert mem['chart_plan']['purchases']['month'] == [1, 8]
    assert mem['chart_adjust']['interim_wanted'] is False     # plan still waiting: no JEV
    mem['captured'].append('スペンソニア')
    policy.map_step(map_screen(), mem, FRAME)
    assert mem['active'] == j2
    # The base chart is never mutated.
    assert chart.CHAPTER_1_ORDERS == BASE_ORDERS


def test_second_generation_reusing_local_steps_runs_its_own_orders():
    mem = stuck_memory()
    policy.map_step(map_screen(), mem, FRAME)
    first = mem['chart_adjust']['request_id']
    mem['_adjusted'] = adjust.validate(adjusted_doc(first, orders=[
        {'step': 'J1', 'general': 'ココット', 'source': 'ゴーメン', 'target': 'スペンソニア'}]))
    policy.map_step(map_screen(), mem, FRAME)
    policy._finish_order(mem, 'launched')
    mem['_adjusted'] = None
    policy.map_step(map_screen(), mem, FRAME)            # exhausted: new request
    second = mem['chart_adjust']['request_id']
    assert second != first
    mem['_adjusted'] = adjust.validate(adjusted_doc(second, orders=[
        {'step': 'J1', 'general': 'ヴィーナス', 'source': 'カストーラ', 'target': 'スペンソニア'}]))
    policy.map_step(map_screen(), mem, FRAME)
    new_j1 = adjust.execution_step(second, 'J1')
    assert new_j1 != adjust.execution_step(first, 'J1')
    assert mem['active'] == new_j1
    assert decisions(mem, 'order_start')[-1]['general'] == 'ヴィーナス'
    assert mem['orders'][adjust.execution_step(first, 'J1')] == 'launched'


def test_adjusted_order_cards_drive_battle_tactics():
    from docich.hanjuku_screen import Battle
    mem = stuck_memory()
    policy.map_step(map_screen(), mem, FRAME)
    rid = mem['chart_adjust']['request_id']
    mem['_adjusted'] = adjust.validate(adjusted_doc(rid, orders=[
        {'step': 'J2', 'general': chart.HERO, 'source': 'ゴーメン', 'target': 'けっかい',
         'cards': ['クースカン', 'ノリウツール']},
        {'step': 'J3', 'general': 'ココット', 'source': 'ゴーメン', 'target': 'スペンソニア',
         'cards': ['ブンシーン']}]))
    policy.map_step(map_screen(), mem, FRAME)
    j2, j3 = adjust.execution_step(rid, 'J2'), adjust.execution_step(rid, 'J3')
    base = [t['card'] for t in policy._tactics(mem, '1-B1')]
    assert base == [t['card'] for t in chart.tactics(1)]
    derived = [t for t in policy._tactics(mem, j2) if t.get('step') == j2]
    assert [(t['enemy'], t['card']) for t in derived] == [('クイーン', 'クースカン'), ('クイーン', 'ノリウツール')]
    assert derived[0]['after_clash'] and derived[1]['after_card'] == 'クースカン'
    [default] = [t for t in policy._tactics(mem, j3) if t.get('step') == j3]
    assert default['enemy'] is None and default['open'] and default['card'] == 'ブンシーン'

    def fight(step, enemy, hp_seq):
        mem['battle'] = None
        mem['attack'] = {'general': chart.HERO, 'castle': 'けっかい', 'side': 'attack', 'step': step}
        out = []
        for enemy_hp in hp_seq:
            screen = Screen(lines=[], hand=None, text='')
            screen.kind = 'battle'
            screen.battle = Battle(enemy=enemy, ally=chart.HERO, enemy_hp=enemy_hp, ally_hp=80)
            out.append(policy.battle_step(screen, mem))
        return out
    # Same HP/clash state as the base 1-B1: the adjusted step now opens クースカン.
    assert fight(j2, 'クイーン', [90, 90, 85]) == fight('1-B1', 'クイーン', [90, 90, 85])
    assert fight(j2, 'クイーン', [90, 90, 85])[-1] == [policy.pad('b')]
    assert mem['battle']['card_flow']['card'] == 'クースカン'
    # Explicit default: an unverified card is used once at the opening, any enemy.
    assert fight(j3, 'ガルバンゾー', [50, 50])[-1] == [policy.pad('b')]
    assert mem['battle']['card_flow']['card'] == 'ブンシーン'


def test_chapter_change_drops_adjusted_chart():
    from docich.hanjuku_screen import Screen as S
    mem = stuck_memory()
    mem['chart_adjust'] = {'request_id': 'x'}
    mem['chart_plan'] = {'request_id': 'x', 'orders': [{'step': 'A:x:J1'}]}
    screen = S(lines=[], hand=None, text='', header={'chapter': 2, 'year': 1, 'month': 1, 'gold': 0})
    policy.observe_events(screen, mem)
    assert 'chart_adjust' not in mem and 'chart_plan' not in mem


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
    {'request_digest': 'nothex'},
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
    assert mem['active'] == adjust.execution_step(request['request_id'], 'J1')


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
    assert state['policy']['active'] == adjust.execution_step(rid, 'J1')
    assert '_adjusted' not in state['policy']


# ---------------------------------------------------------------- step 4: JEV interim
def _answer(mem, choice, confidence=0.9, status='ok'):
    state = mem['chart_adjust']
    return {'request_id': state['request_id'], 'seq': state.get('interim_count', 0),
            'status': status, 'choice': choice, 'confidence': confidence}


def test_interim_candidates_exclude_boss_and_captured_castles():
    mem = stuck_memory()
    candidates = policy.interim_candidates(mem)
    targets = {c['target'] for c in candidates.values()}
    assert targets == {'ジョンリギ', 'スペンソニア'}
    assert all(c['after'] is None for c in candidates.values())
    # Owner (2026-09-28): attacks carry cards - the chart's for that castle, else the opener.
    chart_cards = {o['target']: list(o['cards']) for o in chart.orders(1) if o['cards']}
    for c in candidates.values():
        assert c['cards'] == chart_cards.get(c['target'], list(policy.INTERIM_CARDS))
    # ジョンリギ is uncaptured: 1-C2 (ココット from ジョンリギ) starts from ほんじょう.
    assert all(c['source'] in set(mem['captured']) | {'ほんじょう'} for c in candidates.values())


def test_jev_interim_choice_becomes_a_bounded_deterministic_order():
    mem = stuck_memory()
    policy.map_step(map_screen(), mem, FRAME)
    assert mem['chart_adjust']['interim_wanted'] is True
    label = next(k for k, v in policy.interim_candidates(mem).items() if v['target'] == 'スペンソニア')
    mem['_interim'] = _answer(mem, label)
    policy.map_step(map_screen(), mem, FRAME)
    [interim] = decisions(mem, 'chart_interim_order')
    assert mem['active'] == interim['chart_step'] and interim['chart_step'].startswith('I:')
    assert interim['target'] == 'スペンソニア'
    # The interim sortie does not change the pending LLM request.
    rid = mem['chart_adjust']['request_id']
    mem['orders'][mem['active']] = 'launched'
    mem['active'] = None
    policy.map_step(map_screen(), mem, FRAME)
    assert mem['chart_adjust']['request_id'] == rid and len(decisions(mem, 'chart_adjust_request')) == 1
    assert mem['chart_adjust']['interim_wanted'] is True      # second (last) attempt
    # A stale answer (old seq) is ignored.
    mem['_interim'] = {**_answer(mem, label), 'seq': 0}
    policy.map_step(map_screen(), mem, FRAME)
    assert len(decisions(mem, 'chart_interim_order')) == 1
    # hold is not a choice: the answer falls back to the first attack candidate.
    mem['_interim'] = _answer(mem, 'hold')
    policy.map_step(map_screen(), mem, FRAME)
    orders = decisions(mem, 'chart_interim_order')
    assert len(orders) == 2
    assert orders[-1]['strategy_variant'] == 'chart_interim_fallback'
    assert orders[-1]['target'] in {'ジョンリギ', 'スペンソニア'}
    assert not decisions(mem, 'chart_interim_hold')
    assert mem['chart_adjust']['interim_wanted'] is False     # JEV budget spent
    # Even after the JEV budget, the next free slot still sorties (no pure hold).
    mem['orders'][mem['active']] = 'launched'
    mem['active'] = None
    policy.map_step(map_screen(), mem, FRAME)
    assert len(decisions(mem, 'chart_interim_order')) == 3
    assert mem['active'] is not None
    # The LLM chart still wins when it arrives (finish the interim first).
    mem['orders'][mem['active']] = 'launched'
    mem['active'] = None
    mem['_adjusted'] = adjust.validate(adjusted_doc(rid))
    policy.map_step(map_screen(), mem, FRAME)
    assert mem['active'] == adjust.execution_step(rid, 'J1')
    assert 'interim_order' not in mem['chart_adjust']


@pytest.mark.parametrize('answer', [
    {'choice': 'attack_99'}, {'choice': None, 'status': 'timeout'},
    {'confidence': 0.2}, {'status': 'missing_key', 'choice': None},
    {'choice': 'hold'}])
def test_unusable_jev_answers_fallback_to_first_attack(answer):
    mem = stuck_memory()
    policy.map_step(map_screen(), mem, FRAME)
    mem['_interim'] = {**_answer(mem, 'attack_1'), **answer}
    policy.map_step(map_screen(), mem, FRAME)
    [order] = decisions(mem, 'chart_interim_order')
    assert order['strategy_variant'] == 'chart_interim_fallback'
    assert order['target'] in {'ジョンリギ', 'スペンソニア'}
    assert mem.get('active') == order['chart_step']
    assert not decisions(mem, 'chart_interim_hold')


def test_hanjuku_interim_ask_projects_state_and_validates_choice():
    from docich import hanjuku_interim
    mem = stuck_memory()
    policy.map_step(map_screen(), mem, FRAME)
    seen = {}

    def transport(request, **kwargs):
        seen.update(request=request, kwargs=kwargs)
        return {'status': 'ok', 'meta': {'latency_ms': 12.0},
                'data': {'answers': {'interim_action': {'choice': 'attack_1', 'confidence': 0.8}}}}
    answer = hanjuku_interim.ask(mem, env={}, transport=transport)
    assert answer['status'] == 'ok' and answer['choice'] == 'attack_1' and answer['seq'] == 0
    request = seen['request']
    assert set(request) == {'model', 'state', 'questions'}
    # No hold/skip label: only attack candidates.
    assert set(request['questions']['interim_action']['criteria']) == set(policy.interim_candidates(mem))
    assert 'hold' not in request['questions']['interim_action']['criteria']
    assert '_records' not in json.dumps(request, ensure_ascii=False)
    bad = hanjuku_interim.ask(mem, env={}, transport=lambda r, **k: {
        'status': 'ok', 'data': {'answers': {'interim_action': {'choice': 'up', 'confidence': 1}}}})
    assert bad['status'] == 'invalid_response' and bad['choice'] is None
    hold_out = hanjuku_interim.ask(mem, env={}, transport=lambda r, **k: {
        'status': 'ok', 'data': {'answers': {'interim_action': {'choice': 'hold', 'confidence': 1}}}})
    assert hold_out['status'] == 'invalid_response' and hold_out['choice'] is None
    boom = hanjuku_interim.ask(mem, env={}, transport=lambda r, **k: 1 / 0)
    assert boom['status'] == 'network_error'


def test_bot_asks_jev_once_per_pending_seq(tmp_path):
    import importlib.util
    path = Path(__file__).resolve().parents[1] / 'brains/hanjuku/bot.py'
    spec = importlib.util.spec_from_file_location('hanjuku_bot_interim_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    mem = stuck_memory()
    policy.map_step(map_screen(), mem, FRAME)
    del mem['_records']
    state = {'policy': mem}
    calls = []
    fake = lambda m, **k: calls.append(1) or _answer(m, 'attack_1')
    cfg = {'interim_jev': True, 'interim_timeout_ms': 1500}
    meta = {'hanjuku': {'game': 'hanjuku-hero', 'runtime_id': 'r', 'generation': 1, 'lease_id': 'l'}}
    assert module.ask_interim(tmp_path, state, meta, settings=cfg, ask=fake)['choice'] == 'attack_1'
    assert module.ask_interim(tmp_path, state, meta, settings=cfg, ask=fake) is None
    assert len(calls) == 1
    assert module.ask_interim(tmp_path, {'policy': mem}, meta, settings={**cfg, 'interim_jev': False},
                              ask=fake) is None


# ---------------------------------------------------------------- step 3: async worker
class Game:
    def __init__(self, **chart_adjust):
        self.raw = {'hanjuku': {'chart_adjust': {'enabled': True, 'agents': 'codex', **chart_adjust}}}


def _requested(tmp_path):
    mem = stuck_memory()
    policy.map_step(map_screen(), mem, FRAME)
    [record] = decisions(mem, 'chart_adjust_request')
    identity = {'game': 'hanjuku-hero', 'runtime_id': 'r', 'generation': 1, 'lease_id': 'l'}
    return adjust.write_request(tmp_path, record, identity)


def test_worker_generates_validates_and_saves_adjusted_chart(tmp_path):
    from docich import hanjuku_chart_worker as worker
    request = _requested(tmp_path)
    prompts = []

    def generate(g, cfg, prompt):
        prompts.append(prompt)
        body = adjusted_doc('f' * 16, chapter=9, request_id='0' * 16)
        return 'ここに案:\n' + json.dumps(body, ensure_ascii=False), 'codex'
    assert worker.consider(None, Game(), tmp_path, generate=generate, background=False)
    saved = adjust.load(tmp_path)
    # Identity comes from the request, never from model output.
    assert saved['request_id'] == request['request_id'] and saved['chapter'] == 1
    assert saved['source'] == 'llm:codex'
    assert 'スペンソニア' in prompts[0] and 'orders_locked' in prompts[0]
    # Answered: no further generation for this request.
    assert worker.consider(None, Game(), tmp_path, generate=generate, background=False) is None
    assert len(prompts) == 1


def test_worker_rejects_bad_output_and_bounds_attempts(tmp_path):
    from docich import hanjuku_chart_worker as worker
    _requested(tmp_path)
    bad = lambda g, cfg, prompt: ('{"orders": [{"step": "J1", "source": "月", "target": "けっかい"}]}', 'x')
    assert worker.consider(None, Game(), tmp_path, generate=bad, background=False)
    assert worker.consider(None, Game(), tmp_path, generate=bad, background=False)
    assert worker.consider(None, Game(), tmp_path, generate=bad, background=False) is None
    assert adjust.load(tmp_path) is None
    events = [json.loads(line) for line in
              (tmp_path / f'{adjust.HISTORY_LOG}.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [e['status'] for e in events if e['event'] == 'adjust_worker'] == ['invalid_output'] * 2


def test_worker_disabled_without_agents_or_on_terminal(tmp_path):
    from docich import hanjuku_chart_worker as worker
    _requested(tmp_path)
    never = lambda *a: pytest.fail('must not generate')
    assert worker.consider(None, Game(agents=''), tmp_path, generate=never, background=False) is None
    assert worker.consider(None, Game(), tmp_path, terminal=True, generate=never, background=False) is None
    with pytest.raises(ValueError):
        adjust.settings({'timeout_s': 5})


def test_worker_discards_answer_for_superseded_request(tmp_path):
    from docich import hanjuku_chart_worker as worker
    _requested(tmp_path)

    def generate(g, cfg, prompt):
        (tmp_path / adjust.REQUEST_FILE).write_text(json.dumps({'request_id': 'c' * 16}))
        return json.dumps(adjusted_doc('x')), 'codex'
    worker.consider(None, Game(), tmp_path, generate=generate, background=False)
    assert adjust.load(tmp_path) is None


# ---------------------------------------------------------------- step 5: month purchases
def test_adjusted_month_purchases_replace_the_plan_and_record_recruit_gap():
    from docich.hanjuku_screen import Screen as S
    mem = stuck_memory()
    policy.map_step(map_screen(), mem, FRAME)
    doc = adjusted_doc(mem['chart_adjust']['request_id'])
    doc['purchases'] = {'month': [1, 8], 'cards': [['クースカン', 2]], 'soldiers': 50, 'generals': 1}
    mem['_adjusted'] = adjust.validate(doc)
    policy.map_step(map_screen(), mem, FRAME)
    mem = json.loads(json.dumps({k: v for k, v in mem.items() if k != '_adjusted'}))
    mem['_records'] = []
    header = {'chapter': 1, 'year': 1, 'month': 8, 'gold': 60}
    shop = policy._plan(mem, header)
    # 60 - 2*24 would leave 12: under the wage reserve nothing goes to soldiers.
    assert shop['items'] == [['クースカン', 2]] and shop['soldiers'] == 0
    [plan] = decisions(mem, 'month_plan')
    assert plan['strategy_variant'] == 'chart_adjusted'
    assert plan['deviation_reason'] == 'recruit_owner_rule'   # chart count replaced by the owner rule
    # An uncovered month refills soldiers with the gold left minus the wage reserve.
    refill = policy._plan(mem, {**header, 'month': 9})
    assert refill['items'] == [] and refill['soldiers'] == 60 - policy.WAGE_RESERVE
    assert refill['variant'] == 'soldier_refill_only'
    # The base chart month still uses the base plan.
    mem['shop'] = None
    assert policy._plan(mem, {**header, 'month': 5, 'gold': 300})['variant'] == 'chart'


# ---------------------------------------------------------------- step 6: post-GO review
def test_review_collates_base_adjusted_and_outcomes(tmp_path):
    from docich import hanjuku_chart_review as review
    from docich.hanjuku_run import append_log
    adjust.save(tmp_path, adjusted_doc('d' * 16))
    adjust.save(tmp_path, adjusted_doc('e' * 16, orders=[
        {'step': 'J1', 'general': chart.HERO, 'source': 'ゴーメン', 'target': 'けっかい'}]))
    j1, other_j1 = adjust.execution_step('d' * 16, 'J1'), adjust.execution_step('e' * 16, 'J1')
    rows = [
        {'decision': 'order_start', 'chart_step': '1-C2', 'target': 'スペンソニア', 'general': 'ココット'},
        {'decision': 'battle_result', 'chart_step': '1-C2', 'outcome': 'loss', 'side': 'attack',
         'castle': 'スペンソニア'},
        {'decision': 'order_retry', 'chart_step': '1-C2'},
        {'decision': 'chart_adjust_request', 'chart_step': None, 'request_id': 'd' * 16,
         'off_chart_reason': 'orders_locked', 'blocked': [{'step': '1-B1', 'after': ['all_captured']}]},
        {'decision': 'order_start', 'chart_step': j1, 'target': 'スペンソニア', 'general': 'ココット'},
        {'decision': 'battle_result', 'chart_step': j1, 'outcome': 'win', 'side': 'attack',
         'castle': 'スペンソニア'},
        # A later generation reusing J1 (different target) does not mix outcomes.
        {'decision': 'order_start', 'chart_step': other_j1, 'target': 'けっかい', 'general': 'どうし'},
        {'decision': 'battle_result', 'chart_step': other_j1, 'outcome': 'loss', 'side': 'attack',
         'castle': 'けっかい'},
    ]
    for row in rows:
        append_log(tmp_path, 'hanjuku_decisions', {'event': 'decision', 'chapter': 1, **row})
    summary = review.review(tmp_path, {'runtime_id': 'r'})
    report = json.loads((tmp_path / review.REVIEW_FILE).read_text(encoding='utf-8'))
    types = {p['type']: p for p in report['proposals']}
    assert types['review_base_step']['step'] == '1-C2'
    assert types['promote_adjusted_step']['order']['target'] == 'スペンソニア'
    assert types['cover_off_chart']['off_chart_reason'] == 'orders_locked'
    assert types['promote_adjusted_step']['step'] == j1
    assert report['steps'][j1]['kind'] == 'adjusted' and report['runtime_id'] == 'r'
    assert report['steps'][other_j1]['wins'] == 0 and report['steps'][j1]['losses'] == 0
    assert summary['proposals'] == 3
    assert chart.CHAPTER_1_ORDERS == BASE_ORDERS


def test_save_persists_only_normalized_fields(tmp_path):
    adjust.save(tmp_path, adjusted_doc('e' * 16, injected='x' * 1000, generated_at='now'))
    stored = json.loads((tmp_path / adjust.ADJUSTED_FILE).read_text(encoding='utf-8'))
    assert 'injected' not in stored and stored['generated_at'] is None
    assert adjust.load(tmp_path)['request_id'] == 'e' * 16


def test_invalid_local_step_names_are_rejected():
    for step in ('A:x', 'I:1', 'x' * 13, '', '1-A1'):
        with pytest.raises(ValueError):
            adjust.validate(adjusted_doc('a' * 16, orders=[
                {'step': step, 'general': 'x', 'source': 'ゴーメン', 'target': 'けっかい'}]))


def test_worker_lock_is_held_across_processes_until_generation_finishes(tmp_path):
    import subprocess
    import threading
    from docich import hanjuku_chart_worker as worker
    _requested(tmp_path)
    release, entered = threading.Event(), threading.Event()

    def slow(g, cfg, prompt):
        entered.set()
        release.wait(10)
        return 'not json', 'codex'
    assert worker.consider(None, Game(), tmp_path, generate=slow)
    assert entered.wait(5)
    # A second observer process sees the lock held and never generates.
    code = f"""
import sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[1] / 'src')!r})
from docich import hanjuku_chart_worker as w
class G: raw = {{'hanjuku': {{'chart_adjust': {{'enabled': True, 'agents': 'codex'}}}}}}
def gen(*a): print('GENERATED'); return '', ''
print(w.consider(None, G(), {str(tmp_path)!r}, generate=gen, background=False))
"""
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=30)
    assert out.stdout.split() == ['None'], out.stderr
    state = json.loads((tmp_path / worker.STATE).read_text(encoding='utf-8'))
    assert state['attempts'] == 1
    release.set()
    for thread in threading.enumerate():
        if thread.name == 'hanjuku-chart-adjust':
            thread.join(5)
    # After the (failed) generation the lock is free: the next attempt may run.
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=30)
    assert out.stdout.split()[0] == 'GENERATED', out.stderr
    assert json.loads((tmp_path / worker.STATE).read_text(encoding='utf-8'))['attempts'] == 2


# ---------------------------------------------------------------- re-review (#1086)
def _text_screen(text, kind='text'):
    value = Screen(lines=[], hand=None, text=text)
    value.kind = kind
    return value


def _battle(mem, enemy, ally, hp_seq):
    from docich.hanjuku_screen import Battle
    out = []
    for enemy_hp in hp_seq:
        screen = _text_screen('', 'battle')
        screen.battle = Battle(enemy=enemy, ally=ally, enemy_hp=enemy_hp, ally_hp=80)
        out.append(policy.battle_step(screen, mem))
    return out


def _launch(monkeypatch, mem):
    """target_step with the cursor on the goal: the sortie is confirmed."""
    monkeypatch.setattr(policy, 'nav_step', lambda *a, **k: 'arrived')
    return policy.target_step(_text_screen('', 'map_target'), mem, FRAME)


def _adopt(mem, orders):
    policy.map_step(map_screen(), mem, FRAME)
    rid = mem['chart_adjust']['request_id']
    mem['_adjusted'] = adjust.validate(adjusted_doc(rid, orders=orders))
    policy.map_step(map_screen(), mem, FRAME)
    mem['_adjusted'] = None
    return rid


def test_adjusted_boss_order_reaches_boss_entry_and_battle_tactics(monkeypatch):
    mem = stuck_memory()
    mem['captured'] += ['スペンソニア', 'ジョンリギ']
    mem['orders']['1-B1'] = 'failed'
    rid = _adopt(mem, [{'step': 'J2', 'general': chart.HERO, 'source': 'スペンソニア',
                        'target': 'けっかい', 'cards': ['クースカン', 'ノリウツール']}])
    j2 = adjust.execution_step(rid, 'J2')
    assert mem['active'] == j2
    # Boss guard still applies: no hero/cards evidence, no target confirmation.
    assert _launch(monkeypatch, mem) == []
    mem['order_context'] = {j2: {'actual_general': chart.HERO,
                                 'observed_metric': {'cards': sorted(['クースカン', 'ノリウツール'])}}}
    assert _launch(monkeypatch, mem) == [policy.pad('a')]
    assert mem['launched']['けっかい'] == {'general': chart.HERO, 'step': j2}
    # Unknown general on the measured boss entry text is still refused.
    assert policy.message_step(_text_screen('ココットしょうぐんがボスじょうにせめこんだ!!'), mem) == []
    entry = f'{chart.HERO}しょうぐんがボスじょうにせめこんだ!!'
    assert policy.message_step(_text_screen(entry), mem) == [policy.pad('a')]
    assert mem['attack']['step'] == j2 and mem['attack']['entry_evidence'] == 'measured_boss_entry'
    assert _battle(mem, 'クイーン', chart.HERO, [90, 90, 85])[-1] == [policy.pad('b')]
    assert mem['battle']['card_flow']['card'] == 'クースカン'
    # A lost boss battle retries the same adjusted order with its own kit.
    mem['battle'].update(enemy_hp=40, ally_hp=0, card_flow=None)
    policy.battle_end(mem, 'map')
    policy.battle_end(mem, 'map')
    assert mem['orders'][j2] == 'pending'
    assert mem['retry_context'][j2]['expected_metric']['cards'] == ['クースカン', 'ノリウツール']


def test_a_later_chapter_enemy_general_advances_the_chapter():
    # g454 08:24: クイーン defeated, then ピオーネ/ヘラ (debut chapter 2)
    # attacked; no chapter-2 castle name appeared and the bot stayed on
    # chapter 1 coordinates.
    mem = stuck_memory()
    policy.observe_chapter_general(mem, 'ピオーネ')
    assert mem['chapter'] == 2
    [seen] = decisions(mem, 'chapter_seen')
    assert seen['observed_metric'] == {'chapter': 2, 'evidence': 'ピオーネ'}
    # A chapter-1 general or an unknown name never moves the chapter.
    mem = stuck_memory()
    policy.observe_chapter_general(mem, 'クイーン')
    policy.observe_chapter_general(mem, 'ヒュドラ')
    assert mem['chapter'] == 1 and not decisions(mem, 'chapter_seen')
    # A further jump (debut 7) needs this chapter's boss defeat as context;
    # with it, the chapter advances to the general's debut chapter.
    policy.observe_chapter_general(mem, 'ミモザ')
    assert mem['chapter'] == 1
    mem['boss_defeated'] = 1
    policy.observe_chapter_general(mem, 'ミモザ')
    assert mem['chapter'] == 7


def test_a_battle_panel_with_a_next_chapter_enemy_advances_the_chapter():
    from docich.hanjuku_screen import Battle
    mem = stuck_memory()
    for _ in range(2):
        screen = _text_screen('', 'battle')
        screen.battle = Battle(enemy='ピオーネ', ally='どうし', enemy_hp=46, ally_hp=90)
        policy.battle_step(screen, mem)
    assert mem['chapter'] == 2
    assert decisions(mem, 'chapter_seen')[-1]['observed_metric']['evidence'] == 'ピオーネ'


def test_an_unclassified_card_use_leaves_the_sortie_kit():
    # g452 07:14: ヴィーナス selected both carried イッテツーン (no calibrated
    # receipt); the next battle re-planned them and opened an empty list.
    mem = {'_records': []}
    order = {'step': 'I:x:1', 'general': 'ヴィーナス', 'source': 'スペンソニア',
             'target': 'ジョンリギ', 'cards': ['イッテツーン', 'イッテツーン'], 'after': None}
    mem['launched_orders'] = {'I:x:1': order}
    for _ in range(2):
        policy._card_use_unclassified(
            mem, {'step': 'I:x:1', 'cards_selected': ['イッテツーン'], 'cards_unclassified': [],
                  'card_flow': {'card': 'イッテツーン', 'stage': 'announce', 'selection_planned': True}},
            '実使用告知を確認できないまま白兵戦へ復帰')
    assert mem['kit_spent'] == {'I:x:1': ['イッテツーン', 'イッテツーン']}
    assert policy._deploy_cards(order, mem) == []
    assert [t['card'] for t in policy._tactics(mem, 'I:x:1') if t.get('step') == 'I:x:1'] == []
    # A new sortie of the same order carries a fresh kit.
    mem['active'] = 'I:x:1'
    policy._finish_order(mem, 'launched')
    assert policy._deploy_cards(order, mem) == ['イッテツーン', 'イッテツーン']
    assert [t['card'] for t in policy._tactics(mem, 'I:x:1') if t.get('step') == 'I:x:1'] \
        == ['イッテツーン', 'イッテツーン']


def test_a_second_battle_does_not_replan_a_spent_sortie_card():
    mem = stuck_memory()
    step = 'I:abc:1'
    order = {'step': step, 'general': 'ヴィーナス', 'source': 'スペンソニア',
             'target': 'ジョンリギ', 'cards': ['イッテツーン', 'イッテツーン'], 'after': None}
    mem['orders'] = {step: 'launched'}
    mem['launched_orders'] = {step: order}
    mem['kit_spent'] = {step: ['イッテツーン', 'イッテツーン']}
    mem['_records'] = []
    mem['attack'] = {'general': 'ヴィーナス', 'castle': 'ジョンリギ', 'side': 'attack',
                     'step': step, 'entry_evidence': None}
    actions = _battle(mem, 'キャンディー', 'ヴィーナス', [26, 26])
    assert decisions(mem, 'battle_card') == []
    assert decisions(mem, 'battle_start')[0]['planned_cards'] == []
    assert actions[-1] == [policy.pad('b')]  # check rescue resources, never re-plan a spent card
    assert decisions(mem, 'battle_survival')


def test_dropped_chart_card_is_never_planned_or_announced_in_battle():
    # g438 04:18: the sortie dropped ミックミー (never in stock), but the battle
    # still planned it and announced 開幕にミックミーを使います for the missing card.
    mem = stuck_memory()
    mem['captured'] += ['スペンソニア', 'ジョンリギ']
    mem['orders']['1-B1'] = 'failed'
    rid = _adopt(mem, [{'step': 'J2', 'general': chart.HERO, 'source': 'スペンソニア',
                        'target': 'けっかい', 'cards': ['クースカン', 'ミックミー', 'ミックミー']}])
    j2 = adjust.execution_step(rid, 'J2')
    mem['card_drop'] = {j2: ['ミックミー', 'ミックミー']}   # both copies left behind
    derived = [t for t in policy._tactics(mem, j2) if t.get('step') == j2]
    assert [(t['enemy'], t['card']) for t in derived] == [('クイーン', 'クースカン')]
    mem['_records'] = []
    mem['attack'] = {'general': chart.HERO, 'castle': 'けっかい', 'side': 'attack',
                     'step': j2, 'entry_evidence': 'measured_boss_entry'}
    assert _battle(mem, 'クイーン', chart.HERO, [90, 90, 85])[-1] == [policy.pad('b')]
    assert mem['battle']['card_flow']['card'] == 'クースカン'
    assert [r['card'] for r in decisions(mem, 'battle_card')] == ['クースカン']
    assert decisions(mem, 'battle_start')[0]['planned_cards'] == ['クースカン']


def test_boss_kits_unverified_card_requires_measured_boss_evidence():
    # The boss kit must not fire in the road/guard fight on the way (g438
    # 04:18: ソーピニヨン road battle opened the boss kit's ミックミー).
    mem = stuck_memory()
    mem['captured'] += ['スペンソニア', 'ジョンリギ']
    mem['orders']['1-B1'] = 'failed'
    rid = _adopt(mem, [{'step': 'J2', 'general': 'ヴィーナス', 'source': 'スペンソニア',
                        'target': 'けっかい', 'cards': ['クースカン', 'ミックミー', 'ミックミー']}])
    j2 = adjust.execution_step(rid, 'J2')
    defaults = [t for t in policy._tactics(mem, j2)
                if t.get('step') == j2 and t['card'] == 'ミックミー']
    assert defaults and all(t['open'] and t['boss_only'] for t in defaults)

    def fight(enemy, hp_seq, **attack):
        mem['battle'] = None
        mem['battle_seen'] = None
        mem['_records'] = []
        mem['attack'] = attack or None
        from docich.hanjuku_screen import Battle
        out = []
        for enemy_hp in hp_seq:
            screen = _text_screen('', 'battle')
            screen.battle = Battle(enemy=enemy, ally='ヴィーナス', enemy_hp=enemy_hp, ally_hp=80)
            out.append(policy.battle_step(screen, mem))
        return out
    # An unmeasured location never opens the kit.
    fight('ソーピニヨン', [48, 48])
    assert not decisions(mem, 'battle_card')
    assert 'card_flow' not in (mem['battle'] or {})
    assert decisions(mem, 'battle_start')[0]['planned_cards'] == []
    # The measured boss entry does.
    fight('クイーン', [90, 90], general='ヴィーナス', castle='けっかい', side='attack',
          step=j2, entry_evidence='measured_boss_entry')
    assert [r['card'] for r in decisions(mem, 'battle_card')] == ['ミックミー']
    assert mem['battle']['card_flow']['card'] == 'ミックミー'
    # g484: entry text was not recognized, but the Queen panel was measured.
    # Use the known carried kit without inventing a castle/side/entry receipt.
    mem['sorties'] = {j2: {'general': 'ヴィーナス', 'target': 'けっかい',
                          'status': 'en_route'}}
    fight('クイーン', [70, 70])
    assert [r['card'] for r in decisions(mem, 'battle_card')] == ['ミックミー']
    assert decisions(mem, 'battle_start')[0]['planned_cards'] == ['クースカン', 'ミックミー', 'ミックミー']
    assert mem['battle']['castle'] is None and mem['battle']['side'] is None
    assert mem['battle'].get('entry_evidence') is None
    # A boss name from another chapter cannot unlock this chapter's kit.
    fight('にせヒーロー', [70, 70])
    assert not decisions(mem, 'battle_card')
    assert decisions(mem, 'battle_start')[0]['planned_cards'] == []


def test_launched_old_generation_keeps_its_tactics_after_a_new_plan(monkeypatch):
    mem = stuck_memory()
    first = _adopt(mem, [{'step': 'J1', 'general': 'ココット', 'source': 'ゴーメン',
                          'target': 'スペンソニア', 'cards': ['ブンシーン']}])
    old_j1 = adjust.execution_step(first, 'J1')
    assert _launch(monkeypatch, mem) == [policy.pad('a')]
    # A new plan arrives before ココット reaches スペンソニア, and its unit is
    # also sent to スペンソニア (overwriting the per-castle launched record).
    second = _adopt(mem, [{'step': 'J1', 'general': 'ヴィーナス', 'source': 'カストーラ',
                           'target': 'スペンソニア'}])
    new_j1 = adjust.execution_step(second, 'J1')
    assert second != first and mem['active'] == new_j1
    assert _launch(monkeypatch, mem) == [policy.pad('a')]
    assert mem['launched']['スペンソニア']['general'] == 'ヴィーナス'
    assert policy.message_step(
        _text_screen('ココットしょうぐんがスペンソニアじょうにのりこんだ'), mem) == [policy.pad('a')]
    assert mem['attack']['step'] == old_j1
    mem['_records'] = []
    assert _battle(mem, 'ガルバンゾー', 'ココット', [50, 50])[-1] == [policy.pad('b')]
    assert mem['battle']['card_flow']['card'] == 'ブンシーン'
    assert decisions(mem, 'battle_start')[0]['planned_cards'] == ['ブンシーン']
    # A lost battle retries the old order even though its plan was replaced.
    mem['battle'].update(enemy_hp=30, ally_hp=0, card_flow=None, castle='スペンソニア', side='attack')
    policy.battle_end(mem, 'map')
    policy.battle_end(mem, 'map')
    assert mem['orders'][old_j1] == 'pending'
    mem['active'] = None
    assert policy.next_order(mem)['step'] == old_j1
    # The later unit still binds to its own execution id when it arrives.
    assert policy.message_step(
        _text_screen('ヴィーナスしょうぐんがスペンソニアじょうにのりこんだ'), mem) == [policy.pad('a')]
    assert mem['attack']['step'] == new_j1


def test_ambiguous_sorties_are_not_guessed(monkeypatch):
    mem = stuck_memory()
    mem['sorties'] = {
        'A:aaaaaaaa:J1': {'general': 'ココット', 'target': 'スペンソニア', 'status': 'en_route'},
        'A:bbbbbbbb:J1': {'general': 'ココット', 'target': 'スペンソニア', 'status': 'en_route'},
        'A:cccccccc:J9': {'general': chart.HERO, 'target': 'けっかい', 'status': 'en_route'},
        'A:dddddddd:J9': {'general': chart.HERO, 'target': 'けっかい', 'status': 'en_route'}}
    mem['launched_orders'] = {k: {'step': k, 'general': v['general'], 'target': v['target'],
                                  'source': 'ゴーメン', 'cards': [], 'after': None}
                              for k, v in mem['sorties'].items()}
    assert policy.message_step(
        _text_screen('ココットしょうぐんがスペンソニアじょうにのりこんだ'), mem) == [policy.pad('a')]
    assert mem['attack']['step'] is None
    assert decisions(mem, 'attack_observed')[-1]['deviation_reason'] == 'ambiguous_sortie'
    assert all(v['status'] == 'en_route' for v in mem['sorties'].values())
    # An ambiguous boss entry advances the screen without binding either sortie.
    mem['attack'] = None
    entry = f'{chart.HERO}しょうぐんがボスじょうにせめこんだ!!'
    assert policy.message_step(_text_screen(entry), mem) == [policy.pad('a')]
    assert mem['attack']['step'] is None
    assert decisions(mem, 'attack_observed')[-1]['deviation_reason'] == 'ambiguous_sortie'
    assert all(v['status'] == 'en_route' for v in mem['sorties'].values())


def test_ambiguous_boss_entry_fights_with_the_marching_sorties_kit():
    # g460 17:32: the boss entry could not name which どうし->けっかい sortie it
    # was - the base 1-B1 and the adjusted K1 were both still on the road - so
    # no order may be bound. The battle must still fight with the kit of the
    # sortie that is marching: with an unknown step every charted tactic is
    # filtered out and the hero swings bare-handed (planned_cards=[] -> HP
    # 88..0 -> 17:35 game over).
    mem = stuck_memory()
    mem['captured'] += ['スペンソニア', 'ジョンリギ']
    k1 = 'A:bd2304e3:K1'
    base = {'step': '1-B1', 'general': chart.HERO, 'source': 'スペンソニア', 'target': 'けっかい',
            'cards': ['クースカン', 'ノリウツール'], 'after': None,
            'note': 'クースカン ノリウツールを持ちボス城へ 途中敵は無視'}
    adjusted = {'step': k1, 'general': chart.HERO, 'source': 'スペンソニア', 'target': 'けっかい',
                'cards': ['クースカン', 'ノリウツール', 'イッテツーン'],
                'after': ['captured', 'スペンソニア'], 'note': '卵落としにイッテツーンを携行'}
    mem['orders'].update({'1-B1': 'launched', k1: 'launched'})
    mem['launched_orders'] = {'1-B1': base, k1: adjusted}
    mem['sorties'] = {
        '1-B1': {'general': chart.HERO, 'target': 'けっかい', 'status': 'en_route', 'tick': 1684},
        k1: {'general': chart.HERO, 'target': 'けっかい', 'status': 'en_route', 'tick': 2292}}
    mem['_records'] = []
    entry = f'{chart.HERO}しょうぐんがボスじょうにせめこんだ!!'
    assert policy.message_step(_text_screen(entry), mem) == [policy.pad('a')]
    assert mem['attack'] == {'general': chart.HERO, 'castle': 'けっかい', 'side': 'attack',
                             'step': None, 'entry_evidence': 'measured_boss_entry'}
    _battle(mem, 'クイーン', chart.HERO, [70, 70])
    assert decisions(mem, 'battle_start')[0]['planned_cards'] \
        == ['クースカン', 'ノリウツール', 'イッテツーン']
    assert [r['observed_metric']['sortie_step'] for r in decisions(mem, 'battle_step_resolved')] == [k1]
    # A battle at a castle we hold is a defence and keeps its own step.
    context = policy._battle_context({'attack': {'general': chart.HERO, 'castle': 'スペンソニア',
                                                 'side': 'attack', 'step': None},
                                      'captured': ['スペンソニア'], 'sorties': mem['sorties']},
                                     chart.HERO)
    assert (context['side'], context['step']) == ('defense', None)


def test_interim_runs_after_an_exhausted_plan_but_not_while_a_plan_waits(monkeypatch):
    mem = stuck_memory()
    rid = _adopt(mem, [{'step': 'J1', 'general': 'ココット', 'source': 'ゴーメン', 'target': 'スペンソニア'},
                       {'step': 'J2', 'general': chart.HERO, 'source': 'スペンソニア', 'target': 'けっかい',
                        'after': ['captured', 'スペンソニア']}])
    policy._finish_order(mem, 'launched')
    policy.map_step(map_screen(), mem, FRAME)            # J2 waits for the capture
    state = mem['chart_adjust']
    assert state['request_id'] != rid and state['interim_wanted'] is False
    mem['_interim'] = {'request_id': state['request_id'], 'seq': 0, 'status': 'ok',
                       'choice': 'attack_1', 'confidence': 0.9}
    policy.map_step(map_screen(), mem, FRAME)
    assert mem.get('active') is None and not decisions(mem, 'chart_interim_order')
    # Exhaust the plan: J2 failed. Now an interim sortie may run.
    mem['orders'][adjust.execution_step(rid, 'J2')] = 'failed'
    mem['_interim'] = None
    policy.map_step(map_screen(), mem, FRAME)
    state = mem['chart_adjust']
    assert state['interim_wanted'] is True
    mem['_interim'] = {'request_id': state['request_id'], 'seq': 0, 'status': 'ok',
                       'choice': 'attack_1', 'confidence': 0.9}
    policy.map_step(map_screen(), mem, FRAME)
    assert mem['active'].startswith(adjust.INTERIM_PREFIX)
    assert mem['chart_plan']['purchases'] is not None          # plan evidence kept


def test_unpriced_adjusted_cards_resize_soldiers_from_gold_after_merchant():
    mem = stuck_memory()
    policy.map_step(map_screen(), mem, FRAME)
    doc = adjusted_doc(mem['chart_adjust']['request_id'])
    # ダイチスイム is a valid purchase whose price was never measured.
    doc['purchases'] = {'month': [1, 8], 'cards': [['ダイチスイム', 5]], 'soldiers': 80}
    mem['_adjusted'] = adjust.validate(doc)
    policy.map_step(map_screen(), mem, FRAME)
    mem['_adjusted'] = None
    mem['_records'] = []
    header = {'chapter': 1, 'year': 1, 'month': 8, 'gold': 60}
    shop = policy._plan(mem, header)
    [plan] = decisions(mem, 'month_plan')
    assert plan['plan']['unpriced_cards'] == ['ダイチスイム']
    assert shop['soldiers'] == 60 - policy.WAGE_RESERVE and shop['soldiers_from_gold']  # provisional only
    # The merchant really charged for the cards: 80G remain on screen.
    shop['merchant_done'] = True
    month = _text_screen('', 'month_menu')
    month.header = {**header, 'gold': 80}
    policy.month_step(month, mem)
    [recalc] = decisions(mem, 'soldier_plan_recalc')
    assert shop['soldiers'] == 80 - policy.WAGE_RESERVE and recalc['observed_metric']['gold_after_merchant'] == 80
    # Recomputed once; a later frame does not resize again.
    month.header = {**header, 'gold': 5}
    policy.month_step(month, mem)
    assert shop['soldiers'] == 80 - policy.WAGE_RESERVE and len(decisions(mem, 'soldier_plan_recalc')) == 1


def test_soldier_recalc_holds_on_unreadable_gold_and_skips_when_broke():
    mem = {'chapter': 1, '_records': [], 'chart_plan': {'purchases': {
        'month': [1, 8], 'cards': [['ダイチスイム', 5]], 'soldiers': 30, 'generals': 0, 'note': ''}}}
    header = {'chapter': 1, 'year': 1, 'month': 8, 'gold': 40}
    shop = policy._plan(mem, header)
    shop['merchant_done'] = True
    month = _text_screen('', 'month_menu')
    month.header = None
    assert policy.month_step(month, mem) == [] and not shop.get('soldiers_recalculated')
    month.header = {**header, 'gold': 0}
    policy.month_step(month, mem)
    assert shop['soldiers'] == 0 and shop['soldiers_done'] is True


def test_worker_prompts_with_garrisons_and_drops_orders_whose_general_is_elsewhere(tmp_path):
    # g421 e7df88f1: F2/F3/F5 sent ココット/ヴィーナス from スペンソニア, where only
    # どうし stood; each failed at the castle list. The request now says who is
    # where, and orders that cannot start are dropped at save.
    from docich import hanjuku_chart_worker as worker
    mem = stuck_memory()
    mem.update(tick=500, lost=['ジョンリギ'],
               garrison={'スペンソニア': [chart.HERO], 'カストーラ': ['ヴィーナス']},
               sorties={'I:1': {'general': 'ゼウス', 'target': 'スペンソニア', 'status': 'en_route', 'tick': 450},
                        'I:0': {'general': 'ココット', 'target': 'ゴーメン', 'status': 'en_route', 'tick': 1}})
    policy.map_step(map_screen(), mem, FRAME)
    [record] = decisions(mem, 'chart_adjust_request')
    assert record['garrison'] == {'カストーラ': ['ヴィーナス'], 'スペンソニア': [chart.HERO]}
    assert record['lost'] == ['ジョンリギ'] and record['home_lost'] is False
    assert record['en_route'] == [{'general': 'ゼウス', 'target': 'スペンソニア'}]   # stale ココット ages out
    adjust.write_request(tmp_path, record, {'game': 'hanjuku-hero', 'runtime_id': 'r', 'generation': 1,
                                            'lease_id': 'l'})
    prompts = []

    def order(step, general, source):
        return {'step': step, 'general': general, 'source': source, 'target': 'けっかい',
                'cards': [], 'after': None}

    def generate(g, cfg, prompt):
        prompts.append(prompt)
        body = {'orders': [order('F1', chart.HERO, 'スペンソニア'),      # recorded there
                           order('F2', 'ヴィーナス', 'スペンソニア'),     # recorded at カストーラ
                           order('F3', 'ゼウス', 'スペンソニア'),         # marching
                           order('F4', 'ココット', 'ゴーメン'),           # unknown: allowed
                           order('F5', chart.HERO, 'ジョンリギ')]}        # lost source
        return json.dumps(body, ensure_ascii=False), 'codex'
    assert worker.consider(None, Game(), tmp_path, generate=generate, background=False)
    assert '"garrison"' in prompts[0] and 'カストーラ' in prompts[0] and '"en_route"' in prompts[0]
    saved = adjust.load(tmp_path)
    assert [o['step'] for o in saved['orders']] == ['F1', 'F4']
    events = [json.loads(line) for line in
              (tmp_path / f'{adjust.HISTORY_LOG}.jsonl').read_text(encoding='utf-8').splitlines()]
    [dropped] = [e for e in events if e['event'] == 'adjusted_orders_dropped']
    assert {d['step']: d['why'] for d in dropped['dropped']} == {
        'F2': 'general_elsewhere', 'F3': 'general_marching', 'F5': 'source_lost'}


def test_adjust_prompt_grounds_cards_in_observed_stock_and_chapter_purchases(tmp_path):
    # g438 04:04: the model planned ミックミー/エンジェリン for chapter 1, where
    # neither is sold or owned; it was never told what the player holds.
    from docich import hanjuku_chart_worker as worker
    mem = stuck_memory()
    mem['card_stock'] = {'イッテツーン': 10, 'クースカン': 2}
    policy.map_step(map_screen(), mem, FRAME)
    [record] = decisions(mem, 'chart_adjust_request')
    assert record['card_stock'] == {'イッテツーン': 10, 'クースカン': 2}
    adjust.write_request(tmp_path, record, {'game': 'hanjuku-hero', 'runtime_id': 'r',
                                            'generation': 1, 'lease_id': 'l'})
    request = json.loads((tmp_path / adjust.REQUEST_FILE).read_text(encoding='utf-8'))
    assert request['card_stock'] == {'イッテツーン': 10, 'クースカン': 2}
    prompt = worker.build_prompt(request, [])
    assert '"card_stock"' in prompt and '"クースカン": 2' in prompt
    assert 'card_stock に無い札・在庫0の札' in prompt
    # The chapter's charted month purchases tell the model which cards its
    # shops sell (chapter 1 lists no ミックミー/エンジェリン).
    assert '"purchases"' in prompt and '"chart_gold"' in prompt
    # The 卵落 rule grounds which cards can actually drop an egg, on max HP.
    assert '卵落値: ' in prompt and 'イッテツーン=8' in prompt and 'クースカン=0' in prompt
    assert 'mod 16' in prompt and 'ally_max_hp / enemy_max_hp' in prompt
    assert '開戦時HPは負傷していることがある' in prompt


def test_recent_results_carry_battle_start_hp_for_the_egg_drop_rule(tmp_path):
    from docich import hanjuku_chart_worker as worker
    records = [{'event': 'decision', 'decision': 'battle_start', 'enemy': 'キッシュ',
                'ally': 'ココット', 'enemy_hp': 26, 'ally_hp': 24, 'chart_step': 'A:x:J1'},
               {'event': 'decision', 'decision': 'battle_card_selected', 'card': 'グリンボー'},
               {'event': 'decision', 'decision': 'order_failed', 'general': 'ココット'}]
    (tmp_path / 'hanjuku_decisions.jsonl').write_text(
        '\n'.join(json.dumps(r, ensure_ascii=False) for r in records) + '\n', encoding='utf-8')
    assert worker._recent_results(tmp_path) == [
        {'decision': 'battle_start', 'enemy': 'キッシュ', 'ally': 'ココット',
         'enemy_hp': 26, 'ally_hp': 24, 'chart_step': 'A:x:J1',
         'ally_max_hp': 24, 'enemy_max_hp': 26},
        {'decision': 'order_failed', 'general': 'ココット'}]


def test_recent_results_maps_the_named_hero_to_fixed_max_hp(tmp_path):
    from docich import hanjuku_chart_worker as worker
    record = {'event': 'decision', 'decision': 'battle_start', 'enemy': 'キッシュ',
              'ally': chart.HERO, 'enemy_hp': 20, 'ally_hp': 60, 'chart_step': 'A:x:J1'}
    (tmp_path / 'hanjuku_decisions.jsonl').write_text(
        json.dumps(record, ensure_ascii=False) + '\n', encoding='utf-8')
    [result] = worker._recent_results(tmp_path)
    assert result['ally'] == chart.HERO
    assert result['ally_max_hp'] == 90
    assert result['enemy_max_hp'] == 26


def test_same_situation_with_new_card_stock_republishes_the_request():
    mem = stuck_memory()
    policy.map_step(map_screen(), mem, FRAME)
    [first] = decisions(mem, 'chart_adjust_request')
    rid, digest = first['request_id'], mem['chart_adjust']['request_digest']
    assert digest == adjust.request_digest(first)
    mem['card_stock'] = {'クースカン': 2}
    policy.map_step(map_screen(), mem, FRAME)
    requests = decisions(mem, 'chart_adjust_request')
    assert len(requests) == 2
    second = requests[-1]
    assert second['request_id'] == rid and second['card_stock'] == {'クースカン': 2}
    assert mem['chart_adjust']['request_digest'] != digest


def test_write_request_binds_the_payload_revision(tmp_path):
    mem = stuck_memory()
    policy.map_step(map_screen(), mem, FRAME)
    [record] = decisions(mem, 'chart_adjust_request')
    payload = adjust.write_request(tmp_path, record, {'game': 'hanjuku-hero', 'runtime_id': 'r',
                                                      'generation': 1, 'lease_id': 'l'})
    assert payload['request_digest'] == adjust.request_digest(record)
    written = json.loads((tmp_path / adjust.REQUEST_FILE).read_text(encoding='utf-8'))
    assert written['request_digest'] == payload['request_digest']
    assert adjust.request_digest({**record, 'card_stock': {'クースカン': 2}}) \
        != payload['request_digest']


def test_a_bound_answer_is_adopted_only_for_the_current_payload_revision():
    mem = stuck_memory()
    policy.map_step(map_screen(), mem, FRAME)
    rid = mem['chart_adjust']['request_id']
    digest = mem['chart_adjust']['request_digest']
    mem['_adjusted'] = adjust.validate(adjusted_doc(rid, request_digest='0' * 64))
    policy.map_step(map_screen(), mem, FRAME)
    assert not decisions(mem, 'chart_adjust_applied') and mem.get('active') is None
    mem['_adjusted'] = adjust.validate(adjusted_doc(rid, request_digest=digest))
    policy.map_step(map_screen(), mem, FRAME)
    [applied] = decisions(mem, 'chart_adjust_applied')
    assert applied['steps'] == [adjust.execution_step(rid, 'J1'),
                                adjust.execution_step(rid, 'J2')]
    # A digestless answer predates the revision field: adopted once (hot-load).
    mem = stuck_memory()
    policy.map_step(map_screen(), mem, FRAME)
    mem['_adjusted'] = adjust.validate(adjusted_doc(mem['chart_adjust']['request_id']))
    policy.map_step(map_screen(), mem, FRAME)
    assert decisions(mem, 'chart_adjust_applied')


def test_worker_discards_answer_when_the_payload_revision_changed(tmp_path):
    from docich import hanjuku_chart_worker as worker
    request = _requested(tmp_path)

    def generate(g, cfg, prompt):
        current = json.loads((tmp_path / adjust.REQUEST_FILE).read_text(encoding='utf-8'))
        current['card_stock'] = {'イッテツーン': 0}
        current['request_digest'] = adjust.request_digest(current)
        (tmp_path / adjust.REQUEST_FILE).write_text(json.dumps(current), encoding='utf-8')
        return json.dumps(adjusted_doc(request['request_id'])), 'codex'
    assert worker.consider(None, Game(), tmp_path, generate=generate, background=False)
    assert adjust.load(tmp_path) is None
    events = [json.loads(line) for line in
              (tmp_path / f'{adjust.HISTORY_LOG}.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [e['status'] for e in events if e['event'] == 'adjust_worker'] == ['superseded']


def test_an_answer_with_no_startable_order_is_invalid(tmp_path):
    request = {'request_id': 'a' * 16, 'lost': [], 'en_route': [],
               'garrison': {'スペンソニア': [chart.HERO], 'ゴーメン': ['ココット']}}
    doc = adjusted_doc('a' * 16, orders=[{'step': 'J1', 'general': 'ココット', 'source': 'スペンソニア',
                                          'target': 'けっかい', 'cards': [], 'after': None}])
    with pytest.raises(ValueError):
        adjust.save(tmp_path, doc, request)
    assert adjust.load(tmp_path) is None
    assert adjust.save(tmp_path, doc)['orders'][0]['step'] == 'J1'   # no request: unchanged behaviour



def test_two_carried_unverified_cards_keep_distinct_identity_after_first_selection():
    from docich.hanjuku_screen import Battle, Screen
    from docich.hanjuku_font import TextLine
    order = {'step': 'I:pair:1', 'general': 'ヴィーナス', 'source': 'カストーラ',
             'target': 'キカンドン', 'cards': ['イッテツーン', 'イッテツーン'], 'after': None}
    mem = {'chapter': 1, 'launched_orders': {'I:pair:1': order},
           'attack': {'general': 'ヴィーナス', 'castle': 'キカンドン', 'side': 'attack', 'step': 'I:pair:1'}}
    battle = Screen(lines=[], hand=None, text='', kind='battle',
                    battle=Battle('ガルバンゾー', 30, 'ヴィーナス', 82))
    policy.battle_step(battle, mem)
    assert policy.battle_step(battle, mem) == [policy.pad('b')]
    cur = mem['battle']
    first = cur['card_flow']['tactic_id']
    for expected_remaining in (1, 0):
        cur['card_flow']['stage'] = 'list'
        listing = Screen(lines=[TextLine(176, tuple((176+8*i,c) for i,c in enumerate('イッテツーン')))],
                         hand=(150,170,172,186), text='イッテツーン', kind='text')
        assert policy.card_list_step(listing, mem) == [policy.pad('a')]
        policy._card_use_unclassified(mem, cur, '選択後の告知未確認')
        assert len(policy._deploy_cards(order, mem)) == expected_remaining
        out = policy.battle_step(battle, mem)
        if expected_remaining:
            assert out == [policy.pad('b')]
            assert cur['card_flow']['card'] == 'イッテツーン'
            assert cur['card_flow']['tactic_id'] != first
        else:
            assert cur['card_flow'] is None
    assert mem['kit_spent']['I:pair:1'] == ['イッテツーン', 'イッテツーン']
    assert len(set(cur['tactics_done'])) == 2


def g514_locked_plan():
    """Limited read-only snapshot; already persisted, bypasses new validation."""
    source = Path(__file__).parent / 'fixtures/hanjuku_g514_locked_plan.json'
    receipt = json.loads(source.read_text())
    mem = receipt['policy']
    mem['_records'] = []
    return receipt, mem


def test_g514_adopted_self_locked_plan_reopens_existing_retake_candidates():
    receipt, mem = g514_locked_plan()
    before = copy.deepcopy(mem)
    rid = adjust.request_id(mem)
    assert rid == mem['chart_plan']['request_id'] == mem['chart_adjust']['request_id']
    assert not policy._plan_pending(mem) and receipt['plan_pending'] is False
    assert policy.interim_candidates(mem) == receipt['candidates']
    assert mem.get('active') is None and not mem.get('recall')
    assert policy.next_order(mem) is None
    policy._off_chart(mem)
    assert mem['chart_adjust']['interim_wanted'] is True
    mem['_interim'] = {'request_id': rid, 'seq': 0, 'status': 'ok',
                       'choice': 'retake_1', 'confidence': 1}
    policy._off_chart(mem)
    order = policy.next_order(mem)
    assert order['target'] == 'ナキューメラ' and order['source'] == 'ほんじょう'
    assert order['purpose'] == 'retake' and order['general'] == 'ヴィーナス'
    assert order['after'] is None
    assert mem['captured'] == before['captured'] and mem['lost'] == before['lost']
    assert mem['garrison'] == before['garrison']
    assert mem.get('house') == before.get('house')  # an in-progress scan remains owned by house
    assert not decisions(mem, 'chart_adjust_applied')  # no duplicate adoption


@pytest.mark.parametrize('present,allows', [(['ヴィーナス', 'ゼウス'], True),
                                            (['ヴィーナス'], False), (None, False)])
def test_g514_recovered_retake_still_requires_a_readable_spare_defender(monkeypatch, present, allows):
    _, mem = g514_locked_plan()
    mem['chart_adjust']['interim_count'] = policy.INTERIM_LIMIT
    policy._off_chart(mem)
    order = policy.next_order(mem)
    assert order['target'] == 'ナキューメラ'
    mem['active'] = order['step']
    monkeypatch.setattr(policy, '_present_generals', lambda screen: present)
    result = policy._keep_sortie_defender(
        Screen(lines=[], hand=(150, 40), text='', kind='general_list'), mem, order)
    assert (result is None) == allows
    if result:
        assert all('a' not in action.get('buttons', []) for action in result)


def test_g514_live_plan_and_recent_target_reservation_still_suppress_retake():
    _, mem = g514_locked_plan()
    mem['chart_plan']['orders'][0]['after'] = None
    mem['garrison']['カストーラ'] = ['ココット', 'ゼウス']
    assert policy._plan_pending(mem)
    policy._off_chart(mem)
    assert not mem['chart_adjust']['interim_wanted']
    _, mem = g514_locked_plan()
    mem['sorties']['recent'] = {'general': 'ヴィーナス', 'target': 'ナキューメラ',
                               'status': 'en_route', 'tick': mem['tick'] - 1}
    assert not any(o['target'] == 'ナキューメラ' for o in policy.interim_candidates(mem).values())
