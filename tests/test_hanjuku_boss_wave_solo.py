"""A boss wave may take the last general once every normal castle is held (v407).

g1133 2026-11-05: after the first boss waves every castle held a single
general, so ``_boss_wave_pick`` (two free generals) could never form another
wave and the plan fell back to staging moves that enemy attacks kept
interrupting. Owner 2026-11-04 already allows a rear castle to send its last
general; once the boss is the only enemy left a wave may do the same, except
from the home castle.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import replan

CELLS = chart.castles(6)
NORMAL = [c for c in CELLS if c != 'ボス']
HOME = chart.home_castle(6)


def memory(captured=None, garrison=None):
    return {'chapter': 6, 'tick': 1000, 'month': '50-11', 'gold': 500, '_records': [],
            'orders': {}, 'world': {'sorties': {}},
            'captured': list(NORMAL if captured is None else captured),
            'garrison': dict(garrison if garrison is not None else
                             {c: ['ゼウス'] for c in NORMAL})}


def boss_attacks(mem):
    return [c for c in replan.interim_candidates(mem, allow_sole=False).values()
            if c.get('target') == 'ボス' and c.get('purpose') == 'attack']


def test_single_general_castles_cannot_send_boss_waves():
    # After #3567 there is no post-conquest exception: every castle keeps a defender.
    assert boss_attacks(memory()) == []


def test_the_boss_wave_prefers_the_castle_nearest_the_boss():
    # g1133 2026-11-06: the name-order tie-break sent アルマムーン (428 cells)
    # before オケアナ (203); only オケアナ's marches ever entered the boss.
    garrison = {c: ['ゼウス'] for c in NORMAL}
    nearest = min((c for c in NORMAL if c != HOME), key=lambda c:
                  sum((CELLS[c][i] - CELLS['ボス'][i]) ** 2 for i in (0, 1)))
    garrison[nearest] = ['ゼウス', 'ポセイドン']
    attacks = boss_attacks(memory(garrison=garrison))
    assert attacks
    cells = CELLS
    boss = next(xy for name, xy in cells.items() if chart.is_boss_castle(name))

    def distance(castle):
        xy = cells[castle]
        return ((xy[0] - boss[0]) ** 2 + (xy[1] - boss[1]) ** 2) ** 0.5

    assert attacks[0]['source'] == nearest
    assert attacks[0]['general'] in ('ゼウス', 'ポセイドン')


def test_a_live_egg_general_goes_before_a_broken_one_in_the_same_castle():
    # The boss fight's damage comes from eggs (owner 2026-11-05); a broken
    # general in the same castle should not take the wave slot.
    garrison = {c: ['ゼウス'] for c in NORMAL}
    # アマンディーヌ sorts before ゼウス, so the name order alone would send the
    # broken general; only the egg rank moves the live egg ahead.
    garrison['オケアナ'] = ['アマンディーヌ', 'ゼウス']
    mem = memory(garrison=garrison)
    mem['general_statuses'] = {'statuses': {
        'アマンディーヌ': {'broken': True, 'egg': None},
        'ゼウス': {'broken': False, 'egg': 'ベビーエッグ', 'uses': 4}}}
    attacks = boss_attacks(mem)
    assert attacks
    assert attacks[0]['source'] == 'オケアナ'
    assert attacks[0]['general'] == 'ゼウス'


def test_the_home_castle_alone_does_not_send_a_boss_wave():
    attacks = boss_attacks(memory(garrison={HOME: ['ゼウス']}))
    assert attacks == []


def test_before_every_normal_castle_is_held_the_reserve_still_applies():
    missing = next(c for c in NORMAL if c != HOME)   # the home castle is ours by definition
    mem = memory(captured=[c for c in NORMAL if c != missing],
                 garrison={c: ['ゼウス'] for c in NORMAL if c != missing})
    assert boss_attacks(mem) == []


def test_a_live_egg_outranks_a_nearer_eggless_castle():
    """g1163 2026-11-09 13:36: an eggless ヘーゼル was sent and lost for nothing.

    The wave picker used to rank the march distance before the egg, so the
    nearest castle's spent-egg general went first even when another castle held
    a live egg.
    """
    garrison = {c: ['ゼウス'] for c in NORMAL}
    cells = CELLS
    boss = cells['ボス']
    near, far = sorted((c for c in NORMAL if c != HOME),
                       key=lambda c: sum((cells[c][i] - boss[i]) ** 2 for i in (0, 1)))[:2]
    garrison[near] = ['ヘーゼル', 'ゼウス']
    garrison[far] = ['ポセイドン', 'アマンディーヌ']
    mem = memory(garrison=garrison)
    mem['general_statuses'] = {'statuses': {
        'ヘーゼル': {'broken': True, 'egg': None, 'uses': 0},
        'ゼウス': {'broken': True, 'egg': None, 'uses': 0},
        'ポセイドン': {'broken': False, 'egg': 'ベビーエッグ', 'uses': 3},
        'アマンディーヌ': {'broken': True, 'egg': None, 'uses': 0}}}
    attacks = boss_attacks(mem)
    assert attacks
    assert attacks[0]['source'] == far
    assert attacks[0]['general'] == 'ポセイドン'
