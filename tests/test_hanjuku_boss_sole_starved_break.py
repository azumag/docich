"""Bounded recovery when a boss-only endgame strands every castle at 0/1 generals.

docich#5769: Chapter 2 reached にせヒーロー with no castle able to keep a
defender and still launch the boss wave. The recovery may consolidate one
singleton into the boss source only after a bounded boss-phase starvation
wait; the ordinary leave-one-defender rule outside that phase is unchanged.
"""
from docich import hanjuku_chart as chart, hanjuku_chart_adjust as chart_adjust
from docich.hanjuku.os_layer.world import NAME
from docich.hanjuku.strategy import orders as ORD, replan


def _ch2_starved_memory(tick=1000):
    chapter = 2
    home = chart.home_castle(chapter)
    normal = [c for c in chart.castles(chapter)
              if not chart.is_boss_castle(c) and c != home]
    owned = [home] + normal
    garrison = {c: [] for c in owned}
    garrison[home] = [NAME]
    garrison['スペランザ'] = ['ゼウス']
    garrison['フーリック'] = ['ココット']
    return {'chapter': chapter, 'captured': normal, 'tick': tick,
            '_records': [], 'plan': {'orders': {}},
            'world': {'garrison': garrison, 'garrison_tick': {}, 'sorties': {}}}


def _consolidation(plan):
    return next((o for o in plan['orders'] if o.get('boss_sole_consolidation') is True), None)


def test_boss_only_sole_starved_generates_bounded_consolidation_after_wait():
    mem = _ch2_starved_memory()

    assert replan._mechanical_boss_plan(mem, 'rid') is True
    assert _consolidation(mem['plan']['chart_plan']) is None
    assert mem['boss_sole_starved']['since'] == 1000

    mem['tick'] += replan.BOSS_SOLE_STARVED_TICKS
    assert replan._mechanical_boss_plan(mem, 'rid2') is True
    order = _consolidation(mem['plan']['chart_plan'])

    assert order is not None
    assert order['purpose'] == 'move'
    assert order['general'] == 'ココット'
    assert order['source'] == 'フーリック'
    assert order['target'] == 'スペランザ'
    assert order['step'] in mem['boss_sole_consolidation']['steps']
    assert ORD._boss_sole_consolidation_permitted(order, mem) is True
    assert ORD._source_spare_missing(order, mem) is False
    assert chart_adjust.startable(order, replan._adjust_situation(mem)) is None
    assert (ORD.next_order(mem) or {}).get('step') == order['step']

    # Once the consolidation arrives, the boss source has the ordinary
    # two-general list required before any boss sortie may leave.
    mem['world']['garrison']['フーリック'] = []
    mem['world']['garrison']['スペランザ'] = ['ゼウス', 'ココット']
    boss = next(o for o in mem['plan']['chart_plan']['orders'] if o['step'] == '2-B1')
    assert ORD._source_spare_missing(boss, mem) is False


def test_singleton_consolidation_never_applies_outside_boss_only_phase():
    mem = _ch2_starved_memory()
    mem['boss_sole_starved'] = {'chapter': 2, 'since': 1000}
    mem['tick'] = 1000 + replan.BOSS_SOLE_STARVED_TICKS
    assert replan._mechanical_boss_plan(mem, 'rid') is True
    order = _consolidation(mem['plan']['chart_plan'])
    assert order is not None

    # One ordinary castle is still enemy, so this is merely the normal phase.
    # The same singleton move is again refused by the owner leave-one rule.
    mem['captured'] = [c for c in mem['captured'] if c != 'アウスパジア']
    assert ORD.boss_only_phase(mem) is False
    assert ORD._boss_sole_consolidation_permitted(order, mem) is False
    assert ORD._source_spare_missing(order, mem) is True


def test_singleton_consolidation_is_exact_step_and_chapter_capped():
    mem = _ch2_starved_memory()
    mem['boss_sole_starved'] = {'chapter': 2, 'since': 1000}
    mem['tick'] = 1000 + replan.BOSS_SOLE_STARVED_TICKS
    assert replan._mechanical_boss_plan(mem, 'rid') is True
    order = _consolidation(mem['plan']['chart_plan'])
    assert order is not None

    forged = dict(order, step='C:forged')
    assert ORD._boss_sole_consolidation_permitted(forged, mem) is False

    mem['boss_sole_consolidation']['steps'] += ['C:used-1', 'C:used-2']
    mem['order_start_tick'] = {'C:used-1': 1100, 'C:used-2': 1200}
    assert ORD.boss_sole_consolidation_used(mem) == 2
    assert ORD._boss_sole_consolidation_permitted(order, mem) is False


def test_unknown_boss_phase_roster_does_not_count_as_starvation():
    mem = _ch2_starved_memory()
    mem['world']['garrison'].pop('アウスパジア', None)
    assert ORD.boss_only_phase(mem) is True
    assert ORD.boss_sole_starved(mem) is False
    assert ORD.note_boss_sole_starved(mem) is None
