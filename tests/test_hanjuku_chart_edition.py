"""Chart edition (owner 2026-10-13): speedrun default, steady keeps margin.

The edition is pinned when a new game's name entry completes; the steady
edition applies the leave-one-defender guard to base chart advances too.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from docich import hanjuku_chart as chart
from docich import hanjuku_policy as p
from docich.hanjuku import orchestrator
from docich.hanjuku.strategy import edition
from test_hanjuku_chart_bot import Canvas, name_screen


def test_setting_and_resolve_fall_back_to_speedrun():
    assert edition.setting('steady') == 'steady'
    for raw in (None, '', 'STEADY', 'fast', 1, True):
        assert edition.setting(raw) == 'speedrun'
    assert edition.resolve('steady', None) == 'steady'
    assert edition.resolve('speedrun', 4) == 'speedrun'
    assert edition.resolve('ab', 748) == 'steady'
    assert edition.resolve('ab', 749) == 'speedrun'
    for generation in (None, 0, -2, '748', True):
        assert edition.resolve('ab', generation) == 'speedrun'


def test_memory_without_a_pinned_edition_is_speedrun():
    assert edition.current({}) == 'speedrun'
    assert edition.current({'chart_edition': 'bogus'}) == 'speedrun'
    assert edition.steady({'chart_edition': 'steady'}) is True


def _base_advance():
    order = next(o for o in chart.orders(1) if o['step'] == '1-A2')
    assert order['source'] == 'キカンドン' and order['after'] == ('captured', 'キカンドン')
    return order


def _sole_defender_memory(**extra):
    return {'chapter': 1, 'captured': ['キカンドン', 'ナキューメラ', 'ジョンリギ'],
            'garrison': {'キカンドン': ['ゼウス'], 'アルマムーン': ['どうし', 'ココット']}, **extra}


def test_speedrun_base_advance_may_empty_its_source():
    order = _base_advance()
    assert p._source_spare_missing(order, _sole_defender_memory()) is False


def test_steady_base_advance_keeps_a_defender_at_its_source():
    order = _base_advance()
    mem = _sole_defender_memory(chart_edition='steady')
    assert p._source_spare_missing(order, mem) is True
    mem['garrison']['キカンドン'] = ['ゼウス', 'ミント']
    assert p._source_spare_missing(order, mem) is False


def test_steady_keeps_boss_and_move_orders_unchanged():
    boss = next(o for o in chart.orders(1) if o['step'] == '1-B1')
    mem = _sole_defender_memory(chart_edition='steady',
                                garrison={'スペンソニア': ['ゼウス']})
    assert p._source_spare_missing(boss, mem) is False
    move = dict(_base_advance(), purpose='move')
    assert p._source_spare_missing(move, _sole_defender_memory(chart_edition='steady')) is False


def _enter_name(state, *, chart_edition, generation=748):
    identity = {'game': 'hanjuku-hero', 'runtime_id': 'g748-test', 'generation': generation,
                'lease_id': 'lease'}
    frames = [name_screen(menu=True), name_screen(cell='あ'), name_screen(cell='だ'),
              name_screen(cell='ど'), name_screen('ど', cell='え'),
              name_screen('どう', cell='し'), name_screen('どうし', cell='し')]
    records = []
    for frame in frames:
        _, state = orchestrator.decide(frame, state, run_identity=identity,
                                       chart_edition=chart_edition)
        records += state.pop('_records', [])
    return state, records


def test_new_game_pins_the_edition_once_at_name_entry():
    state, records = _enter_name({}, chart_edition='ab', generation=748)
    assert state['policy']['chapter'] == 1
    assert state['policy']['chart_edition'] == 'steady'
    pinned = [r for r in records if r['decision'] == 'chart_edition_pinned']
    assert len(pinned) == 1 and pinned[0]['strategy_variant'] == 'steady'
    assert pinned[0]['observed_metric'] == {'setting': 'ab', 'generation': 748}
    # A later config change does not switch the game in progress.
    _, state = orchestrator.decide(Canvas((0, 0, 0)).frame(), state, chart_edition='speedrun')
    assert state['policy']['chart_edition'] == 'steady'
    # A second name screen is a new game: it re-pins from the current setting.
    state, _ = _enter_name(state, chart_edition='speedrun', generation=750)
    assert state['policy']['chart_edition'] == 'speedrun'


def test_default_setting_pins_speedrun():
    state, _ = _enter_name({}, chart_edition=None, generation=748)
    assert state['policy']['chart_edition'] == 'speedrun'
