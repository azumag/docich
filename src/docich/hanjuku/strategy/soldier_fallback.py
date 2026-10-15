"""A skipped egg recovery must not starve a measured, insufficient army.

The deferral belongs to one run/chapter/month and is adopted only once. It
releases money for necessary soldiers, not for a retry or a hiring fee. The
normal quantity editor and exact payment receipt still own every purchase.
"""
from __future__ import annotations

from ..decision_log import _record


DEFERRAL_KEY = 'soldier_egg_deferral'


def egg_deferred_for_soldiers(shop):
    """A refreshed deferral, never an arbitrary truthy saved value."""
    state = shop.get(DEFERRAL_KEY)
    return (shop.get('egg') == 'skipped' and isinstance(state, dict)
            and type(state.get('version')) is int and state['version'] == 1
            and state.get('active') is True)


def refresh_soldier_egg_deferral(mem, shop, header):
    """Replan one unpaid refill after a known skip; never renew its budget."""
    # Local import keeps the budget implementation in economy and avoids an
    # import cycle: economy calls this only after its module is initialized.
    from .economy import (_month_key, _soldier_count_current, _soldier_count_scope,
                          _soldier_spendable, SOLDIER_ENOUGH)

    state = shop.get(DEFERRAL_KEY)
    if isinstance(state, dict):
        state['active'] = False  # revalidate even on a faded/stale observation
    if (not isinstance(header, dict)
            or any(type(header.get(k)) is not int for k in ('year', 'month', 'gold'))
            or header['year'] < 1 or not 1 <= header['month'] <= 12 or header['gold'] < 0):
        return False
    scope = _soldier_count_scope(mem, header)
    if (scope is None or shop.get('key') != _month_key(header)
            or not _soldier_count_current(mem, header)
            or mem['soldiers_seen'] >= SOLDIER_ENOUGH
            or shop.get('egg') != 'skipped' or mem.get('month_sub')
            or shop.get('soldier_receipt')
            or shop.get('recruit') in ('opened', 'unverified')):
        return False
    check = mem.get('army_count_check')
    if (isinstance(check, dict) and check.get('scope') == scope
            and (check.get('stage') not in ('opening', 'requested', 'quantity_requested', 'closing', 'measured')
                 or (check.get('stage') == 'closing' and check.get('result') != 'measured')
                 or (check.get('refill_requested') and DEFERRAL_KEY not in shop))):
        return False  # a failed inspection/refill gets no new monthly attempt
    if any(type(shop.get(k, 0)) is not int or shop.get(k, 0) < 0
           for k in ('reserve', 'recruit_reserve')):
        return False
    proposal = dict(shop, reserve=0, recruit_reserve=0)
    available = _soldier_spendable(mem, proposal, header, recruit=False)
    if available is None:
        return False  # unread funds/payroll or unpriced necessities cannot pay
    count = mem['soldiers_seen']
    if DEFERRAL_KEY in shop:
        if (not isinstance(state, dict) or type(state.get('version')) is not int
                or state['version'] != 1 or state.get('scope') != scope
                or type(state.get('count')) is not int or state['count'] != count
                or type(state.get('amount')) is not int
                or not 0 < state['amount'] <= SOLDIER_ENOUGH - count):
            return False
        # No changes to soldiers, soldiers_done, receipts, inspection limits
        # or month-exit limits here. A completed/aborted attempt stays ended.
    else:
        amount = min(SOLDIER_ENOUGH - count, available)
        if amount <= 0:
            return False
        state = shop[DEFERRAL_KEY] = {
            'version': 1, 'scope': scope, 'count': count, 'amount': amount,
            'previous_reserve': shop.get('reserve', 0),
        }
        shop.update(soldiers=amount, soldiers_target=amount, soldiers_done=False,
                    soldiers_recalculated=True, soldier_total_target=SOLDIER_ENOUGH,
                    soldiers_before_recruit=amount, soldiers_before_recruit_done=False,
                    closed=False)
        _record(mem, 'soldier_refill_after_egg_skip', month=shop['key'],
                observed_metric={'soldiers': count, 'gold': header['gold'],
                                 'refill': amount, 'released_egg_reserve': state['previous_reserve']},
                reason='卵回復を見送っても兵士不足は放置せず、賃金予備・修理・必要品を残して補充する。支払いと人数増加は未確定')
    state['active'] = True
    shop.update(reserve=0, recruit_priority=False, recruit_reserve=0)
    if shop.get('recruit') != 'done':
        shop['recruit'] = 'deferred_army'
    return True
