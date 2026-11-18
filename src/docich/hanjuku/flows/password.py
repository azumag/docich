"""手順層: 章クリア後のパスワード入力画面 (オヴァリウム等の扉).

画面構成 (g1190 2026-11-19, #4564 を実機プローブで解明):
- 右上: 収集したパスワード語のスクロール可能リスト + 語を指す手カーソル。
- 左下: 未入力の残り語を並べた 3×3 グリッド。
- A = 手が指す語を入力 (下グリッドから消える)。up/down = 手カーソル移動。
- 全語を入力すると扉が開きフィールドへ戻る。失敗時はパネルが閉じて
  「パスワードヲ…」の再タイプで先頭からやり直す。

従来は「A/B/START では進まない」と判断して保留 (return []) していたが、
手カーソルをリスト先頭まで上げて A+down で全語を上から順に入力すれば
解けることを実機プローブで確認した (state: prod1190.ch11-password-solved)。
語の入力順は問われず、全語を選び切れば扉が開く。
"""
from __future__ import annotations

from ..decision_log import _record
from ..os_layer.commands import pad


# 手カーソルは画面上で見えているが語の位置は OCR から読まず、上から順に
# 掃引するだけで全語を網羅できる。up は先頭で無操作なので打ち過ぎても安全。
_TOP_PRESSES = 18          # 10語リスト: どの位置からでも先頭へ届く回数
_SWEEP_PICKS = 14          # 全語 + スクロール端の余分な down を吸収する余裕
_MAX_CYCLES = 6            # 失敗再タイプのループ上限 (これを超えたら保留へ)


def password_step(screen, mem):
    """リスト先頭まで上げてから A+down で全語を上から順に入力する。"""
    st = mem.setdefault('password_entry', {})
    if st.get('cycles', 0) >= _MAX_CYCLES:
        return []
    tops = st.get('tops', 0)
    picks = st.get('picks', 0)
    if tops < _TOP_PRESSES:
        st['tops'] = tops + 4
        return [pad('up')] * 4
    if picks < _SWEEP_PICKS:
        st['picks'] = picks + 2
        return [pad('a'), pad('down'), pad('a'), pad('down')]
    cycles = st.get('cycles', 0) + 1
    _record(mem, 'password_entry_resweep', screen=screen.kind,
            observed_metric={'cycles': cycles},
            reason='全語掃引後もパスワード画面のため先頭から再入力')
    if cycles >= _MAX_CYCLES:
        _record(mem, 'situation_held', screen=screen.kind,
                observed_metric={'cycles': cycles},
                reason='パスワード入力の掃引を繰り返しても扉が開かないため保留')
        st['cycles'] = cycles
        return []
    # 失敗で先頭へ戻る。手カーソル位置は不明なので上げ直す。
    st.clear()
    st['cycles'] = cycles
    st['tops'] = 4
    return [pad('up')] * 4
