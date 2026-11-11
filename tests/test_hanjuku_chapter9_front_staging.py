"""Chapter 9 walks every rear general, the hero included, toward the front.

Owner 2026-11-11: 前線への中継移動も今やって, then 三人づつとかじゃなくて全員移動する
ようにしないとだし、主人公も移動しないと新しく雇った将軍は後方からしか出れないよ.
g1163 11:20 held 17 generals at アルマムーン and only ビーツ at ハヤク; sorties
only leave the road castle beside the target, and recruits join at the hero's
castle.
"""
import pytest

from docich import hanjuku_chart as chart
from docich import hanjuku_chart_adjust as chart_adjust
from docich.hanjuku import orchestrator  # noqa: F401  (package import side effects)
from docich.hanjuku.strategy import replan

HOME = chart.home_castle(9)
ROAD = chart.corridor(9)
FRONT = ROAD[1]          # ハヤク
REAR = ['どうし', 'ミント', 'ジェラート', 'ガルバンゾー', 'シナモン', 'マフィン',
        'アルファルファ', 'ユイートル', 'マカデミア']


@pytest.fixture(autouse=True)
def mechanical(monkeypatch):
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)


def memory(front, incoming=(), rear=REAR, owned=(FRONT,)):
    garrison = {HOME: list(rear), FRONT: list(front)}
    return {'chapter': 9, 'tick': 35000, 'month': '18-1', 'captured': list(owned), 'orders': {},
            'garrison': garrison,
            'sorties': {f'I:move{i}': {'general': g, 'source': HOME, 'target': FRONT,
                                       'status': 'en_route', 'tick': 34900 + i}
                        for i, g in enumerate(incoming)},
            '_records': []}


def plan(mem):
    with chart.map_context(mem):
        return list(replan.interim_candidates(mem).values())


def staging(orders, target=FRONT):
    return [o for o in orders if o['purpose'] == 'move' and o['target'] == target]


def test_every_rear_general_and_the_hero_walk_to_the_front():
    orders = plan(memory(['ビーツ']))
    moves = staging(orders)
    assert {o['general'] for o in moves} == set(REAR)
    assert all(o['source'] == HOME for o in moves)
    # The hero leaves while others are still listed with him.
    names = [o['general'] for o in moves]
    assert names.index('どうし') == 1


def test_the_fronts_attack_runs_before_the_queued_relays():
    orders = plan(memory(['ビーツ']))
    moves = staging(orders)
    attack = next(i for i, o in enumerate(orders)
                  if o['purpose'] == 'attack' and o['target'] == ROAD[2])
    assert orders.index(moves[0]) < attack < orders.index(moves[1])


def test_marching_generals_are_not_moved_again():
    orders = plan(memory(['ビーツ'], incoming=['ライム', 'ミモザ']))
    assert not {'ライム', 'ミモザ'} & {o['general'] for o in staging(orders)}


def test_the_hero_never_attacks():
    orders = plan(memory(['どうし', 'ビーツ'], rear=['ミント']))
    assert all(o['general'] != 'どうし' for o in orders if o['purpose'] == 'attack')


def test_far_castles_walk_one_hop_at_a_time():
    # Own アルマムーン..サヌト: the front is サヌト, the rear walks hop by hop.
    owned = ROAD[1:4]
    mem = memory([], owned=owned)
    mem['garrison'] = {HOME: ['どうし', 'ミント'], ROAD[1]: ['ジェラート'], ROAD[2]: [], ROAD[3]: ['ビーツ']}
    orders = plan(mem)
    hops = {(o['source'], o['target'], o['general']) for o in orders if o['purpose'] == 'move'}
    assert (HOME, ROAD[1], 'ミント') in hops and (HOME, ROAD[1], 'どうし') in hops
    assert (ROAD[1], ROAD[2], 'ジェラート') in hops


def test_a_hero_move_is_startable_only_on_chapter_nine():
    order = {'source': HOME, 'general': chart.HERO, 'target': FRONT, 'purpose': 'move'}
    garrison = {HOME: [chart.HERO, 'ミント']}
    assert chart_adjust.startable(order, {'chapter': 9, 'garrison': garrison}) is None
    assert chart_adjust.startable(order, {'chapter': 8, 'garrison': garrison}) == 'hero_not_dispatchable'
    attack = {**order, 'purpose': 'attack'}
    assert chart_adjust.startable(attack, {'chapter': 9, 'garrison': garrison}) == 'hero_not_dispatchable'


def _ch9_move(general):
    from test_hanjuku_off_chart import _move_memory
    mem = _move_memory()
    order = mem['chart_adjust']['interim_order']
    order.update(general=general, source=HOME, target=FRONT)
    mem.update(chapter=9, captured=[FRONT])
    return mem


def _scan(mem, names):
    from docich import hanjuku_policy as policy
    from test_hanjuku_off_chart import _move_list
    mem['general_list_scan'] = {'step': 'I:m:1', 'castle': HOME, 'end': 'wrapped',
                                'names': names, 'tick': mem.get('tick') or 0}
    with chart.map_context(mem):          # production decides inside the chapter's map context
        return policy.deploy_step(_move_list(names), mem)


def test_deploy_moves_the_hero_when_chapter_nine_names_him():
    mem = _ch9_move(chart.HERO)
    actions = _scan(mem, ['ミント', chart.HERO, 'ジェラート'])
    assert mem['general_override']['I:m:1'] == chart.HERO
    assert actions and actions[0]['buttons'] in (['down'], ['a'])


def test_deploy_lets_the_last_general_leave_a_chapter_nine_rear_castle():
    mem = _ch9_move('ミント')
    actions = _scan(mem, ['ミント'])
    assert mem['general_override']['I:m:1'] == 'ミント'
    assert actions != [{'type': 'pad', 'buttons': ['b'], 'hold_ms': 100}] * 2
