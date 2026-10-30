"""An exhausted named camp recheck must not suppress a visible camp forever."""

import pytest

from docich import hanjuku_camp_recheck as recheck
from docich import hanjuku_policy as p
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import camp_menu
from test_hanjuku_house import status
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


def test_exhausted_predispatch_recheck_releases_only_after_verified_camp_status():
    mem = memory()
    row = _exhaust_named_recheck(mem)

    # The final named retry keeps its normal cooldown. Once that expires, the
    # real visible camp may be opened again, but the stale named row remains
    # until the currently opened tent actually identifies that same actor.
    mem['tick'] = row['retry_after']
    assert p.camp_recall_step(MAP, mem, FRAME) == [p.pad('a')]
    assert 'ゼウス' in mem['retreat_rechecks']
    assert mem['recall']['stage'] == 'menu'
    assert not mem['recall'].get('retreat_recheck')

    assert p.camp_recall_step(camp_menu(1), mem, FRAME) == [p.pad('a')]
    assert mem['recall']['stage'] == 'recheck_read'
    assert p.camp_recall_step(parse(status('ゼウス', main=False, hp=6)), mem, FRAME) == [p.pad('b')]

    assert 'ゼウス' not in (mem.get('retreat_rechecks') or {})
    assert mem['recall']['named_status']['general'] == 'ゼウス'
    assert any(record.get('event') == 'retreat_recheck_budget_released'
               for record in mem['_records'])

    assert p.camp_recall_step(camp_menu(3), mem, FRAME) == [p.pad('a')]
    assert mem['recall']['stage'] == 'dest'


@pytest.mark.parametrize('status_name', ['dispatch_unconfirmed', 'arrival_unconfirmed'])
def test_exhausted_postdispatch_recheck_is_not_released(status_name):
    mem = memory()
    row = mem['retreat_rechecks']['ゼウス']
    row.update(attempts=recheck.ATTEMPT_LIMIT, retry_after=mem['tick'], status=status_name)

    assert p.camp_recall_step(MAP, mem, FRAME) is None
    assert mem['retreat_rechecks']['ゼウス']['status'] == status_name
    assert not mem.get('recall')
    assert not any(record.get('event') == 'retreat_recheck_budget_released'
                   for record in mem['_records'])
