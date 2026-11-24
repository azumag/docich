"""手順層: 章クリア後のパスワード入力画面 (オヴァリウム等の扉).

画面構成 (g1190 2026-11-19, #4564 を実機プローブで解明):
- 右上: 収集したパスワード語のスクロール可能リスト + 語を指す手カーソル。
- 左下: 未入力の残り語を並べた 3×3 グリッド。
- up/down = 手カーソル移動、A = 手が指す語を入力。
- 全語を **決められた順** で入力すると扉が開く。リスト表示順ではない。

2026-11-24: リストの語順は**収集順でランごとに変わる**ことが判明した
(前回プローブでは われらが=row2 / エッグママの=row4、今回の実測では
われらが=row0 / エッグママの=row2)。固定 index 列
`_TARGET_ORDER` はそのランでは必ず外れるため、画面の灰色リストを
読み、正しい語順 (PHRASE) の次の語へ手を送って A する方式に変えた。
灰色リストが読めない配色のときだけ従来の固定 index にフォールバックする。
"""
from __future__ import annotations

from ..decision_log import _record
from ..os_layer.commands import pad


# 扉の正しい語順 (第11話オヴァリウム).
PHRASE = ('われらが', 'かんじゅく', 'せいめいたいの', 'いだいなる', 'はは',
          'エッグママの', 'きゅうでん', 'オヴァリウムよ', 'われを', 'みちびけ')

# 灰色リストが読めない配色のときの従来順 (リスト先頭を 0 とする).
_TARGET_ORDER = (1, 0, 5, 3, 2, 4, 9, 8, 6, 7)
_TOP_PRESSES = 15          # どの位置からでも先頭へ届く (実測 up×15)
_MAX_CYCLES = 2            # 扉が開かない場合のやり直し上限
_ADVANCE_PRESSES = 2       # 扉演出を進める A の上限


def _rows(screen):
    return [(y, w) for y, w in (getattr(screen, 'password_list', None) or ()) if w]


def _selected(screen, rows):
    hand = screen.hand
    if not hand or not rows:
        return None
    center = (hand[1] + hand[3]) / 2.0
    return min(rows, key=lambda row: abs((row[0] + 4) - center))


def _grid_words(screen):
    """Words still shown in the remaining grid (already-entered ones are gone)."""
    text = (screen.text or '').replace(' ', '').replace('\u3000', '')
    if 'パスワード' not in text:
        # No readable grid (synthetic/test screen): assume a fresh attempt.
        return set(PHRASE)
    for marker in ('パスワードヲジュンバンニイレテクダサイ。', 'パスワードヲ'):
        if marker in text:
            text = text.split(marker, 1)[-1]
            break
    return {word for word in PHRASE if word in text}


def _dynamic_step(screen, mem, st):
    """Enter the phrase words in order, using the measured on-screen list.

    A partial attempt (some words already consumed from the grid by the old
    fixed-index entry) is finished in phrase order first; that completes a
    wrong sequence, the door asks to retry, and the next attempt starts from
    the full grid and enters the correct order.
    """
    rows = _rows(screen)
    remaining = _grid_words(screen)
    step = int(st.get('dyn_step') or 0)
    if not remaining:
        advance = int(st.get('dyn_advance') or 0)
        if advance < _ADVANCE_PRESSES:
            st['dyn_advance'] = advance + 1
            return [pad('a', 10)]
        cycles = int(st.get('cycles') or 0) + 1
        _record(mem, 'password_entry_resweep', screen=screen.kind,
                observed_metric={'cycles': cycles, 'order': 'measured_list'},
                reason='実測リスト順の入力後もパスワード画面のため先頭から再試行')
        st.clear()
        st['cycles'] = cycles
        return [pad('up')]
    if len(remaining) < len(PHRASE):
        # Partial attempt: consume the remaining words in phrase order.
        want = next((w for w in PHRASE if w in remaining), None)
    else:
        want = PHRASE[step] if step < len(PHRASE) else None
    if want is None:
        st['dyn_advance'] = int(st.get('dyn_advance') or 0) + 1
        return [pad('a', 10)]
    sel = _selected(screen, rows)
    if sel is None:
        return [pad('down')]
    search = st.setdefault('dyn_search', {})
    # Row y-coordinates are fixed; include the words so a real scroll is
    # progress rather than a false 'stuck' observation.
    sign = tuple((y, word) for y, word in rows)
    if search.get('sign') == sign:
        search['stuck'] = int(search.get('stuck') or 0) + 1
    else:
        search['sign'] = sign
        search['stuck'] = 0
    if sel[1] == want:
        st['dyn_step'] = PHRASE.index(want) + 1
        st.pop('dyn_search', None)
        return [pad('a', 10)]
    wanted = [y for y, w in rows if w == want]
    if wanted:
        search['dir'] = 'down' if wanted[0] > sel[0] else 'up'
        return [pad(search['dir'])]
    if int(search.get('stuck') or 0) >= 2:
        search['dir'] = 'up' if search.get('dir') == 'down' else 'down'
        search['stuck'] = 0
        search['sign'] = None
    direction = search.get('dir') or 'down'
    search['dir'] = direction
    search['presses'] = int(search.get('presses') or 0) + 1
    if search['presses'] % 20 == 0:
        search.update(dir='down', stuck=0, sign=None)
    return [pad(direction)]


def _legacy_step(screen, mem, st):
    tops = st.get('tops', 0)
    if tops < _TOP_PRESSES:
        st['tops'] = tops + 1
        return [pad('up')]
    idx = st.get('idx', 0)
    step = st.get('step', 0)
    if step < len(_TARGET_ORDER):
        target = _TARGET_ORDER[step]
        if idx < target:
            st['idx'] = idx + 1
            return [pad('down')]
        if idx > target:
            st['idx'] = idx - 1
            return [pad('up')]
        st['step'] = step + 1
        return [pad('a', 10)]
    advance = st.get('advance', 0)
    if advance < _ADVANCE_PRESSES:
        st['advance'] = advance + 1
        return [pad('a', 10)]
    cycles = st.get('cycles', 0) + 1
    _record(mem, 'password_entry_resweep', screen=screen.kind,
            observed_metric={'cycles': cycles, 'target_order': list(_TARGET_ORDER)},
            reason='実測順の入力後もパスワード画面のため先頭から再試行')
    st.clear()
    st['cycles'] = cycles
    st['tops'] = 1
    return [pad('up')]


def password_step(screen, mem):
    """Enter the phrase in order; read the list when the grey rows are usable."""
    st = mem.setdefault('password_entry', {})
    if _rows(screen):
        if int(st.get('cycles') or 0) >= 6:
            return []
        return _dynamic_step(screen, mem, st)
    if st.get('cycles', 0) >= _MAX_CYCLES:
        return []
    return _legacy_step(screen, mem, st)
