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


def measured_card_select(names=('イッテツーン', 'ダイチスイム', 'ブラッキー', 'フットバース'),
                         *, selected=0, stock='2', remaining='3'):
    rows = {31: [(136 + 8*i, ch) for i, ch in enumerate('きりふだセレクト')]
                  + [(208 + 8*i, ch) for i, ch in enumerate(f'あと{remaining}こ')],
            127: [(136 + 8*i, ch) for i, ch in enumerate('バトルようのきりふだです')]}
    for i, name in enumerate(names):
        rows[55 + 16*i] = [(160 + 8*j, ch) for j, ch in enumerate(name)] + [(232, stock)]
    for x, y in [(16, 23), (160, 23), (136, 47), (144, 47), (136, 55), (144, 55),
                 (160, 63), (160, 79), (32, 87), (184, 95), (32, 103), (208, 119)]:
        rows.setdefault(y, []).append((x, UNKNOWN))
    lines = [TextLine(y, tuple(sorted(cells))) for y, cells in sorted(rows.items())]
    return Screen(lines=lines, text=''.join(line.known for line in lines), kind='card_select',
                  hand=(138, 49 + selected*16, 158, 62 + selected*16))


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
    assert policy.deploy_step(measured_card_select(('クースカン', 'ノリウツール', 'イッテツーン', 'ダイチスイム')), mem) == [policy.pad('a')]
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
               general_override={'1-A2': 'ゼウス'})
    screen = measured_loaded_sortie()
    assert policy.deploy_step(screen, mem) == [policy.pad('a')]
    rec = mem['_records'][-1]
    assert rec['general'] == 'ゼウス' and rec['planned_general'] == 'どうし'
    assert rec['strategy_variant'] == 'substitute_general' and rec['deviation_reason']
    context = mem['order_context']['1-A2']
    assert context['actual_general'] == 'ゼウス' and context['planned_general'] == 'どうし'
    assert context['expected_metric'] == {'general': 'どうし', 'cards': ['フットバース']}
    assert context['observed_metric'] == {'general': 'ゼウス', 'cards': ['フットバース']}


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


def test_boss_visible_hero_does_not_authorize_an_uncalibrated_kit(monkeypatch):
    mem = memory()
    mem['general_override'] = {'1-B1': 'ゼウス'}
    mem['card_override'] = {'1-B1': ['イッテツーン', 'イッテツーン']}
    assert policy.deploy_step(menu('general_list', ['どうし', 'ゼウス']), mem) == [policy.pad('a')]
    assert mem['sortie_general']['1-B1'] == 'どうし' and '1-B1' not in mem['general_override']
    for cards in (['イッテツーン', 'イッテツーン'], ['クースカン', 'ノリウツール']):
        assert policy.deploy_step(menu('sortie_confirm', ['うむッ!'] + cards), mem) == []
    assert '1-B1' not in mem['order_context']
    monkeypatch.setattr(policy, 'nav_step', lambda *args: pytest.fail('unverified boss navigation'))
    assert policy.target_step(menu('map_target', []), mem, None) == []


def test_boss_target_uses_existing_context_without_relabeling_hero(monkeypatch):
    # Unit-test an already supplied context; this does not calibrate a receipt.
    mem = memory()
    mem['order_context'] = {'1-B1': {'actual_general': 'どうし',
                                   'observed_metric': {'cards': ['クースカン', 'ノリウツール']}}}
    mem['general_override'] = {'1-B1': 'ゼウス'}
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
    mem = foot_order_memory()
    context = {'strategy_variant': 'retry_with_opening_cards', 'deviation_reason': '敗北後の再出撃',
               'expected_metric': {'cards': ['フットバース'], 'goal': '次戦勝利'}}
    if retry:
        mem['retry_context'] = {'1-A2': context}
    nav_results = iter([[policy.pad('down')], 'arrived'])
    monkeypatch.setattr(policy, 'nav_step', lambda *args: next(nav_results))
    screens = [menu('castle_menu', ['しゅつげき']),
               menu('general_list', ['ゼウス', 'どうし']),
               menu('general_list', ['どうし']),
               measured_card_select(), measured_card_select(selected=3),
               measured_card_select(), measured_loaded_sortie(),
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
            assert plan['chart_step'] == '1-A2'
        else:
            assert plan['strategy_variant'] == 'chart' and plan['deviation_reason'] is None
        seen.update(record['decision'] for record in records)
    assert {'card_pick', 'sortie_confirm', 'order_launched'} <= seen
    if retry:
        assert 'sortie_input' in seen
        assert mem['order_context']['1-A2']['expected_metric'] == context['expected_metric']


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


def measured_empty_sortie():
    # Reconstruct only the measured text-cell layout, never the ROM screenshot.
    spans = [(47, 136, 'きりふだ'), (63, 176, 'きりふだは'), (79, 176, 'ありません……'),
             (95, 136, 'ーしゅつげき'), (95, 192, 'しますか?ー'),
             (111, 160, 'うむッ!'), (127, 160, 'いかんッ!')]
    rows = {}
    for y, x, text in spans:
        rows.setdefault(y, []).extend((x + 8 * i, ch) for i, ch in enumerate(text))
    # Measured non-body glyph rows and left/cursor tiles are not card text.
    for x, y in [(16, 23), (104, 23), (160, 39), (200, 55), (168, 87),
                 (0, 95), (136, 103), (144, 103), (136, 111), (144, 111), (72, 119)]:
        rows.setdefault(y, []).append((x, UNKNOWN))
    lines = [TextLine(y, tuple(sorted(cells))) for y, cells in sorted(rows.items())]
    return Screen(lines=lines, text=''.join(line.known for line in lines),
                  kind='sortie_confirm', hand=(138, 105, 156, 118))


def empty_order_memory():
    return {'chapter': 1, 'active': '1-A1', 'variant': 'chart',
            'orders': {'1-A1': 'pending'}, 'picked': []}


def replace_cell(screen, y, x, value):
    rows = {line.y: dict(line.cells) for line in screen.lines}
    if value is None:
        rows[y].pop(x)
    else:
        rows.setdefault(y, {})[x] = value
    screen.lines = [TextLine(row_y, tuple(sorted(cells.items()))) for row_y, cells in sorted(rows.items())]
    screen.text = ''.join(line.known for line in screen.lines)


def test_measured_empty_sortie_ignores_only_background_and_cursor_unknowns():
    screen = measured_empty_sortie()
    assert any(UNKNOWN in line.text for line in screen.lines)
    assert policy._empty_sortie_inventory(screen)
    mem = empty_order_memory()
    assert policy.deploy_step(screen, mem) == [policy.pad('a')]
    record = mem['_records'][-1]
    assert record['decision'] == 'sortie_confirm' and record['cards'] == []
    assert record['inventory_evidence'] == 'measured_empty_sortie'
    assert mem['order_context']['1-A1']['observed_metric']['cards'] == []


@pytest.mark.parametrize('y,x,value', [(63, 208, None), (79, 224, None),
    (63, 200, UNKNOWN), (79, 232, UNKNOWN), (47, 136, 'あ'),
    (95, 192, UNKNOWN), (111, 160, UNKNOWN), (127, 192, None)])
def test_partial_empty_receipt_and_unknown_body_cells_hold(y, x, value):
    screen = measured_empty_sortie()
    replace_cell(screen, y, x, value)
    assert not policy._empty_sortie_inventory(screen)
    mem = empty_order_memory()
    assert policy.deploy_step(screen, mem) == []
    assert mem['_records'][-1]['decision'] == 'situation_held'
    assert '1-A1' not in mem['order_context']


def test_empty_receipt_rejects_shifted_text_and_present_card_names():
    for change in ('shift', 'card'):
        screen = measured_empty_sortie()
        if change == 'shift':
            screen.lines = [TextLine(line.y, tuple((x - 8, ch) for x, ch in line.cells))
                            if line.y == 63 else line for line in screen.lines]
        else:
            screen.lines.append(TextLine(143, tuple((136 + 8 * i, ch) for i, ch in enumerate('クースカン'))))
        assert not policy._empty_sortie_inventory(screen)
        assert policy.deploy_step(screen, empty_order_memory()) == []


def test_missing_empty_receipt_never_turns_no_readable_cards_into_empty_inventory():
    screen = menu('sortie_confirm', ['うむッ!', 'いかんッ!'])
    assert policy.deploy_step(screen, empty_order_memory()) == []


def test_measured_empty_receipt_does_not_approve_boss_without_its_kit():
    mem = memory()
    assert policy._empty_sortie_inventory(measured_empty_sortie())
    assert policy.deploy_step(measured_empty_sortie(), mem) == []
    assert mem['_records'][-1]['decision'] == 'situation_held'
    assert '1-B1' not in mem['order_context']


def test_nonempty_uncalibrated_unknowns_remain_held():
    screen = menu('sortie_confirm', ['うむッ!', 'クースカン', 'ノリウツール'])
    replace_cell(screen, 23, 16, UNKNOWN)
    assert not policy._empty_sortie_inventory(screen)
    assert policy.deploy_step(screen, memory()) == []


def foot_order_memory():
    return {'chapter': 1, 'active': '1-A2', 'variant': 'chart',
            'orders': {'1-A2': 'pending'}, 'picked': []}


def test_measured_card_menu_ignores_background_and_moves_to_footbath():
    mem = foot_order_memory()
    for selected in range(3):
        screen = measured_card_select(selected=selected)
        assert any(UNKNOWN in line.text for line in screen.lines)
        assert policy.deploy_step(screen, mem) == [policy.pad('down')]
        assert mem['picked'] == []
    assert policy.deploy_step(measured_card_select(selected=3), mem) == [policy.pad('a')]
    assert mem['picked'] == ['フットバース']
    record = mem['_records'][-1]
    assert record['decision'] == 'card_pick'
    assert record['resulting_event'] == 'selection_planned_not_yet_confirmed'
    assert record['observed_metric'] == {'stock': 2, 'remaining': 3,
                                         'inventory_evidence': 'measured_four_row_card_select'}
    assert not mem['order_context']


@pytest.mark.parametrize('y,x,value', [(31, 136, 'あ'), (31, 224, UNKNOWN), (31, 240, 'こ'),
    (55, 160, UNKNOWN), (71, 168, 'あ'), (103, 232, UNKNOWN), (103, 232, None),
    (103, 224, '1'), (103, 240, '0'), (87, 208, UNKNOWN), (127, 136, None)])
def test_unmeasured_card_menu_cells_never_select_or_infer_missing(y, x, value):
    screen = measured_card_select(selected=3)
    replace_cell(screen, y, x, value)
    mem = foot_order_memory()
    assert policy.deploy_step(screen, mem) == []
    assert mem['picked'] == [] and mem['active'] == '1-A2'
    assert mem['_records'][-1]['decision'] == 'situation_held'
    assert not policy._measured_card_select(screen)


@pytest.mark.parametrize('hand', [None, (122, 49, 139, 62), (138, 50, 158, 63),
                                   (138, 113, 158, 126), (138, 49, 158, 61)])
def test_unknown_card_cursor_position_or_shape_is_held(hand):
    screen = measured_card_select()
    screen.hand = hand
    mem = foot_order_memory()
    assert policy.deploy_step(screen, mem) == [] and mem['picked'] == []


@pytest.mark.parametrize('change', ['zero_stock', 'zero_slots', 'missing_row', 'extra_row', 'wrong_name'])
def test_zero_inventory_or_unmeasured_list_layout_is_held(change):
    screen = measured_card_select(selected=3)
    if change == 'zero_stock':
        replace_cell(screen, 103, 232, '0')
    elif change == 'zero_slots':
        replace_cell(screen, 31, 224, '0')
    elif change == 'missing_row':
        screen.lines = [line for line in screen.lines if line.y != 87]
    elif change == 'extra_row':
        replace_cell(screen, 111, 160, 'あ')
    else:
        screen = measured_card_select(('クースカン', 'ダイチスイム', 'ブラッキー', 'ノリウツール'))
    mem = foot_order_memory()
    assert policy.deploy_step(screen, mem) == []
    assert mem['picked'] == []
    assert mem['_records'][-1]['decision'] == 'situation_held'


def test_old_minimal_synthetic_card_label_is_not_a_measured_inventory():
    mem = foot_order_memory()
    assert policy.deploy_step(menu('card_select', ['フットバース']), mem) == []
    assert mem['picked'] == []


@pytest.mark.parametrize('y,x,text', [(119, 160, 'クースカン'), (119, 136, 'あ'),
                                     (39, 136, 'あ'), (39, 160, 'ノリウツール')])
def test_extra_right_pane_text_outside_measured_rows_rejects_menu(y, x, text):
    screen = measured_card_select(selected=3)
    for i, ch in enumerate(text):
        replace_cell(screen, y, x + 8*i, ch)
    mem = foot_order_memory()
    assert policy._measured_card_select(screen) is None
    assert policy.deploy_step(screen, mem) == []
    assert mem['picked'] == []
    assert mem['_records'][-1]['decision'] == 'situation_held'


@pytest.mark.parametrize('retry', [False, True])
def test_calibrated_card_cursor_decision_is_joined_to_action_plan(tmp_path, retry):
    mem = foot_order_memory()
    context = {'strategy_variant': 'retry_with_opening_cards',
               'deviation_reason': '戦闘敗北後の再出撃',
               'expected_metric': {'goal': '次戦の勝利'}}
    if retry:
        mem['retry_context'] = {'1-A2': context}
    actions = policy.deploy_step(measured_card_select(), mem)
    assert actions == [policy.pad('down')]
    assert mem['picked'] == []
    assert len(mem['_records']) == 1
    record = mem['_records'][0]
    assert record['decision'] == 'sortie_input' and record['reason']
    observed = {'desired_card': 'フットバース', 'selected_y': 55, 'selected_card': 'イッテツーン',
                'target_y': 103, 'target_stock': 2, 'remaining': 3,
                'inventory_evidence': 'measured_four_row_card_select'}
    assert record['observed_metric'] == observed
    if retry:
        assert all(record[key] == value for key, value in context.items())
    else:
        assert record['strategy_variant'] == 'chart' and record['deviation_reason'] is None
        assert record['expected_metric'] == {'card': 'フットバース', 'goal': '予定切り札へカーソルを合わせる'}
    path = Path(__file__).resolve().parents[1] / 'brains/hanjuku/bot.py'
    spec = importlib.util.spec_from_file_location('sortie_card_move_trace_bot', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = {'step': 1115, 'screen_kind': 'card_select', 'policy': mem}
    identity = {'game': 'hanjuku-hero', 'runtime_id': 'g328-test', 'generation': 328, 'lease_id': 'test-lease'}
    module.persist(tmp_path, state, mem['_records'], {'hanjuku': identity},
                   actions=actions, frame_sha256='c'*64)
    entries = [json.loads(line) for line in (tmp_path/'hanjuku_decisions.jsonl').read_text().splitlines()]
    plan, decision = entries
    assert plan['reason_decisions'] == ['sortie_input']
    assert plan['decision_id'] == decision['decision_id'] == state['decision_trace']['decision_id']
    assert plan['frame_sha256'] == decision['frame_sha256'] == 'c'*64
    assert decision['observed_metric'] == observed
    for key in ('chart_step', 'strategy_variant', 'deviation_reason', 'expected_metric'):
        assert plan[key] == decision[key] == record[key]
    assert all(plan[key] == decision[key] == value for key, value in identity.items())


@pytest.mark.parametrize('y', [55, 71, 87, 103])
@pytest.mark.parametrize('x', [136, 144])
def test_known_text_in_card_row_cursor_margin_rejects_menu(y, x):
    screen = measured_card_select(selected=3)
    replace_cell(screen, y, x, 'あ')
    mem = foot_order_memory()
    assert policy._measured_card_select(screen) is None
    assert policy.deploy_step(screen, mem) == []
    assert mem['picked'] == []
    assert mem['_records'][-1]['decision'] == 'situation_held'


def measured_loaded_sortie(card='フットバース'):
    screen = measured_empty_sortie()
    rows = {line.y: dict(line.cells) for line in screen.lines}
    for y in (47, 63, 79):
        rows[y] = {x: ch for x, ch in rows[y].items() if x < 136}
    rows[47].update((136 + 8*i, ch) for i, ch in enumerate('きりふだ'))
    rows[47].update((176 + 8*i, ch) for i, ch in enumerate(card))
    rows[47].update({16: 'H', 24: 'P', 56: '9', 64: '0', 72: '/', 88: '9', 96: '0'})
    rows.setdefault(31, {}).update({136: 'へ', 144: 'い', 152: 'し', 208: '6', 224: 'に', 232: 'ん'})
    screen.lines = [TextLine(y, tuple(sorted(cells.items()))) for y, cells in sorted(rows.items())]
    screen.text = ''.join(line.known for line in screen.lines)
    return screen


@pytest.mark.parametrize('right_edge', [156, 158])
def test_measured_single_card_receipt_ignores_left_stats_and_background(right_edge):
    screen = measured_loaded_sortie()
    screen.hand = (138, 105, right_edge, 118)
    assert policy._single_card_sortie_inventory(screen) == ['フットバース']
    mem = foot_order_memory()
    assert policy.deploy_step(screen, mem) == [policy.pad('a')]
    record = mem['_records'][-1]
    assert record['cards'] == ['フットバース']
    assert record['inventory_evidence'] == 'measured_single_card_sortie'
    assert mem['order_context']['1-A2']['observed_metric']['cards'] == ['フットバース']


@pytest.mark.parametrize('y,x,value', [(47, 200, UNKNOWN), (47, 216, None), (47, 224, '2'),
    (47, 232, UNKNOWN), (63, 176, UNKNOWN), (79, 176, 'あ'), (55, 136, 'あ'),
    (87, 160, 'あ'), (119, 160, 'あ'), (95, 136, UNKNOWN), (111, 160, UNKNOWN)])
def test_partial_or_extra_single_card_receipt_is_held(y, x, value):
    screen = measured_loaded_sortie()
    replace_cell(screen, y, x, value)
    assert policy._single_card_sortie_inventory(screen) is None
    mem = foot_order_memory()
    assert policy.deploy_step(screen, mem) == []
    assert mem['_records'][-1]['decision'] == 'situation_held'
    assert '1-A2' not in mem['order_context']


@pytest.mark.parametrize('hand', [None, (130, 105, 150, 118), (138, 121, 156, 134)])
def test_single_card_receipt_with_unknown_cursor_is_held(hand):
    screen = measured_loaded_sortie()
    screen.hand = hand
    assert policy.deploy_step(screen, foot_order_memory()) == []


def test_single_card_and_empty_receipts_cannot_substitute_for_required_inventory():
    assert policy.deploy_step(measured_loaded_sortie(), empty_order_memory()) == []
    assert policy.deploy_step(measured_empty_sortie(), foot_order_memory()) == []
    assert policy.deploy_step(measured_loaded_sortie('クースカン'), memory()) == []


@pytest.mark.parametrize('y', [63, 79, 143])
def test_additional_card_is_not_a_calibrated_one_card_receipt(y):
    screen = measured_loaded_sortie()
    for i, ch in enumerate('クースカン'):
        replace_cell(screen, y, 176 + i*8, ch)
    assert policy._single_card_sortie_inventory(screen) is None
    assert policy.deploy_step(screen, foot_order_memory()) == []


def test_old_complete_name_synthetic_does_not_confirm_unmeasured_layout():
    screen = menu('sortie_confirm', ['うむッ!', 'フットバース'])
    assert policy.deploy_step(screen, foot_order_memory()) == []
