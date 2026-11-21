"""手順層: 章クリア後のパスワード入力画面 (オヴァリウム等の扉).

画面構成 (g1190 2026-11-19, #4564 を実機プローブで解明):
- 右上: 収集したパスワード語のスクロール可能リスト + 語を指す手カーソル。
- 左下: 未入力の残り語を並べた 3×3 グリッド。
- up/down = 手カーソル移動、A = 手が指す語を入力。
- 全語を **決められた順** で入力すると扉が開く。リスト表示順ではない。

2026-11-22 の実機プローブで、第11話オヴァリウムの
正解順がリスト index [1,0,5,3,2,4,9,8,6,7] であることを確認した。
旧実装の「上から A+down で全語」は、語順が違うため
「パスワードガ ナガイマス。ヤリナオシマスカ?」で失敗し、6サイクル後に
保留 (screen_stalled) していた。ここでは 1入力/観測で index を移動し、
A で確定する。扉が開いて screen_kind が変わればこの flow は呼ばれない。
"""
from __future__ import annotations

from ..decision_log import _record
from ..os_layer.commands import pad


# 第11話オヴァリウムの実測順 (リスト先頭を 0 とする)。
_TARGET_ORDER = (1, 0, 5, 3, 2, 4, 9, 8, 6, 7)
_TOP_PRESSES = 15          # どの位置からでも先頭へ届く (実測 up×15)
_MAX_CYCLES = 2            # 扉が開かない場合のやり直し上限
_ADVANCE_PRESSES = 2       # 扉演出を進める A の上限


def password_step(screen, mem):
    """リスト先頭へ上げ、実測順に A で語を確定する。1入力/観測。"""
    st = mem.setdefault('password_entry', {})
    if st.get('cycles', 0) >= _MAX_CYCLES:
        return []
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
