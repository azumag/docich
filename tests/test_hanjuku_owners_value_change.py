"""A partial flag read is not an ownership change.

g1163 2026-11-12 08:5x: the same map arrived as 10, 11 or 12 readable flags and
every set counted as news — 56 `world_map_owners` records an hour while nothing
had changed hands.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.os_layer import world as W

ROAD = chart.corridor(9)
A, B, C = ROAD[0], ROAD[1], ROAD[2]


def memory(flags):
    return {'chapter': 9, 'tick': 1000, 'month': '26-12', 'captured': [B],
            'world_map_flags': dict(flags), '_records': []}


def records(mem):
    return [r for r in mem['_records'] if r.get('decision') == 'world_map_owners']


def test_the_same_values_are_not_recorded_twice():
    mem = memory({B: 'own', C: 'enemy'})
    with chart.map_context(mem):
        W._apply_world_flags(mem, {B: 'own', C: 'enemy'})
    assert records(mem) == []


def test_a_partial_read_of_unchanged_values_is_silent():
    mem = memory({B: 'own', C: 'enemy'})
    with chart.map_context(mem):
        W._apply_world_flags(mem, {C: 'enemy'})       # B's roof was hidden
    assert records(mem) == []
    # the known half is not forgotten
    assert mem['world_map_flags'][B] == 'own'


def test_a_real_change_needs_two_reads_and_is_then_remembered():
    mem = memory({B: 'own', C: 'enemy'})
    with chart.map_context(mem):
        W._apply_world_flags(mem, {B: 'own', C: 'own'})     # first sighting
        W._apply_world_flags(mem, {B: 'own', C: 'own'})     # confirmed
    assert len(records(mem)) >= 1
    assert mem['world_map_flags'][C] == 'own'


def test_a_single_disagreeing_read_does_not_change_hands():
    """g1163: ドンドン／サヌト changed hands in the same minute they were taken."""
    mem = memory({B: 'own', C: 'enemy'})
    with chart.map_context(mem):
        W._apply_world_flags(mem, {B: 'own', C: 'own'})
    # one misread is held pending, not applied
    assert mem.get('captured') == [B]
    assert mem['world_map_pending'] == {C: 'own'}


def test_the_same_disagreement_twice_changes_hands():
    mem = memory({B: 'own', C: 'enemy'})
    with chart.map_context(mem):
        W._apply_world_flags(mem, {B: 'own', C: 'own'})
        W._apply_world_flags(mem, {B: 'own', C: 'own'})
    assert C in mem['captured']
    assert mem['world_map_pending'] == {}


def test_an_agreeing_read_clears_a_pending_disagreement():
    mem = memory({B: 'own', C: 'enemy'})
    with chart.map_context(mem):
        W._apply_world_flags(mem, {C: 'own'})       # misread
        W._apply_world_flags(mem, {C: 'enemy'})     # back to the known value
    assert C not in mem['captured']
    assert mem['world_map_pending'] == {}


def test_a_single_disagreeing_read_does_not_write_ownership():
    """g1163 2026-11-13 16:0x: castle_ownership said ドンドン enemy while
    captured still held it and lost did not list it. The flag write happened
    before _confirmed_flags; only the captured/lost update was held back.
    """
    mem = memory({B: 'own', C: 'enemy'})
    with chart.map_context(mem):
        W._apply_world_flags(mem, {B: 'own', C: 'own'})     # one misread
    assert (mem.get('castle_ownership') or {}).get(C, {}).get('owner') != 'own'
    assert mem['world_map_pending'] == {C: 'own'}


def test_a_confirmed_read_moves_ownership_with_captured_and_lost():
    mem = memory({B: 'own', C: 'enemy'})
    with chart.map_context(mem):
        W._apply_world_flags(mem, {B: 'own', C: 'own'})
        W._apply_world_flags(mem, {B: 'own', C: 'own'})
    assert mem['castle_ownership'][C]['owner'] == 'own'
    assert C in mem['captured']


def test_a_single_enemy_flag_keeps_ownership_and_capture():
    mem = memory({B: 'own', C: 'enemy'})
    with chart.map_context(mem):
        W._apply_world_flags(mem, {B: 'enemy', C: 'enemy'})   # one misread
    assert (mem.get('castle_ownership') or {}).get(B, {}).get('owner') != 'enemy'
    assert B in mem['captured']


def test_a_confirmed_enemy_flag_moves_ownership_too():
    mem = memory({B: 'own', C: 'enemy'})
    with chart.map_context(mem):
        W._apply_world_flags(mem, {B: 'enemy', C: 'enemy'})
        W._apply_world_flags(mem, {B: 'enemy', C: 'enemy'})
    assert mem['castle_ownership'][B]['owner'] == 'enemy'
    assert B not in mem['captured'] and B in mem['lost']


def test_a_captured_castle_is_due_for_its_own_roster_read():
    """g1163 2026-11-13 17:3x: retaking オオキク left four generals unknown.

    The castle's own list is the receipt for who stands there after the
    capture; the due time gives the winner a moment to enter.
    """
    mem = memory({B: 'own', C: 'enemy'})
    with chart.map_context(mem):
        W._apply_world_flags(mem, {B: 'own', C: 'own'})
        W._apply_world_flags(mem, {B: 'own', C: 'own'})
    assert C in mem['captured']
    assert mem['roster_read_due'][C] == mem['tick'] + W.CAPTURE_READ_DELAY
