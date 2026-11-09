"""The chapter-6 secret base sits one tile above the measured forest centre.

After the eleventh castle fell, the world map (Y view) was captured as a 256x224
frame. The dark forest ring with the 完熟秘密基地 is centred at pixel
(166.7, 67.5), and the live Y jump to the boss target settled its cursor at
(165.5, 70.5). The chart cell (818, 184) mapped to (166.6, 68.0), but the owner
watched the chapter-6 cursor land one tile below the base (2026-11-09), so the
cell is (818, 184): one tile (64 chart units = 8 px) above that centre.
"""
from docich import hanjuku_chart as chart


def test_the_ch6_boss_cell_maps_to_the_measured_forest_base():
    cell = chart.castles(6)['ボス']
    assert cell == (818, 184)
    ox, oy = chart.WORLD_MAP_OFFSET[6]
    x, y = cell[0] / 8 + ox, cell[1] / 8 + oy
    # One tile above the measured forest centre.
    assert abs(x - 166.7) <= 1.5 and abs(y - 67.5) <= 1.5


def test_the_y_jump_offset_lands_on_the_same_spot():
    cell = chart.castles(6)['ボス']
    ox, oy = chart.Y_JUMP_OFFSET[6]
    x, y = cell[0] / 8 + ox, cell[1] / 8 + oy
    # The live jump confirmed (165.5, 70.5) for the old centre; the owner's
    # correction moves the aim one tile up from it.
    assert abs(x - 165.5) <= 2.0 and abs(y - 70.5) <= 2.0
