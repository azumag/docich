"""docich#5082: the boss phase gets one bounded 'last general' exception order.

g1190 chapter 1 month 9-10: every castle's roster was measured and each held
exactly one general, no unread castle remained, ``need=2`` failed everywhere,
so ``chart_interim_hold`` looped for 600 s while zero ``order_start`` reached
the boss. Once the same bound #5095's dispatch guarantee uses
(``ORDER_DISPATCH_STARVE_TICKS``) has passed, the interim slot mints one
bounded boss order — the source farthest from the boss first, besieged
castles never emptied, the home castle only as the last resort.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_chart as chart
from docich import hanjuku_chart_adjust as chart_adjust
from docich.hanjuku.strategy import orders, replan
from docich.hanjuku.state.regions import world as _world, plan as _plan_region, flow as _flow


CH = 1
CASTLES = chart.castles(CH)
BOSS = chart.boss_castle(CH)            # けっかい
HOME = chart.home_castle(CH)            # アルマムーン
NORMAL = [c for c in CASTLES if not chart.is_boss_castle(c)]
CAPTURED = [c for c in NORMAL if c != HOME]
STARVE = orders.ORDER_DISPATCH_STARVE_TICKS
T0 = 10_000

# One measured, deployable general per owned castle: the g1190 starvation.
GARRISON = {
    HOME: ['ココット'],
    'キカンドン': ['ヴィーナス'],
    'ナキューメラ': ['ゼウス'],          # farthest owned castle from けっかい
    'ジョンリギ': ['キール'],
    'ゴーメン': ['ミュスカデ'],
    'スペンソニア': ['カペリーニ'],
    'カストーラ': ['ポセイドン'],
}


@pytest.fixture(autouse=True)
def mechanical_path(monkeypatch):
    """Run the path production uses: no speedrun chart (conftest enables it)."""
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)
    assert not chart.speedrun_enabled(CH)


def boss_starved_mem(**overrides):
    """Chapter 1 boss phase: every normal castle held with one general each,
    the boss cell confirmed (two readings), nothing startable."""
    mem = {'chapter': CH, 'tick': T0, '_records': [],
           'captured': list(CAPTURED), 'lost': [],
           'garrison': {c: list(names) for c, names in GARRISON.items()},
           'orders': {}, 'sorties': {},
           'boss_cell': {'chapter': CH, 'cell': list(CASTLES[BOSS]),
                         'readings': 2, 'tick': T0}}
    mem.update(overrides)
    return mem


def interim(mem):
    return (mem.get('chart_adjust') or {}).get('interim_order')


def run_interim(mem, tick=None):
    if tick is not None:
        mem['tick'] = tick
    state = mem.setdefault('chart_adjust', {})
    replan._mechanical_interim(mem, state, chart_adjust.request_id(mem), 'digest-a')
    return interim(mem)


def exception_records(mem):
    return [r for r in mem['_records'] if r['decision'] == 'boss_last_general_order']


def test_starved_boss_phase_arms_then_mints_one_exception_order():
    mem = boss_starved_mem()
    assert replan.interim_candidates(mem) == {}
    assert orders.boss_only_phase(mem) is True

    # First evaluation inside the bound: arm the episode and hold as before.
    assert run_interim(mem) is None
    assert mem['boss_last_general_since'] == T0
    assert [r for r in mem['_records'] if r['decision'] == 'chart_interim_hold']
    assert not exception_records(mem)

    # Past the #5095 bound the exception mints the boss sortie instead.
    order = run_interim(mem, T0 + STARVE + 1)
    assert order is not None
    assert order['boss_last_general'] is True
    assert order['purpose'] == 'attack'
    assert order['target'] == BOSS
    assert order['source'] == 'ナキューメラ'   # farthest from the boss cell
    assert order['general'] == 'ゼウス'
    minted = exception_records(mem)
    assert len(minted) == 1
    assert minted[0]['chart_step'] == order['step']
    assert minted[0]['deviation_reason'] == 'boss_last_general'
    assert minted[0]['observed_metric']['source_is_home'] is False
    assert minted[0]['observed_metric']['wait'] == STARVE + 1


def test_the_wait_stays_a_hold_inside_the_bound():
    mem = boss_starved_mem(boss_last_general_since=T0)
    assert run_interim(mem, T0 + STARVE - 1) is None
    assert not exception_records(mem)
    assert [r for r in mem['_records'] if r['decision'] == 'chart_interim_hold']


def test_an_unconfirmed_boss_cell_never_arms_the_episode():
    mem = boss_starved_mem(boss_cell={'chapter': CH, 'cell': list(CASTLES[BOSS]),
                                    'readings': 1, 'tick': T0})
    assert run_interim(mem, T0 + STARVE + 1) is None
    assert 'boss_last_general_since' not in mem
    assert not exception_records(mem)


def test_an_unread_castle_keeps_the_probe_route():
    # docich#5082 ordering: probe > exception. One unmeasured roster means the
    # #5095-style source probe still runs instead of the last-general order.
    garrison = {c: list(names) for c, names in GARRISON.items()}
    del garrison['ジョンリギ']
    mem = boss_starved_mem(garrison=garrison, boss_last_general_since=T0)
    order = run_interim(mem, T0 + STARVE + 1)
    assert order is not None
    assert order.get('source_probe') is True
    assert order.get('boss_last_general') is not True
    assert not exception_records(mem)


def test_a_measured_pair_anywhere_mints_an_ordinary_wave_not_the_exception():
    # need=2 works at ナキューメラ: the normal wave goes out, no exception.
    garrison = {c: list(names) for c, names in GARRISON.items()}
    garrison['ナキューメラ'] = ['ゼウス', 'ビーツ']
    mem = boss_starved_mem(garrison=garrison)
    order = run_interim(mem, T0 + STARVE + 1)
    assert order is not None
    assert order['target'] == BOSS
    assert order.get('boss_last_general') is not True
    assert not exception_records(mem)


def test_outside_the_boss_phase_the_exception_never_arms():
    mem = boss_starved_mem(lost=['ジョンリギ'], boss_last_general_since=T0)
    order = run_interim(mem, T0 + STARVE + 1)
    assert not (order or {}).get('boss_last_general')
    assert 'boss_last_general_since' not in mem
    assert not exception_records(mem)


def test_a_besieged_castle_is_never_emptied():
    # ナキューメラ is the farthest source but its wall was just attacked: the
    # exception falls to the next farthest free castle (キカンドン).
    mem = boss_starved_mem(defense_seen={'ナキューメラ': T0 + STARVE},
                           boss_last_general_since=T0)
    order = run_interim(mem, T0 + STARVE + 1)
    assert order is not None
    assert order['source'] == 'キカンドン'
    assert order['general'] == 'ヴィーナス'


def test_the_home_castle_is_only_the_last_resort():
    # Every other owned castle measured empty: only the home castle's last
    # general can go, and the record marks the pick ``source=home``.
    garrison = {c: [] for c in GARRISON}
    garrison[HOME] = ['ココット']
    mem = boss_starved_mem(garrison=garrison, boss_last_general_since=T0)
    order = run_interim(mem, T0 + STARVE + 1)
    assert order is not None
    assert order['source'] == HOME
    assert order['general'] == 'ココット'
    minted = exception_records(mem)
    assert len(minted) == 1
    assert minted[0]['observed_metric']['source_is_home'] is True
    assert 'source=home' in minted[0]['reason']


def test_one_exception_per_episode_until_its_sortie_settles():
    mem = boss_starved_mem(boss_last_general_since=T0)
    order = run_interim(mem, T0 + STARVE + 1)
    assert order['source'] == 'ナキューメラ' and order['general'] == 'ゼウス'

    # The issued step failed before launching: still one mint per episode.
    _plan_region(mem).setdefault('orders', {})[order['step']] = 'failed'
    mem['chart_adjust'].pop('interim_order')
    assert run_interim(mem, T0 + STARVE + 2) is None
    assert len(exception_records(mem)) == 1

    # The sortie actually left and is still marching: no second order.
    # (sorties live in the world region — _sorties() reads world.sorties,
    # never the top level; writing to mem['sorties'] would be invisible to
    # the implementation and _mechanical_interim normalises it away.)
    _world(mem).setdefault('sorties', {})[order['step']] = {
        'status': 'en_route', 'tick': T0 + STARVE + 1,
        'general': 'ゼウス', 'source': 'ナキューメラ', 'target': BOSS}
    mem['chart_adjust'].pop('interim_order', None)
    assert run_interim(mem, T0 + STARVE + 3) is None
    assert len(exception_records(mem)) == 1

    # The march settled but its general never came back (the roster now reads
    # empty): the episode closes, the bounded wait re-arms, and the next
    # evaluation picks the next farthest castle.
    _world(mem)['sorties'][order['step']]['status'] = 'arrived'
    _world(mem)['garrison']['ナキューメラ'] = []
    close = run_interim(mem, T0 + STARVE + 4)
    assert close is None
    assert 'boss_last_general_used' not in mem
    assert mem['boss_last_general_since'] == T0 + STARVE + 4

    order2 = run_interim(mem, T0 + 2 * STARVE + 5)
    assert order2 is not None
    assert order2['source'] == 'キカンドン'
    assert order2['general'] == 'ヴィーナス'
    assert len(exception_records(mem)) == 2


def test_the_exception_order_survives_the_source_spare_guards():
    # The one-general source that must stay shut for ordinary sorties opens
    # only for this bounded exception (orders.py's boss_last_general guards).
    mem = boss_starved_mem(boss_last_general_since=T0)
    order = run_interim(mem, T0 + STARVE + 1)
    assert orders._source_spare_missing(order, mem) is False
    assert orders._reserve_source_guard(order, mem) is False
    plain = {k: v for k, v in order.items() if k != 'boss_last_general'}
    assert orders._source_spare_missing(plain, mem) is True
    assert orders._reserve_source_guard(plain, mem) is True


def test_exception_order_passes_the_shared_companion_receipt_without_a_new_wait():
    """PR #5310's shared deadline must not re-block this bounded exception.

    The source's single fit general is itself a measured companion source, so
    ``_ready`` opens the order without starting ``BOSS_COMPANION_WAIT_TICKS``.
    A plain boss order from the same one-general castle would be held by that
    shared gate — the exception exists precisely to break that starvation.
    """
    mem = boss_starved_mem(boss_last_general_since=T0)
    order = run_interim(mem, T0 + STARVE + 1)
    assert order['boss_last_general'] is True
    assert orders._boss_companion_source(order, mem) == order['source']

    flow = _flow(mem)
    assert orders._ready(order, mem) is True
    assert not (flow.get('boss_companion_wait') or {}).get(order['step'])
    assert not (flow.get('boss_companion_wait_expired') or {}).get(order['step'])


def test_a_pre_condition_break_releases_the_unlaunched_slot():
    mem = boss_starved_mem(boss_last_general_since=T0)
    order = run_interim(mem, T0 + STARVE + 1)
    assert order['boss_last_general'] is True

    # A second general was measured at the source before the sortie launched:
    # need=2 works again, so the exception's slot must not pin the plan.
    _world(mem)['garrison']['ナキューメラ'] = ['ゼウス', 'ビーツ']
    order2 = run_interim(mem, T0 + STARVE + 2)
    assert order2 is not None
    assert order2.get('boss_last_general') is not True
    assert order2['target'] == BOSS
