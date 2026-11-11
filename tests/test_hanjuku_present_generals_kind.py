"""Only a general list carries residents.

g1163 2026-11-12 01:4x: the saved frames show the main menu reading back
しょうぐん/しろ/きりふだ/システム and a sortie confirmation reading へいし/にん/P when
the same helper is asked anyway. None of those are people standing in a castle,
so the helper now refuses any other screen kind outright.
"""
from docich.hanjuku.os_layer.screen import Screen


def _screen(kind, text, options):
    rows = []
    return Screen(rows, None, text, kind=kind)


class _Line:
    def __init__(self, y, spans):
        self.y = y
        self._spans = spans
        self.cells = {}
        self.text = ''.join(w for _, w in spans)

    def spans(self):
        return list(self._spans)

    def span(self, x0, x1):
        return ''.join(w for x, w in self._spans if x0 <= x < x1)

    def words(self, x0, x1):
        return [w for x, w in self._spans if x0 <= x < x1]


def _list_screen(kind, names):
    from docich.hanjuku.os_layer import readers
    class _S:
        def __init__(self):
            self.kind = kind
            self.text = 'しゅつげき' + ''.join(n for _, n in names)
            self.hand = (120, 40)
            self.lines = [_Line(y, [(120, n)]) for y, (_, n) in zip(range(40, 200, 16), names)]
        def has(self, *_a):
            return False
    return _S()


def test_a_main_menu_is_not_a_general_list():
    from docich.hanjuku.os_layer.readers import _present_generals
    screen = _list_screen('main_menu', [(120, 'しょうぐん'), (120, 'しろ')])
    assert _present_generals(screen) is None


def test_a_sortie_confirmation_is_not_a_general_list():
    from docich.hanjuku.os_layer.readers import _present_generals
    screen = _list_screen('sortie_confirm', [(120, 'へいし'), (120, 'にん')])
    assert _present_generals(screen) is None


def test_an_unreadable_kind_is_not_a_general_list():
    from docich.hanjuku.os_layer.readers import _present_generals
    screen = _list_screen('unknown', [(120, 'バトウラ')])
    assert _present_generals(screen) is None


def test_a_general_list_still_yields_its_residents():
    from docich.hanjuku.os_layer.readers import _present_generals
    screen = _list_screen('general_list', [(120, 'どうし'), (120, 'マスカット')])
    assert _present_generals(screen) == ['どうし', 'マスカット']
