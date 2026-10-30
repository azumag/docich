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

    # The finite failure budget still protects the camp during its cooldown and
    # a longer stale window; it is not a permanent ban anymore.
    mem['tick'] = row['retry_after']
    assert p.camp_recall_step(MAP, mem, FRAME) is None
    assert mem['retreat_rechecks']['ゼウス']['attempts'] == recheck.ATTEMPT_LIMIT

    mem['tick'] = row['retry_after'] + p.EXHAUSTED_CAMP_RECHECK_REARM_TICKS
    assert p.camp_recall_step(MAP, mem, FRAME) == [p.pad('x')]
    assert mem['retreat_rechecks']['ゼウス']['attempts'] == 1
    assert mem['recall']['retreat_recheck']
    assert mem['recall']['general'] == 'ゼウス'
    assert any(record.get('event') == 'retreat_recheck_budget_rearmed'
               for record in mem['_records'])


@pytest.mark.parametrize('status_name', ['dispatch_unconfirmed', 'arrival_unconfirmed'])
def test_exhausted_postdispatch_recheck_is_never_rearmed(status_name):
    mem = memory()
    row = mem['retreat_rechecks']['ゼウス']
    row.update(attempts=recheck.ATTEMPT_LIMIT, retry_after=mem['tick'], status=status_name)
    mem['tick'] += p.EXHAUSTED_CAMP_RECHECK_REARM_TICKS * 2

    assert p.camp_recall_step(MAP, mem, FRAME) is None
    assert mem['retreat_rechecks']['ゼウス']['status'] == status_name
    assert mem['retreat_rechecks']['ゼウス']['attempts'] == recheck.ATTEMPT_LIMIT
    assert not mem.get('recall')
    assert not any(record.get('event') == 'retreat_recheck_budget_rearmed'
                   for record in mem['_records'])
