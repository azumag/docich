"""docich#5681/#5155: boss-phase dispatch starvation crosses screen owners.

The starvation check must run before monthly/camp/general-jump foreground
work, a starved boss dispatch must get the next free map back from the
post-month scan, and an unread recruitment-payment prompt must abort instead
of holding the monthly scene forever.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_chart as chart
from docich.hanjuku import orchestrator as O
from docich.hanjuku.flows import map as map_flow
from docich.hanjuku.flows import month as month_flow
from docich.hanjuku.flows import post_month_status
from docich.hanjuku.os_layer.pixels import Frame
from docich.hanjuku.strategy import orders
from test_hanjuku_boss_dispatch_starved import boss_phase_mem, BOSS, STARVE
from test_hanjuku_off_chart import map_screen
from test_hanjuku_recruit_paid_stall import recruit_memory
from test_hanjuku_chart_bot import Canvas
from docich.hanjuku_screen import parse


def test_general_jump_does_not_arm_while_boss_dispatch_is_starved():
    mem = boss_phase_mem(world={'sorties': {
        'S1': {'target': BOSS, 'status': 'overdue', 'general': 'ゼウス'}}})
    assert map_flow.general_jump_step(map_screen(80, 90), mem, None) is None
    assert 'general_jump' not in mem

    mem = boss_phase_mem(order_dispatch_since=10_000 - 5,
                         world={'sorties': {
                             'S1': {'target': BOSS, 'status': 'overdue', 'general': 'ゼウス'}}})
    assert map_flow.general_jump_step(map_screen(80, 90), mem, None) is not None
    assert isinstance(mem.get('general_jump'), dict)


def test_post_month_scan_yields_a_free_map_to_starved_boss_dispatch():
    mem = boss_phase_mem(month='1-1', _run_identity=None)
    mem['post_month_status'] = {
        'scope': [1, '1-1'], 'identity': None, 'stage': 'process',
        'tasks': [{'kind': 'recall', 'name': 'ゼウス'}],
        'statuses': {}, 'steps': 0, 'phase_reads': 0,
    }
    assert post_month_status.step(map_screen(80, 90), mem, None,
                                  boss_dispatch=True) is None
    assert any(r['decision'] == 'post_month_boss_dispatch_floor'
               for r in mem['_records'])


def test_unread_recruit_payment_confirmation_aborts_after_a_bound():
    mem = recruit_memory()
    c = Canvas()
    c.text(24, 183, '50Gかかりますが よろしいでしょうか?')
    c.text(184, 183, 'うむッ!')
    c.text(184, 199, 'いかんッ!')
    c.hand(162, 177)
    screen = parse(c.frame())
    for _ in range(month_flow.MONTH_SUB_UNREAD_CONFIRM_HOLD):
        assert month_flow.month_sub_step(screen, mem) == []
    assert month_flow.month_sub_step(screen, mem) == [month_flow.pad('b')]
    assert mem['month_sub'].get('aborted') is True
    assert any(r['decision'] == 'month_sub_unread_confirm_abort'
               for r in mem['_records'])


def test_decide_measures_dispatch_starvation_before_screen_owners(monkeypatch):
    calls = []
    real = orders.order_dispatch_starved

    def probe(mem):
        calls.append(int(mem.get('tick') or 0))
        return real(mem)

    monkeypatch.setattr(O.strategy_orders, 'order_dispatch_starved', probe)
    frame = Frame(256, 224, bytes(256 * 224 * 3))
    mem = boss_phase_mem(order_dispatch_since=10_000 - STARVE - 1)
    O.decide(frame, {'policy': mem})
    assert calls == [10_001]  # decide() increments tick before dispatch
