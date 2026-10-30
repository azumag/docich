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


def test_exhausted_predispatch_recheck_rearms_after_stale_wait():
    mem = memory()
    row = _exhaust_named_recheck(mem)
    original_retry_after = row['retry_after']

    # The normal retry boundary alone does not refill the named budget.  The
    # existing anonymous path may still inspect a visible camp at that point,
    # so isolate the new stale-rearm policy from ordinary camp handling here.
    mem['tick'] = original_retry_after
    assert p._rearm_exhausted_camp_rechecks(mem) == []
    assert row['attempts'] == recheck.ATTEMPT_LIMIT
    assert row['retry_after'] == original_retry_after

    mem['tick'] = original_retry_after + p.EXHAUSTED_CAMP_RECHECK_REARM_TICKS
    assert p._rearm_exhausted_camp_rechecks(mem) == ['ゼウス']
    assert row['attempts'] == 0
    assert row['retry_after'] == mem['tick']
    assert row['status'] == 'needs_observation'
    assert any(record.get('event') == 'retreat_recheck_budget_rearmed'
               for record in mem['_records'])

    # The refreshed budget re-enters the measured named roster/SELECT flow;
    # it does not infer identity from the current camp coordinate.
    assert p.camp_recall_step(MAP, mem, FRAME) == [p.pad('x')]
    assert row['attempts'] == 1
    assert mem['recall']['retreat_recheck']
    assert mem['recall']['general'] == 'ゼウス'


@pytest.mark.parametrize('status_name', ['dispatch_unconfirmed', 'arrival_unconfirmed'])
def test_exhausted_postdispatch_recheck_is_never_rearmed(status_name):
    mem = memory()
    row = mem['retreat_rechecks']['ゼウス']
    row.update(attempts=recheck.ATTEMPT_LIMIT, retry_after=mem['tick'], status=status_name)
    mem['tick'] += p.EXHAUSTED_CAMP_RECHECK_REARM_TICKS * 2

    # The ordinary anonymous camp observer is allowed to open a visible tent,
    # but the named row that may already own a sent return A must never receive
    # a fresh retry budget.
    assert p._rearm_exhausted_camp_rechecks(mem) == []
    assert row['status'] == status_name
    assert row['attempts'] == recheck.ATTEMPT_LIMIT
    assert not any(record.get('event') == 'retreat_recheck_budget_rearmed'
                   for record in mem['_records'])
