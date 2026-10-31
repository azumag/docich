"""An exhausted named camp recheck must not suppress a camp forever."""

import pytest

from docich import hanjuku_camp_recheck as recheck
from docich import hanjuku_policy as p
from test_hanjuku_retreat_recheck import FRAME, MAP, main_menu, memory


def _exhaust_named_recheck(mem):
    for n in range(recheck.ATTEMPT_LIMIT):
        mem['tick'] = 100 + recheck.RETRY_TICKS * n
        assert p.camp_recall_step(MAP, mem, FRAME) == [p.pad('x')]
        screen = main_menu()
        screen.hand = None
        for _ in range(recheck.READ_LIMIT):
            p.camp_recall_step(screen, mem, FRAME)
    row = mem['retreat_rechecks']['ゼウス']
    assert row['attempts'] == recheck.ATTEMPT_LIMIT
    return row


def _row(mem):
    return mem['retreat_rechecks']['ゼウス']


def test_exhausted_predispatch_recheck_rearms_after_stale_wait():
    mem = memory()
    original_retry_after = _exhaust_named_recheck(mem)['retry_after']

    # _scope() normalizes queue rows by copying them back into memory, so every
    # assertion intentionally reads the current row from mem instead of keeping
    # a stale object reference across a policy call.
    mem['tick'] = original_retry_after
    assert p._rearm_exhausted_camp_rechecks(mem) == []
    assert _row(mem)['attempts'] == recheck.ATTEMPT_LIMIT
    assert _row(mem)['retry_after'] == original_retry_after

    mem['tick'] = original_retry_after + p.EXHAUSTED_CAMP_RECHECK_REARM_TICKS
    assert p._rearm_exhausted_camp_rechecks(mem) == ['ゼウス']
    assert _row(mem)['attempts'] == 0
    assert _row(mem)['retry_after'] == mem['tick']
    assert _row(mem)['status'] == 'needs_observation'
    assert any(record.get('event') == 'retreat_recheck_budget_rearmed'
               for record in mem['_records'])

    # The refreshed budget re-enters the measured named roster/SELECT flow;
    # it does not infer identity from the current camp coordinate.
    assert p.camp_recall_step(MAP, mem, FRAME) == [p.pad('x')]
    assert _row(mem)['attempts'] == 1
    assert mem['recall']['retreat_recheck']
    assert mem['recall']['general'] == 'ゼウス'


@pytest.mark.parametrize('status_name', ['dispatch_unconfirmed', 'arrival_unconfirmed'])
def test_exhausted_postdispatch_recheck_is_never_rearmed(status_name):
    mem = memory()
    row = _row(mem)
    row.update(attempts=recheck.ATTEMPT_LIMIT, retry_after=mem['tick'], status=status_name)
    mem['tick'] += p.EXHAUSTED_CAMP_RECHECK_REARM_TICKS * 2

    # The ordinary anonymous camp observer is allowed to open a visible tent,
    # but the named row that may already own a sent return A must never receive
    # a fresh retry budget.
    assert p._rearm_exhausted_camp_rechecks(mem) == []
    assert _row(mem)['status'] == status_name
    assert _row(mem)['attempts'] == recheck.ATTEMPT_LIMIT
    assert not any(record.get('event') == 'retreat_recheck_budget_rearmed'
                   for record in mem['_records'])
