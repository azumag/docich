"""Sortie uncertainty uses synthetic structured observations, not ROM assets.

Quantity layouts remain uncalibrated; these fixtures assert holds rather than
pretending a synthetic inventory format is measured on the live game.
"""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_policy as policy
from docich.hanjuku_font import TextLine, UNKNOWN
from docich.hanjuku_pixels import Frame, read_png
from docich.hanjuku_screen import Screen


def menu(kind, words, hand=True):
    lines = [TextLine(47 + 16 * index, tuple((144 + 8 * i, ch) for i, ch in enumerate(word)))
             for index, word in enumerate(words)]
    return Screen(lines=lines, text=''.join(words), kind=kind,
                  hand=(122, 41, 139, 53) if hand else None)


def memory():
    return {'chapter': 1, 'active': '1-B1', 'variant': 'chart',
            'orders': {'1-B1': 'pending'}, 'picked': [], 'sortie_general': {'1-B1': 'どうし'}}


@pytest.mark.parametrize('word,hand', [('クースカン', False), ('クースカン1', True),
                                      ('クースカン' + UNKNOWN, True), ('ノリウツール', True)])
def test_unreadable_card_target_never_becomes_missing_or_picked(word, hand):
    mem = memory()
    screen = menu('card_select', [word], hand)
    # A known card preceding an unknown glyph is still not a complete label.
    for _ in range(2):
        assert policy.deploy_step(screen, mem) == []
        assert mem['picked'] == [] and mem['active'] == '1-B1'
        assert mem['orders']['1-B1'] == 'pending'
    assert all(r['decision'] == 'situation_held' for r in mem['_records'])
    record = mem['_records'][-1]
    assert record['card'] == 'クースカン'
    assert record['observed_metric']['confirmation'] == 'unclassified'
    assert record['observed_metric']['candidates']
    assert record['deviation_reason'] and record['strategy_variant'] != 'chart'


def test_exact_card_selection_is_only_a_plan_and_clears_stale_context():
    mem = memory()
    mem['order_context'] = {'1-B1': {'actual_general': 'old'}}
    assert policy.deploy_step(menu('card_select', ['クースカン']), mem) == [policy.pad('a')]
    assert mem['picked'] == ['クースカン']
    assert '1-B1' not in mem['order_context']
    assert mem['_records'][-1]['resulting_event'] == 'selection_planned_not_yet_confirmed'


def test_general_cursor_failure_does_not_install_a_substitute():
    mem = memory()
    assert policy.deploy_step(menu('general_list', ['どうし', 'ゼウス'], hand=False), mem) == []
    assert not mem.get('general_override') and not mem.get('source_override')
    assert mem['active'] == '1-B1' and mem['_records'][-1]['decision'] == 'situation_held'


@pytest.mark.parametrize('cards,hand', [([], True), (['クースカン'], True),
    (['クースカン1', 'ノリウツール'], True),
    (['クースカン', 'ノリウツール', UNKNOWN], True),
    (['クースカン', 'ノリウツール'], False)])
def test_uncertain_or_mismatched_sortie_is_not_approved(cards, hand):
    mem = memory()
    mem['picked'] = ['クースカン', 'ノリウツール']  # even a legacy false selection is insufficient
    mem['order_context'] = {'1-B1': {'actual_general': 'old'}}
    assert policy.deploy_step(menu('sortie_confirm', ['うむッ!'] + cards, hand), mem) == []
    assert mem['active'] == '1-B1' and mem['orders']['1-B1'] == 'pending'
    assert 'cursor' not in mem and '1-B1' not in mem['order_context']
    assert mem['picked'] == ['クースカン', 'ノリウツール']
    rec = mem['_records'][-1]
    assert rec['decision'] == 'situation_held'
    assert rec['strategy_variant'] == 'sortie_cards_unclassified'
    assert rec['observed_metric']['confirmation'] == 'unclassified'
    assert not any(r['decision'] == 'sortie_confirm' for r in mem['_records'])


def test_confirmed_card_names_record_selected_and_planned_generals_separately():
    mem = memory()
    mem.update(active='1-A2', orders={'1-A2': 'pending'},
               general_override={'1-A2': 'ゼウス'},
               card_override={'1-A2': ['クースカン', 'ノリウツール']})
    screen = menu('sortie_confirm', ['うむッ!', 'クースカン', 'ノリウツール'])
    assert policy.deploy_step(screen, mem) == [policy.pad('a')]
    rec = mem['_records'][-1]
    assert rec['general'] == 'ゼウス' and rec['planned_general'] == 'どうし'
    assert rec['strategy_variant'] == 'substitute_general' and rec['deviation_reason']
    context = mem['order_context']['1-A2']
    assert context['actual_general'] == 'ゼウス' and context['planned_general'] == 'どうし'
    assert context['expected_metric'] == {'general': 'どうし', 'cards': ['クースカン', 'ノリウツール']}
    assert context['observed_metric'] == {'general': 'ゼウス', 'cards': ['クースカン', 'ノリウツール']}


def test_launch_record_uses_selected_general_and_keeps_chart_general(monkeypatch):
    mem = memory()
    mem.update(active='1-A2', orders={'1-A2': 'pending'}, general_override={'1-A2': 'ゼウス'})
    monkeypatch.setattr(policy, 'nav_step', lambda *args: 'arrived')
    assert policy.target_step(menu('map_target', []), mem, None) == [policy.pad('a')]
    rec = mem['_records'][-1]
    assert rec['decision'] == 'order_launched'
    assert rec['general'] == mem['launched']['ゴーメン']['general'] == 'ゼウス'
    assert rec['planned_general'] == 'どうし'
    assert rec['strategy_variant'] == 'substitute_general' and rec['deviation_reason']
    assert 'ゼウス' in rec['reason'] and 'どうしを' not in rec['reason']


@pytest.mark.parametrize('kind,record', [('card_select', None), ('sortie_confirm', None),
    ('general_list', None),
    ('general_list', {'decision': 'sortie_input', 'screen': 'general_list', 'reason': '主人公選択'}),
    ('map', {'decision': 'order_start', 'cards': ['クースカン'], 'general': 'どうし',
             'source': 'スペンソニア', 'target': 'けっかい', 'reason': '携行計画'}),
    ('text', {'decision': 'card_missing'}),
    ('text', {'decision': 'situation_held', 'screen': 'card_select', 'reason': '読取保留'})])
def test_sortie_capture_keeps_exact_read_frame_even_without_records(tmp_path, kind, record):
    path = Path(__file__).resolve().parents[1] / 'brains/hanjuku/bot.py'
    spec = importlib.util.spec_from_file_location('sortie_capture_bot', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    frame = Frame(256, 224, bytes(256 * 224 * 3))
    state = {'step': 241, 'screen_kind': kind, 'policy': memory()}
    module.persist(tmp_path, state, [] if record is None else [record], {'hanjuku': {}},
                   actions=[], frame_sha256=frame.digest(), frame=frame)
    entries = [json.loads(line) for line in (tmp_path/'hanjuku_decisions.jsonl').read_text().splitlines()]
    plan = entries[0]
    assert plan['snapshot'] == 'decision-001.png'
    assert read_png(tmp_path/'hanjuku_frames'/plan['snapshot']).digest() == plan['frame_sha256']
    assert state['decision_trace']['frame_sha256'] == plan['frame_sha256']
    assert len(list((tmp_path/'hanjuku_frames').glob('*.png'))) == 1


@pytest.mark.parametrize('words,hand', [(['ゼウス'], True), (['どうし', 'ゼウス'], False),
                                       (['どうし' + UNKNOWN], True)])
def test_boss_never_substitutes_for_an_unconfirmed_hero(words, hand):
    mem = memory()
    mem['general_override'] = {'1-B1': 'ゼウス'}
    assert policy.deploy_step(menu('general_list', words, hand), mem) == []
    assert '1-B1' not in mem['sortie_general']
    assert not mem.get('source_override')
    assert mem['orders']['1-B1'] == 'pending' and mem['active'] == '1-B1'
    assert mem['_records'][-1]['decision'] == 'situation_held'


def test_boss_visible_hero_replaces_legacy_override_and_requires_chart_kit(monkeypatch):
    mem = memory()
    mem['general_override'] = {'1-B1': 'ゼウス'}
    mem['card_override'] = {'1-B1': ['イッテツーン', 'イッテツーン']}
    assert policy.deploy_step(menu('general_list', ['どうし', 'ゼウス']), mem) == [policy.pad('a')]
    assert mem['sortie_general']['1-B1'] == 'どうし' and '1-B1' not in mem['general_override']
    assert policy.deploy_step(menu('sortie_confirm', ['うむッ!', 'イッテツーン', 'イッテツーン']), mem) == []
    assert policy.deploy_step(menu('sortie_confirm', ['うむッ!', 'クースカン', 'ノリウツール']), mem) == [policy.pad('a')]
    assert mem['order_context']['1-B1']['actual_general'] == 'どうし'
    # Persisted stale overrides cannot relabel the verified boss hero at launch.
    mem['general_override']['1-B1'] = 'ゼウス'
    monkeypatch.setattr(policy, 'nav_step', lambda *args: 'arrived')
    assert policy.target_step(menu('map_target', []), mem, None) == [policy.pad('a')]
    assert mem['launched']['けっかい']['general'] == 'どうし'


@pytest.mark.parametrize('kind', ['card_select', 'sortie_confirm', 'map_target'])
def test_legacy_boss_flow_without_hero_evidence_is_held(kind, monkeypatch):
    mem = memory()
    mem.pop('sortie_general')
    mem['general_override'] = {'1-B1': 'ゼウス'}
    mem['order_context'] = {'1-B1': {'actual_general': 'ゼウス',
                                   'observed_metric': {'cards': ['クースカン', 'ノリウツール']}}}
    monkeypatch.setattr(policy, 'nav_step', lambda *args: pytest.fail('unverified boss navigation'))
    screen = menu(kind, ['うむッ!', 'クースカン', 'ノリウツール'])
    actions = policy.target_step(screen, mem, None) if kind == 'map_target' else policy.deploy_step(screen, mem)
    assert actions == [] and mem['active'] == '1-B1'
    assert mem['_records'][-1]['decision'] == 'situation_held'


def retry_context():
    return {'strategy_variant': 'retry_chart_boss_kit',
            'deviation_reason': 'ボス戦のHP敗北を確認。主人公と既定切り札を再確認して再試行',
            'expected_metric': {'general': 'どうし', 'cards': ['クースカン', 'ノリウツール'],
                                'goal': 'クイーン戦勝利'}}


@pytest.mark.parametrize('retry', [False, True])
def test_retry_context_follows_every_sortie_input_and_action_plan(tmp_path, monkeypatch, retry):
    path = Path(__file__).resolve().parents[1] / 'brains/hanjuku/bot.py'
    spec = importlib.util.spec_from_file_location('sortie_retry_trace_bot', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    mem = memory()
    context = retry_context()
    if retry:
        mem['retry_context'] = {'1-B1': context}
    nav_results = iter([[policy.pad('down')], 'arrived'])
    monkeypatch.setattr(policy, 'nav_step', lambda *args: next(nav_results))
    screens = [menu('castle_menu', ['しゅつげき']),
               menu('general_list', ['ゼウス', 'どうし']),
               menu('general_list', ['どうし']),
               menu('card_select', ['ノリウツール', 'クースカン']),
               menu('card_select', ['クースカン']),
               menu('card_select', ['ノリウツール']),
               menu('card_select', []),
               menu('sortie_confirm', ['いかんッ!', 'うむッ!', 'クースカン', 'ノリウツール']),
               menu('sortie_confirm', ['うむッ!', 'クースカン', 'ノリウツール']),
               menu('map_target', []), menu('map_target', [])]
    seen = set()
    for step, screen in enumerate(screens):
        mem['_records'] = []
        actions = (policy.target_step(screen, mem, None) if screen.kind == 'map_target'
                   else policy.deploy_step(screen, mem))
        assert actions
        records = mem['_records']
        state = {'step': step, 'screen_kind': screen.kind, 'policy': mem}
        module.persist(tmp_path, state, records, {'hanjuku': {}}, actions=actions, frame_sha256='e'*64)
        entries = [json.loads(line) for line in (tmp_path/'hanjuku_decisions.jsonl').read_text().splitlines()]
        plan = next(r for r in reversed(entries) if r['event'] == 'action_plan')
        if retry:
            assert records, screen.kind
            for key in ('strategy_variant', 'deviation_reason', 'expected_metric'):
                assert plan[key] == context[key], (screen.kind, key)
                assert all(record[key] == context[key] for record in records), (screen.kind, key)
            assert plan['chart_step'] == '1-B1'
        else:
            assert plan['strategy_variant'] == 'chart' and plan['deviation_reason'] is None
        seen.update(record['decision'] for record in records)
    assert {'card_pick', 'sortie_confirm', 'order_launched'} <= seen
    if retry:
        assert 'sortie_input' in seen
        assert mem['order_context']['1-B1']['expected_metric'] == context['expected_metric']


def test_retry_hold_preserves_strategy_and_logs_current_uncertainty():
    mem = memory()
    context = retry_context()
    mem['retry_context'] = {'1-B1': context}
    assert policy.deploy_step(menu('card_select', ['クースカン'], hand=False), mem) == []
    record = mem['_records'][-1]
    assert record['decision'] == 'situation_held'
    assert all(record[key] == value for key, value in context.items())
    assert record['observed_metric']['confirmation'] == 'unclassified'
    assert 'カーソル' in record['reason']
    assert mem['picked'] == []
