"""A plain text screen gets the same bounded A an unreadable one does.

g1163 2026-11-12 21:3x: 101 no-input `text` frames in half an hour — the
screens had no hand cursor, so no choice was pending and only A could move on.
"""
from docich.hanjuku import orchestrator as O
from docich.hanjuku.os_layer.screen import Screen


def test_a_text_screen_without_a_hand_is_a_plain_text_hold():
    assert O.plain_text_hold(Screen([], None, '', kind='text')) is True


def test_a_text_screen_with_a_hand_is_not():
    assert O.plain_text_hold(Screen([], (10, 20, 30, 40), '', kind='text')) is False


def test_other_kinds_are_not_plain_text():
    assert O.plain_text_hold(Screen([], None, '', kind='map')) is False
    assert O.plain_text_hold(Screen([], None, '', kind='castle_menu')) is False
