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
    monkeypatch.setattr(policy, 'Y_JUMP_OFFSET', {})     # exercises roof navigation, not Y jumps
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
    # A lone general is never pulled out of its castle; only an unread list
    # (opened and checked by the move itself) may be the donor then.
    mem['garrison']['ほんじょう'] = ['ゼウス']
    moves = [c for c in policy.interim_candidates(mem).values() if c['purpose'] == 'move']
    assert moves and all(c['source'] != 'ほんじょう' for c in moves)
    assert all(mem['garrison'].get(c['source']) is None for c in moves)
    assert all(c['general'] == policy.MOVE_ANY_GENERAL for c in moves)


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
           'active': 'X1', 'launched_orders': {'X1': order}, 'cursor': wrong, 'uncertain': False,
           'select_used': True}
    pinned = map_screen(232, 200)                      # map corner: the cursor cannot move
    for _ in range(policy.NAV_STILL_LIMIT):
        assert [a['buttons'][0] for a in policy.map_step(pinned, mem, FRAME)] == ['down']
    assert policy.map_step(pinned, mem, FRAME) == []
    [stuck] = decisions(mem, 'nav_stuck')
    assert stuck['observed_metric']['cursor'] == wrong
    assert mem['uncertain'] is True and mem['nav_search'] is True
    # Searching first shows the hero with SELECT, then steers inland; the
    # same lone roof no longer anchors.
    assert policy.map_step(pinned, mem, FRAME) == [policy.pad('select')]
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
    monkeypatch.setattr(policy, 'Y_JUMP_OFFSET', {})     # exercises roof navigation, not Y jumps
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
    monkeypatch.setattr(policy, 'Y_JUMP_OFFSET', {})     # exercises roof navigation, not Y jumps
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


def _move_list(names):
    from docich.hanjuku_font import TextLine
    lines = [TextLine(47, tuple((64 + 8 * i, ch) for i, ch in enumerate('しゅつげき'))),
             TextLine(63, tuple((64 + 8 * i, ch) for i, ch in enumerate('ステータス')))]
    lines += [TextLine(39 + 16 * n, tuple((144 + 8 * i, ch) for i, ch in enumerate(name)))
              for n, name in enumerate(names)]
    return Screen(lines=lines, hand=(122, 33, 139, 45), kind='general_list',
                  text='しゅつげきステータス' + ''.join(names))


def _move_memory():
    order = {'step': 'I:m:1', 'general': policy.MOVE_ANY_GENERAL, 'source': 'ジョンリギ',
             'target': 'ほんじょう', 'cards': [], 'after': None, 'purpose': 'move', 'note': 't'}
    return {'chapter': 1, 'captured': ['ジョンリギ'], 'orders': {}, 'picked': [], '_records': [],
            'active': 'I:m:1', 'chart_adjust': {'request_id': 'm', 'interim_order': order}}


def test_a_move_from_an_unread_castle_sends_a_non_hero_and_keeps_one_behind():
    mem = _move_memory()
    actions = policy.deploy_step(_move_list([chart.HERO, 'キャラウェイ']), mem)
    assert mem['general_override']['I:m:1'] == 'キャラウェイ'
    assert mem['garrison']['ジョンリギ'] == [chart.HERO, 'キャラウェイ']
    assert decisions(mem, 'move_general_picked')
    assert actions and actions[0]['buttons'] in (['down'], ['a'])


def test_a_move_from_a_castle_with_one_general_is_cancelled_and_remembered():
    """g407: ジョンリギ held only ココット; the move must not empty it."""
    mem = _move_memory()
    assert policy.deploy_step(_move_list(['ココット']), mem) == [policy.pad('b'), policy.pad('b')]
    assert mem['orders']['I:m:1'] == 'failed' and mem['garrison']['ジョンリギ'] == ['ココット']
    assert not [c for c in policy.interim_candidates(mem).values()
                if c['purpose'] == 'move' and c['source'] == 'ジョンリギ']


def _world_map_frame(owners):
    """Synthetic Y view: gold frame pixel, open sea, one flag per castle (chapter 1)."""
    from docich import hanjuku_screen
    px = bytearray(256 * 224 * 3)

    def put(x, y, rgb):
        i = (y * 256 + x) * 3
        px[i:i + 3] = bytes(rgb)
    for x in range(256):
        for y in range(224):
            put(x, y, hanjuku_screen.WORLD_SEA)
    put(128, 10, hanjuku_screen.WORLD_BORDER)
    ox, oy = policy.WORLD_MAP_OFFSET[1]
    for name, owner in owners.items():
        wx, wy = CASTLES[name]
        mx, my = round(wx / 8 + ox), round(wy / 8 + oy)
        rgb = policy.WORLD_FLAG_OWN if owner == 'own' else policy.WORLD_FLAG_ENEMY
        for dx in range(4):
            for dy in range(2):
                put(mx + dx, my + dy, rgb)
    return Frame(256, 224, bytes(px))


def test_the_y_whole_map_view_updates_every_castle_owner_and_closes_with_y():
    from docich.hanjuku_screen import parse
    frame = _world_map_frame({'ほんじょう': 'own', 'キカンドン': 'enemy', 'ジョンリギ': 'own'})
    screen = parse(frame)
    assert screen.kind == 'world_map'
    mem = {'chapter': 1, 'captured': ['キカンドン'], '_records': [], 'tick': 7}
    assert policy.world_map_step(screen, mem, frame) == [policy.pad('y')]
    assert mem['captured'] == ['ジョンリギ'] and mem['lost'] == ['キカンドン']
    [owners] = decisions(mem, 'world_map_owners')
    assert owners['observed_metric'] == {'ほんじょう': 'own', 'キカンドン': 'enemy', 'ジョンリギ': 'own'}


def test_the_map_opens_the_y_view_when_idle_and_a_survey_is_due():
    mem = {'chapter': 1, 'orders': {}, 'picked': [], '_records': [], 'tick': 10}
    assert policy.map_step(map_screen(100, 100), mem, FRAME) == [policy.pad('y')]
    assert decisions(mem, 'world_map_open') and mem['world_map_tick'] == 10
    mem['_records'] = []
    assert policy._world_map_wanted(mem) is False
    mem['world_map_due'] = True                              # a castle was attacked
    assert policy._world_map_wanted(mem) is True
    mem.pop('world_map_due')
    mem['tick'] = 10 + policy.WORLD_SURVEY_TICKS
    assert policy._world_map_wanted(mem) is True
    assert policy._world_map_wanted({'chapter': 2, 'tick': 0}) is False   # not calibrated


def test_a_lost_search_first_presses_select_to_show_the_hero(monkeypatch):
    monkeypatch.setattr(policy, 'castle_roofs', lambda *_a, **_k: [])
    mem = {'chapter': 1, 'orders': {}, 'picked': [], '_records': [], 'cursor': [500, 500],
           'uncertain': True, 'nav_search': True}
    goal = CASTLES['ほんじょう']
    assert policy.nav_step(map_screen(120, 120), mem, FRAME, goal) == [policy.pad('select')]
    assert decisions(mem, 'select_to_hero') and mem['select_used'] is True
    assert policy.nav_step(map_screen(140, 120), mem, FRAME, goal) != [policy.pad('select')]
    # Never on the sortie target marker.
    mem = {'chapter': 1, '_records': [], 'cursor': [500, 500], 'uncertain': True, 'nav_search': True}
    marker = Screen(lines=[], hand=None, text='', kind='map_target', marker=(120, 120))
    assert policy.nav_step(marker, mem, FRAME, goal) != [policy.pad('select')]


def test_a_home_castle_shown_taken_on_the_y_map_is_retaken_first_and_never_a_fallback():
    frame = _world_map_frame({'ほんじょう': 'enemy', 'ゴーメン': 'own', 'ジョンリギ': 'own'})
    from docich.hanjuku_screen import parse
    mem = {'chapter': 1, 'captured': [], '_records': [], 'tick': 3, 'orders': {},
           'garrison': {'ほんじょう': [], 'ジョンリギ': ['ココット', 'ゼウス']}}
    policy.world_map_step(parse(frame), mem, frame)
    assert mem['home_lost'] is True and mem['lost'][0] == 'ほんじょう'
    assert 'ほんじょう' not in policy._owned(mem)
    first_label, first = next(iter(policy.interim_candidates(mem).items()))
    assert first_label == 'retake_1' and first['target'] == 'ほんじょう'
    assert first['source'] == 'ジョンリギ'
    # Recaptured: back to normal.
    frame = _world_map_frame({'ほんじょう': 'own'})
    policy.world_map_step(parse(frame), mem, frame)
    assert mem['home_lost'] is False and 'ほんじょう' in policy._owned(mem)


def _y_view(cursor_centre, *, gold=False, shift=0):
    """Synthetic Y view with the jump cursor (white ring or gold G corners) at a centre."""
    from docich import hanjuku_screen
    px = bytearray(256 * 224 * 3)

    def put(x, y, rgb):
        i = (y * 256 + x) * 3
        px[i:i + 3] = bytes(rgb)
    for x in range(256):
        for y in range(224):
            put(x, y, hanjuku_screen.WORLD_SEA)
    put(128, 10, hanjuku_screen.WORLD_BORDER)
    cx, cy = cursor_centre
    import math
    for a in range(0, 360, 12):
        x, y = round(cx + 5.5 * math.cos(math.radians(a))), round(cy + 5.5 * math.sin(math.radians(a)))
        put(x, y, (255, 182 - shift, 0) if gold else (255, 255 - shift, 255))
    return Frame(256, 224, bytes(px))


def test_a_far_source_is_reached_through_the_y_map_cursor():
    from docich.hanjuku_screen import parse
    order = {'step': 'X', 'general': 'ゼウス', 'source': 'ジョンリギ', 'target': 'スペンソニア',
             'cards': [], 'after': None, 'note': 't'}
    mem = {'chapter': 1, 'captured': ['ジョンリギ'], 'orders': {}, 'picked': [], '_records': [],
           'active': 'X', 'launched_orders': {'X': order}, 'cursor': list(CASTLES['ほんじょう'])}
    assert policy.map_step(map_screen(140, 120), mem, FRAME) == [policy.pad('y')]
    assert mem['y_jump']['goal'] == 'ジョンリギ'
    gx, gy = CASTLES['ジョンリギ']
    ox, oy = policy.Y_JUMP_OFFSET[1]
    start = (CASTLES['ほんじょう'][0] / 8 + ox, CASTLES['ほんじょう'][1] / 8 + oy)
    frame = _y_view(start)
    assert parse(frame).kind == 'world_map'
    actions = policy.world_map_step(parse(frame), mem, frame)
    assert {a['buttons'][0] for a in actions} == {'left', 'up'}
    frame = _y_view((gx / 8 + ox, gy / 8 + oy))
    assert policy.world_map_step(parse(frame), mem, frame) == [policy.pad('a')]
    assert mem['cursor'] == [gx, gy] and 'y_jump' not in mem
    assert decisions(mem, 'y_jump_confirm')
    # Close enough now: no second jump.
    assert not policy._want_y_jump(mem, order, 'ジョンリギ', 'map')


def test_the_sortie_target_jump_reads_the_gold_g_cursor_and_is_bounded():
    from docich.hanjuku_screen import parse
    order = {'step': 'X', 'general': 'ゼウス', 'source': 'ほんじょう', 'target': 'キカンドン',
             'cards': [], 'after': None, 'note': 't'}
    mem = {'chapter': 1, 'orders': {}, 'picked': [], '_records': [], 'active': 'X',
           'launched_orders': {'X': order}, 'cursor': list(CASTLES['ほんじょう'])}
    marker = Screen(lines=[], hand=None, text='', kind='map_target', marker=(140, 120))
    assert policy.target_step(marker, mem, FRAME) == [policy.pad('y')]
    gx, gy = CASTLES['キカンドン']
    ox, oy = policy.Y_JUMP_OFFSET[1]
    frame = _y_view((gx / 8 + ox, gy / 8 + oy), gold=True)
    assert policy.world_cursor(frame) is not None
    assert policy.world_map_step(parse(frame), mem, frame) == [policy.pad('a')]
    # An unreadable cursor closes Y after a few frames and falls back.
    mem['cursor'] = list(CASTLES['ほんじょう'])
    assert policy.target_step(marker, mem, FRAME) == [policy.pad('y')]
    blank = _world_map_frame({})
    results = [policy.world_map_step(parse(blank), mem, blank) for _ in range(policy.Y_JUMP_WAIT)]
    assert results[-1] == [policy.pad('y')] and decisions(mem, 'y_jump_failed')
    mem['cursor'] = list(CASTLES['ほんじょう'])
    assert policy.target_step(marker, mem, FRAME) != [policy.pad('y')]    # limit reached


def test_the_y_cursor_is_found_with_live_capture_colour_shifts():
    """g419 08:23: live frames drew gold 255,181,0 and white 255,254,255, so every jump failed."""
    for gold in (False, True):
        frame = _y_view((150.0, 140.0), gold=gold, shift=1)
        found = policy.world_cursor(frame)
        assert found is not None and abs(found[0] - 150) <= 1 and abs(found[1] - 140) <= 1


def test_a_target_is_confirmed_only_on_a_roof_of_the_expected_owner(monkeypatch):
    """g419 08:34/08:44: the hero and ココット were sent to open fields by dead reckoning."""
    def arrived(_screen, mem, *_a, **_k):
        return 'arrived'
    monkeypatch.setattr(policy, 'nav_step', arrived)
    monkeypatch.setattr(policy, 'Y_JUMP_OFFSET', {})
    order = {'step': 'X', 'general': 'どうし', 'source': 'ほんじょう', 'target': 'ゴーメン',
             'cards': [], 'after': None, 'note': 't'}
    mem = {'chapter': 1, 'orders': {}, 'picked': [], '_records': [], 'active': 'X',
           'launched_orders': {'X': order}}
    marker = Screen(lines=[], hand=None, text='', kind='map_target', marker=(8, 55))
    monkeypatch.setattr(policy, 'castle_roofs', lambda *_a, **_k: [])     # open field
    for _ in range(policy.TARGET_MISS_LIMIT - 1):
        assert policy.target_step(marker, mem, FRAME) == []
    assert policy.target_step(marker, mem, FRAME) == [policy.pad('b')]    # cancelled, not sent
    assert mem['orders']['X'] == 'pending' and not decisions(mem, 'order_launched')
    # On the enemy roof it launches.
    mem['active'] = 'X'
    monkeypatch.setattr(policy, 'castle_roofs',
                        lambda *_a, **_k: [{'kind': 'enemy', 'target': (10, 57), 'clipped': False}])
    assert policy.target_step(marker, mem, FRAME) == [policy.pad('a')]
    assert decisions(mem, 'order_launched')


def test_a_jump_whose_view_closed_early_does_not_block_later_jumps():
    """g419 08:45: an event closed the Y view mid-jump; the stale jump blocked jumps for 10 min."""
    order = {'step': 'X', 'general': 'ゼウス', 'source': 'ほんじょう', 'target': 'キカンドン',
             'cards': [], 'after': None, 'note': 't'}
    mem = {'chapter': 1, 'orders': {}, 'picked': [], '_records': [], 'active': 'X',
           'launched_orders': {'X': order}, 'cursor': [300, 300],
           'y_jump': {'goal': 'ほんじょう', 'mode': 'map', 'step': 'X', 'moves': 8, 'wait': 0,
                      'seen': True}}
    assert policy.map_step(map_screen(140, 120), mem, FRAME) == [policy.pad('y')]
    assert decisions(mem, 'y_jump_failed') and mem['y_jump']['moves'] == 0


def test_a_jump_confirms_within_one_view_pixel():
    from docich.hanjuku_screen import parse
    gx, gy = CASTLES['ほんじょう']
    ox, oy = policy.Y_JUMP_OFFSET[1]
    mem = {'chapter': 1, '_records': [], 'y_jump': {'goal': 'ほんじょう', 'mode': 'map', 'step': 'X',
                                                    'moves': 0, 'wait': 0}}
    frame = _y_view((gx / 8 + ox, gy / 8 + oy))
    found = policy.world_cursor(frame)
    assert abs(found[0] - (gx / 8 + ox)) <= policy.Y_JUMP_TOL
    assert abs(found[1] - (gy / 8 + oy)) <= policy.Y_JUMP_TOL
    assert policy.world_map_step(parse(frame), mem, frame) == [policy.pad('a')]


def test_the_failed_menu_hold_is_bounded_and_cleared_by_a_y_jump(monkeypatch):
    """g419 09:06: 221 holds after a Y jump left the cell unanchored with menu_miss set."""
    def arrived(_screen, mem, *_a, **_k):
        mem['anchor'], mem['uncertain'] = None, False
        return 'arrived'
    monkeypatch.setattr(policy, 'nav_step', arrived)
    monkeypatch.setattr(policy, 'Y_JUMP_OFFSET', {})
    monkeypatch.setattr(policy, 'castle_roofs',
                        lambda *_a, **_k: [{'kind': 'own', 'target': (140, 120), 'clipped': False}])
    mem = {'chapter': 1, 'orders': {}, 'picked': [], '_records': [], 'active': '1-C1',
           'menu_miss': 1, 'cursor': list(CASTLES['ほんじょう'])}
    held = [policy.map_step(map_screen(140, 120), mem, FRAME) for _ in range(policy.MENU_HOLD_LIMIT)]
    assert all(h == [] for h in held)
    assert policy.map_step(map_screen(140, 120), mem, FRAME) == [policy.pad('a')]


def test_the_map_cursor_is_found_next_to_solid_white_snow():
    """g419 09:26: snow matched the bracket pattern everywhere; 220 frames read no cursor."""
    from docich.hanjuku_screen import parse
    px = bytearray(256 * 224 * 3)

    def put(x, y, rgb):
        i = (y * 256 + x) * 3
        px[i:i + 3] = bytes(rgb)
    for x in range(256):
        for y in range(224):
            put(x, y, (240, 240, 240) if x >= 150 else (8, 149, 255))    # snow | sea
    cx, cy = 90, 110                                                    # cursor top-left
    for yy in (cy + 1, cy + 14):
        for xx in (*range(cx + 2, cx + 5), *range(cx + 11, cx + 14)):
            put(xx, yy, (255, 255, 255))
    for d in (2, 3, 4, 10, 11, 12):
        put(cx + 1, cy + d, (255, 255, 255))
        put(cx + 14, cy + d, (255, 255, 255))
    screen = parse(Frame(256, 224, bytes(px)), phase='field')
    assert screen.cursor == (cx, cy)


def test_a_just_opened_jump_waits_for_the_view_instead_of_being_dropped():
    """g419 09:52: the target screen still showed right after Y, and the jump was dropped at 0 moves."""
    order = {'step': 'X', 'general': 'ゼウス', 'source': 'ほんじょう', 'target': 'ゴーメン',
             'cards': [], 'after': None, 'note': 't'}
    mem = {'chapter': 1, 'orders': {}, 'picked': [], '_records': [], 'active': 'X', 'tick': 10,
           'launched_orders': {'X': order}, 'cursor': list(CASTLES['ほんじょう'])}
    marker = Screen(lines=[], hand=None, text='', kind='map_target', marker=(140, 120))
    assert policy.target_step(marker, mem, FRAME) == [policy.pad('y')]
    mem['tick'] = 11
    assert policy.target_step(marker, mem, FRAME) == []                 # still opening
    assert mem['y_jump'] and not decisions(mem, 'y_jump_failed')
    mem['tick'] = 10 + policy.Y_JUMP_OPEN_GRACE
    policy.target_step(marker, mem, FRAME)
    assert decisions(mem, 'y_jump_failed')                              # bounded


def test_gold_edge_arrows_are_not_a_cursor_and_a_far_jump_is_never_confirmed():
    """g419 10:49: two gold edge arrows read as one cursor; the jump confirmed 50 px off."""
    from docich.hanjuku_screen import parse
    f2 = _world_map_frame({})

    def with_gold(points):
        buf = bytearray(256 * 224 * 3)
        for x in range(256):
            for y in range(224):
                buf[(y * 256 + x) * 3:(y * 256 + x) * 3 + 3] = bytes(f2.pixel(x, y))
        for x, y in points:
            buf[(y * 256 + x) * 3:(y * 256 + x) * 3 + 3] = bytes((255, 181, 0))
        return Frame(256, 224, bytes(buf))
    far = [(x, 55) for x in range(118, 125)] + [(x, 150) for x in range(60, 67)]
    assert policy.world_cursor(with_gold(far)) is None
    mem = {'chapter': 1, '_records': [], 'y_jump': {'goal': 'ゴーメン', 'mode': 'map', 'step': 'X',
                                                    'moves': policy.Y_JUMP_MOVES, 'wait': 0}}
    view = _y_view((150.0, 140.0))                # far from ゴーメン
    assert policy.world_map_step(parse(view), mem, view) == [policy.pad('y')]
    assert decisions(mem, 'y_jump_failed') and 'y_jump' not in mem


def test_the_boss_sortie_accepts_the_hero_row_despite_the_hand_and_icon_tiles():
    """g421 11:48: どうし read cleanly with the hand and an icon on its row; held 80+ minutes."""
    from docich.hanjuku_font import TextLine, UNKNOWN
    clean = TextLine(39, ((120, UNKNOWN), (128, UNKNOWN), (144, 'ど'), (152, 'う'), (160, 'し'),
                          (208, UNKNOWN)))
    assert policy._name_read_cleanly(clean, 'どうし')
    assert not policy._name_read_cleanly(
        TextLine(39, ((136, UNKNOWN), (144, 'ど'), (152, 'う'), (160, 'し'))), 'どうし')
    assert not policy._name_read_cleanly(
        TextLine(39, ((144, 'ど'), (152, UNKNOWN), (160, 'し'))), 'どうし')


def test_the_boss_tower_target_needs_a_y_jump_and_a_cancel_resets_the_jumps(monkeypatch):
    """g421 13:48: the tower has no own/enemy roof; the jump limit carried over a cancel."""
    monkeypatch.setattr(policy, 'castle_roofs', lambda *_a, **_k: [])
    order = {'step': 'F1', 'general': 'どうし', 'source': 'スペンソニア', 'target': 'けっかい',
             'cards': [], 'after': None, 'note': 't'}
    mem = {'chapter': 1, 'orders': {}, 'picked': [], '_records': [], 'active': 'F1',
           'launched_orders': {'F1': order}, 'y_jumps': {'F1:target': 2, 'F1:map': 1, 'G:map': 1}}
    marker = Screen(lines=[], hand=None, text='', kind='map_target', marker=(8, 8))
    assert not policy._target_roof_under_marker(marker, mem, FRAME, order)
    mem['y_jumped'] = {'F1': 'けっかい'}
    assert policy._target_roof_under_marker(marker, mem, FRAME, order)
    mem.pop('y_jumped')
    mem['target_miss'] = {'F1': policy.TARGET_MISS_LIMIT - 1}
    assert policy._unverified_target(marker, mem, order) == [policy.pad('b')]
    assert mem['y_jumps'] == {'G:map': 1}                     # the retry may jump again


def test_a_boss_sortie_without_its_general_gives_up_and_a_cancelled_boss_retries():
    """g421 14:08: ココット was not at スペンソニア and the boss backup held 12+ minutes."""
    from docich.hanjuku_font import TextLine
    order = {'step': 'F2', 'general': 'ココット', 'source': 'スペンソニア', 'target': 'けっかい',
             'cards': [], 'after': None, 'note': 't'}
    hero = {'step': 'F1', 'general': 'どうし', 'source': 'スペンソニア', 'target': 'けっかい',
            'cards': [], 'after': None, 'note': 't'}
    mem = {'chapter': 1, 'orders': {'F1': 'failed'}, 'picked': [], '_records': [], 'active': 'F2',
           'launched_orders': {'F2': order}, 'target_cancel': {'F1': 2},
           'captured': ['スペンソニア'], 'chart_plan': {'request_id': 'x', 'orders': [hero, order]}}
    rows = [TextLine(47, tuple((64 + 8 * i, ch) for i, ch in enumerate('しゅつげき'))),
            TextLine(39, tuple((144 + 8 * i, ch) for i, ch in enumerate('どうし'))),
            TextLine(55, tuple((144 + 8 * i, ch) for i, ch in enumerate('リーキ')))]
    screen = Screen(lines=rows, hand=(122, 33, 139, 45), kind='general_list',
                    text='しゅつげきどうしリーキステータス')
    for _ in range(policy.BOSS_ABSENT_LIMIT - 1):
        assert policy.deploy_step(screen, mem) == []
    assert policy.deploy_step(screen, mem) == [policy.pad('b'), policy.pad('b')]
    assert mem['orders']['F2'] == 'failed'
    assert policy.next_order(mem)['step'] == 'F1'          # boss hero retry is still due


def test_after_a_y_jump_the_cursor_is_walked_onto_the_nearest_roof_then_selects(monkeypatch):
    """g421 14:45: a jump landed a few px off スペンソニア and fell into a 20-minute search."""
    order = {'step': 'J3', 'general': 'どうし', 'source': 'スペンソニア', 'target': 'けっかい',
             'cards': [], 'after': None, 'note': 't'}
    mem = {'chapter': 1, 'orders': {}, 'picked': [], '_records': [], 'active': 'J3',
           'captured': ['スペンソニア'], 'launched_orders': {'J3': order},
           'cursor': list(CASTLES['スペンソニア']),
           'near_goal': {'step': 'J3', 'goal': 'スペンソニア', 'mode': 'map'}}
    roofs = [{'kind': 'own', 'target': (150, 110), 'clipped': False}]
    monkeypatch.setattr(policy, 'castle_roofs', lambda *_a, **_k: roofs)
    moves = policy.map_step(map_screen(140, 120), mem, FRAME)
    assert {a['buttons'][0] for a in moves} == {'right', 'up'}
    assert policy.map_step(map_screen(149, 111), mem, FRAME) == [policy.pad('a')]
    assert mem['expect_menu'] is True and 'near_goal' not in mem
    # No roof near the landing: re-open Y instead of searching.
    mem.update(near_goal={'step': 'J3', 'goal': 'スペンソニア', 'mode': 'map'}, expect_menu=False)
    monkeypatch.setattr(policy, 'castle_roofs', lambda *_a, **_k: [])
    assert policy.map_step(map_screen(140, 120), mem, FRAME) == [policy.pad('y')]
    assert decisions(mem, 'align_failed') and not mem.get('nav_search')


def test_select_re_places_a_lost_cursor_on_the_heros_castle(monkeypatch):
    """Owner hint: use SELECT when the position is lost (g421 wandered 11 min in 2 h)."""
    monkeypatch.setattr(policy, 'castle_roofs', lambda *_a, **_k: [])
    mem = {'chapter': 1, '_records': [], 'cursor': [500, 500], 'uncertain': True,
           'captured': ['スペンソニア'], 'garrison': {'スペンソニア': ['どうし', 'リーキ']}}
    goal = CASTLES['ゴーメン']
    first = policy.nav_step(map_screen(120, 120), mem, FRAME, goal)
    assert first != [policy.pad('select')]                            # one unanchored frame
    assert policy.nav_step(map_screen(120, 120), mem, FRAME, goal) == [policy.pad('select')]
    assert mem['cursor'] == list(CASTLES['スペンソニア']) and decisions(mem, 'select_to_hero')
    # Hero marching: his castle is unknown, so no re-placement.
    mem = {'chapter': 1, '_records': [], 'cursor': [500, 500], 'uncertain': True, 'tick': 5,
           'captured': ['スペンソニア'], 'garrison': {'スペンソニア': ['どうし']},
           'sorties': {'X': {'general': 'どうし', 'target': 'ゴーメン', 'status': 'en_route', 'tick': 4}}}
    assert policy._hero_castle(mem) is None


def test_a_general_far_behind_opens_the_rescue_menu_before_the_melee_decides():
    """g421 15:09/15:13: 26 vs 48 and 27 vs 38 died with an unused egg."""
    far = {'ally_hp': 26, 'enemy_hp': 48, 'start_ally_hp': 26, 'enemy': 'ソーピニヨン', 'ally': 'キャンディー'}
    assert policy._survival_needed(far)
    close = {'ally_hp': 27, 'enemy_hp': 38, 'start_ally_hp': 27, 'enemy': 'コリアンダー', 'ally': 'ビシソワーズ'}
    assert not policy._survival_needed(close)
    close['ally_hp'] = 26                                  # after one clash: 26 <= 70% of 38
    assert policy._survival_needed(close)
    ahead = {'ally_hp': 82, 'enemy_hp': 39, 'start_ally_hp': 82, 'enemy': 'カシュー', 'ally': 'ヴィーナス'}
    assert not policy._survival_needed(ahead)
