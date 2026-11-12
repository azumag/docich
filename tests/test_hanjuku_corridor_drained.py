"""A dispatched general only refills a castle once he has arrived.

g1163 2026-11-11 20:3x-21:4x: of 93 chapter-9 order starts, 21 ended as
「出撃元に出せる将軍がいないため、この城を一時的に対象から外す」. The planner tried
to sortie from a castle whose live list was empty because merely *sending* a
general into it cleared the drained mark while he was still on the road.
"""
from docich.hanjuku.strategy import corridor


def memory(entries, sorties, tick=1000, month='22-1'):
    return {'chapter': 9, 'tick': tick, 'month': month,
            'corridor_drained': entries, 'world': {'sorties': sorties}, '_records': []}


def drained(castle='サヌト', tick=900, month='22-1', kind='empty'):
    return {castle: {'kind': kind, 'tick': tick, 'month': month}}


def test_an_empty_castle_stays_skipped_with_no_dispatch():
    mem = memory(drained(), {})
    assert corridor._drained(mem, 'サヌト', 'attack') is True


def test_a_march_still_on_the_road_does_not_refill_the_castle():
    mem = memory(drained(), {'I:c9:タオ>サヌト:7': {
        'target': 'サヌト', 'tick': 950, 'status': 'en_route', 'general': 'リーキ'}})
    assert corridor._drained(mem, 'サヌト', 'attack') is True


def test_a_confirmed_arrival_refills_the_castle():
    mem = memory(drained(), {'I:c9:タオ>サヌト:7': {
        'target': 'サヌト', 'tick': 950, 'status': 'arrived', 'general': 'リーキ'}})
    assert corridor._drained(mem, 'サヌト', 'attack') is False


def test_an_overdue_march_is_rechecked_once_the_window_passes():
    sorties = {'I:c9:タオ>サヌト:7': {
        'target': 'サヌト', 'tick': 950, 'status': 'en_route', 'general': 'リーキ'}}
    assert corridor._drained(memory(drained(), sorties, tick=1000), 'サヌト', 'attack') is True
    late = 950 + corridor.ARRIVAL_RECHECK_TICKS
    # v704: the mark protects its own reading for DRAINED_MIN_HOLD ticks first.
    assert corridor._drained(memory(drained(), sorties, tick=late), 'サヌト', 'attack') is True
    older = 900 + corridor.DRAINED_MIN_HOLD + corridor.ARRIVAL_RECHECK_TICKS
    assert corridor._drained(memory(drained(), sorties, tick=older), 'サヌト', 'attack') is False


def test_a_march_sent_before_the_empty_read_does_not_refill():
    mem = memory(drained(tick=900), {'I:c9:タオ>サヌト:7': {
        'target': 'サヌト', 'tick': 800, 'status': 'arrived', 'general': 'リーキ'}})
    assert corridor._drained(mem, 'サヌト', 'attack') is True


def test_the_mark_survives_a_month_change_when_nobody_was_sent():
    """A new month is not news about this castle (v697)."""
    mem = memory(drained(month='21-12'), {}, tick=1000, month='22-1')
    assert corridor._drained(mem, 'サヌト', 'attack') is True


def test_a_month_change_rechecks_a_castle_someone_was_sent_into():
    """A dispatch into the castle keeps the old re-read behaviour."""
    mem = memory(drained(tick=100, month='21-12'), {'I:c9:タオ>サヌト:7': {
        'target': 'サヌト', 'tick': 200, 'status': 'en_route', 'general': 'リーキ'}},
        tick=1000, month='22-1')
    assert corridor._drained(mem, 'サヌト', 'attack') is False


def test_the_hero_alone_still_walks_forward():
    mem = memory(drained(kind='hero'), {})
    assert corridor._drained(mem, 'サヌト', 'move') is False


def test_a_march_that_ended_without_arriving_keeps_the_castle_drained():
    """An overdue/closed march never puts anybody in the castle.

    g1163 2026-11-12 00:2x-00:3x re-ordered サヌト>オシロガ every couple of
    minutes and closed it with 「出撃元に出せる将軍がいない」 each time.
    """
    for status in ('overdue', 'closed'):
        mem = memory(drained(), {'I:c9:タオ>サヌト:7': {
            'target': 'サヌト', 'tick': 950, 'status': status, 'general': 'リーキ'}},
            tick=950 + corridor.ARRIVAL_RECHECK_TICKS)
        assert corridor._drained(mem, 'サヌト', 'attack') is True


def test_an_in_flight_march_is_rechecked_after_the_window():
    mem = memory(drained(), {'I:c9:タオ>サヌト:7': {
        'target': 'サヌト', 'tick': 950, 'status': 'en_route', 'general': 'リーキ'}},
        tick=900 + corridor.DRAINED_MIN_HOLD + corridor.ARRIVAL_RECHECK_TICKS)
    assert corridor._drained(mem, 'サヌト', 'attack') is False


def test_a_fresh_empty_reading_is_not_re_probed():
    """g1163 11:0x: 47 order starts an hour against 8 launches into empty castles."""
    mem = memory(drained(tick=1000), {'I:c9:タオ>サヌト:7': {
        'target': 'サヌト', 'tick': 1010, 'status': 'en_route', 'general': 'リーキ'}},
        tick=1000 + corridor.DRAINED_MIN_HOLD - 1)
    assert corridor._drained(mem, 'サヌト', 'attack') is True
