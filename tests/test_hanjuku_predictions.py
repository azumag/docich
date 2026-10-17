"""No real Twitch calls, game processes, tokens, ROMs or production writes."""
from __future__ import annotations

import copy
from dataclasses import replace
import io
import json
from pathlib import Path
from types import SimpleNamespace
import urllib.error

import pytest

from docich import hanjuku_predictions as p, hanjuku_progress as progress
from docich import hanjuku_prediction_api as api
from docich import prediction_leaderboard as leaderboard
from docich.config import load_global
from docich.game_switch import atomic_write_json
from docich.retroarch_boundary import read_record

ROOT = Path(__file__).resolve().parents[1]
ID = {'game': 'hanjuku-hero', 'runtime_id': 'g1-abcdef', 'generation': 1, 'lease_id': 'lease1'}
ID2 = {**ID, 'runtime_id': 'g2-abcdef', 'generation': 2, 'lease_id': 'lease2'}
DIGEST = 'a' * 64


class FakeAPI:
    def __init__(self):
        self.rows = []
        self.calls = []
        self.failure = None
        self.after_create = None
        self.before_list_return = None

    def list(self, prediction_id=None):
        self.calls.append(('GET', prediction_id))
        if self.failure:
            raise self.failure
        if self.before_list_return:
            self.before_list_return()
        return copy.deepcopy([r for r in self.rows if not prediction_id or r['id'] == prediction_id])

    def create(self, title, outcomes, window):
        self.calls.append(('POST', title, tuple(outcomes), window))
        row = {'id': f'prediction-{len(self.rows)}', 'title': title, 'status': 'ACTIVE',
               'outcomes': [{'id': f'outcome-{i}', 'title': title} for i, title in enumerate(outcomes)]}
        self.rows.append(row)
        if self.after_create:
            raise self.after_create
        return [copy.deepcopy(row)]

    def end(self, prediction_id, status, outcome_id=None):
        self.calls.append(('PATCH', prediction_id, status, outcome_id))
        if self.failure:
            raise self.failure
        row = next(r for r in self.rows if r['id'] == prediction_id)
        row['status'] = status
        if outcome_id is not None:
            row['winning_outcome_id'] = outcome_id
        return [copy.deepcopy(row)]


@pytest.fixture
def env(tmp_path):
    g = replace(load_global(ROOT, ROOT / 'config/docich.soren-live.toml'), state_dir=tmp_path / 'run')
    remote = FakeAPI()
    def setup(identity=ID, chapter=1):
        runtime = g.state_dir / 'runtimes' / identity['runtime_id']
        runtime.mkdir(parents=True, exist_ok=True)
        atomic_write_json(g.state_dir / 'game_switch.json', {'phase': 'ready', 'active': identity})
        atomic_write_json(runtime / 'hanjuku_run.json', {**identity, 'schema': 1})
        if chapter is not None:
            progress.record(runtime, identity, {'chapter': chapter, 'chapter_evidence': 'header'}, [], DIGEST)
        return runtime
    runtime = setup()
    tick = lambda now=100, identity=ID: p.tick(g, identity, now=now, client_factory=lambda _: (remote, None))
    state = lambda: read_record(g.state_dir / p.FILE)
    return SimpleNamespace(g=g, remote=remote, runtime=runtime, setup=setup, tick=tick, state=state)


def terminal(env, reason='game_over', identity=ID):
    runtime = env.g.state_dir / 'runtimes' / identity['runtime_id']
    row = {**identity, 'schema': 1, 'terminal_reason': reason, 'frame_sha256': DIGEST,
           'name_entered': True, 'gameplay_seen': True, 'phase': 'title', 'title_count': 3,
           'observed_monotonic': 10, 'title_since': 7, 'unchanged_seconds': 300}
    if reason == 'input_stalled':
        # hanjuku_run.terminal() bounds input_stalled by monotonic deltas
        # (INPUT_STALL_SECONDS / INPUT_STALL_OBSERVATIONS), not by game_over fields.
        row.update(observed_monotonic=1000, observations=500,
                   last_input_monotonic=100, last_input_observations=200)
    atomic_write_json(runtime / 'hanjuku_run.json', row)


@pytest.mark.parametrize('best, expected', [(1,(2,1)), (2,(3,1)), (3,(4,2)), (4,(5,2)), (5,(6,3)), (11,(12,6))])
def test_thresholds(best, expected):
    assert tuple(p.thresholds(best).values()) == expected
    assert p.thresholds(12) is None


@pytest.mark.parametrize('bad', [True, False, -1, 13, '1', 1.5, None])
def test_no_count_coercion(bad):
    with pytest.raises(ValueError):
        p.thresholds(bad)


def test_outcomes_cover_disjoint_ranges():
    for target in range(2, 13):
        middle = target // 2
        expected = [2] * middle + [1] * (target - middle) + [0] * (13 - target)
        assert [p.winner(i, target, middle) for i in range(13)] == expected
    # 4択目（画面停滞で一時終了）は run の終わり方で選ぶため、突破数の区分には入らない。
    assert p.outcome_titles(2,1) == ['2話突破（新記録）', '1話突破', '1話突破できず',
                                     '画面停滞で一時終了（同じ状態から再開予定）']
    assert p.outcome_titles(6,3) == ['6話突破（新記録）', '3〜5話突破', '3話突破できず',
                                     '画面停滞で一時終了（同じ状態から再開予定）']
    # 受理は「生成は4択・row が自己申告する3か4択」。#2238 の legacy 互換。
    legacy, current = p.accepted_outcome_titles(2, 1)
    assert legacy == p.outcome_titles(2, 1)[:3]
    assert current == p.outcome_titles(2, 1)


def test_reach_two_is_one_clear_and_monotonic(env):
    progress.record(env.runtime, ID, {'chapter': 2, 'chapter_evidence': 'ピオーネ'}, [], DIGEST)
    progress.record(env.runtime, ID, {'chapter': 1}, [], 'b'*64)
    assert progress.completed_count(env.runtime, ID) == 1
    assert progress.read(env.runtime, ID)['frame_sha256'] == DIGEST


@pytest.mark.parametrize('policy', [{'chapter': 2}, {'chapter': '2'}, {'chapter': True},
                                  {'chapter': 2, 'chapter_evidence': 'アルマムーン'},
                                  {'year': 6, 'month': 3, 'boss_defeated': 1}])
def test_unverified_chapter_is_unknown(policy):
    assert progress.policy_cleared(policy) is None


def test_only_classified_final_boss_win_can_clear_twelve(env):
    policy = {'chapter': 12, 'chapter_evidence': 'header'}
    progress.record(env.runtime, ID, policy, [{'outcome': 'win', 'enemy': 'クーモン'}], DIGEST)
    assert progress.completed_count(env.runtime, ID) == 11
    progress.record(env.runtime, ID, policy, [{'decision': 'battle_result', 'outcome': 'win',
                    'enemy': 'クーモン', 'resulting_event': 'chapter_12_boss_defeated'}], DIGEST)
    assert progress.completed_count(env.runtime, ID) == 12


def test_new_game_in_same_generation_is_ambiguous(env):
    progress.record(env.runtime, ID, {'chapter': 1}, [{'decision': 'new_game_detected'}], DIGEST)
    assert progress.completed_count(env.runtime, ID) is None


def test_initial_create_and_no_duplicate_after_restart(env):
    assert env.tick()['mode'] == 'active'
    state = env.state()
    assert state['round']['target'] == 2 and state['round']['middle'] == 1
    assert state['round']['window'] == 300
    assert len(state['round']['title']) <= 45
    for t in (101, 130, 160, 190):
        assert env.tick(t)['mode'] == 'active'
    assert len([c for c in env.remote.calls if c[0] == 'POST']) == 1


@pytest.mark.parametrize('best, seconds', [(1, 300), (5, 900), (11, 1800)])
def test_created_window_scales_with_frozen_target(env, best, seconds):
    state = p._load(env.g.state_dir, 1)
    state['best_cleared'] = best
    atomic_write_json(env.g.state_dir / p.FILE, state)
    assert env.tick()['mode'] == 'active'
    assert env.state()['round']['window'] == seconds
    assert [call[-1] for call in env.remote.calls if call[0] == 'POST'] == [seconds]


def test_existing_legacy_window_is_preserved_when_setting_changes(env, monkeypatch):
    env.tick()
    state = env.state()
    state['round']['window'] = 120
    atomic_write_json(env.g.state_dir / p.FILE, state)
    monkeypatch.setattr(p, 'config', lambda _: (True, 1, 600))
    assert env.tick(130)['mode'] == 'active'
    assert env.state()['round']['window'] == 120
    assert len([call for call in env.remote.calls if call[0] == 'POST']) == 1


def test_rejected_create_retry_keeps_intent_window(env, monkeypatch):
    create = env.remote.create

    def reject(*args):
        raise api.APIError('rejected', rejected=True)

    monkeypatch.setattr(env.remote, 'create', reject)
    assert env.tick()['error'] == 'rejected'
    assert env.state()['round']['window'] == 300
    monkeypatch.setattr(env.remote, 'create', create)
    monkeypatch.setattr(p, 'config', lambda _: (True, 1, 600))
    assert env.tick(400)['mode'] == 'active'
    assert [call[-1] for call in env.remote.calls if call[0] == 'POST'] == [300]


@pytest.mark.parametrize('ending', [30, 61, 600, 1800])
def test_scaled_windows_stay_within_api_limits_and_end_at_maximum(ending):
    windows = [p.voting_window(target, ending) for target in range(1, 13)]
    assert windows == sorted(windows)
    assert all(30 <= window <= ending for window in windows)
    assert windows[-1] == ending
    assert p.voting_window(5, 61) == 30
    assert p.voting_window(7, 61) == 35


@pytest.mark.parametrize('target, ending', [(True, 1800), (0, 1800), (13, 1800),
                                          (2, True), (2, 29), (2, 1801)])
def test_invalid_voting_window_fails_closed(target, ending):
    with pytest.raises(ValueError):
        p.voting_window(target, ending)


@pytest.mark.parametrize('ending', [True, '1800', 29, 1801])
def test_invalid_ending_window_config_fails_closed(env, monkeypatch, ending):
    monkeypatch.setattr(p, 'load_game', lambda *_: SimpleNamespace(raw={
        'hanjuku': {'predictions': {'enabled': True, 'ending_window_seconds': ending}}}))
    assert env.tick()['error'] == 'invalid_state'
    assert env.remote.calls == []


@pytest.mark.parametrize('status', ['ACTIVE', 'LOCKED'])
def test_existing_unrelated_remote_is_never_modified(env, status):
    env.remote.rows = [{'id': 'other', 'title': '他のゲーム', 'status': status, 'outcomes': []}]
    for t in (100, 130, 160):
        assert env.tick(t)['mode'] == 'blocked'
    assert all(c[0] == 'GET' for c in env.remote.calls)
    env.remote.rows[0]['status'] = 'RESOLVED'
    assert env.tick(190)['mode'] == 'active'


@pytest.mark.parametrize('reason', ['game_over', 'screen_stalled', 'input_stalled'])
@pytest.mark.parametrize('chapter', [1, 2, 3, 7])
def test_terminal_uses_cleared_maximum_and_frozen_thresholds(env, reason, chapter):
    # 4択目（画面停滞で一時終了）は run の終わり方で選ぶため突破数に依存しない。
    # game_over / input_stalled は従来どおり突破数で判定する（#2238）。
    index = {'game_over': {1: 2, 2: 1, 3: 0, 7: 0},
             'input_stalled': {1: 2, 2: 1, 3: 0, 7: 0},
             'screen_stalled': {1: 3, 2: 3, 3: 3, 7: 3}}[reason][chapter]
    env.tick()
    progress.record(env.runtime, ID, {'chapter': chapter, 'chapter_evidence': 'header'}, [], DIGEST)
    terminal(env, reason)
    # New terminal bypasses the normal 30-second API cooldown.
    assert env.tick(101)['mode'] == 'resolved'
    row = env.state()['round']
    assert (row['target'], row['middle']) == (2,1)
    assert env.remote.rows[0]['winning_outcome_id'] == f'outcome-{index}'
    assert row['result']['cleared'] == chapter-1 and row['result']['reason'] == reason
    assert env.state()['best_cleared'] == max(1, chapter-1)
    assert read_record(env.runtime / p.RESULT_FILE)['cleared'] == chapter-1
    env.tick(131)
    assert len([c for c in env.remote.calls if c[0] == 'POST']) == 1
    assert len([c for c in env.remote.calls if c[0] == 'PATCH' and c[2] == 'RESOLVED']) == 1


def test_created_prediction_always_offers_four_outcomes(env):
    """選択肢は run 開始時に固定されるため、checkpoint の有無によらず常に4択（#2238）。"""
    env.tick()
    row = env.state()['round']
    assert row['outcome_titles'] == p.outcome_titles(2, 1)
    assert len(row['outcome_titles']) == 4 and len(row['outcome_ids']) == 4
    assert env.remote.rows[0]['outcomes'][3]['title'] == p.STALLED_OUTCOME_TITLE
    # 同じ4択 row は durable reread（_load）でも受理される。
    assert p._load(env.g.state_dir, 1)['round']['outcome_ids'] == row['outcome_ids']


def test_screen_stalled_without_progress_also_resolves_as_the_fourth_outcome(env):
    """突破数の証拠が無い停滞終了も CANCELED にせず4択目で記帳する（#2238）。"""
    env.setup(identity=ID2, chapter=None)
    env.tick(100, ID2)
    terminal(env, 'screen_stalled', ID2)
    assert env.tick(101, ID2)['mode'] == 'resolved'
    row = env.state()['round']
    assert row['result']['reason'] == 'screen_stalled' and row['result']['cleared'] is None
    assert row['status'] == 'RESOLVED'
    assert env.remote.rows[0]['winning_outcome_id'] == 'outcome-3'


def test_input_stalled_without_progress_still_cancels(env):
    """input_stalled は4択目に含めない（owner 決定 2026-10-15）。"""
    env.setup(identity=ID2, chapter=None)
    env.tick(100, ID2)
    terminal(env, 'input_stalled', ID2)
    assert env.tick(101, ID2)['mode'] == 'canceled'
    assert env.state()['round']['status'] == 'CANCELED'
    assert 'winning_outcome_id' not in env.remote.rows[0]


def test_legacy_three_option_row_is_accepted_and_settles_as_before(env):
    """デプロイ前の3択 row は受理し、停滞終了でも従来どおり CANCELED にする。

    4択へ変えた後に legacy row を `_load` が拒否すると #2213 の invalid_state
    ループが再発するため、受理だけ legacy を許す（生成は常に4択）。
    """
    env.setup(identity=ID2, chapter=None)
    env.tick(100, ID2)
    state = env.state()
    row = state['round']
    row['outcome_titles'] = row['outcome_titles'][:3]
    row['outcome_ids'] = row['outcome_ids'][:3]
    atomic_write_json(env.g.state_dir / p.FILE, state)
    env.remote.rows[0]['outcomes'] = env.remote.rows[0]['outcomes'][:3]
    assert len(p._load(env.g.state_dir, 1)['round']['outcome_titles']) == 3
    terminal(env, 'screen_stalled', ID2)
    assert env.tick(101, ID2)['mode'] == 'canceled'
    assert env.state()['round']['status'] == 'CANCELED'


def test_input_stalled_terminal_survives_the_durable_reread(env):
    """The accepted terminal reasons must match hanjuku_run.TERMINAL_REASONS.

    Regression (#933), observed in production on 2026-10-14: with
    ``terminal_reason='input_stalled'`` the first ``_result()`` wrote the durable
    ``hanjuku_prediction_result.json``, and the second ``_result()`` in the same
    ``_advance()`` refused that record because the accepted set was hardcoded to
    ``{game_over, screen_stalled}``. ``_advance`` raised before the round was
    persisted, so the tick looped forever in ``error=invalid_state`` with the
    fixed 300 s retry and the prediction could never be settled or canceled.
    """
    env.tick()
    progress.record(env.runtime, ID, {'chapter': 2, 'chapter_evidence': 'header'}, [], DIGEST)
    terminal(env, 'input_stalled')
    assert env.tick(101)['mode'] == 'resolved'
    row = env.state()['round']
    assert row['result']['reason'] == 'input_stalled' and row['result']['cleared'] == 1
    # Reading the same durable record again (what _load/_result do every tick)
    # must keep accepting it, and the settled round must stay terminal.
    assert p._load(env.g.state_dir, 1)['round']['result']['reason'] == 'input_stalled'
    assert env.tick(131)['mode'] == 'resolved'
    assert env.remote.rows[0]['winning_outcome_id'] == 'outcome-1'
    assert env.state()['best_cleared'] == 1


def test_next_run_uses_new_record(env):
    env.tick()
    progress.record(env.runtime, ID, {'chapter': 6, 'chapter_evidence': 'header'}, [], DIGEST)
    terminal(env, 'screen_stalled')
    env.tick(101)
    env.setup(ID2)
    env.tick(140, ID2)
    row = env.state()['round']
    assert (row['target'], row['middle']) == (6,3)


def test_api_outage_persists_result_and_common_tick_retries_after_teardown(env):
    env.tick()
    progress.record(env.runtime, ID, {'chapter': 3, 'chapter_evidence': 'header'}, [], DIGEST)
    terminal(env, 'screen_stalled')
    env.remote.failure = api.APIError('transport')
    assert env.tick(101)['error'] == 'transport'
    assert env.state()['round']['result']['cleared'] == 2
    assert env.state()['best_cleared'] == 2
    env.setup(ID2)
    env.remote.failure = None
    assert env.tick(401, None)['mode'] == 'resolved'
    # 画面停滞の終端なので、突破数ではなく4択目（outcome-3）で確定する（#2238）。
    assert env.remote.rows[0]['winning_outcome_id'] == 'outcome-3'


def test_unknown_score_resolves_as_temporary_end_without_guessing_zero(env):
    """突破数の証拠が無い停滞終了は、0 と推測せず4択目で記帳する（#2238）。"""
    (env.runtime / progress.FILE).unlink()
    env.tick()
    terminal(env, 'screen_stalled')
    assert env.tick(101)['mode'] == 'resolved'
    assert env.state()['round']['result']['cleared'] is None
    assert env.remote.rows[0]['winning_outcome_id'] == 'outcome-3'


def test_unknown_score_refunds_instead_of_guessing_zero(env):
    """停滞終了でも4択目でもない終端（input_stalled）は従来どおり返金する。"""
    (env.runtime / progress.FILE).unlink()
    env.tick()
    terminal(env, 'input_stalled')
    assert env.tick(101)['mode'] == 'canceled'
    assert not any(c[0] == 'PATCH' and c[2] == 'RESOLVED' for c in env.remote.calls)
    assert env.state()['round']['result']['cleared'] is None


def _set_bets(env, users_by_outcome):
    """Give the created round's outcomes explicit Twitch bet totals."""
    for index, outcome in enumerate(env.remote.rows[0]['outcomes']):
        users = users_by_outcome.get(index, 0)
        outcome['users'] = users
        outcome['channel_points'] = 100 * users


@pytest.mark.parametrize('outcomes, expected', [
    ([{'users': 0, 'channel_points': 0}, {'users': 2, 'channel_points': 500}], 1),
    ([{'users': 1}, {'channel_points': 1}], 2),
    ([{'users': None, 'channel_points': 0}, {'channel_points': 3}], 1),
    ([{'id': 'a'}], None),
    ([], None),
    ([{'users': True}], None),
    ([{'users': '1'}], None),
    (['not-an-outcome'], None),
])
def test_contested_outcomes_counts_only_readable_bets(outcomes, expected):
    assert p._contested_outcomes({'outcomes': outcomes}) == expected
    assert p._contested_outcomes(None) is None


def test_no_contest_is_false_when_the_counts_are_unreadable():
    assert p._no_contest({'outcomes': [{'users': 0}]}) is True
    assert p._no_contest({'outcomes': [{'users': 1}, {'users': 0}]}) is True
    assert p._no_contest({'outcomes': [{'users': 1}, {'users': 2}]}) is False
    assert p._no_contest({'outcomes': [{'id': 'a'}]}) is False


def test_a_single_outcome_with_bets_is_canceled_and_refunded(env):
    assert env.tick()['mode'] == 'active'
    _seed_top_predictors(env)
    _set_bets(env, {1: 2})

    assert _settle(env)['mode'] == 'canceled'

    statuses = [c[2] for c in env.remote.calls if c[0] == 'PATCH']
    assert statuses == ['LOCKED', 'CANCELED']
    assert env.state()['round']['status'] == 'CANCELED'
    # 不成立キャンセルでも参加実績と返金を残す（#933 追記）。
    ledger = leaderboard.load(env.g.state_dir / leaderboard.FILE)
    round_ = ledger['rounds'][env.remote.rows[0]['id']]
    assert round_['status'] == 'CANCELED' and round_['cancel_reason'] == 'no-contest'
    assert leaderboard.stats(ledger) == {'rounds': 1, 'viewers': 4, 'canceled': 1}
    assert all(entry['hit'] is None and entry['won'] == 0
               and entry['refunded'] == entry['used'] for entry in round_['predictors'])
    # 勝敗が確定していないので的中率の分母には入らない。
    assert leaderboard.ranking(ledger, min_rounds=1) == []


def test_no_bets_at_all_is_canceled_and_refunded(env):
    assert env.tick()['mode'] == 'active'
    _seed_top_predictors(env)
    _set_bets(env, {})

    assert _settle(env)['mode'] == 'canceled'

    assert not any(c[0] == 'PATCH' and c[2] == 'RESOLVED' for c in env.remote.calls)
    ledger = leaderboard.load(env.g.state_dir / leaderboard.FILE)
    assert leaderboard.stats(ledger) == {'rounds': 1, 'viewers': 4, 'canceled': 1}


def test_a_contested_prediction_resolves_as_before(env):
    assert env.tick()['mode'] == 'active'
    _seed_top_predictors(env)
    _set_bets(env, {0: 1, 2: 3})

    assert _settle(env)['mode'] == 'resolved'

    assert env.remote.rows[0]['winning_outcome_id'] == 'outcome-1'
    ledger = leaderboard.load(env.g.state_dir / leaderboard.FILE)
    # 4択すべてに top predictor が1人ずついる（_seed_top_predictors）。
    assert leaderboard.stats(ledger) == {'rounds': 1, 'viewers': 4, 'canceled': 0}
    assert [c[2] for c in env.remote.calls if c[0] == 'PATCH'] == ['LOCKED', 'RESOLVED']


def test_unreadable_bet_counts_resolve_as_before(env):
    # Twitch only omits users/channel_points when the caller cannot see them, so
    # the old settle path stays in place instead of inventing a cancel.
    assert env.tick()['mode'] == 'active'
    _seed_top_predictors(env)

    assert _settle(env)['mode'] == 'resolved'


def test_the_locked_row_decides_the_contest(env):
    assert env.tick()['mode'] == 'active'
    _seed_top_predictors(env)
    _set_bets(env, {0: 1, 2: 1})
    original = env.remote.end

    def end_with_a_late_correction(prediction_id, status, outcome_id=None):
        rows = original(prediction_id, status, outcome_id)
        if status == 'LOCKED':
            # Betting is closed at the lock, so this row is authoritative even
            # when it disagrees with the earlier GET.
            for index, outcome in enumerate(rows[0]['outcomes']):
                outcome['users'] = 1 if index == 2 else 0
                outcome['channel_points'] = 100 if index == 2 else 0
        return rows

    env.remote.end = end_with_a_late_correction

    assert _settle(env)['mode'] == 'canceled'
    assert not any(c[0] == 'PATCH' and c[2] == 'RESOLVED' for c in env.remote.calls)


def test_wrong_lease_or_bad_terminal_never_settles(env):
    env.tick()
    terminal(env)
    path = env.runtime / 'hanjuku_run.json'
    evidence = read_record(path)
    for override in ({'lease_id':'wrong'}, {'title_count':1}, {'frame_sha256':'bad'}):
        atomic_write_json(path, {**evidence, **override})
        assert env.tick(131)['mode'] == 'error'
    assert not any(c[0] == 'PATCH' for c in env.remote.calls)
    assert not (env.runtime / p.RESULT_FILE).exists()


def test_read_failure_does_not_mean_no_prediction(env):
    env.remote.failure = api.APIError('auth')
    assert env.tick()['error'] == 'auth'
    assert not any(c[0] == 'POST' for c in env.remote.calls)
    assert env.state()['round'] is None


def test_create_timeout_reconciles_exact_owned_intent_without_duplicate(env):
    env.remote.after_create = api.APIError('transport')
    assert env.tick()['error'] == 'transport'
    assert env.state()['round']['attempted'] is True
    env.remote.after_create = None
    assert env.tick(400)['mode'] == 'active'
    assert len([c for c in env.remote.calls if c[0] == 'POST']) == 1
    assert env.state()['round']['id'] == 'prediction-0'


def test_ambiguous_create_without_remote_never_reposts(env):
    def fail(*args):
        raise api.APIError('transport')
    env.remote.create = fail
    env.tick()
    assert env.tick(400)['error'] == 'create_unknown'
    assert env.state()['round']['attempted'] is True


def test_rejected_create_may_retry_but_not_after_result_is_known(env):
    def fail(*args):
        raise api.APIError('rate_limited', rejected=True)
    env.remote.create = fail
    env.tick()
    assert env.state()['round']['attempted'] is False
    progress.record(env.runtime, ID, {'chapter': 3, 'chapter_evidence': 'header'}, [], DIGEST)
    assert env.tick(400)['mode'] == 'known_result'


def test_create_rechecks_owner_after_remote_get(env):
    env.remote.before_list_return = lambda: atomic_write_json(
        env.g.state_dir / 'game_switch.json', {'phase':'ready', 'active': ID2})
    assert env.tick()['mode'] == 'pending'
    assert not any(c[0] == 'POST' for c in env.remote.calls)


def test_manual_interruption_only_cancels_owned_prediction(env):
    env.tick()
    env.setup(ID2)
    assert env.tick(131, None)['mode'] == 'canceled'
    assert not (env.runtime / p.RESULT_FILE).exists()


def test_remote_canceled_owned_prediction_is_not_recreated_in_same_run(env):
    env.tick()
    assert len([c for c in env.remote.calls if c[0] == 'POST']) == 1
    env.remote.rows[0]['status'] = 'CANCELED'
    assert env.tick(131)['mode'] == 'canceled'
    assert env.tick(161)['mode'] == 'canceled'
    assert len([c for c in env.remote.calls if c[0] == 'POST']) == 1


def test_global_retry_never_creates_prediction(env):
    assert env.tick(100, None)['mode'] == 'idle'
    assert env.remote.calls == []
    assert not (env.g.state_dir / p.FILE).exists()


def test_foreign_game_or_unready_canonical_cannot_create(env):
    atomic_write_json(env.g.state_dir / 'game_switch.json', {'phase':'switching', 'active':ID})
    env.tick()
    assert not any(c[0] == 'POST' for c in env.remote.calls)
    atomic_write_json(env.g.state_dir / 'game_switch.json', {'phase':'ready', 'active':ID2})
    env.tick(131)
    assert not any(c[0] == 'POST' for c in env.remote.calls)


def test_top_outcome_already_known_does_not_open_betting(env):
    progress.record(env.runtime, ID, {'chapter': 3, 'chapter_evidence': 'header'}, [], DIGEST)
    assert env.tick()['mode'] == 'known_result'
    assert env.remote.calls == []


def test_top_outcome_during_voting_locks_but_waits_for_terminal(env):
    env.tick()
    progress.record(env.runtime, ID, {'chapter': 3, 'chapter_evidence': 'header'}, [], DIGEST)
    env.tick(131)
    assert env.remote.rows[0]['status'] == 'LOCKED'
    assert not (env.runtime / p.RESULT_FILE).exists()


def test_pause_blocks_creation_not_result_recording_or_pending_settlement(env):
    pause = env.g.state_dir / 'hanjuku_predictions.paused'
    pause.touch()
    assert env.tick()['mode'] == 'paused'
    assert env.remote.calls == []
    pause.unlink()
    env.tick(131)
    pause.touch()
    terminal(env)
    assert env.tick(132)['mode'] == 'resolved'
    assert pause.exists()


@pytest.mark.parametrize('unavailable', ['disabled','explore','unconfigured'])
def test_existing_global_twitch_gates_hold_api_but_keep_record(env, unavailable):
    terminal(env)
    result = p.tick(env.g, ID, now=100, client_factory=lambda _: (None, unavailable))
    assert result['mode'] == unavailable
    assert read_record(env.runtime / p.RESULT_FILE)['cleared'] == 0
    assert env.remote.calls == []


def test_invalid_ledger_is_not_overwritten_or_treated_as_empty(env):
    env.g.state_dir.joinpath(p.FILE).write_text('{corrupt')
    assert env.tick()['error'] == 'invalid_state'
    assert env.g.state_dir.joinpath(p.FILE).read_text() == '{corrupt'
    assert env.remote.calls == []


def test_clock_rollback_stops_network(env):
    env.tick()
    before = list(env.remote.calls)
    assert env.tick(99)['error'] == 'clock_regressed'
    assert env.remote.calls == before


def test_legacy_snapshot_requires_identity_and_real_chapter_evidence(env):
    (env.runtime / progress.FILE).unlink()
    path = env.runtime / 'hanjuku_bot.json'
    atomic_write_json(path, {'decision_trace':ID, 'policy':{'chapter':2, 'chapter_evidence':'ピオーネ'}})
    assert progress.completed_count(env.runtime, ID) == 1
    atomic_write_json(path, {'decision_trace':ID2, 'policy':{'chapter':2, 'chapter_evidence':'header'}})
    with pytest.raises(Exception, match='identity'):
        progress.completed_count(env.runtime, ID)


@pytest.mark.parametrize('field,value', [('cleared',True), ('cleared','2'), ('cleared',-1),
                                        ('cleared',13), ('schema',True), ('lease_id','wrong')])
def test_malformed_progress_does_not_settle(env, field, value):
    env.tick()
    row = read_record(env.runtime / progress.FILE)
    atomic_write_json(env.runtime / progress.FILE, {**row, field:value})
    terminal(env)
    assert env.tick(101)['mode'] == 'error'
    assert not any(c[0] == 'PATCH' for c in env.remote.calls)


def test_dotenv_literal_loading_never_executes_and_respects_latest_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'resolve_soren_root', lambda _: tmp_path)
    monkeypatch.setenv('TWITCH_PREDICTIONS_ENABLED', '1')
    monkeypatch.setenv('TWITCH_PREDICTIONS_TOKEN', 'old-secret')
    tmp_path.joinpath('.env').write_text("export TWITCH_PREDICTIONS_ENABLED=0\nTWITCH_PREDICTIONS_TOKEN='new-secret' # comment\nIGNORE=$(touch should-not-exist)\n")
    settings = api.settings(None)
    assert settings['TWITCH_PREDICTIONS_TOKEN'] == 'new-secret'
    assert api.configured_client(None) == (None, 'disabled')
    assert not tmp_path.joinpath('should-not-exist').exists()


def test_transport_is_fixed_host_bounded_no_secrets_in_errors():
    class Opener:
        def open(self, request, timeout):
            assert request.full_url.startswith(api.ENDPOINT + '?')
            assert timeout == 5
            assert request.get_header('Authorization') == 'Bearer test-secret'
            raise urllib.error.HTTPError(request.full_url, 401, 'test-secret body', {}, None)
    client = api.Helix('test-secret', 'client', 'channel', opener=Opener())
    with pytest.raises(api.APIError) as error:
        client.list()
    assert str(error.value) == 'auth' and 'test-secret' not in str(error.value)
    assert error.value.rejected
    assert api.NoRedirect().redirect_request(None,None,302,'',{},'https://other.example') is None


@pytest.mark.parametrize('prediction_id', [None, 'owned-prediction'])
def test_helix_lists_latest_one_or_exact_owned_prediction(prediction_id):
    from urllib.parse import parse_qs, urlsplit

    class Opener:
        def open(self, request, timeout):
            assert request.get_method() == 'GET'
            query = parse_qs(urlsplit(request.full_url).query)
            expected = {'broadcaster_id': ['channel']}
            if prediction_id is None:
                expected['first'] = ['1']
            else:
                expected['id'] = [prediction_id]
            assert query == expected
            return io.BytesIO(b'{"data":[]}')

    client = api.Helix('test-secret', 'client', 'channel', opener=Opener())
    assert client.list(prediction_id) == []


@pytest.mark.parametrize('body', [b'{}', b'{"data":null}', b'{bad', b'x'*(api.MAX_BYTES+1),
                                 b'{"data":[{"id":"a","title":"b","status":"OTHER","outcomes":[]}]}'])
def test_bad_remote_response_fails_closed(body):
    class Opener:
        def open(self, request, timeout):
            return io.BytesIO(body)
    with pytest.raises(api.APIError):
        api.Helix('test-secret','client','channel',opener=Opener()).list()


def test_diagnostics_is_allowlisted_and_contains_no_titles_or_tokens(env):
    import importlib.util
    spec = importlib.util.spec_from_file_location('prediction_diagnostics_test', ROOT/'ops/vm_actions/collect_diagnostics.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    env.tick()
    state = env.state()
    state.update(token='not-for-logs', error='not-for-logs')
    state['round']['title'] = 'not-for-logs'
    atomic_write_json(env.g.state_dir/p.FILE, state)
    result = module._collect_hanjuku_predictions(env.g.state_dir)
    assert result['target'] == 2 and result['middle'] == 1 and result['best_cleared'] == 1
    assert result['window_seconds'] == 300
    assert 'not-for-logs' not in json.dumps(result)
    # The leaderboard write failure is a fixed, diagnosable code (#933).
    state.update(error='leaderboard', mode='error')
    atomic_write_json(env.g.state_dir/p.FILE, state)
    assert module._collect_hanjuku_predictions(env.g.state_dir)['error'] == 'leaderboard'


def test_integration_uses_side_channel_outside_input_gate():
    source = (ROOT/'src/docich/retro_corner.py').read_text()
    loop = source[source.index('    def _wait_hanjuku'):source.index('    def _retry_restoring_tick')]
    assert loop.index('shared_section(self.g.state_dir, adapter.observe)') < loop.index('prediction_tick(self.g, owned_identity)')
    assert loop.index('prediction_tick(self.g, owned_identity)') < loop.index('with self._locked():', loop.index('prediction_tick(self.g, owned_identity)'))
    assert 'prediction_tick(self.g)' in (ROOT/'src/docich/corner_rotation.py').read_text()


def test_saved_settlement_survives_runtime_retention(env):
    import shutil
    env.tick()
    progress.record(env.runtime, ID, {'chapter': 3, 'chapter_evidence': 'header'}, [], DIGEST)
    terminal(env, 'screen_stalled')
    env.remote.failure = api.APIError('transport')
    env.tick(101)
    env.setup(ID2)
    shutil.rmtree(env.runtime)
    env.remote.failure = None
    assert env.tick(401, None)['mode'] == 'resolved'
    # 画面停滞の終端なので4択目で確定する（#2238）。
    assert env.remote.rows[0]['winning_outcome_id'] == 'outcome-3'
    assert env.state()['best_cleared'] == 2


@pytest.mark.parametrize('override', [{'cleared': True}, {'generation': 2}, {'frame_sha256':'bad'},
                                     {'reason':'manual_stop'}, {'schema': True}])
def test_corrupt_saved_result_never_resolves(env, override):
    env.tick()
    terminal(env)
    env.remote.failure = api.APIError('transport')
    env.tick(101)
    state = env.state()
    state['round']['result'].update(override)
    atomic_write_json(env.g.state_dir/p.FILE, state)
    env.remote.failure = None
    assert env.tick(401, None)['error'] == 'invalid_state'
    assert env.state() == state
    assert not any(c[0] == 'PATCH' for c in env.remote.calls)


def test_lock_contention_never_creates_duplicate(env):
    with p.locked(env.g.state_dir) as held:
        assert held
        assert env.tick()['mode'] == 'pending'
    assert env.remote.calls == []


def test_invalid_progress_is_observable_without_losing_round_ownership(env):
    env.tick()
    terminal(env)
    env.runtime.joinpath(progress.FILE).write_text('{bad')
    assert env.tick(101)['error'] == 'invalid_state'
    assert env.state()['error'] == 'invalid_state'
    assert env.state()['round']['id'] == 'prediction-0'
    assert env.runtime.joinpath(progress.FILE).read_text() == '{bad'


def test_production_client_requires_ownership_aware_soren_before_enabling(tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'resolve_soren_root', lambda _: tmp_path)
    for key, value in {'TWITCH_PREDICTIONS_ENABLED':'1', 'TWITCH_PREDICTIONS_TOKEN':'test-token',
                       'TWITCH_CLIENT_ID':'test-client', 'TWITCH_BROADCASTER_ID':'test-channel',
                       'EXPLORE_MODE':'0'}.items():
        monkeypatch.setenv(key, value)
    assert api.configured_client(None) == (None, 'incompatible_soren')
    wrapper = tmp_path / 'twitch_predictions.sh'
    wrapper.write_text('#!/bin/bash\n# legacy\n')
    assert api.configured_client(None) == (None, 'incompatible_soren')
    wrapper.write_text('# SOREN_REMOTE_PREDICTION_OWNERSHIP_V1: verified by the companion rollout\n')
    client, reason = api.configured_client(None)
    assert isinstance(client, api.Helix) and reason is None


def _seed_top_predictors(env):
    """Give every outcome one public top predictor as Twitch would."""
    for index, outcome in enumerate(env.remote.rows[0]['outcomes']):
        outcome['top_predictors'] = [{
            'user_id': f'u{index}', 'user_login': f'login{index}', 'user_name': f'同志{index}',
            'channel_points_used': 100 + index,
            'channel_points_won': 200 if index == 1 else 0,
        }]


def _settle(env, now=101):
    progress.record(env.runtime, ID, {'chapter': 2, 'chapter_evidence': 'header'}, [], DIGEST)
    terminal(env, 'game_over')
    return env.tick(now)


def test_settled_round_is_recorded_in_the_yosou_leaderboard(env):
    assert env.tick()['mode'] == 'active'
    prediction_id = env.remote.rows[0]['id']
    _seed_top_predictors(env)

    assert _settle(env)['mode'] == 'resolved'

    ledger = leaderboard.load(env.g.state_dir / leaderboard.FILE)
    round_ = ledger['rounds'][prediction_id]
    assert round_['corner'] == 'hanjuku'
    assert round_['title'] == env.state()['round']['title']
    assert round_['winning_outcome_id'] == env.remote.rows[0]['winning_outcome_id']
    assert {entry['user_id']: entry['hit'] for entry in round_['predictors']} == {
        f'u{index}': outcome['id'] == round_['winning_outcome_id']
        for index, outcome in enumerate(env.remote.rows[0]['outcomes'])
    }
    assert leaderboard.stats(ledger) == {'rounds': 1, 'viewers': 4, 'canceled': 0}

    # The runtime-retention tick after teardown never counts the round twice.
    env.tick(131)
    assert len(leaderboard.load(env.g.state_dir / leaderboard.FILE)['rounds']) == 1


def test_patch_response_without_predictors_uses_one_authoritative_read(env):
    assert env.tick()['mode'] == 'active'
    prediction_id = env.remote.rows[0]['id']
    _seed_top_predictors(env)
    original = env.remote.end

    def end_without_predictors(*args, **kwargs):
        rows = original(*args, **kwargs)
        for outcome in rows[0]['outcomes']:
            outcome.pop('top_predictors', None)
        return rows

    env.remote.end = end_without_predictors

    assert _settle(env)['mode'] == 'resolved'

    calls = env.remote.calls
    patch_at = max(index for index, call in enumerate(calls)
                   if call[0] == 'PATCH' and call[2] == 'RESOLVED')
    assert any(call == ('GET', prediction_id) for call in calls[patch_at + 1:])
    ledger = leaderboard.load(env.g.state_dir / leaderboard.FILE)
    assert leaderboard.stats(ledger) == {'rounds': 1, 'viewers': 4, 'canceled': 0}


def test_broken_ledger_keeps_the_settled_round_retryable(env):
    assert env.tick()['mode'] == 'active'
    _seed_top_predictors(env)
    path = env.g.state_dir / leaderboard.FILE
    path.write_text('{broken', encoding='utf-8')

    first = _settle(env)
    assert first['mode'] == 'error' and first['error'] == 'leaderboard'
    assert env.state()['round']['status'] != 'RESOLVED'
    assert path.read_text(encoding='utf-8') == '{broken'

    path.write_text(json.dumps(leaderboard.new_ledger()), encoding='utf-8')
    retried = env.tick(101 + p.RETRY_SECONDS + 1)
    assert retried['mode'] == 'resolved'
    assert len(leaderboard.load(path)['rounds']) == 1


@pytest.mark.parametrize('settled_by', ['patch', 'remote'])
def test_malformed_settled_row_is_refused_without_recording(env, settled_by):
    assert env.tick()['mode'] == 'active'
    prediction_id = env.remote.rows[0]['id']
    duplicate = {'user_id': 'u1', 'user_login': 'alice', 'user_name': 'Alice',
                 'channel_points_used': 10, 'channel_points_won': 5}
    env.remote.rows[0]['outcomes'][0]['top_predictors'] = [duplicate, dict(duplicate)]

    if settled_by == 'patch':
        result = _settle(env, now=131)
    else:
        env.remote.rows[0].update(status='RESOLVED', winning_outcome_id='outcome-1')
        result = env.tick(131)

    assert result['mode'] == 'error' and result['error'] == 'invalid_response'
    assert env.state()['round']['status'] == 'RESOLVED'
    assert not (env.g.state_dir / leaderboard.FILE).exists()

    # Neither a cooldown tick nor a common retry tick re-reads the rejected row.
    calls = list(env.remote.calls)
    env.tick(132, None)
    env.tick(131 + p.RETRY_SECONDS + 1, None)
    assert env.remote.calls == calls
    assert env.state()['round']['id'] == prediction_id
    assert env.state()['round']['status'] == 'RESOLVED'

    # The verified terminal round must not block the next canonical generation.
    env.setup(ID2)
    assert env.tick(131 + p.RETRY_SECONDS + 32, ID2)['mode'] == 'active'
    assert env.state()['round']['runtime_id'] == ID2['runtime_id']
    assert env.state()['round']['id'] != prediction_id
    env.tick(131 + p.RETRY_SECONDS + 62, ID2)
    assert len([call for call in env.remote.calls if call == ('GET', prediction_id)]) == 1
    assert len([call for call in env.remote.calls if call[0] == 'POST']) == 2
    assert not (env.g.state_dir / leaderboard.FILE).exists()


@pytest.mark.parametrize('settled_by', ['patch', 'remote'])
def test_malformed_canceled_row_is_refused_without_recording(env, settled_by):
    """The P2 invariant (PR #2205) also holds on the no-contest cancel path.

    A cancelled row that violates the record contract must not be recorded, and
    the round must be held terminal so the old ACTIVE ownership neither keeps
    re-reading the same id nor blocks the next canonical generation. Without the
    terminal status the tick would retry the rejected id forever.
    """
    assert env.tick()['mode'] == 'active'
    prediction_id = env.remote.rows[0]['id']
    _seed_top_predictors(env)
    _set_bets(env, {1: 2})
    # A cancelled prediction cannot name a winner: extract() refuses this row.
    env.remote.rows[0]['winning_outcome_id'] = 'outcome-1'

    if settled_by == 'patch':
        result = _settle(env, now=131)
    else:
        env.remote.rows[0].update(status='CANCELED')
        result = env.tick(131)

    assert result['mode'] == 'error' and result['error'] == 'invalid_response'
    assert env.state()['round']['status'] == 'CANCELED'
    assert not (env.g.state_dir / leaderboard.FILE).exists()

    # Neither a cooldown tick nor a common retry tick re-reads the rejected row.
    calls = list(env.remote.calls)
    env.tick(132, None)
    env.tick(131 + p.RETRY_SECONDS + 1, None)
    assert env.remote.calls == calls
    assert env.state()['round']['id'] == prediction_id
    assert env.state()['round']['status'] == 'CANCELED'

    # The rejected terminal round must not block the next canonical generation.
    env.setup(ID2)
    assert env.tick(131 + p.RETRY_SECONDS + 32, ID2)['mode'] == 'active'
    assert env.state()['round']['runtime_id'] == ID2['runtime_id']
    assert env.state()['round']['id'] != prediction_id
    env.tick(131 + p.RETRY_SECONDS + 62, ID2)
    assert len([call for call in env.remote.calls if call == ('GET', prediction_id)]) == 1
    assert len([call for call in env.remote.calls if call[0] == 'POST']) == 2
    assert not (env.g.state_dir / leaderboard.FILE).exists()


@pytest.mark.parametrize('settled_by', ['patch', 'remote'])
@pytest.mark.parametrize('error_code', ['leaderboard', 'transport'])
def test_retryable_recording_error_keeps_owned_round_nonterminal(env, monkeypatch, settled_by, error_code):
    assert env.tick()['mode'] == 'active'
    prediction_id = env.remote.rows[0]['id']
    _seed_top_predictors(env)
    record = p._record_settled

    def fail(*args):
        raise api.APIError(error_code)

    monkeypatch.setattr(p, '_record_settled', fail)
    if settled_by == 'patch':
        result = _settle(env, now=131)
    else:
        env.remote.rows[0].update(status='RESOLVED', winning_outcome_id='outcome-1')
        result = env.tick(131)
    assert result['error'] == error_code
    assert env.state()['round']['status'] not in p.DONE
    assert env.state()['round']['id'] == prediction_id
    assert not (env.g.state_dir / leaderboard.FILE).exists()

    monkeypatch.setattr(p, '_record_settled', record)
    assert env.tick(131 + p.RETRY_SECONDS + 1, None)['mode'] == 'resolved'
    assert set(leaderboard.load(env.g.state_dir / leaderboard.FILE)['rounds']) == {prediction_id}
    assert len([call for call in env.remote.calls if call[0] == 'POST']) == 1
    assert len([call for call in env.remote.calls
                if call[0] == 'PATCH' and call[2] == 'RESOLVED']) == (settled_by == 'patch')


def test_unverified_settlement_response_keeps_owned_round_retryable(env, monkeypatch):
    assert env.tick()['mode'] == 'active'
    _seed_top_predictors(env)
    end = env.remote.end

    def invalid_response(*args, **kwargs):
        rows = end(*args, **kwargs)
        if args[1] == 'RESOLVED':
            rows[0]['status'] = 'LOCKED'
        return rows

    monkeypatch.setattr(env.remote, 'end', invalid_response)
    result = _settle(env, now=131)
    assert result['error'] == 'invalid_response'
    assert env.state()['round']['status'] not in p.DONE
    assert not (env.g.state_dir / leaderboard.FILE).exists()
    assert env.tick(131 + p.RETRY_SECONDS + 1, None)['mode'] == 'resolved'
    assert len(leaderboard.load(env.g.state_dir / leaderboard.FILE)['rounds']) == 1


def test_broken_ledger_keeps_the_canceled_round_retryable(env):
    assert env.tick()['mode'] == 'active'
    prediction_id = env.remote.rows[0]['id']
    _seed_top_predictors(env)
    _set_bets(env, {1: 2})
    path = env.g.state_dir / leaderboard.FILE
    path.write_text('{broken', encoding='utf-8')

    first = _settle(env, now=131)
    assert first['mode'] == 'error' and first['error'] == 'leaderboard'
    assert env.state()['round']['status'] != 'CANCELED'
    assert path.read_text(encoding='utf-8') == '{broken'

    # The next observation of the already-canceled remote records the refunded
    # participation exactly once.
    path.write_text(json.dumps(leaderboard.new_ledger()), encoding='utf-8')
    assert env.tick(131 + p.RETRY_SECONDS + 1, None)['mode'] == 'canceled'
    ledger = leaderboard.load(path)
    assert set(ledger['rounds']) == {prediction_id}
    assert ledger['rounds'][prediction_id]['cancel_reason'] == 'no-contest'
    assert leaderboard.stats(ledger)['canceled'] == 1
    env.tick(131 + 2 * p.RETRY_SECONDS + 2, None)
    assert len(leaderboard.load(path)['rounds']) == 1


def test_a_non_contest_cancel_is_not_recorded(env):
    """An ``input_stalled`` ending cancels the round, but it is not the refunded
    no-contest round the 予想王 ledger keeps (Issue #933 scopes the addendum to
    no-contest cancellations)."""
    env.setup(identity=ID2, chapter=None)
    env.tick(100, ID2)
    terminal(env, 'input_stalled', ID2)

    assert env.tick(101, ID2)['mode'] == 'canceled'

    assert env.state()['round']['status'] == 'CANCELED'
    assert [c[2] for c in env.remote.calls if c[0] == 'PATCH'] == ['CANCELED']
    assert not (env.g.state_dir / leaderboard.FILE).exists()


@pytest.mark.parametrize('missing_counts', [False, True])
def test_cancel_participation_survives_ledger_failure_and_state_reload(env, monkeypatch, missing_counts):
    assert env.tick()['mode'] == 'active'
    prediction_id = env.remote.rows[0]['id']
    _set_bets(env, {0: 1})
    env.remote.rows[0]['outcomes'][0]['top_predictors'] = [{
        'user_id': 'u1', 'user_login': 'alice', 'user_name': 'Alice',
        'channel_points_used': 100, 'channel_points_won': None}]
    end = env.remote.end

    def end_without_participation(*args):
        if args[1] == 'CANCELED':
            # Write-ahead evidence must already be durable when cancel starts.
            snapshot = p._load_cancel_snapshot(env.g.state_dir, env.state()['round'])
            assert snapshot['outcomes'][0]['top_predictors'][0]['channel_points_used'] == 100
        response = end(*args)
        if args[1] == 'CANCELED':
            # PATCH and every subsequent GET omit the formerly known voters.
            for source in (response[0], env.remote.rows[0]):
                for outcome in source['outcomes']:
                    outcome.pop('top_predictors', None)
                    if missing_counts:
                        outcome.pop('users', None)
                        outcome.pop('channel_points', None)
        return response

    monkeypatch.setattr(env.remote, 'end', end_without_participation)
    save = leaderboard.save
    attempts = []

    def fail_once(path, ledger):
        attempts.append(copy.deepcopy(ledger))
        if len(attempts) == 1:
            raise OSError('synthetic ledger write failure')
        save(path, ledger)

    monkeypatch.setattr(leaderboard, 'save', fail_once)
    assert _settle(env, now=131)['error'] == 'leaderboard'
    assert len(attempts[0]['rounds'][prediction_id]['predictors']) == 1
    assert p._load(env.g.state_dir, 1)['round']['status'] not in p.DONE
    snapshot_path = p._cancel_snapshot_path(env.g.state_dir, env.state()['round'])
    assert snapshot_path.is_file()
    assert snapshot_path.stat().st_mode & 0o777 == 0o600

    assert env.tick(131 + p.RETRY_SECONDS + 1, None)['mode'] == 'canceled'
    ledger = leaderboard.load(env.g.state_dir / leaderboard.FILE)
    assert leaderboard.stats(ledger) == {'rounds': 1, 'viewers': 1, 'canceled': 1}
    alice = leaderboard.boards(ledger)['plays'][0]
    assert (alice['participations'], alice['used'], alice['refunded'], alice['net']) == (1, 100, 100, 0)
    assert not snapshot_path.exists()
    before = (env.g.state_dir / leaderboard.FILE).read_bytes()
    env.tick(131 + 2 * p.RETRY_SECONDS + 2, None)
    assert (env.g.state_dir / leaderboard.FILE).read_bytes() == before


def _locked_cancel_snapshot(env):
    assert env.tick()['mode'] == 'active'
    _set_bets(env, {0: 1})
    remote = env.remote.rows[0]
    remote['status'] = 'LOCKED'
    remote['outcomes'][0]['top_predictors'] = [{
        'user_id': 'u1', 'user_login': 'alice', 'user_name': 'Alice',
        'channel_points_used': 100}]
    return env.state()['round'], copy.deepcopy(remote)


@pytest.mark.parametrize('failure', ['io', 'oversize', 'invalid_predictor', 'before_replace'])
def test_snapshot_failure_prevents_cancel(env, monkeypatch, failure):
    row, remote = _locked_cancel_snapshot(env)
    snapshot_path = p._cancel_snapshot_path(env.g.state_dir, row)
    write = p.atomic_write_json

    def fail_snapshot(path, payload):
        if path == snapshot_path:
            if failure == 'io':
                raise OSError('synthetic snapshot write failure')
            if failure == 'before_replace':
                def crash(phase, _):
                    if phase == 'after_file_fsync':
                        raise OSError('synthetic crash before replace')
                return atomic_write_json(path, payload, crash_hook=crash)
        return write(path, payload)

    monkeypatch.setattr(p, 'atomic_write_json', fail_snapshot)
    if failure == 'oversize':
        env.remote.rows[0]['padding'] = 'あ' * (p.MAX_CANCEL_SNAPSHOT_BYTES // 3)
    elif failure == 'invalid_predictor':
        env.remote.rows[0]['outcomes'][0]['top_predictors'][0]['channel_points_used'] = -1

    assert _settle(env, now=131)['mode'] == 'error'
    assert env.remote.rows[0]['status'] == 'LOCKED'
    assert not any(c[0] == 'PATCH' and c[2] == 'CANCELED' for c in env.remote.calls)
    assert not snapshot_path.exists()
    assert not list(env.g.state_dir.glob(f'.{snapshot_path.name}.*'))
    assert not (env.g.state_dir / leaderboard.FILE).exists()
    assert env.state()['round']['status'] not in p.DONE


@pytest.mark.parametrize('corruption', ['id', 'outcome', 'predictor', 'empty', 'json', 'oversize', 'symlink'])
def test_corrupt_or_foreign_snapshot_is_refused_without_empty_record(env, corruption):
    row, remote = _locked_cancel_snapshot(env)
    snapshot = p._save_cancel_snapshot(env.g.state_dir, row, remote)
    path = p._cancel_snapshot_path(env.g.state_dir, row)
    if corruption == 'id':
        snapshot['id'] = 'other-prediction'
    elif corruption == 'outcome':
        snapshot['outcomes'][0]['id'] = 'other-outcome'
    elif corruption == 'predictor':
        snapshot['outcomes'][0]['top_predictors'][0]['channel_points_used'] = -1
    elif corruption == 'empty':
        snapshot = {}
    atomic_write_json(path, snapshot)
    if corruption == 'json':
        path.write_text('{broken', encoding='utf-8')
    elif corruption == 'oversize':
        path.write_bytes(b' ' * (p.MAX_CANCEL_SNAPSHOT_BYTES + 1))
    elif corruption == 'symlink':
        path.unlink()
        path.symlink_to(env.g.state_dir / 'missing-snapshot')
    original = None if path.is_symlink() else path.read_bytes()
    env.remote.rows[0]['status'] = 'CANCELED'
    for outcome in env.remote.rows[0]['outcomes']:
        outcome.pop('top_predictors', None)

    assert env.tick(131, None)['error'] == 'invalid_state'
    assert path.is_symlink() if corruption == 'symlink' else path.read_bytes() == original
    assert env.state()['round']['status'] not in p.DONE
    assert not (env.g.state_dir / leaderboard.FILE).exists()


def test_snapshot_is_not_replaced_by_a_poorer_locked_retry(env):
    row, remote = _locked_cancel_snapshot(env)
    p._save_cancel_snapshot(env.g.state_dir, row, remote)
    path = p._cancel_snapshot_path(env.g.state_dir, row)
    original = path.read_bytes()
    for outcome in remote['outcomes']:
        outcome.pop('top_predictors', None)
    assert p._save_cancel_snapshot(env.g.state_dir, row, remote)['outcomes'][0]['top_predictors']
    assert path.read_bytes() == original
    assert p._load_cancel_snapshot(env.g.state_dir, {**row, 'id': 'other'}) is None


def test_large_snapshot_reloads_without_expanding_the_intent(env):
    row, remote = _locked_cancel_snapshot(env)
    remote['outcomes'][0]['top_predictors'] = [{
        'user_id': f'u{i}', 'user_login': f'alice{i}', 'user_name': '同' * 128,
        'channel_points_used': 100} for i in range(100)]
    saved = p._save_cancel_snapshot(env.g.state_dir, row, remote)
    path = p._cancel_snapshot_path(env.g.state_dir, row)
    assert 16384 < path.stat().st_size <= p.MAX_CANCEL_SNAPSHOT_BYTES
    assert (env.g.state_dir / p.FILE).stat().st_size < 16384
    assert p._load(env.g.state_dir, 1)['round']['id'] == row['id']
    assert p._load_cancel_snapshot(env.g.state_dir, row) == saved


def test_cancel_cleanup_failure_retries_a_committed_ledger_without_duplicates(env, monkeypatch):
    row, remote = _locked_cancel_snapshot(env)
    snapshot_path = p._cancel_snapshot_path(env.g.state_dir, row)
    unlink = Path.unlink

    def fail_once(path, *args, **kwargs):
        if path == snapshot_path and path.exists():
            assert (env.g.state_dir / leaderboard.FILE).is_file()
            monkeypatch.setattr(Path, 'unlink', unlink)
            raise OSError('synthetic cleanup failure after ledger commit')
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail_once)
    assert _settle(env, now=131)['mode'] == 'error'
    assert snapshot_path.is_file()
    assert env.state()['round']['status'] not in p.DONE
    path = env.g.state_dir / leaderboard.FILE
    original = path.read_bytes()
    for outcome in env.remote.rows[0]['outcomes']:
        outcome.pop('top_predictors', None)
    assert env.tick(131 + p.RETRY_SECONDS + 1, None)['mode'] == 'canceled'
    assert path.read_bytes() == original
    assert not snapshot_path.exists()
    assert leaderboard.boards(leaderboard.load(path))['plays'][0]['participations'] == 1
