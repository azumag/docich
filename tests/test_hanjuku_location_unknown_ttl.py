"""A stale location mark must not keep the army out of the road for good.

g1163 2026-11-11 23:0x: 29 of the 32 living generals sat in
``general_location_unknown``. Each overdue march added one and nothing cleared
them, so the plan had about three usable generals, the front starved and the
captured road castles flipped back and forth. The mark is still right — an
overdue march is not proof of arrival — but it has to expire, because the next
sortie reads the castle's live list and settles where the general stands.
"""
from docich.hanjuku.os_layer.world import (
    LOCATION_UNKNOWN_TTL, expire_unknown_locations, mark_location_unknown)


def memory(unknown=(), at=None, tick=1000):
    world = {'general_location_unknown': list(unknown)}
    if at is not None:
        world['general_location_unknown_at'] = dict(at)
    return {'tick': tick, 'month': '22-1', 'world': world}


def test_a_fresh_mark_keeps_the_general_out():
    mem = memory(['バトウラ'], {'バトウラ': 950})
    assert expire_unknown_locations(mem, now=1000) == []
    assert mem['world']['general_location_unknown'] == ['バトウラ']


def test_a_mark_older_than_the_window_is_released():
    mem = memory(['バトウラ'], {'バトウラ': 1000 - LOCATION_UNKNOWN_TTL})
    assert expire_unknown_locations(mem, now=1000) == ['バトウラ']
    assert mem['world']['general_location_unknown'] == []
    assert 'バトウラ' not in mem['world']['general_location_unknown_at']


def test_only_the_expired_names_are_released():
    mem = memory(['バトウラ', 'リーキ'], {'バトウラ': 100, 'リーキ': 990})
    assert expire_unknown_locations(mem, now=1000) == ['バトウラ']
    assert mem['world']['general_location_unknown'] == ['リーキ']


def test_a_mark_without_a_timestamp_starts_its_clock_and_stays():
    mem = memory(['バトウラ'])
    assert expire_unknown_locations(mem, now=1234) == []
    assert mem['world']['general_location_unknown_at']['バトウラ'] == 1234


def test_marking_records_the_first_tick_only():
    mem = memory()
    mark_location_unknown(mem, 'キャンディー', tick=500)
    mark_location_unknown(mem, 'キャンディー', tick=900)
    assert mem['world']['general_location_unknown'] == ['キャンディー']
    assert mem['world']['general_location_unknown_at']['キャンディー'] == 500


def test_marking_ignores_a_missing_name():
    mem = memory()
    mark_location_unknown(mem, '', tick=500)
    mark_location_unknown(mem, None, tick=500)
    assert mem['world']['general_location_unknown'] == []


def test_an_empty_list_is_left_alone():
    mem = memory()
    assert expire_unknown_locations(mem, now=9999) == []
