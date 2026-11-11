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
