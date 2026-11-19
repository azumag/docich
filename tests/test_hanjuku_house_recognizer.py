"""docich#4737: the small あたしの家 blob the castle scan's 25 px floor drops.

All geometry here is synthetic; the thresholds live in os_layer/screen.py.
"""
from docich.hanjuku.os_layer.pixels import Frame
from docich.hanjuku.os_layer.screen import house_roofs

OWN_RED = (220, 40, 60)      # _roof_kind 'own': r>=190, g<=70, 30<=b<=85
HEART_RED = (248, 0, 0)      # the heart mark's brighter banner red
OUTLINE = (32, 32, 32)       # the hut's dark walls/door outline


def _rgb():
    return bytearray(256 * 224 * 3)


def _put(rgb, x0, y0, w, h, colour):
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            o = (y * 256 + x) * 3
            rgb[o:o + 3] = bytes(colour)


def _frame(rgb):
    return Frame(256, 224, bytes(rgb))


def test_small_own_blob_over_a_hut_is_a_house():
    rgb = _rgb()
    _put(rgb, 160, 180, 4, 3, OWN_RED)    # 12 px: under the 25 px roof floor
    _put(rgb, 156, 184, 16, 12, OUTLINE)  # hut walls and door under the blob
    found = house_roofs(_frame(rgb), [])
    assert [r['kind'] for r in found] == ['house']
    assert found[0]['target'] == (161, 174)   # bottom-centre anchor, like roofs
    assert not found[0]['clipped']


def test_heart_red_blob_over_a_hut_is_a_house():
    rgb = _rgb()
    _put(rgb, 160, 180, 4, 3, HEART_RED)
    _put(rgb, 156, 184, 16, 12, OUTLINE)
    assert [r['kind'] for r in house_roofs(_frame(rgb), [])] == ['house']


def test_bare_flag_fragment_is_not_a_house():
    rgb = _rgb()
    _put(rgb, 160, 180, 4, 3, OWN_RED)
    assert house_roofs(_frame(rgb), []) == []


def test_tiny_speck_is_not_a_house():
    rgb = _rgb()
    _put(rgb, 160, 180, 2, 2, OWN_RED)    # 4 px < the minimum component
    _put(rgb, 156, 184, 16, 12, OUTLINE)
    assert house_roofs(_frame(rgb), []) == []


def test_blob_inside_a_castle_box_is_excluded():
    rgb = _rgb()
    _put(rgb, 160, 180, 4, 3, OWN_RED)
    _put(rgb, 156, 184, 16, 12, OUTLINE)
    roofs = [{'kind': 'own', 'sprite_box': (150, 170, 190, 200)}]
    assert house_roofs(_frame(rgb), roofs) == []
