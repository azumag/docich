"""Chapter 9 marches in squads of at most six, and its step ids never collide.

Owner 2026-11-11: ３２人全員移動させると大変だから、先発隊６人を敵城にいかせ、
しろがとれたら将軍とプラス６人（どちらも６人待つという意味ではなく、６人がMAX
という意味）をいかせる. Also: 月次の将軍チェックもとばしていいんじゃないか.
"""
from docich import hanjuku_chart as chart
from docich import hanjuku_policy as policy
from docich.hanjuku import orchestrator  # noqa: F401
from docich.hanjuku.flows import house
from docich.hanjuku.state.regions import plan as _plan_region, sorties as _sorties
from docich.hanjuku.strategy import corridor
from docich.hanjuku.strategy import orders as O

from test_hanjuku_chapter9_mechanical_march import FRONT, HOME, NEXT, _deploy, _mem, _orders

HERO = chart.HERO


def _send(mem, source, target, n, *, purpose='move', status='en_route', general=None, tick=None):
    for i in range(n):
        step = f'{corridor.PREFIX}{source}>{target}:{100 + len(_sorties(mem))}'
        _sorties(mem)[step] = {'general': general or f'将軍{len(_sorties(mem))}', 'source': source,
                               'target': target, 'purpose': purpose, 'status': status,
                               'tick': int(mem['tick']) if tick is None else tick}
        _plan_region(mem).setdefault('orders', {})[step] = 'launched'


def _by(mem, purpose):
    return [o for o in _orders(mem) if o['purpose'] == purpose and not o.get('corridor_hero')]


def test_a_settled_id_above_a_pending_hole_gives_a_fresh_step():
    """g1163 15:41-17:34: タオ>サヌト 0-3,6 done, 4,7 pending, 5 launched -> :6 (done)."""
    mem = _mem()
    attack = _orders(mem)[0]
    prefix = attack['step'].rsplit(':', 1)[0] + ':'
    status = _plan_region(mem).setdefault('orders', {})
    status.update({prefix + str(i): s for i, s in enumerate(
        ['done', 'done', 'done', 'done', 'pending', 'launched', 'done', 'pending'])})
    fresh = _orders(mem)[0]['step']
    assert fresh == prefix + '8'
    with chart.map_context(mem):
        order = O.next_order(mem)
    assert order is not None and order['step'] == fresh


def test_the_vanguard_is_at_most_six_and_never_waits_for_six():
    mem = _mem()
    _send(mem, FRONT, NEXT, 1, purpose='retake')
    assert _by(mem, 'retake')                # one marching: another may go
    _send(mem, FRONT, NEXT, 5, purpose='retake')
    assert not _by(mem, 'retake')            # six marching: the vanguard is full
    for sortie in _sorties(mem).values():
        sortie['status'] = 'arrived'
    assert _by(mem, 'retake')                # settled: room again


def test_each_capture_moves_at_most_six_per_hop():
    mem = _mem()
    _orders(mem)                             # opens the wave for NEXT
    _send(mem, HOME, FRONT, 5)
    assert _by(mem, 'move')
    _send(mem, HOME, FRONT, 1)
    assert not _by(mem, 'move')              # this capture's six have left
    mem['tick'] += 50
    mem['captured'] = [FRONT, NEXT]          # a capture opens the next wave
    mem['lost'] = []
    assert {o['source'] for o in _by(mem, 'move')} == {HOME, FRONT}   # fresh budget on every hop


def test_a_wiped_vanguard_opens_a_new_budget():
    mem = _mem()
    _orders(mem)
    _send(mem, HOME, FRONT, 6, status='arrived')
    assert not _by(mem, 'move')
    mem['tick'] += 30
    corridor.mark_drained(mem, FRONT, 'empty')   # the front's list was empty
    assert [o['source'] for o in _by(mem, 'move')] == [HOME]
    assert mem[corridor.WAVE]['refill'] == 1


def test_the_hero_walks_forward_only_up_to_one_castle_behind_the_front():
    """Owner 2026-11-12: 主人公は前線の自城一つ前の自城に常駐する.

    With HOME and FRONT ours, the post is HOME itself, so the hero stays there.
    A third capture moves the post to FRONT and only then does he step forward.
    """
    mem = _mem(garrison={HOME: [HERO, 'バトウラ'], FRONT: []})
    assert not [o for o in _orders(mem) if o.get('corridor_hero')]
    mem = _mem(garrison={HOME: [HERO, 'バトウラ'], FRONT: ['リーキ']})
    mem['captured'] = [FRONT, NEXT]          # NEXT taken: the post becomes FRONT
    mem['lost'] = []
    mem['tick'] += 50
    hero = [o for o in _orders(mem) if o.get('corridor_hero')]
    assert len(hero) == 1 and (hero[0]['source'], hero[0]['target']) == (HOME, FRONT)
    assert hero[0]['step'].endswith(':h0')
    _send(mem, HOME, FRONT, 1, general=HERO)
    assert not [o for o in _orders(mem) if o.get('corridor_hero')]


def _hero_forward(mem):
    """Three castles ours: the post is FRONT, so the hero in HOME must walk."""
    mem['captured'] = [FRONT, NEXT]
    mem['lost'] = []
    mem['tick'] += 50
    return mem


def test_the_hero_order_steers_the_cursor_to_him():
    mem = _hero_forward(_mem(garrison={HOME: ['バトウラ', HERO], FRONT: ['リーキ']}))
    hero = next(o for o in _orders(mem) if o.get('corridor_hero'))
    assert _deploy(mem, hero, ['バトウラ', HERO]) == [policy.pad('down')]
    mem = _hero_forward(_mem(garrison={HOME: [HERO, 'バトウラ'], FRONT: ['リーキ']}))
    hero = next(o for o in _orders(mem) if o.get('corridor_hero'))
    assert _deploy(mem, hero, [HERO, 'バトウラ']) == [policy.pad('a')]
    assert _plan_region(mem)['general_override'][hero['step']] == HERO


def test_a_hero_order_without_the_hero_ends_for_this_capture():
    mem = _hero_forward(_mem(garrison={HOME: [HERO], FRONT: ['リーキ']}))
    hero = next(o for o in _orders(mem) if o.get('corridor_hero'))
    assert _deploy(mem, hero, ['バトウラ']) == [policy.pad('b'), policy.pad('b')]
    assert not [o for o in _orders(mem) if o.get('corridor_hero')]


def test_an_attack_keeps_the_heros_last_companion():
    mem = _mem()
    attack = _orders(mem)[0]
    assert _deploy(mem, attack, [HERO, 'バトウラ']) == [policy.pad('b'), policy.pad('b')]
    assert mem[corridor.DRAINED][FRONT]['kind'] == 'hero'
    mem = _mem()
    attack = _orders(mem)[0]
    assert _deploy(mem, attack, [HERO, 'バトウラ', 'ミント']) == [policy.pad('down')]


def test_no_egg_survey_or_repair_trip_starts_during_the_march():
    from docich.hanjuku.os_layer.screen import Screen
    mem = _mem(goals={})
    screen = Screen(lines=[], hand=None, kind='map', text='')
    with chart.map_context(mem):
        assert house.step(screen, mem, None) is None
    assert 'house' not in mem


def test_an_open_survey_is_closed_through_its_own_exit():
    from docich.hanjuku.os_layer.screen import Screen
    mem = _mem()
    mem['house'] = {'phase': 'castle_check_next', 'age': 0, 'total': 0, 'chapter': 9,
                    'seen': [], 'pending': ['ブラウニー'], 'month_scan': False}
    screen = Screen(lines=[], hand=None, kind='map', text='')
    with chart.map_context(mem):
        house.step(screen, mem, None)
    assert mem.get('house') is None or mem['house']['phase'] == 'close'
    assert [r for r in mem['_records'] if r.get('decision') == 'house_deferred']


def test_the_fourth_to_sixth_attacker_is_not_held_as_a_duplicate_retake():
    """g1163 20:16-20:37: three retakes marching held every further one (167 holds)."""
    mem = _mem()
    _send(mem, FRONT, NEXT, 3, purpose='retake')
    attack = _by(mem, 'retake')[0]                 # the squad still has room
    with chart.map_context(mem):
        assert O._retake_reserved(attack, mem) is False
        plain = {k: v for k, v in attack.items() if k != 'corridor'}
        assert O._retake_reserved(plain, mem) is True   # other chapters keep the gate
