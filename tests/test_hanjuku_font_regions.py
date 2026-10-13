"""Region-limited OCR must match full-frame masks; no gameplay timing changes."""
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich.hanjuku_font import dark, light, read_lines, row_masks
from docich.hanjuku_glyphs import GLYPHS, MARKS
from docich.hanjuku_pixels import Frame


def old_masks(frame, predicate):
    out = []
    for y in range(frame.height):
        value = 0
        for x in range(frame.width):
            value = value << 1 | bool(predicate(*frame.pixel(x, y)))
        out.append(value)
    return out


def frame_with_marks(y):
    rgb = bytearray(bytes((16, 72, 57)) * (256 * 224))
    code = {v: k for k, v in GLYPHS.items()}
    marks = {v: k for k, v in MARKS.items()}
    def tile(x, row, bits):
        for dy in range(8):
            for dx in range(8):
                if bits >> (63 - dy * 8 - dx) & 1:
                    i = ((row + dy) * 256 + x + dx) * 3
                    rgb[i:i + 3] = b'\xff\xff\xff'
    tile(176, y, code['は'])
    tile(176, y - 8, marks['゛'])
    tile(184, y, code['は'])
    tile(184, y - 8, marks['゜'])
    tile(200, y, 0x0123456789ABCDEF)  # Keep unknowns, never substitute glyphs.
    return Frame(256, 224, bytes(rgb))


@pytest.mark.parametrize('dy', range(8))
def test_marks_outside_rect_and_non_aligned_columns_match_full_masks(dy):
    y = 176 + dy
    frame = frame_with_marks(y)
    rect = (179, y, 215, 216)
    full = row_masks(frame, light)
    expected = read_lines(frame, rect=rect, masks=full)
    assert read_lines(frame, rect=rect) == expected
    assert 'ばぱ' in ''.join(line.text for line in expected)


@pytest.mark.parametrize('predicate', [light, dark, lambda r,g,b: 95 <= min(r,g,b) <= max(r,g,b) <= 120])
@pytest.mark.parametrize('rect', [(0,0,256,224), (0,160,256,200), (176,176,256,216),
                                  (13,7,99,43), (248,216,280,240), (0,0,7,7)])
def test_region_results_match_legacy_on_deterministic_noise(predicate, rect):
    rng = random.Random(1024)
    frame = Frame(256, 224, bytes(rng.randrange(256) for _ in range(256 * 224 * 3)))
    legacy = old_masks(frame, predicate)
    assert row_masks(frame, predicate) == legacy
    assert read_lines(frame, rect=rect, predicate=predicate) == read_lines(frame, rect=rect, masks=legacy)


def test_narrow_menu_samples_only_region_plus_kana_context():
    frame = frame_with_marks(176)
    calls = 0
    def counted(r, g, b):
        nonlocal calls
        calls += 1
        return light(r, g, b)
    read_lines(frame, predicate=counted, rect=(176,176,256,216))
    assert calls == 80 * 48
    assert calls < frame.width * frame.height / 10


def test_supplied_masks_do_not_invoke_predicate():
    frame = frame_with_marks(176)
    masks = row_masks(frame, light)
    def forbidden(*_):
        raise AssertionError('supplied masks must be reused')
    assert read_lines(frame, rect=(176,176,256,216), predicate=forbidden, masks=masks)


# These are synthetic recognition controls built from the measured glyphs,
# not new screenshots, natural-run evidence, or a whole-game accuracy score.
from docich.hanjuku.os_layer.font import UNKNOWN, TextLine, find, joined


def _text_line(text, y=176):
    return TextLine(y, tuple((24 + 8 * i, ch) for i, ch in enumerate(text) if ch != ' '))


@pytest.mark.parametrize('label', [
    'しゅつげきしますか', 'きりふだセレクト', 'よろしいでっか',
    'たまごをつかう', 'せめこまれ', 'バカンス', 'ゼウス', '144G',
])
def test_unknown_tiles_cannot_be_deleted_to_create_a_match(label):
    for split in range(1, len(label)):
        damaged = label[:split] + UNKNOWN + label[split:]
        line = _text_line(damaged)
        assert find([line], label) is None
        assert label not in joined([line])
        assert joined([line]) == damaged
        # Human-readable presentation remains separate from decision text.
        assert line.known.replace(' ', '') == label


@pytest.mark.parametrize('label', ['しゅつげきしますか', 'たまごをつかう', 'ゼウス', '144G'])
def test_readable_labels_keep_spaces_border_and_other_word_compatibility(label):
    line = _text_line(UNKNOWN * 2 + ' ' + label + ' ' + UNKNOWN * 2)
    assert find([line], label) is line
    assert joined([line]) == label
    spaced = _text_line(' '.join(label))
    assert find([spaced], label) is spaced
    assert joined([spaced]) == label
    later = _text_line(label, y=192)
    damaged = _text_line(label[0] + UNKNOWN + label[1:])
    assert find([damaged, later], label) is later


def test_internal_unknown_runs_survive_compaction_without_mutating_cells():
    line = _text_line(UNKNOWN + 'ゼ' + UNKNOWN * 2 + ' ウス' + UNKNOWN)
    cells = line.cells
    assert joined([line]) == 'ゼ' + UNKNOWN * 2 + 'ウス'
    assert line.cells == cells
    assert line.words() == ['ゼ', 'ウス']
    assert [word for _, word in line.spans()] == ['ゼ', 'ウス']


def test_empty_and_decoration_only_rows_do_not_become_text():
    assert joined([]) == ''
    assert joined([TextLine(168, ()), _text_line(UNKNOWN * 5)]) == ''
    # Kana accent rows contain unknown tiles already consumed by the row
    # below. Preserve the established multiline dialogue joining behavior.
    first, second = _text_line('たまごに', 176), _text_line('もどれ', 192)
    assert joined([_text_line(UNKNOWN * 3, 168), first,
                   _text_line(UNKNOWN * 2, 184), second]) == 'たまごにもどれ'


def _recognition_frame(text, y, predicate):
    import unicodedata
    background, ink = ((16, 72, 57), (255, 255, 255)) if predicate is light else (
        (230, 220, 190), (0, 0, 0))
    rgb = bytearray(bytes(background) * (256 * 224))
    glyphs = {ch: bits for bits, ch in GLYPHS.items()}
    accents = {'\u3099': 0x00000000000a0a00, '\u309a': 0x0000000000040a04}
    unknown_bits = 0x0123456789ABCDEF
    assert unknown_bits not in GLYPHS and unknown_bits not in MARKS

    def draw(x, row, bits):
        for dy in range(8):
            for dx in range(8):
                if bits >> (63 - dy * 8 - dx) & 1:
                    i = ((row + dy) * 256 + x + dx) * 3
                    rgb[i:i + 3] = bytes(ink)

    for i, ch in enumerate(text):
        if ch == ' ':
            continue
        x = 24 + i * 8
        if ch == UNKNOWN:
            draw(x, y, unknown_bits)
            continue
        parts = unicodedata.normalize('NFD', ch)
        draw(x, y, glyphs[parts[0]])
        if len(parts) == 2:
            draw(x, y - 8, accents[parts[1]])
    return Frame(256, 224, bytes(rgb))


@pytest.mark.parametrize('dy', range(8))
@pytest.mark.parametrize('predicate', [light, dark])
@pytest.mark.parametrize('label', ['しゅつげきしますか', 'きりふだセレクト', 'ゼウス', '144G'])
def test_native_glyph_pixels_retain_unknown_barrier_after_reading(label, predicate, dy):
    y = 176 + dy
    damaged = label[:2] + UNKNOWN + label[2:]
    frame = _recognition_frame(damaged, y, predicate)
    # A real glyph read, including voiced kana and every vertical offset;
    # no mocked lines, fuzzy glyph substitutions or image-model calls.
    lines = read_lines(frame, predicate=predicate, rect=(24, y, 240, y + 8))
    assert joined(lines) == damaged
    assert find(lines, label) is None
    intact = read_lines(_recognition_frame(label, y, predicate), predicate=predicate,
                        rect=(24, y, 240, y + 8))
    assert joined(intact) == label
    assert find(intact, label) is not None
