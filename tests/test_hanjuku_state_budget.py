"""The brain's own memory record must stay readable.

g1163 2026-11-11 20:19: ``hanjuku_bot.json`` reached 265 143 bytes against the
262 144-byte ``BOT_STATE_LIMIT``. The brain exits with ``policy_error`` on that
read and only writes after a successful read, so the record could never shrink
itself: 200 failed observations, no input, screen froze, ``screen_stalled``.
"""
import json

import pytest

from docich.adapters.base import AdapterError
from docich.hanjuku import state_budget


def _state(**policy):
    return {'bot_version': 'test', 'step': 1, 'policy': policy}


def _dump(state, limit, path):
    path.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    return path


def test_a_small_record_is_returned_untouched():
    state = _state(tick=1)
    assert state_budget.compact_state(state, state_budget.OVERSIZED_READ_LIMIT) is state


@pytest.mark.parametrize('status', ['pending', 'failed'])
def test_wide_plan_does_not_reencode_the_whole_state_per_step(monkeypatch, status):
    """Long runs must not pay N full-state serializations each observation."""
    steps = [f'A:城{i}:M{i:06x}' for i in range(400)]
    state = _state(plan={
        'orders': dict.fromkeys(steps, status),
        'order_context': {step: {'general': 'リーキ', 'cards': ['クースカン'] * 4}
                          for step in steps},
    })
    original_size = state_budget.size
    full_encodes = 0

    def counted(value):
        nonlocal full_encodes
        if isinstance(value, dict) and value.get('bot_version') == 'test':
            full_encodes += 1
        return original_size(value)

    monkeypatch.setattr(state_budget, 'size', counted)
    out = state_budget.compact_state(state, 8192)
    assert full_encodes <= 4
    if status == 'pending':
        assert out == state
    else:
        assert 'order_context' not in out['policy']['plan']
        assert original_size(out) < original_size(state)
        assert out['policy']['plan']['orders'] == state['policy']['plan']['orders']
    assert len(state['policy']['plan']['orders']) == len(steps)


def test_incremental_json_budget_matches_actual_bytes_at_each_removal():
    members = {'城"\\\n': ['リーキ', {'名前': 'クースカン'}],
               'empty': {}, 'last': '日本語'}
    state = _state(plan={'order_context': members})
    remaining = state_budget.size(state)
    for key in list(members) + ['absent']:
        remaining = state_budget._drop_sized(members, key, remaining)
        assert remaining == state_budget.size(state)


def test_caches_are_dropped_before_live_orders():
    state = _state(
        obs_cost={f'k{i}': i for i in range(2000)},
        general_statuses={f'g{i}': i for i in range(500)},
        plan={'orders': {'S:1': 'pending'}},
    )
    limit = 4096
    out = state_budget.compact_state(state, limit)
    assert state_budget.size(out) <= state_budget.target(limit)
    assert 'obs_cost' not in out['policy']
    assert out['policy']['plan']['orders'] == {'S:1': 'pending'}
    # The caller's record is untouched: the write path must not mutate the
    # decision state the rest of the observation already used.
    assert 'obs_cost' in state['policy']


def test_retired_orders_without_references_are_dropped():
    orders = {f'I:c9:タオ>サヌト:{i}': 'failed' for i in range(200)}
    orders['I:c9:タオ>サヌト:200'] = 'pending'
    orders['I:c9:サヌト>オシロガ:3'] = 'failed'
    state = _state(plan={'orders': dict(orders)},
                   world={'sorties': {'I:c9:サヌト>オシロガ:3': {'status': 'en_route'}}})
    out = state_budget.compact_state(state, 4096)
    kept = out['policy']['plan']['orders']
    assert kept['I:c9:タオ>サヌト:200'] == 'pending'
    # A retired order a live sortie still names must survive.
    assert kept['I:c9:サヌト>オシロガ:3'] == 'failed'
    assert len(kept) < len(orders)


def test_launched_orders_reference_their_step():
    orders = {f'A:{i}': 'done' for i in range(100)}
    orders['A:7'] = 'done'
    state = _state(plan={'orders': dict(orders)},
                   world={'launched_orders': {'A:7': {'source': 'タオ'}}})
    out = state_budget.compact_state(state, 4096)
    assert 'A:7' in out['policy']['plan']['orders']


def test_history_keeps_its_newest_tail():
    state = _state(sortie_history=[{'n': i} for i in range(400)],
                   plan={'orders': {}})
    out = state_budget.compact_state(state, 4096)
    kept = out['policy']['sortie_history']
    assert len(kept) == state_budget.HISTORY_KEEP
    assert kept[-1] == {'n': 399}


def test_an_oversized_record_is_still_read_once(tmp_path):
    limit = 512
    path = _dump(_state(obs_cost={f'k{i}': i for i in range(400)}), limit, tmp_path / 'b.json')
    assert path.stat().st_size > limit
    state = state_budget.load_state(path, limit)
    assert state['policy']['obs_cost']
    # ... and the next write is compact enough to be read again.
    compacted = state_budget.compact_state(state, limit)
    assert state_budget.size(compacted) <= limit


def test_a_record_beyond_the_recovery_cap_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(state_budget, 'OVERSIZED_READ_LIMIT', 64)
    path = _dump(_state(obs_cost={'k': 'x' * 200}), 100, tmp_path / 'b.json')
    with pytest.raises(AdapterError):
        state_budget.load_state(path, 100)


def test_missing_record_is_an_empty_state(tmp_path):
    assert state_budget.load_state(tmp_path / 'missing.json', 512) == {}


def test_a_symlinked_record_is_refused(tmp_path):
    target = _dump(_state(tick=1), 512, tmp_path / 'real.json')
    link = tmp_path / 'link.json'
    link.symlink_to(target)
    with pytest.raises(AdapterError):
        state_budget.load_state(link, 512)


def test_the_size_is_the_utf8_bytes_the_reader_counts(tmp_path):
    """The read limit is a file size, not a character count.

    The live record measured 165 814 characters while occupying 202 953 bytes
    (castle names are three bytes each), so a character-counted budget would
    have let it pass the 256 KiB read limit again.
    """
    state = _state(castle_names=['サヌト', 'オシロガ', 'ドンドン', 'オオキク'] * 20)
    path = _dump(state, 512, tmp_path / 'b.json')
    assert state_budget.size(state) == path.stat().st_size
    assert state_budget.size(state) > len(json.dumps(state, ensure_ascii=False))


def test_a_wide_record_over_the_limit_is_compacted_by_its_bytes():
    state = _state(obs_cost={f'サヌト{i}': i for i in range(300)},
                   plan={'orders': {f'I:c9:サヌト>オシロガ:{i}': 'failed' for i in range(200)}})
    limit = 8192
    assert state_budget.size(state) > limit
    out = state_budget.compact_state(state, limit)
    assert state_budget.size(out) <= state_budget.target(limit)


def test_stale_panel_labels_are_dropped_from_the_garrison(tmp_path):
    """A record written before v678 still names the status panel's labels.

    The castle-status read only runs when the whole name column matches the
    member count, so a polluted castle can stay polluted; the load repairs it.
    """
    state = _state(world={'garrison': {
        'ハヤク': ['ハヤク', 'じょう', 'しゅうにゅう', 'G', 'レベル',
                  'しょうぐん', 'めい', 'ラズベリー', 'セルリアク', 'セミヨン'],
        'タオ': ['バトウラ'],
    }})
    path = _dump(state, 65536, tmp_path / 'b.json')
    loaded = state_budget.load_state(path, 65536)
    assert loaded['policy']['world']['garrison'] == {
        'ハヤク': ['ラズベリー', 'セルリアク', 'セミヨン'], 'タオ': ['バトウラ']}
    # The caller's record is untouched.
    assert len(state['policy']['world']['garrison']['ハヤク']) == 10


def test_a_clean_garrison_is_returned_unchanged():
    state = _state(world={'garrison': {'タオ': ['バトウラ', 'リーキ']}})
    assert state_budget.sanitize_state(state) is state


def test_settled_marches_are_dropped_before_the_record_grows_too_wide():
    """120 KB of launch bookkeeping had built up by chapter 9 month 23-12."""
    from docich.hanjuku.state_budget import SETTLED_MARCH_TICKS
    old = 1000
    state = _state(tick=old + SETTLED_MARCH_TICKS + 500,
                   plan={'orders': {'I:c9:タオ>サヌト:1': 'failed',
                                    'I:c9:サヌト>オシロガ:2': 'launched'}},
                   world={'launched_orders': {
                       'I:c9:タオ>サヌト:1': {'cards': ['クースカン'] * 200, 'tick': old},
                       # a live launch is recent, otherwise the retirement below drops it
                       'I:c9:サヌト>オシロガ:2': {'cards': ['クースカン'] * 40, 'tick': 4500},
                   },
                       'sorties': {'I:c9:タオ>サヌト:1': {'tick': old, 'status': 'arrived'}}})
    limit = 4096
    out = state_budget.compact_state(state, limit)
    assert state_budget.size(out) < state_budget.size(state)
    assert state_budget.size(out) <= limit
    world = out['policy']['world']
    # the settled march is gone; the live launched one is kept
    assert 'I:c9:タオ>サヌト:1' not in world['launched_orders']
    assert 'I:c9:サヌト>オシロガ:2' in world['launched_orders']


def test_a_recent_settled_march_is_kept():
    from docich.hanjuku.state_budget import SETTLED_MARCH_TICKS
    state = _state(tick=5000,
                   plan={'orders': {'I:x': 'failed'}},
                   world={'launched_orders': {'I:x': {'tick': 5000 - SETTLED_MARCH_TICKS + 10}}})
    out = state_budget.compact_state(state, 1024)
    assert 'I:x' in out['policy']['world']['launched_orders']


def test_an_ancient_launched_order_is_retired_and_its_records_dropped():
    """Chapter 9 carried 68 'launched' orders that never retired (~1 KB each)."""
    from docich.hanjuku.state_budget import SETTLED_MARCH_TICKS
    old = 1000
    orders = {f'I:c9:タオ>サヌト:{i}': 'launched' for i in range(40)}
    launched = {step: {'cards': ['クースカン'] * 30, 'tick': old} for step in orders}
    sorties = {step: {'tick': old, 'status': 'arrived'} for step in orders}
    state = _state(tick=old + SETTLED_MARCH_TICKS + 500,
                   plan={'orders': dict(orders)},
                   world={'launched_orders': launched, 'sorties': sorties})
    limit = 8192
    out = state_budget.compact_state(state, limit)
    assert state_budget.size(out) <= limit
    assert out['policy']['plan']['orders'] == {step: 'failed' for step in orders}
    assert out['policy']['world']['launched_orders'] == {}


def test_an_order_whose_march_is_still_walking_is_not_retired():
    from docich.hanjuku.state_budget import SETTLED_MARCH_TICKS
    old = 1000
    state = _state(tick=old + SETTLED_MARCH_TICKS + 500,
                   plan={'orders': {'I:c9:タオ>サヌト:1': 'launched'}},
                   world={'launched_orders': {'I:c9:タオ>サヌト:1': {
                              'cards': ['クースカン'] * 60, 'tick': old}},
                          'sorties': {'I:c9:タオ>サヌト:1': {
                              'tick': old, 'status': 'en_route'}}})
    out = state_budget.compact_state(state, 8192)
    assert out['policy']['plan']['orders']['I:c9:タオ>サヌト:1'] == 'launched'


def test_retired_plan_step_maps_are_dropped_before_the_record_stays_unreadable():
    """g1163 2026-11-13: ``plan`` held 250 KB of context for 226 failed orders.

    The status card and the shared gap card refuse a record above 262 144 bytes,
    so the broadcast showed "状態: 未確認" and the left observation card
    disappeared while the game itself kept running.
    """
    dead = {f'A:dead{i:04d}:M{i:06x}': {'actual_general': 'リーキ',
                                        'expected_metric': {'cards': ['イッテツーン'] * 12},
                                        'observed_metric': {'cards': ['イッテツーン'] * 12}}
            for i in range(120)}
    live = {'A:live0001:M000001': {'actual_general': 'グレナデン',
                                   'expected_metric': {'cards': ['クースカン'] * 12}}}
    state = _state(tick=1,
                   plan={'orders': {**{step: 'failed' for step in dead},
                                    **{step: 'pending' for step in live}},
                         'order_context': {**dead, **live},
                         'card_override': {**{step: ['イッテツーン'] * 12 for step in dead}},
                         'sortie_general': {**{step: 'ユイートル' for step in dead}}})
    limit = 16384
    assert state_budget.size(state) > limit
    out = state_budget.compact_state(state, limit)
    plan = out['policy']['plan']
    assert state_budget.size(out) <= state_budget.target(limit)
    assert set(plan['order_context']) == set(live)
    # A budget stop can leave some dead entries behind; what must never happen
    # is dropping the live context or stopping above the readable limit.
    assert len(plan.get('card_override') or {}) < len(dead)


def test_a_step_still_referenced_by_a_walking_march_keeps_its_context():
    step = 'A:live0001:M000001'
    state = _state(tick=1,
                   plan={'orders': {step: 'failed'},
                         'order_context': {step: {'actual_general': 'グレナデン'}},
                         'card_override': {step: ['クースカン'] * 12}},
                   world={'sorties': {step: {'status': 'en_route'}}})
    limit = 1024
    out = state_budget.compact_state(state, limit)
    assert step in out['policy']['plan']['order_context']
