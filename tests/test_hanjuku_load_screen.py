"""The save/load menu loads with A instead of the shop's endless B.

g1190 2026-11-15: after the local state was replaced by an older save, the
load menu read as phase 'shop' and the bot pressed B until screen_stalled.
"""
from pathlib import Path

from docich.hanjuku_bot import decide
from docich.hanjuku_pixels import read_png
from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.screen import parse

ROOT = Path(__file__).resolve().parents[1]
FRAME = ROOT / 'tests' / 'fixtures' / 'hanjuku' / 'g1190-load-screen.png'


def test_the_load_menu_is_read_from_its_prompt():
    screen = parse(read_png(FRAME), phase=classify(read_png(FRAME)))
    assert 'ロードなされますかな' in screen.text


def test_the_load_prompt_presses_a_and_records_it():
    actions, result = decide(read_png(FRAME), {'policy': {'name': {'done': True}}})
    assert actions == [{'type': 'pad', 'buttons': ['a'], 'hold_ms': 100}]
    assert result['_records'][-1]['decision'] == 'load_screen_confirm'


def test_the_save_slot_names_its_chapter():
    _, result = decide(read_png(FRAME), {'policy': {'name': {'done': True}}})
    assert result['policy']['chapter'] == 9      # the "だい9わ" slot
