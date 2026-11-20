"""#4564: the Ovarium entry password screen is classified and swept."""
from types import SimpleNamespace

from docich.hanjuku.flows import prompts as p
from docich.hanjuku.os_layer.screen import classify_text


def password_screen():
    return SimpleNamespace(kind='password', text='パスワードヲ ジュンバンニ イレテクダサイ。',
                           lines=[], menu_rows=[], menu_cursor=None, selected=None)


def test_password_screen_is_not_misclassified_as_shop():
    screen = SimpleNamespace(text='パスワードヲ ジュンバンニ イレテクダサイ。',
                             lines=[], menu_rows=[], menu_cursor=None)
    assert classify_text(screen) == 'password'


def test_sweep_scrolls_to_top_then_picks_a_then_down():
    mem = {}
    screen = password_screen()
    ups = [p.password_step(screen, mem) for _ in range(3)]
    assert all(len(u) == 1 and u[0]['buttons'] == ['up'] for u in ups)
    mem['password_sweep']['ups'] = p.PASSWORD_UP_FIRST
    step = p.password_step(screen, mem)
    assert [a['buttons'] for a in step] == [['a'], ['down']]


def test_sweep_releases_at_the_bound():
    mem = {'password_sweep': {'ups': p.PASSWORD_UP_FIRST, 'picks': p.PASSWORD_SWEEP_MAX}}
    assert p.password_step(password_screen(), mem) is None
    assert 'password_sweep' not in mem
