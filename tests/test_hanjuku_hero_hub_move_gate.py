"""The once-a-month hero->hub move waits for complete rosters (#4546).

g1190 62-9 (chapter 11): タタール was retaken and held six generals while
どうし stayed at アルマムーン and no ``hero_hub_move`` was issued — the
gather orders went out and the hold left no trace. The move now fires only
when the latest *complete* rosters prove the hub is ours and staffed and
the hero is at home; a partial-roster month is held, never guessed, and
every held evaluation records ``hero_hub_move_skipped`` with its reason.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import replan
from docich.hanjuku.state.regions import world as _world, plan as _plan_region

FRONT = ['チリベット', 'タプロベイン', 'ジャンセニア', 'ゴンダル', 'マランマ']
HUB = 'タタール'


def decisions(mem, kind):
    return [r for r in mem.get('_records') or () if r['decision'] == kind]


def memory(garrison, partial=None, month='62-9'):
    mem = {'chapter': 11, 'month': month, 'tick': 100, '_records': [],
           'captured': [*FRONT, HUB], 'lost': [],
           'egg_types': {}, 'egg_uses': {}, 'house_eggs': {}}
    _world(mem)['garrison'] = dict(garrison)
    if partial:
        _world(mem)['garrison_partial'] = dict(partial)
    return mem


def orders(mem):
    return [o for o in replan.interim_candidates(mem).values() if isinstance(o, dict)]


def hero_moves(mem):
    return [o for o in orders(mem) if o.get('hero_hub_move') is True]


def skipped(mem):
    return decisions(mem, 'hero_hub_move_skipped')


def test_the_hero_move_leads_the_plan_on_complete_rosters(monkeypatch):
    """62-9: hub ours, hero at home, six generals at the hub -> fire once."""
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)
    mem = memory({'アルマムーン': ['どうし'] + [f'g{i}' for i in range(13)],
                  HUB: [f'h{i}' for i in range(6)]})
    found = orders(mem)
    assert found and found[0].get('hero_hub_move') is True
    assert (found[0]['general'], found[0]['source'], found[0]['target'],
            found[0]['purpose']) == ('どうし', 'アルマムーン', HUB, 'move')
    assert len(hero_moves(mem)) == 1
    assert not skipped(mem)


def test_a_partial_home_roster_holds_the_move(monkeypatch):
    """A page proves names, never the roster: no complete home read, no fire."""
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)
    mem = memory({HUB: [f'h{i}' for i in range(6)]},
                 partial={'アルマムーン': ['どうし'] + [f'g{i}' for i in range(7)]})
    assert not hero_moves(mem)
    [rec] = skipped(mem)
    assert rec['skip_reason'] == 'partial 名簿'


def test_an_unread_hub_roster_holds_the_move(monkeypatch):
    """The hero is never dropped into a hub whose roster is not confirmed."""
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)
    mem = memory({'アルマムーン': ['どうし', 'g1', 'g2']})
    assert not hero_moves(mem)
    [rec] = skipped(mem)
    assert rec['skip_reason'] == 'partial 名簿'


def test_the_move_is_planned_once_a_month(monkeypatch):
    """Issued once, held for the rest of the month, re-planned the next."""
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)
    garrison = {'アルマムーン': ['どうし', 'g1', 'g2'], HUB: ['h1', 'h2']}
    mem = memory(garrison)
    assert len(hero_moves(mem)) == 1
    replan._note_hero_hub_move(mem)        # the issuance path marks the month
    assert not hero_moves(mem)
    [rec] = skipped(mem)
    assert rec['skip_reason'] == '計画競合'
    assert not hero_moves(mem)
    assert len(skipped(mem)) == 1          # the same hold is not re-recorded
    mem['month'] = '62-10'
    assert len(hero_moves(mem)) == 1


def test_a_flagged_order_already_in_the_plan_is_not_duplicated(monkeypatch):
    """A stored plan row carrying the flag is the month's move already."""
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)
    mem = memory({'アルマムーン': ['どうし', 'g1', 'g2'], HUB: ['h1', 'h2']})
    _plan_region(mem)['chart_plan'] = {'orders': [
        {'step': 'A:x:M1', 'local_step': 'M1', 'general': 'どうし',
         'source': 'アルマムーン', 'target': HUB, 'purpose': 'move',
         'hero_hub_move': True}]}
    assert not hero_moves(mem)
    [rec] = skipped(mem)
    assert rec['skip_reason'] == '計画競合'


def test_a_marching_hero_holds_the_move(monkeypatch):
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)
    mem = memory({'アルマムーン': ['どうし', 'g1', 'g2'], HUB: ['h1', 'h2']})
    _world(mem)['sorties'] = {'S1': {'general': 'どうし', 'target': HUB,
                                    'status': 'en_route', 'tick': 50}}
    assert not hero_moves(mem)
    [rec] = skipped(mem)
    assert rec['skip_reason'] == '計画競合'


def test_a_hero_already_at_the_hub_is_not_moved(monkeypatch):
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)
    mem = memory({'アルマムーン': ['g1'], HUB: ['どうし', 'h1', 'h2']})
    assert not hero_moves(mem)
    assert not skipped(mem)


def test_a_hero_confirmed_elsewhere_is_not_moved(monkeypatch):
    """A complete home read that misses him needs no record: he is elsewhere."""
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)
    mem = memory({'アルマムーン': ['g1', 'g2'], 'ゴンダル': ['どうし', 'g3'],
                  HUB: ['h1', 'h2']})
    assert not hero_moves(mem)
    assert not skipped(mem)


def test_rejected_full_chart_keeps_hero_move_for_interim(monkeypatch):
    """An oversized full plan must not consume the month's only hero move."""
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)
    mem = memory({'アルマムーン': ['どうし'] + [f'g{i}' for i in range(12)],
                  HUB: [f'h{i}' for i in range(8)]})
    assert len(orders(mem)) > 16
    state = mem.setdefault('chart_adjust', {})
    replan._mechanical_chart(mem, state, 'a' * 16, 'b' * 64)
    assert not _plan_region(mem).get('chart_plan')
    assert state['interim_order']['hero_hub_move'] is True
    assert state['interim_order']['general'] == 'どうし'
    assert mem['hero_hub_move_eval']['planned'] is True


def test_adopted_full_chart_preserves_hero_permission_and_month(monkeypatch):
    """Validation must not discard trusted mechanical-only move permission."""
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)
    mem = memory({'アルマムーン': ['どうし', 'g1', 'g2'], HUB: ['h1', 'h2']})
    candidates = orders(mem)
    assert 0 < len(candidates) <= 16
    assert any(o.get('hero_hub_move') is True for o in candidates)
    state = mem.setdefault('chart_adjust', {})
    replan._mechanical_chart(mem, state, 'a' * 16, 'b' * 64)
    plan = _plan_region(mem)['chart_plan']
    [hero] = [o for o in plan['orders'] if o.get('hero_hub_move') is True]
    assert (hero['general'], hero['purpose']) == ('どうし', 'move')
    assert mem['hero_hub_move_eval']['planned'] is True
    _plan_region(mem).pop('chart_plan')
    assert not hero_moves(mem)
