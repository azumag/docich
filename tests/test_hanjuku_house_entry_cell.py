"""The house repair entry cell is per chapter (g1097/g1139 chapter 5)."""
import pytest

from docich.hanjuku.flows import house


def mem(chapter, misses=None):
    value = {'chapter': chapter}
    if misses is not None:
        value['house_entry_miss'] = misses
    return value


def test_chapter5_uses_the_house_own_cell_from_the_first_dispatch():
    # g1139 2026-11-06: the charted roof offset landed on the tile beside the
    # house and the general camped there, so the own cell must win first.
    assert house._entry_delta(mem(5)) == house.HOUSE_OWN_CELL_DELTA


def test_chapter5_keeps_the_own_cell_after_a_recorded_miss():
    assert house._entry_delta(mem(5, {'5': 1})) == house.HOUSE_OWN_CELL_DELTA
    assert house._entry_delta(mem(5, {'5': 2})) == house.HOUSE_OWN_CELL_DELTA


def test_measured_chapters_start_with_the_charted_roof_offset():
    # Chapter 4's entry was measured live on g1075 (2026-10-30).
    assert house._entry_delta(mem(4)) == house.HOUSE_ROOF_DELTA
    assert house._entry_delta(mem(2)) == house.HOUSE_ROOF_DELTA


def test_measured_chapters_alternate_to_the_own_cell_after_a_miss():
    # The persisted ledger keys chapters as JSON strings.
    assert house._entry_delta(mem(4, {'4': 1})) == house.HOUSE_OWN_CELL_DELTA
    assert house._entry_delta(mem(4, {'4': 2})) == house.HOUSE_ROOF_DELTA


def test_int_keyed_in_memory_misses_flip_the_same_way():
    assert house._entry_delta(mem(4, {4: 1})) == house.HOUSE_OWN_CELL_DELTA
    assert house._entry_delta(mem(4, {4: 2})) == house.HOUSE_ROOF_DELTA


@pytest.mark.parametrize('misses', [True, '1', 1.0, None])
def test_non_integer_miss_counts_do_not_flip_the_current_cell(misses):
    value = mem(4)
    if misses is not None:
        value['house_entry_miss'] = {'4': misses}
    assert house._entry_delta(value) == house.HOUSE_ROOF_DELTA


def _roof_frame(monkeypatch, roofs):
    """Drive _house_roof_delta with a synthetic marker and roof list."""
    from types import SimpleNamespace
    from docich.hanjuku.os_layer import screen as os_screen
    monkeypatch.setattr(os_screen, 'castle_roofs', lambda _frame: roofs)
    frame = object()
    scr = SimpleNamespace(kind='map_target', marker=(200, 200), cursor=None)
    return scr, frame


def test_two_own_roofs_pick_the_one_nearest_the_charted_delta(monkeypatch):
    # docich#4737: on the own castle's field both the castle flag and the house
    # flag are 'own'-coloured. A second roof inside HOUSE_ROOF_MATCH must not
    # void the reading; the house is the roof nearest the charted offset.
    from docich.hanjuku.flows import house
    # expected delta (12,-8): cursor(200,200) - roof.target = delta.
    # roof A gives delta (14,-6) -> dist |14-12|+|-6+8| = 4 (the house).
    # roof B gives delta (30,10)  -> dist |30-12|+|10+8| = 36 (a castle flag).
    roofs = [{'kind': 'own', 'target': (186, 206), 'clipped': False},
             {'kind': 'own', 'target': (170, 190), 'clipped': False}]
    scr, frame = _roof_frame(monkeypatch, roofs)
    assert house._house_roof_delta(scr, {'chapter': 6}, frame) == (14, -6)


def test_a_single_matching_roof_still_returns_its_delta(monkeypatch):
    from docich.hanjuku.flows import house
    roofs = [{'kind': 'own', 'target': (186, 206), 'clipped': False},
             {'kind': 'enemy', 'target': (50, 50), 'clipped': False}]
    scr, frame = _roof_frame(monkeypatch, roofs)
    assert house._house_roof_delta(scr, {'chapter': 6}, frame) == (14, -6)


def test_no_matching_roof_returns_none(monkeypatch):
    from docich.hanjuku.flows import house
    roofs = [{'kind': 'own', 'target': (50, 50), 'clipped': False}]
    scr, frame = _roof_frame(monkeypatch, roofs)
    assert house._house_roof_delta(scr, {'chapter': 6}, frame) is None
