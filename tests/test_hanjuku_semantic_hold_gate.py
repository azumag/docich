"""Semantic hold keys: menus over an animated map count as one screen (#3599).

The no-input gate (see test_hanjuku_no_input_gate.py) counts observations
whose identity is unchanged. Menu/banner kinds get a semantic key, so a
digest-changing background cannot keep the streak at 1 forever. Kept out of
the gate module so the no-input performance comparison stays apples-to-apples.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich.hanjuku_bot import NO_INPUT_HOLD_MAX, decide
from docich.hanjuku_pixels import Frame


def test_an_animated_castle_menu_is_counted_by_its_semantic_key():
    # #3599: a castle_menu over the animated map got a fresh frame digest on
    # every observation (water cycles), so the digest-keyed streak never
    # passed 2 and the corner ended input_stalled after ~100 s of no input.
    # The menu's own identity -- kind, text, cursor, hand -- is animation
    # independent, so identical reads must accumulate the hold even when the
    # pixels behind the panel keep changing.
    import copy
    from docich.hanjuku_pixels import read_png
    from docich.hanjuku_screen import parse
    from docich.hanjuku.orchestrator import MENU_HOLD_KINDS
    assert 'castle_menu' in MENU_HOLD_KINDS
    root = Path(__file__).parent / 'fixtures' / 'hanjuku'
    base = read_png(root / 'g1059-snow-castle-status-menu.png')
    assert parse(base).kind == 'castle_menu'
    # Variants differ only in the far bottom-right pixel: the parser reads the
    # identical menu while the frame digest changes every observation.
    frames = []
    for i in range(3):
        rgb = bytearray(base.rgb)
        j = (223 * base.width + 255) * 3
        rgb[j:j + 3] = bytes((i + 10, i + 20, i + 30))
        variant = Frame(base.width, base.height, bytes(rgb))
        assert parse(variant).kind == 'castle_menu'
        assert parse(variant).text == parse(base).text
        frames.append(variant)
    assert len({f.digest() for f in frames}) == 3
    # Exhaust the chapterless status probe first: its deliberate A input is
    # not a no-input fallback and must not mask the semantic hold gate.
    state = {'chapter_probe': {'month': None, 'opens': 3, 'seen': []}}
    for i in range(NO_INPUT_HOLD_MAX + 3):
        actions, state = decide(frames[i % 3], copy.deepcopy(state))
        if actions:
            break
    # The digest-keyed gate stayed at 1 forever; the semantic key must fire.
    assert actions
    assert any(r['decision'] == 'no_input_fallback' for r in state['_records'])
