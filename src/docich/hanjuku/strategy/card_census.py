"""台帳を全量で読み直す予約 (``card_census_due``) の所有。

``card_stock`` は「フィールドの在庫」の台帳で、bot が自分で起こした購入・
携行だけでは動かない場面 (章の始め、セーブ状態の読み込み、runtime の世代
交代、ゲームオーバー、商人の上限拒否、``ledger_drift``) では、ゲームと mem
が食い違いうる。そのときだけ台帳を未知に戻し、フィールドの
X→「きりふだ」一覧を末尾まで読む予約を置く (owner 2026-11-22)。

**bot の起動・ホットロード・デプロイでは予約しない。** 半熟は稼働中の
デプロイでホットロードされ、main には1時間に約10件のマージが入る。その
たびに台帳を消すと、購入に使われる前に台帳が消える。エミュが動き続けて
いる限り、ゲームと mem は食い違わない。予約は「ゲーム側の状態が動いた」
と分かる処理だけが置く (呼び出し元は repo の grep で列挙できる):

- ``strategy.campaign._enter_chapter``     章の始め (``card_stock`` は
  ``_reset_chapter_scope`` が章スコープごと消す)
- ``hanjuku_resume.restore``               セーブ状態の読み込み (rollback /
  recover)。復元後の policy は新規 dict なので台帳は最初から未知。
- ``orchestrator._decide_impl``            runtime の世代交代
  (``survey_run_identity`` の変化) とゲームオーバー後のタイトル復帰
- ``flows.shop._holding_limit_step``       商人の所持上限の拒否
- ``strategy.kit._note_ledger_drift``      読みと台帳の食い違い

予約の中身::

    mem['card_census_due'] = {
        'reason': 最後に予約した理由,
        'reasons': [...],           # 予約の履歴 (重複しない)
        'month': '9-50' | None,     # 最初に予約した月 (overdue の基準)
        'tick': <予約時の mem['tick']>,
        'wait_key': (...),          # 直近に記録した待ち (同じ待ちを繰り返さない)
    }

完了 (全量の読みが成功) したら ``flows.card_census`` が ``clear`` する。
中止した月は ``mem['card_census_abort']``、章で2回続けて中止したら
``mem['card_census_disabled']`` が止める。
"""
from __future__ import annotations


def month_index(month):
    """'9-50' -> 9*12+50. None for anything that is not a measured month."""
    if not isinstance(month, str):
        return None
    year, _, number = month.partition('-')
    if not year.isdigit() or not number.isdigit():
        return None
    year, number = int(year), int(number)
    if year <= 0 or not 1 <= number <= 12:
        return None
    return year * 12 + number


def due(mem):
    """The pending reservation dict, or None."""
    value = mem.get('card_census_due')
    return value if isinstance(value, dict) else None


def book(mem, reason, *, month=None, tick=None):
    """Reserve a full read. Keeps the earliest booked month for ``overdue``.

    An existing reservation keeps its first month; the latest reason wins so
    the log names the event that most recently made the ledger untrustworthy.
    """
    if not isinstance(reason, str) or not reason:
        return None
    current = due(mem)
    if month is None:
        month = mem.get('month')
    if not isinstance(month, str):
        month = None
    if current is None:
        current = {'reason': reason, 'reasons': [reason], 'month': month,
                   'tick': tick if type(tick) is int else mem.get('tick'),
                   'wait_key': None}
        mem['card_census_due'] = current
        return current
    current['reason'] = reason
    reasons = current.get('reasons')
    reasons = reasons if isinstance(reasons, list) else []
    if reason not in reasons:
        reasons.append(reason)
    current['reasons'] = reasons
    if current.get('month') is None and month is not None:
        current['month'] = month
    if not isinstance(current.get('tick'), int):
        current['tick'] = tick if type(tick) is int else mem.get('tick')
    return current


def clear(mem):
    """The full read succeeded (or the reservation was withdrawn)."""
    mem.pop('card_census_due', None)


def forget_ledger(mem):
    """The game may be a different state than the ledger: make stock unknown.

    Only the two card ledgers move. Everything else in ``card_stock``'s
    neighbourhood (``card_zero_at_start`` etc.) belongs to a sortie procedure
    that the caller's event already invalidates.
    """
    mem.pop('card_stock', None)
    mem.pop('card_bought_unseen', None)
    mem.pop('card_census', None)
    return True
