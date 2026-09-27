"""Off-chart sorties: retake, attack with idle generals, staff empty castles.

Regressions from g401 (2026-09-27): after the chart ran out the bot pressed A
900 times 11 px below キカンドン (no menu), never noticed that the undefended
ジョンリギ had turned enemy, and only ever re-sent the chart's general (who was
already marching elsewhere) while ゼウス idled in the home castle.
Synthetic roofs and screens only; no ROM images.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_chart as chart
from docich import hanjuku_commentary, hanjuku_interim
from docich import hanjuku_policy as policy
from docich.hanjuku_pixels import Frame
from docich.hanjuku_screen import Screen

FRAME = Frame(256, 224, bytes(256 * 224 * 3))
CASTLES = chart.castles(1)


def map_screen(x, y):
    return Screen(lines=[], hand=None, text='', kind='map', cursor=(x, y))


def decisions(mem, kind):
    return [r for r in mem['_records'] if r['decision'] == kind]


def g401_roofs():
    """Roofs of the g401 20:22 frame: ジョンリギ blue, キカンドン red, a village."""
    return [{'kind': 'enemy', 'target': (175, 45), 'clipped': False},
            {'kind': 'own', 'target': (62, 105), 'clipped': False},
            {'kind': 'own', 'target': (195, 189), 'clipped': False}]


def g401_memory():
    """Policy memory of g401 at 20:07: every first-wave order launched."""
    return {'chapter': 1, 'variant': 'chart', 'picked': [], '_records': [],
            'captured': ['キカンドン', 'ナキューメラ', 'ジョンリギ', 'カストーラ'],
            'orders': {'1-A1': 'launched', '1-A2': 'launched', '1-C1': 'launched',
                       '1-C2': 'launched', '1-V1': 'launched', '1-V2': 'launched'},
            'sorties': {'1-A2': {'general': 'どうし', 'target': 'ゴーメン', 'status': 'en_route'},
                        '1-C2': {'general': 'ココット', 'target': 'スペンソニア',
                                 'status': 'en_route'}},
            'garrison': {'ほんじょう': ['ゼウス'], 'キカンドン': [], 'ジョンリギ': [],
                         'カストーラ': ['ヴィーナス']}}


def test_goal_roof_corrects_a_cursor_one_cell_below_the_source(monkeypatch):
    monkeypatch.setattr(policy, 'castle_roofs', lambda *_a, **_k: g401_roofs())
    order = {'step': 'I:19d3c3:1', 'general': 'どうし', 'source': 'キカンドン',
             'target': 'ゴーメン', 'cards': [], 'after': None, 'note': 'test'}
    mem = {**g401_memory(), 'active': order['step'],
           'chart_adjust': {'request_id': '19d3c3', 'interim_order': order},
           'garrison': {}, 'cursor': [566, 729], 'uncertain': False}
    # The camera voted from ジョンリギ said "arrived" (566,729 vs 567,725) and
    # A opened nothing. The キカンドン roof puts the cursor 11 px too low.
    actions = policy.map_step(map_screen(190, 200), mem, FRAME)
    assert mem['anchor'] == 'キカンドン'
    assert mem['cursor'] == [CASTLES['キカンドン'][0] - 5, CASTLES['キカンドン'][1] + 11]
    # 5 px left and 11 px low of the roof's cell: step right and up, no A.
    assert [a['buttons'][0] for a in actions] == ['right', 'up']
    assert mem.get('expect_menu') is None
    # Standing on the roof's cell, the source is confirmed with A.
    mem['_records'] = []
    actions = policy.map_step(map_screen(195, 189), mem, FRAME)
    assert actions == [policy.pad('a')] and mem['expect_menu'] is True


def test_goal_anchor_ignores_ambiguous_or_absent_goal_roofs():
    roofs = g401_roofs()
    cam = (376, 529)
    assert policy._goal_anchor(roofs, CASTLES, cam, 'ジョンリギ', None) == (cam, 'ジョンリギ')
    assert policy._goal_anchor(roofs, CASTLES, cam, 'ジョンリギ', 'ゴーメン') == (cam, 'ジョンリギ')
    twins = [*roofs, {'kind': 'own', 'target': (197, 190), 'clipped': False}]
    assert policy._goal_anchor(twins, CASTLES, cam, 'ジョンリギ', 'キカンドン') == (cam, 'ジョンリギ')


def test_a_source_that_never_opens_its_menu_falls_back_home_then_fails(monkeypatch):
    def arrived(_screen, mem, *_a, **_k):
        mem['anchor'], mem['uncertain'], mem['cursor'] = 'キカンドン', False, [567, 725]
        return 'arrived'
    monkeypatch.setattr(policy, 'nav_step', arrived)
    order = {'step': 'I:abc:1', 'general': 'どうし', 'source': 'キカンドン',
             'target': 'ゴーメン', 'cards': [], 'after': None, 'note': 'test'}
    mem = {**g401_memory(), 'active': order['step'],
           'chart_adjust': {'request_id': 'abc', 'interim_order': order}}
    presses = 0
    for _ in range(policy.SOURCE_MISS_LIMIT):
        presses += policy.map_step(map_screen(190, 200), mem, FRAME) == [policy.pad('a')]
    assert presses == policy.SOURCE_MISS_LIMIT
    policy.map_step(map_screen(190, 200), mem, FRAME)      # third miss: give the source up
    [changed] = decisions(mem, 'order_source_changed')
    assert changed['observed_metric'] == {'source': 'キカンドン', 'menu_miss': 3}
    assert mem['source_override'][order['step']] == 'ほんじょう'
    assert mem['active'] == order['step']
    for _ in range(policy.SOURCE_MISS_LIMIT + 1):
        policy.map_step(map_screen(190, 200), mem, FRAME)
    [failed] = decisions(mem, 'order_failed')
    assert failed['chart_step'] == order['step']
    assert mem['orders'][order['step']] == 'failed'
    assert mem['active'] != order['step']


def test_castle_menu_clears_the_source_miss_count():
    order = {'step': '1-A2', 'general': 'どうし', 'source': 'キカンドン', 'target': 'ゴーメン',
             'cards': ['フットバース'], 'after': ['captured', 'キカンドン'], 'note': 'test'}
    mem = {'chapter': 1, 'active': '1-A2', 'orders': {'1-A2': 'pending'},
           'captured': ['キカンドン'], 'source_miss': {'1-A2': 2}, '_records': []}
    assert policy._order(mem) == {**order, 'cards': ('フットバース',), 'after': ('captured', 'キカンドン'),
                                  'note': policy._order(mem)['note']}
    screen = Screen(lines=[], hand=None, text='', kind='castle_menu')
    policy.deploy_step(screen, mem)
    assert '1-A2' not in mem['source_miss']


def test_roof_colours_revoke_an_undefended_castle_and_put_its_retake_first(monkeypatch):
    monkeypatch.setattr(policy, 'castle_roofs', lambda *_a, **_k: g401_roofs())
    mem = {**g401_memory(), 'cursor': [566, 729], 'uncertain': False}
    policy.update_world(map_screen(190, 200), mem, FRAME)
    assert 'ジョンリギ' in mem['captured']               # one reading is not enough
    policy.update_world(map_screen(190, 200), mem, FRAME)
    assert 'ジョンリギ' not in mem['captured'] and mem['lost'] == ['ジョンリギ']
    [lost] = decisions(mem, 'castle_lost_observed')
    assert lost['castle'] == 'ジョンリギ' and lost['observed_metric'] == {'roof': 'enemy', 'readings': 2}
    assert 'ジョンリギ' not in mem['garrison']
    first_label, first = next(iter(policy.interim_candidates(mem).items()))
    assert first_label == 'retake_1' and first['target'] == 'ジョンリギ'
    assert first['purpose'] == 'retake'
    assert 'castle_lost_observed' in hanjuku_commentary.SPOKEN


def test_roof_readings_never_touch_home_or_boss_and_need_two_roofs(monkeypatch):
    home = CASTLES['ほんじょう']
    cam = (home[0] - 100, home[1] - 100)
    lone = [{'kind': 'enemy', 'target': (100, 100), 'clipped': False}]
    mem = {'chapter': 1, 'captured': [], '_records': []}
    for _ in range(3):
        policy.observe_owners(mem, lone, cam)
    assert mem['captured'] == [] and not decisions(mem, 'castle_lost_observed')
    kikan = CASTLES['キカンドン']
    pair = [*lone, {'kind': 'own', 'target': (kikan[0] - cam[0], kikan[1] - cam[1]),
                    'clipped': False}]
    for _ in range(2):
        policy.observe_owners(mem, pair, cam)
    assert mem['captured'] == ['キカンドン']                 # home stays implicit
    assert [r['decision'] for r in mem['_records']] == ['castle_owned_observed']


def test_off_chart_uses_measured_idle_generals_not_the_marching_chart_general():
    mem = {**g401_memory(), 'captured': ['キカンドン', 'ナキューメラ', 'カストーラ'],
           'lost': ['ジョンリギ']}
    candidates = policy.interim_candidates(mem)
    assert all(c['general'] != 'どうし' for c in candidates.values())
    assert all(c['source'] != 'キカンドン' for c in candidates.values())   # measured empty
    first = candidates['retake_1']
    # ほんじょう and カストーラ each hold one general: the nearer one goes.
    assert (first['general'], first['source'], first['target']) == ('ヴィーナス', 'カストーラ', 'ジョンリギ')
    # A castle that keeps a defender behind is preferred over the nearest.
    mem['garrison']['ほんじょう'] = ['ゼウス', 'アルテミス']
    first = policy.interim_candidates(mem)['retake_1']
    assert (first['general'], first['source']) == ('ゼウス', 'ほんじょう')
    # A castle somebody is still marching on is kept, but after the others.
    targets = [c['target'] for c in candidates.values()]
    assert targets.index('ジョンリギ') < targets.index('ゴーメン')
    assert targets[-2:] == ['ゴーメン', 'スペンソニア']


def test_interim_without_garrison_reading_keeps_the_chart_general_and_source():
    mem = {'chapter': 1, 'captured': ['キカンドン'], 'orders': {}, '_records': []}
    candidates = policy.interim_candidates(mem)
    gomen = next(c for c in candidates.values() if c['target'] == 'ゴーメン')
    assert (gomen['general'], gomen['source'], gomen['purpose']) == ('どうし', 'キカンドン', 'attack')


def test_a_spare_general_moves_into_an_owned_castle_last_seen_empty():
    targets = set(CASTLES) - {'ほんじょう', 'けっかい'}
    mem = {'chapter': 1, 'captured': sorted(targets), 'orders': {}, '_records': [],
           'garrison': {'ほんじょう': ['どうし', 'ゼウス', 'アルテミス'], 'キカンドン': [],
                        'ゴーメン': ['ココット']}}
    candidates = policy.interim_candidates(mem)
    assert list(candidates) == ['move_1']
    move = candidates['move_1']
    assert (move['general'], move['source'], move['target'], move['purpose']) == (
        'ゼウス', 'ほんじょう', 'キカンドン', 'move')
    # A lone general is never pulled out of its castle.
    mem['garrison']['ほんじょう'] = ['ゼウス']
    assert policy.interim_candidates(mem) == {}


def test_launch_and_battles_update_the_measured_garrison():
    mem = {'chapter': 1, 'garrison': {'ほんじょう': ['ゼウス', 'アルテミス']}}
    policy._garrison_move(mem, 'ゼウス', source='ほんじょう')
    assert mem['garrison']['ほんじょう'] == ['アルテミス']
    policy._garrison_move(mem, 'ゼウス', target='ジョンリギ')
    assert mem['garrison']['ジョンリギ'] == ['ゼウス']
    policy._garrison_move(mem, 'ココット', source='キカンドン')      # unknown list stays unknown
    assert 'キカンドン' not in mem['garrison']


def test_an_order_from_a_lost_castle_is_neither_picked_nor_blocking():
    stale = {'step': 'I:abc:1', 'general': 'ゼウス', 'source': 'ジョンリギ', 'target': 'ゴーメン',
             'cards': [], 'after': None, 'note': 'test'}
    mem = {**g401_memory(), 'captured': ['キカンドン', 'ナキューメラ', 'カストーラ'],
           'lost': ['ジョンリギ'], 'active': None}
    mem['chart_adjust'] = {'request_id': policy.chart_adjust.request_id(mem),
                           'interim_order': stale, 'interim_count': policy.INTERIM_LIMIT}
    assert policy.next_order(mem) is None
    actions = policy.map_step(Screen(lines=[], hand=None, text='', kind='map'), mem, FRAME)
    order = policy._order(mem)
    assert order is not None and order['source'] != 'ジョンリギ'
    assert decisions(mem, 'chart_interim_order')[-1]['purpose'] == 'retake'
    assert actions == [] or all(a['type'] == 'pad' for a in actions)


def test_general_list_reading_is_recorded_only_when_complete():
    mem = {'chapter': 1, 'active': '1-C1', 'orders': {'1-C1': 'pending'}, '_records': []}
    empty = Screen(lines=[], hand=None, text='しゅつげきしょうぐんはステータスおりません……',
                   kind='general_list')
    policy._observe_garrison(empty, mem, policy._order(mem))
    assert mem['garrison'] == {'ほんじょう': []}
    unreadable = Screen(lines=[], hand=None, text='しゅつげきステータス', kind='general_list')
    policy._observe_garrison(unreadable, mem, policy._order(mem))
    assert mem['garrison'] == {'ほんじょう': []}
    assert [r['decision'] for r in mem['_records']] == ['garrison_seen']


def test_jev_criteria_and_commentary_name_the_purpose():
    candidates = {
        'retake_1': {'general': 'ゼウス', 'source': 'ほんじょう', 'target': 'ジョンリギ',
                     'purpose': 'retake'},
        'move_2': {'general': 'アルテミス', 'source': 'ほんじょう', 'target': 'キカンドン',
                   'purpose': 'move'}}
    request = hanjuku_interim.build_request({'chapter': 1}, candidates, 'test-model')
    criteria = request['questions']['interim_action']['criteria']
    assert criteria['retake_1'].startswith('Retake our lost castle ジョンリギ')
    assert criteria['move_2'].startswith('Move general アルテミス from ほんじょう')
    base = {'decision': 'chart_interim_order', 'strategy_variant': 'chart_interim_fallback'}
    _, retake = hanjuku_commentary.compose({**base, 'general': 'ゼウス', 'target': 'ジョンリギ',
                                             'purpose': 'retake'})
    assert retake == '調整チャートを待つ間、ゼウスが奪われたジョンリギを白兵で奪い返します。'
    _, move = hanjuku_commentary.compose({**base, 'general': 'アルテミス', 'source': 'ほんじょう',
                                           'target': 'キカンドン', 'purpose': 'move'})
    assert move == '調整チャートを待つ間、アルテミスがほんじょうから空のキカンドンへ移ります。'
    _, lost = hanjuku_commentary.compose({'decision': 'castle_lost_observed', 'castle': 'ジョンリギ'})
    assert lost == 'ジョンリギを敵に奪われました。取り返しに向かいます。'
