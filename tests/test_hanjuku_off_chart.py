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
    # A roof sits under the cursor, yet the menu never opens.
    monkeypatch.setattr(policy, 'castle_roofs',
                        lambda *_a, **_k: [{'kind': 'own', 'target': (190, 200), 'clipped': False}])
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
    assert lost == 'ジョンリギが敵の城になっているのを確認しました。'


def test_a_cell_that_pressing_never_moves_falls_back_to_the_inland_search(monkeypatch):
    """g401 21:31: a lone ほんじょう roof voted as ジョンリギ; "down" for 16 minutes."""
    home, jonrigi = CASTLES['ほんじょう'], CASTLES['ジョンリギ']
    lone = [{'kind': 'own', 'target': (51, 13), 'clipped': False}]
    monkeypatch.setattr(policy, 'castle_roofs', lambda *_a, **_k: lone)
    order = {'step': 'X1', 'general': 'ココット', 'source': 'ほんじょう', 'target': 'ゴーメン',
             'cards': [], 'after': None, 'note': 'test'}
    wrong = [jonrigi[0] - 51 + 232, jonrigi[1] - 13 + 200]
    mem = {'chapter': 1, 'captured': [], 'orders': {}, 'picked': [], '_records': [],
           'active': 'X1', 'launched_orders': {'X1': order}, 'cursor': wrong, 'uncertain': False}
    pinned = map_screen(232, 200)                      # map corner: the cursor cannot move
    for _ in range(policy.NAV_STILL_LIMIT):
        assert [a['buttons'][0] for a in policy.map_step(pinned, mem, FRAME)] == ['down']
    assert policy.map_step(pinned, mem, FRAME) == []
    [stuck] = decisions(mem, 'nav_stuck')
    assert stuck['observed_metric']['cursor'] == wrong
    assert mem['uncertain'] is True and mem['nav_search'] is True
    # Searching steers inland and the same lone roof no longer anchors.
    assert {a['buttons'][0] for a in policy.map_step(pinned, mem, FRAME)} == {'left', 'up'}
    assert mem['uncertain'] is True and home != tuple(mem['cursor'])


def test_moving_cursor_or_leaving_the_map_never_counts_as_stuck(monkeypatch):
    monkeypatch.setattr(policy, 'castle_roofs', lambda *_a, **_k: [])
    order = {'step': 'X1', 'general': 'ココット', 'source': 'ほんじょう', 'target': 'ゴーメン',
             'cards': [], 'after': None, 'note': 'test'}
    mem = {'chapter': 1, 'captured': [], 'orders': {}, 'picked': [], '_records': [],
           'active': 'X1', 'launched_orders': {'X1': order}, 'cursor': [300, 300],
           'uncertain': False}
    for step in range(6):
        policy.map_step(map_screen(40 + 20 * step, 40 + 20 * step), mem, FRAME)
    assert not decisions(mem, 'nav_stuck')


def test_a_dead_reckoned_arrival_is_not_confirmed_until_roofs_agree(monkeypatch):
    """g403 23:04: edge scrolls drifted ~60 px and ココット was sent north of ジョンリギ."""
    jonrigi = CASTLES['ジョンリギ']
    true_cell = [jonrigi[0] - 7, jonrigi[1] - 62]          # where the cursor really was
    cam = (true_cell[0] - 8, true_cell[1] - 8)
    roofs = [{'kind': 'enemy', 'target': (jonrigi[0] - cam[0], jonrigi[1] - cam[1]), 'clipped': False},
             {'kind': 'own', 'target': (CASTLES['キカンドン'][0] - cam[0],
                                        CASTLES['キカンドン'][1] - cam[1]), 'clipped': False}]
    monkeypatch.setattr(policy, 'castle_roofs', lambda *_a, **_k: roofs)
    mem = {'chapter': 1, 'cursor': [jonrigi[0] + 3, jonrigi[1]], 'uncertain': False, '_records': []}
    screen = Screen(lines=[], hand=None, text='', kind='map_target', marker=(8, 8))
    first = policy.nav_step(screen, mem, FRAME, jonrigi)
    assert first != 'arrived' and mem['uncertain'] is True
    assert decisions(mem, 'arrival_unverified')
    second = policy.nav_step(screen, mem, FRAME, jonrigi)
    assert mem['cursor'] == true_cell and mem['anchor'] == 'ジョンリギ'
    assert {a['buttons'][0] for a in second} == {'right', 'down'}


def test_an_unverifiable_arrival_is_held_at_most_a_few_times(monkeypatch):
    """g403 23:24 (v12): a clipped goal at the screen edge showed no roof; nudged for 70 s."""
    monkeypatch.setattr(policy, 'castle_roofs', lambda *_a, **_k: [])
    goal = CASTLES['スペンソニア']
    mem = {'chapter': 1, 'cursor': list(goal), 'uncertain': False, '_records': []}
    screen = Screen(lines=[], hand=None, text='', kind='map_target', marker=(16, 8))
    # No roof in view: nothing could re-anchor, so the old dead-reckoned arrival stands.
    assert policy.nav_step(screen, mem, FRAME, goal) == 'arrived'
    assert not decisions(mem, 'arrival_unverified')
    # An uncertain cell that roofs never re-anchor is held a bounded number of times.
    mem = {'chapter': 1, 'cursor': list(goal), 'uncertain': True, '_records': []}
    results = [policy.nav_step(screen, mem, FRAME, goal) for _ in range(policy.UNVERIFIED_LIMIT + 1)]
    assert results[-1] == 'arrived' and all(r != 'arrived' for r in results[:-1])
    assert len(decisions(mem, 'arrival_unverified')) == policy.UNVERIFIED_LIMIT


def test_no_roof_under_the_cursor_refuses_a_bounded_number_of_source_presses(monkeypatch):
    """g405 00:26: a lone ほんじょう roof voted as キカンドン; A on open sea failed 1-C1."""
    def arrived(_screen, mem, *_a, **_k):
        mem['anchor'], mem['uncertain'] = 'ほんじょう', False
        return 'arrived'
    monkeypatch.setattr(policy, 'nav_step', arrived)
    far = [{'kind': 'own', 'target': (51, 109), 'clipped': False}]
    monkeypatch.setattr(policy, 'castle_roofs', lambda *_a, **_k: far)
    mem = {'chapter': 1, 'orders': {}, 'picked': [], '_records': [], 'active': '1-C1'}
    sea = map_screen(215, 189)
    for _ in range(policy.OFF_CASTLE_LIMIT):
        assert policy.map_step(sea, mem, FRAME) == []
    refused = decisions(mem, 'source_not_under_cursor')
    assert len(refused) == policy.OFF_CASTLE_LIMIT and mem['nav_search'] is True
    assert not mem.get('expect_menu')
    # Bounded: afterwards A is pressed and the menu-miss path takes over.
    assert policy.map_step(sea, mem, FRAME) == [policy.pad('a')]


def test_a_roof_under_the_cursor_confirms_the_source(monkeypatch):
    def arrived(_screen, mem, *_a, **_k):
        mem['anchor'], mem['uncertain'] = 'ほんじょう', False
        return 'arrived'
    monkeypatch.setattr(policy, 'nav_step', arrived)
    monkeypatch.setattr(policy, 'castle_roofs',
                        lambda *_a, **_k: [{'kind': 'own', 'target': (35, 109), 'clipped': False}])
    mem = {'chapter': 1, 'orders': {}, 'picked': [], '_records': [], 'active': '1-C1'}
    assert policy.map_step(map_screen(32, 108), mem, FRAME) == [policy.pad('a')]
    assert not decisions(mem, 'source_not_under_cursor')


def test_moves_come_before_attacks_and_staff_an_empty_home_castle():
    """g407 01:40: home empty, キカンドン held どうし+ゼウス; moves were listed last and never ran."""
    mem = {'chapter': 1, 'orders': {}, '_records': [],
           'captured': ['キカンドン', 'ジョンリギ'], 'lost': ['ナキューメラ'],
           'garrison': {'ほんじょう': [], 'キカンドン': ['どうし', 'ゼウス'], 'ジョンリギ': ['ココット']}}
    candidates = policy.interim_candidates(mem)
    purposes = [c['purpose'] for c in candidates.values()]
    assert purposes[0] == 'retake' and purposes[1] == 'move'
    assert set(purposes[2:]) == {'attack'}
    move = candidates['move_2']
    assert (move['general'], move['source'], move['target']) == ('ゼウス', 'キカンドン', 'ほんじょう')
    # With nothing to retake, the move is the first candidate (the fallback).
    mem['lost'] = []
    first_label, first = next(iter(policy.interim_candidates(mem).items()))
    assert first_label == 'move_1' and first['target'] == 'ほんじょう'


def test_a_move_already_marching_is_not_offered_again():
    mem = {'chapter': 1, 'orders': {}, '_records': [], 'captured': ['キカンドン'], 'tick': 10,
           'garrison': {'ほんじょう': [], 'キカンドン': ['どうし', 'ゼウス', 'ココット']},
           'sorties': {'I:x:1': {'general': 'ゼウス', 'target': 'ほんじょう', 'status': 'en_route',
                                 'tick': 5}}}
    assert not [c for c in policy.interim_candidates(mem).values() if c['purpose'] == 'move']


def _stuck_plan_memory():
    """g407 01:38-02:39: every plan order's general was a stale "marching" unit."""
    plan = [{'step': 'A:p:K1', 'general': 'どうし', 'source': 'ほんじょう', 'target': 'キカンドン',
             'cards': [], 'after': None, 'note': 't'},
            {'step': 'A:p:K3', 'general': 'ココット', 'source': 'ジョンリギ', 'target': 'スペンソニア',
             'cards': [], 'after': None, 'note': 't'},
            {'step': 'A:p:K4', 'general': 'どうし', 'source': 'キカンドン', 'target': 'ゴーメン',
             'cards': [], 'after': ['captured', 'キカンドン'], 'note': 't'}]
    return {'chapter': 1, 'orders': {'1-A2': 'launched_unconfirmed', 'I:b:1': 'launched'},
            '_records': [], 'captured': ['ジョンリギ'], 'lost': ['キカンドン'],
            'chart_plan': {'request_id': 'p', 'orders': plan},
            'garrison': {'ほんじょう': [], 'ジョンリギ': ['ココット']},
            'sorties': {'1-A2': {'general': 'どうし', 'target': None, 'status': 'launched_unconfirmed'},
                        'I:b:1': {'general': 'ココット', 'target': 'スペンソニア', 'status': 'en_route'}}}


def test_sorties_without_a_recent_tick_no_longer_block_their_generals():
    mem = _stuck_plan_memory()
    assert policy._en_route(mem) == (set(), set())          # legacy records: no tick
    # K1's source was last read empty, so the next runnable order is ココット's K3.
    assert policy.next_order(mem)['step'] == 'A:p:K3'
    mem['tick'] = 50
    mem['sorties']['I:b:1']['tick'] = 45
    assert policy.next_order(mem) is None                     # ココット marching right now
    mem['tick'] = 45 + policy.SORTIE_BUSY_TICKS
    assert policy.next_order(mem)['step'] == 'A:p:K3'         # ...but not forever


def test_a_plan_of_orders_that_can_never_run_does_not_block_off_chart_sorties():
    mem = _stuck_plan_memory()
    mem['orders'].update({'A:p:K1': 'failed', 'A:p:K3': 'failed'})
    # K4 waits on キカンドン, which no marching unit is taking.
    assert policy._plan_pending(mem) is False
    mem['tick'] = 10
    mem['sorties']['X'] = {'general': 'ゼウス', 'target': 'キカンドン', 'status': 'en_route', 'tick': 9}
    assert policy._plan_pending(mem) is True


def test_a_recruit_makes_the_home_garrison_unknown_again():
    mem = {'chapter': 1, '_records': [], 'garrison': {'ほんじょう': [], 'ジョンリギ': ['ココット']},
           'month_sub': {'kind': 'recruit', 'gold_before': 100, 'key': '1-9'}}
    screen = Screen(lines=[], hand=None, text='', kind='month_menu')
    screen.header = {'gold': 100 - policy.RECRUIT_COST}
    policy._finish_month_sub(screen, mem, {'recruit': 'opened'})
    assert 'ほんじょう' not in mem['garrison'] and mem['garrison']['ジョンリギ'] == ['ココット']


def _egg_menu(cursor_y=176):
    from docich.hanjuku_font import TextLine
    from docich.hanjuku_screen import classify_text
    rows = [TextLine(176, tuple((176 + 8 * i, ch) for i, ch in enumerate('こうげき'))),
            TextLine(192, tuple((176 + 8 * i, ch) for i, ch in enumerate('もうこうげき')))]
    screen = Screen(lines=rows, hand=None, text='こうげきもうこうげき', menu_rows=rows,
                    menu_cursor=cursor_y)
    screen.kind = classify_text(screen)
    return screen


def test_an_egg_battle_menu_with_a_greyed_egg_row_is_answered_with_attack():
    """g407 02:43: たまごをつかう greyed out, read as plain text and held for 20 minutes."""
    screen = _egg_menu()
    assert screen.kind == 'egg_battle_menu'
    mem = {'chapter': 1, '_records': []}
    assert policy.egg_battle_step(screen, mem) == [policy.pad('a')]
    assert decisions(mem, 'egg_battle_egg_unavailable')
    # Cursor on もうこうげき: move back up to こうげき first.
    assert policy.egg_battle_step(_egg_menu(192), mem) == [policy.pad('up')]
