"""The ambush wave is spread over the whole track, not the sorted head.

Live g1288 2026-11-21 18:5x: four ambush cells all sat at x=674 (the first
sorted cells) while the boss roamed x 658-874, so the crowd never met it.
"""
from docich.hanjuku.flows import boss_pursuit as bp


def memory(names, cells):
    return {
        'chapter': 9, 'tick': 10000,
        'boss_cell': {'cell': [900, 700], 'tick': 9990},
        'boss_cell_track': [{'tick': 9000 + i, 'cell': list(c)} for i, c in enumerate(cells)],
        bp.LIST_KEY: {n: {'step': 'A', 'launched': 9500} for n in names},
    }


TRACK = [(666, 700), (674, 212), (674, 317), (674, 333), (674, 773),
         (682, 565), (690, 181), (698, 581), (714, 637), (754, 629),
         (770, 853), (818, 397), (858, 853), (866, 413), (874, 709), (906, 629)]


def test_four_generals_are_spread_over_the_whole_track():
    names = ['a', 'b', 'c', 'd']
    aims = [bp._ambush(memory(names, TRACK), n) for n in names]
    assert len(set(aims)) == 4
    # The old code put every aim in the western column (x <= 674).
    assert any(aim[0] >= 800 for aim in aims)
    assert len({aim[0] for aim in aims}) >= 3


def test_a_single_general_still_gets_a_track_cell():
    aim = bp._ambush(memory(['solo'], TRACK), 'solo')
    assert aim in TRACK
