"""The hero's castle follows the front one castle for every capture.

Owner 2026-11-15: ９話では、一つ敵城を落としたら前線を一つあげられるので、
主人公のいる城も一つ上げられるはずだが、それが今動いていない.

The post rule only moves a hero it can name. A departure deletes him from the
source roster and the destination roster is read only when that castle is
visited, so a run that resumed with empty rosters named no hero on any
observation and never issued a hero order. These tests pin the two ways the
hero stays movable: the departure belief and the bounded locating read.
"""
import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku import orchestrator  # noqa: F401  (package import side effects)
from docich.hanjuku.strategy import corridor

ROAD = chart.corridor(9)
A, B, C, D, E = ROAD[0], ROAD[1], ROAD[2], ROAD[3], ROAD[4]
HERO = chart.HERO


@pytest.fixture(autouse=True)
def mechanical(monkeypatch):
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)


def memory(owned, garrison=None, **extra):
    """``owned`` castles are ours; the target is the first road castle not ours."""
    if garrison is None:
        garrison = {castle: ['リーキ'] for castle in owned}
    world = {'garrison': dict(garrison), 'sorties': {}, 'launched_orders': {},
             'general_location_unknown': [], 'garrison_tick': {}}
    mem = {'chapter': 9, 'tick': 5000, 'month': '19-4',
           'captured': [c for c in owned if c != chart.home_castle(9)],
           'lost': [], 'orders': {}, 'garrison': dict(garrison), 'sorties': {},
           # the country holds more generals than the rosters name: somebody
           # (possibly the hero) stands in a castle nobody has read
           'country_general_count': {'count': 8, 'scope': [9, '19-4']},
           'world': world, '_records': []}
    mem.update(extra)
    return mem


def drain(mem, castle):
    """The plan stopped visiting this castle: its record can then go stale."""
    corridor.mark_drained(mem, castle, 'empty')


def orders(mem):
    with chart.map_context(mem):
        return corridor.orders(mem)


def hero_orders(mem):
    return [o for o in orders(mem) if o.get('corridor_hero')]


def locate_orders(mem):
    return [o for o in orders(mem) if o.get('hero_locate')]


def test_an_unnamed_hero_reads_the_post_castle_before_the_rear_marches():
    # A, B, C ours and no roster names the hero: the post is B (one behind C),
    # and the plan stopped visiting B, so nothing would ever read it again.
    mem = memory([A, B, C])
    drain(mem, B)
    found = locate_orders(mem)
    assert len(found) == 1
    assert found[0]['source'] == B
    assert found[0]['purpose'] == 'move'
    assert mem[corridor.HERO_LOCATE]['count'] == 1
    # With no attack available the read leads the plan, ahead of the rear march.
    steps = [o['step'] for o in orders(mem)]
    rear = next(o['step'] for o in orders(mem)
                if o['purpose'] == 'move' and o['source'] == A)
    assert steps.index(found[0]['step']) < steps.index(rear)


def test_a_known_hero_gets_no_locating_read():
    mem = memory([A, B, C], garrison={A: [HERO], B: ['リーキ'], C: ['リーキ']})
    drain(mem, B)
    assert locate_orders(mem) == []


def test_a_believed_hero_advances_one_castle_when_the_front_advances():
    # B is the old post; D just fell, so the post is C and the hero (believed
    # at B, no roster entry) must move one castle forward.
    mem = memory([A, B, C, D])
    mem['tick'] = 6000
    corridor.note_hero_post(mem, B, tick=5900)
    hero = hero_orders(mem)
    assert len(hero) == 1
    assert (hero[0]['source'], hero[0]['target']) == (B, C)
    assert locate_orders(mem) == []


def test_the_belief_expires_and_the_read_takes_over():
    mem = memory([A, B, C, D])
    drain(mem, C)
    corridor.note_hero_post(mem, B, tick=5000 - corridor.HERO_POST_TTL - 1)
    assert hero_orders(mem) == []
    assert [o['source'] for o in locate_orders(mem)] == [C]


def test_a_live_list_that_misses_the_hero_drops_the_belief():
    mem = memory([A, B, C], garrison={A: [HERO], B: ['リーキ'], C: ['リーキ']})
    corridor.note_hero_post(mem, C)
    mem['world']['garrison'][A] = [HERO]
    assert corridor._believed_hero(mem) == C
    corridor.hero_absent(mem)
    assert corridor.HERO_POST not in mem
    assert corridor._believed_hero(mem) is None


def test_a_recently_read_castle_is_not_read_again():
    mem = memory([A, B, C])
    drain(mem, B)
    mem['world']['garrison_tick'] = {B: 5000 - 10}    # just read, no hero there
    found = locate_orders(mem)
    assert len(found) == 1
    assert found[0]['source'] == C                    # the front instead


def test_the_locating_reads_are_spaced_and_budgeted():
    mem = memory([A, B, C])
    drain(mem, B)
    assert len(locate_orders(mem)) == 1
    mem['tick'] += 10
    assert locate_orders(mem) == []                   # same read is not repeated
    mem['tick'] += corridor.HERO_LOCATE_SPACING
    assert len(locate_orders(mem)) == 1
    for _ in range(corridor.HERO_LOCATE_BUDGET + 2):
        mem['tick'] += corridor.HERO_LOCATE_SPACING
        locate_orders(mem)
    assert mem[corridor.HERO_LOCATE]['count'] == corridor.HERO_LOCATE_BUDGET
    mem['month'] = '19-5'
    mem['country_general_count']['scope'] = [9, '19-5']
    mem['tick'] += corridor.HERO_LOCATE_SPACING
    assert len(locate_orders(mem)) == 1               # a new month re-opens it


def test_a_marching_hero_is_neither_read_for_nor_moved():
    mem = memory([A, B, C], sorties={'I:c9:%s>%s:h0' % (B, C): {
        'general': HERO, 'source': B, 'target': C, 'purpose': 'move',
        'status': 'en_route', 'tick': 4990}})
    mem['world']['sorties'] = dict(mem['sorties'])
    assert locate_orders(mem) == []
    assert hero_orders(mem) == []


def test_the_departure_belief_is_recorded_from_a_hero_move():
    mem = memory([A, B, C])
    corridor.note_hero_post(mem, B)
    assert corridor._believed_hero(mem) == B
    corridor.clear_hero_post(mem)
    assert corridor._believed_hero(mem) is None


def test_a_hero_departure_records_the_castle_he_walks_into():
    from docich.hanjuku.flows import map as M
    mem = memory([A, B, C], garrison={B: ['リーキ'], C: ['リーキ']})
    order = {'step': 'I:c9:%s>%s:h0' % (B, C), 'source': B, 'target': C,
             'purpose': 'move', 'corridor': True, 'general': '—'}
    snapshot = {'source': B, 'purpose': 'move'}
    with chart.map_context(mem):
        M._commit_departure(mem, order, {'general': HERO}, snapshot)
    assert mem[corridor.HERO_POST]['castle'] == C
    # While he is walking the belief is not used; once the march settles it is.
    assert corridor._believed_hero(mem) is None
    for sortie in mem['world']['sorties'].values():
        sortie['status'] = 'arrived'
    assert corridor._believed_hero(mem) == C
