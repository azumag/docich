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
    assert all(c['cards'] == [] and c['after'] is None for c in candidates.values())
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
    mem['_interim'] = _answer(mem, 'hold')
    policy.map_step(map_screen(), mem, FRAME)
    assert decisions(mem, 'chart_interim_hold')[0]['choice'] == 'hold'
    assert mem['chart_adjust']['interim_wanted'] is False     # limit reached: pure hold
    # The LLM chart still wins when it arrives.
    mem['_adjusted'] = adjust.validate(adjusted_doc(rid))
    policy.map_step(map_screen(), mem, FRAME)
    assert mem['active'] == adjust.execution_step(rid, 'J1')
    assert 'interim_order' not in mem['chart_adjust']


@pytest.mark.parametrize('answer', [
    {'choice': 'attack_99'}, {'choice': None, 'status': 'timeout'},
    {'confidence': 0.2}, {'status': 'missing_key', 'choice': None}])
def test_unusable_jev_answers_hold_without_input(answer):
    mem = stuck_memory()
    policy.map_step(map_screen(), mem, FRAME)
    mem['_interim'] = {**_answer(mem, 'attack_1'), **answer}
    assert policy.map_step(map_screen(), mem, FRAME) == []
    assert mem.get('active') is None and decisions(mem, 'chart_interim_hold')


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
    assert set(request['questions']['interim_action']['criteria']) == {'hold', *policy.interim_candidates(mem)}
    assert '_records' not in json.dumps(request, ensure_ascii=False)
    bad = hanjuku_interim.ask(mem, env={}, transport=lambda r, **k: {
        'status': 'ok', 'data': {'answers': {'interim_action': {'choice': 'up', 'confidence': 1}}}})
    assert bad['status'] == 'invalid_response' and bad['choice'] is None
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
    fake = lambda m, **k: calls.append(1) or _answer(m, 'hold')
    cfg = {'interim_jev': True, 'interim_timeout_ms': 1500}
    meta = {'hanjuku': {'game': 'hanjuku-hero', 'runtime_id': 'r', 'generation': 1, 'lease_id': 'l'}}
    assert module.ask_interim(tmp_path, state, meta, settings=cfg, ask=fake)['choice'] == 'hold'
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
    assert shop['items'] == [['クースカン', 2]] and shop['soldiers'] == 12   # 60 - 2*24
    [plan] = decisions(mem, 'month_plan')
    assert plan['strategy_variant'] == 'chart_adjusted'
    assert plan['deviation_reason'] == 'recruit_menu_unmeasured'
    assert policy._plan(mem, {**header, 'month': 9}) is None
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
    # An ambiguous boss entry is held, never bound to either sortie.
    mem['attack'] = None
    entry = f'{chart.HERO}しょうぐんがボスじょうにせめこんだ!!'
    assert policy.message_step(_text_screen(entry), mem) == []
    assert decisions(mem, 'situation_held')[-1]['observed_metric']['sortie_match'] == 'ambiguous'


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
    doc['purchases'] = {'month': [1, 8], 'cards': [['ダイチスイム', 5]], 'soldiers': 50}
    mem['_adjusted'] = adjust.validate(doc)
    policy.map_step(map_screen(), mem, FRAME)
    mem['_adjusted'] = None
    mem['_records'] = []
    header = {'chapter': 1, 'year': 1, 'month': 8, 'gold': 60}
    shop = policy._plan(mem, header)
    [plan] = decisions(mem, 'month_plan')
    assert plan['plan']['unpriced_cards'] == ['ダイチスイム']
    assert shop['soldiers'] == 50 and shop['soldiers_from_gold']     # provisional only
    # The merchant really charged for the cards: 20G remain on screen.
    shop['merchant_done'] = True
    month = _text_screen('', 'month_menu')
    month.header = {**header, 'gold': 20}
    policy.month_step(month, mem)
    [recalc] = decisions(mem, 'soldier_plan_recalc')
    assert shop['soldiers'] == 20 and recalc['observed_metric']['gold_after_merchant'] == 20
    # Recomputed once; a later frame does not resize again.
    month.header = {**header, 'gold': 5}
    policy.month_step(month, mem)
    assert shop['soldiers'] == 20 and len(decisions(mem, 'soldier_plan_recalc')) == 1


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
