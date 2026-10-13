import copy
import hashlib
import json

import pytest

from docich import hanjuku_repetition as loop


def digest(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def feed(record, value, now, *, action='A', continuous=True, phase='menu'):
    pending = loop.add_input({}, 'buttons', (action,)) if action is not None else {}
    return loop.observe(record, digest(value), phase, pending,
                        now=now, continuous=continuous)


def repeating(period=3, steps=400, interval=1.5):
    record = {}
    for n in range(steps):
        now = 100 + n * interval
        record = feed(record, n % period, now)
        proof = loop.confirmed(record, now=now)
        if proof:
            return record, now, proof
    return record, now, None


@pytest.mark.parametrize('period', [2, 3, 4, 6, 12, 24])
def test_multiple_screen_input_cycles(period):
    record, now, proof = repeating(period)
    assert proof and proof['period'] == period
    assert proof['seconds'] >= 300 and proof['cycles'] >= 4
    assert len(record['history']) <= loop.MAX_HISTORY
    assert len(json.dumps(record)) < 12_000


def test_short_fast_cycles_do_not_end_game():
    _, _, proof = repeating(3, 200, .1)
    assert proof is None


def test_long_cycles_need_four_whole_repetitions():
    record = {}
    for n in range(24 * 3):
        now = 100 + n * 14
        record = feed(record, n % 24, now)
        assert loop.confirmed(record, now=now) is None


@pytest.mark.parametrize('action', [None, 'A'])
def test_no_repeated_screen_evidence(action):
    record = {}
    for n in range(1000):
        now = 100 + n * 1.5
        record = feed(record, n, now, action=action)
        assert loop.confirmed(record, now=now) is None


def test_animated_screen_without_successful_input_is_not_a_loop_proof():
    record = {}
    for n in range(1000):
        now = 100 + n * 1.5
        record = feed(record, n % 3, now, action=None)
        assert loop.confirmed(record, now=now) is None


def test_one_repeated_token_cannot_count_as_multiple_cycles():
    record = {}
    for n in range(1000):
        now = 100 + n * 1.5
        record = feed(record, 1, now)
        assert loop.confirmed(record, now=now) is None
    assert len(record['history']) == 1


@pytest.mark.parametrize('mode', ['gap', 'pause', 'rewind', 'progress'])
def test_discontinuity_never_inherits_old_loop_time(mode):
    record, now, proof = repeating()
    assert proof
    next_time = now + (16 if mode == 'gap' else -1 if mode == 'rewind' else 1.5)
    record = feed(record, 0, next_time, continuous=mode not in {'pause', 'progress'})
    assert loop.confirmed(record, now=next_time) is None
    assert len(record['history']) == 1


def test_real_progress_breaks_candidate_even_when_old_cycle_returns():
    record, now, _ = repeating()
    record = feed(record, 'changed HP/money/chapter', now + 1.5)
    for n in range(100):
        at = now + 3 + n * 1.5
        record = feed(record, n % 3, at)
        assert loop.confirmed(record, now=at) is None


def test_dwell_differences_do_not_prevent_detection():
    record = {}
    now = 100
    proof = None
    for n in range(400):
        for _ in range(n % 4 + 1):
            record = feed(record, n % 3, now)
            proof = loop.confirmed(record, now=now)
            now += 1.5
            if proof:
                break
        if proof:
            break
    assert proof and proof['period'] == 3


def test_dwell_does_not_finish_a_partial_cycle():
    record, now, _ = repeating(3)
    record = feed(record, 0, now + 1.5)
    for n in range(1000):
        at = now + 3 + n * 1.5
        record = feed(record, 0, at)
        assert loop.confirmed(record, now=at) is None


def test_input_order_and_buttons_are_evidence_not_planned_actions():
    ab = loop.add_input(loop.add_input({}, 'buttons', ['A']), 'buttons', ['B'])
    ba = loop.add_input(loop.add_input({}, 'buttons', ['B']), 'buttons', ['A'])
    assert ab['sha256'] != ba['sha256']
    a = feed({}, 1, 100, action='A')
    b = feed({}, 1, 100, action='B')
    assert a['history'][0][1] != b['history'][0][1]


@pytest.mark.parametrize('pending', [[], {'count': True}, {'count': -1},
                                    {'count': 33}, {'sha256': 'x'},
                                    {'count': 0, 'sha256': 'a' * 64}])
def test_untrusted_input_markers_cannot_prove_loop(pending):
    record, now, _ = repeating()
    assert loop.observe(record, digest(1), 'menu', pending,
                        now=now + 1, continuous=True) == {}


@pytest.mark.parametrize('bad', [None, [], {'schema': 1},
                                {'schema': 1, 'last_observed': float('nan')},
                                {'schema': 1, 'last_observed': 1, 'history': [['x', 'y']]}])
def test_bad_history_is_not_stasis(bad):
    record = feed(bad, 1, 100)
    assert len(record['history']) == 1
    assert loop.confirmed(bad, now=100) is None


@pytest.mark.parametrize('field,value', [('cycles', True), ('cycles', 3),
                                        ('offset', True), ('offset', 30),
                                        ('since', float('nan')),
                                        ('since', 100_000),
                                        ('pattern', ['x', 'y'])])
def test_terminal_proof_is_revalidated(field, value):
    record, now, _ = repeating()
    record['candidate'][field] = value
    assert loop.confirmed(record, now=now) is None


def test_tampered_suffix_and_stale_proof_are_rejected():
    record, now, _ = repeating()
    assert loop.confirmed(record, now=now + 1) is None
    record['history'][-2][1] = 'i' + '0' * 64
    assert loop.confirmed(record, now=now) is None


def test_json_roundtrip_and_no_argument_mutation():
    record, now, _ = repeating()
    original = copy.deepcopy(record)
    next_record = feed(json.loads(json.dumps(record)), 0, now + 1.5)
    assert record == original and next_record is not record


def test_history_is_bounded_for_high_observation_rate():
    record, _, proof = repeating(24, 10_000, .04)
    assert proof
    assert len(record['history']) <= loop.MAX_HISTORY
    assert len(json.dumps(record)) < 12_000


def test_input_overflow_resets_instead_of_claiming_evidence():
    pending = {}
    for _ in range(1000):
        pending = loop.add_input(pending, 'buttons', ['A'])
    assert pending == {'count': loop.MAX_INPUTS + 1}
    assert loop.observe({}, digest(1), 'menu', pending, now=100, continuous=True) == {}
