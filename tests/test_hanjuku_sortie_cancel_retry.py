"""docich#5107: 出撃キャンセルの在城ゲートが再試行を永久に塞がないこと。

実測 (ch4 46-11, 2026-11-22 14:23): 将軍一覧で本人の在城を確認した
cancelled が 1 回記録された後、在城ゲートが _ready() を False にし続け、
再試行が起きないためキャンセル回数が上限 (3) に達せず、注文は pending の
まま月の終わりまで停滞した (確定 0 / 発射 0)。
"""
from __future__ import annotations

from docich.hanjuku.flows import sortie_retry_guard  # noqa: F401  (本番と同じパッチを入れる)
from docich.hanjuku.strategy import orders

STEP = 'A:e82075e7:M5b0e2f'
SOURCE = 'フヌイヌム'
GENERAL = 'ポワソン'
TARGET = 'アルマムーン'


def _order():
    return {'step': STEP, 'source': SOURCE, 'target': TARGET, 'purpose': 'sortie'}


def _sortie(status='cancelled'):
    return {'general': GENERAL, 'source': SOURCE, 'status': status,
            'target': TARGET}


def _mem(tick=1000):
    return {
        'chapter': 4,
        'month': 'ch4 46-11',
        'tick': tick,
        'world': {'garrison': {SOURCE: [GENERAL]},
                  'sorties': {STEP: _sortie()}},
        'plan': {'orders': {STEP: 'pending'}},
    }


def test_presence_gate_is_bounded():
    mem = _mem()
    orders._record_sortie_cancelled_observed(mem, _order(), _sortie())
    assert orders._sortie_cancelled_observed_suppressed(_order(), mem) is True
    mem['tick'] += 1
    assert orders._sortie_cancelled_observed_suppressed(_order(), mem) is True
    # 上限の無いゲートは注文を月の終わりまで pending に固定していた。
    mem['tick'] += 1000
    assert orders._sortie_cancelled_observed_suppressed(_order(), mem) is False
    assert STEP not in mem['sortie_cancelled_observed_gates']


def test_retries_reach_the_failure_limit_instead_of_stalling():
    mem = _mem()
    attempts = 0
    for offset in range(5000):
        mem['tick'] = 1000 + offset
        if orders._sortie_cancelled_observed_suppressed(_order(), mem):
            continue
        # ゲートが解けたときだけ実際の出撃が起き、またキャンセルを観測する。
        attempts += 1
        orders._record_sortie_cancelled_observed(mem, _order(), _sortie())
        if mem['plan']['orders'][STEP] == 'failed':
            break
    assert 1 <= attempts <= orders.SORTIE_CANCELLED_OBSERVED_LIMIT
    assert mem['plan']['orders'][STEP] == 'failed'


def test_gate_without_tick_uses_hold_count():
    mem = _mem()
    mem['sortie_cancelled_observed_gates'] = {STEP: {
        'general': GENERAL, 'source': SOURCE, 'month': mem['month'], 'count': 1}}
    for _ in range(orders.SORTIE_CANCELLED_RETRY_HOLDS - 1):
        assert orders._sortie_cancelled_observed_suppressed(_order(), mem) is True
    assert orders._sortie_cancelled_observed_suppressed(_order(), mem) is False
