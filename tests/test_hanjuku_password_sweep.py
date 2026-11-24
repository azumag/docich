"""#4564/#5325: the Ovarium password screen is classified as its own kind."""
from types import SimpleNamespace

from docich.hanjuku.os_layer.screen import classify_text


def test_password_screen_is_not_misclassified_as_shop():
    screen = SimpleNamespace(text='パスワードヲ ジュンバンニ イレテクダサイ。',
                             lines=[], menu_rows=[], menu_cursor=None)
    assert classify_text(screen) == 'password_entry'
