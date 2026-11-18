"""The chapter-clear password screen is recognised and held, never B-spammed.

g1190 2026-11-18 62-2: after the chapter-11 boss the screen 「パスワードヲ
ジュンバンニ イレテクダサイ。」 sat while the shop phase pressed B for twenty
minutes (static frame → every observation held or B'd).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich.hanjuku.os_layer.screen import Screen, classify_text

TEXT = 'パスワードヲジュンバンニイレテクダサイ。かんじゅくわれらがははいだいなる'


def test_the_password_screen_has_its_own_kind():
    screen = Screen([], None, TEXT, kind='text')
    assert classify_text(screen) == 'password_entry'


def test_another_text_is_not_the_password_screen():
    screen = Screen([], None, 'パスワードのニュウリョクガヒツヨウデス', kind='text')
    assert classify_text(screen) != 'password_entry'
