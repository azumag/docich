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
            'orders': {}, 'sorties': {}, 'lost': [],
            'captured': list(NORMAL if captured is None else captured),
            'garrison': dict(garrison if garrison is not None else
                             {c: ['ゼウス'] for c in NORMAL})}


def boss_attacks(mem):
    return [c for c in replan.interim_candidates(mem, allow_sole=False).values()
            if c.get('target') == 'ボス' and c.get('purpose') == 'attack']


def test_a_single_general_castle_can_still_send_a_boss_wave():
    attacks = boss_attacks(memory())
    assert len(attacks) == 1
    assert attacks[0]['source'] != HOME          # the home castle keeps its defender
    assert attacks[0]['general'] == 'ゼウス'


def test_the_home_castle_alone_does_not_send_a_boss_wave():
    attacks = boss_attacks(memory(garrison={HOME: ['ゼウス']}))
    assert attacks == []


def test_before_every_normal_castle_is_held_the_reserve_still_applies():
    missing = next(c for c in NORMAL if c != HOME)   # the home castle is ours by definition
    mem = memory(captured=[c for c in NORMAL if c != missing],
                 garrison={c: ['ゼウス'] for c in NORMAL if c != missing})
    assert boss_attacks(mem) == []
