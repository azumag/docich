"""Pure/state-machine and hook tests for the hanjuku-speedrun RTA records."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

from docich.hanjuku_speedrun.rta import hook, store, timer

BASE = 1_700_000_000.0


def make_runtime(root, runtime_id='g1-abc'):
    runtime = Path(root) / 'run-soren-live' / 'runtimes' / runtime_id
    runtime.mkdir(parents=True)
    shot = runtime / 'screenshots' / 'latest.png'
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b'frame')
    return runtime, shot


def observe(runtime, shot, records=None, *, run_extra=None, t=BASE,
            state=None):
    os.utime(shot, (t, t))
    run = {'name_entered': False, 'clear_ending_seen': False,
           'terminal_reason': None}
    run.update(run_extra or {})
    meta = {'hanjuku': run, 'terminal_reason': run.get('terminal_reason')}
    return hook.observe(runtime, records or [], state or {}, meta,
                        screenshot=shot)


def split_record(chapter, variant='chapter_end_save', decision='prompt'):
    return {'decision': decision, 'strategy_variant': variant,
            'chapter': chapter}


def chapter_seen(new_chapter, evidence='chapter_end_save_party'):
    return {'decision': 'chapter_seen', 'chapter': new_chapter,
            'observed_metric': {'chapter': new_chapter, 'evidence': evidence}}


def start(runtime, shot, t=BASE):
    return observe(runtime, shot, run_extra={'name_entered': True}, t=t)


def full_run(runtime, shot, *, step=10, start_t=BASE):
    start(runtime, shot, t=start_t)
    for chapter in range(1, 12):
        observe(runtime, shot, [split_record(chapter)],
                t=start_t + chapter * step)
    return observe(runtime, shot, run_extra={'clear_ending_seen': True},
                   t=start_t + 12 * step)


def test_twelve_segments_finish_sets_first_pb(tmp_path):
    runtime, shot = make_runtime(tmp_path)
    final = full_run(runtime, shot)

    assert final['status'] == 'finished'
    assert final['current_index'] == 12
    assert len(final['splits']) == 12
    assert all(item['verdict'] == 'gold' for item in final['splits'])
    assert final['total_ms'] == 120_000
    assert final['total_verdict'] == 'gold'
    assert final['pb_updated'] is True

    records = store.load_records(runtime)
    assert records['next_attempt_no'] == 2
    assert records['pb']['attempt_id'] == 'a0001-g1-abc'
    assert records['pb']['total_ms'] == 120_000
    assert records['pb']['segments_ms'] == [10_000] * 12
    assert records['gold_segments_ms'] == [10_000] * 12
    assert records['sum_of_best_ms'] == 120_000
    assert len(store.load_attempts(runtime)) == 1


def test_second_attempt_reports_gold_blue_red_white_and_delta(tmp_path):
    runtime_a, shot_a = make_runtime(tmp_path, 'g1-abc')
    full_run(runtime_a, shot_a)

    # A DNF still records a segment best; it must not replace the PB.
    runtime_b, shot_b = make_runtime(tmp_path, 'g2-def')
    start(runtime_b, shot_b, t=BASE + 1000)
    observe(runtime_b, shot_b, [split_record(1)], t=BASE + 1001)
    observe(runtime_b, shot_b,
            run_extra={'terminal_reason': 'game_over'}, t=BASE + 1002)
    records = store.load_records(runtime_b)
    assert records['pb']['attempt_id'] == 'a0001-g1-abc'
    assert records['gold_segments_ms'][0] == 1_000

    runtime_c, shot_c = make_runtime(tmp_path, 'g3-ghi')
    start(runtime_c, shot_c, t=BASE + 2000)
    # Durations: 0.5 s (gold), 15 s (red), 10 s (white), 0.8 s (gold),
    # 10 s (white).  The PB segments are all 10 s = 10,000 ms.
    results = {}
    at = {1: 2000.5, 2: 2015.5, 3: 2025.5, 4: 2026.3, 5: 2036.3}
    for chapter in range(1, 6):
        result = observe(runtime_c, shot_c, [split_record(chapter)],
                         t=BASE + at[chapter])
        results[chapter] = result['splits'][-1]
    assert results[1]['verdict'] == 'gold'
    assert results[1]['segment_ms'] == 500
    assert results[1]['delta_ms'] == -9_500
    assert results[2]['verdict'] == 'red'
    assert results[2]['segment_ms'] == 15_000
    assert results[2]['delta_ms'] == 5_000
    assert results[3]['verdict'] == 'white'
    assert results[3]['segment_ms'] == 10_000
    assert results[3]['delta_ms'] == 0
    assert results[4]['verdict'] == 'gold'
    assert results[4]['segment_ms'] == 800
    assert results[4]['delta_ms'] == -9_200
    assert results[5]['verdict'] == 'white'

    # Blue needs a segment best faster than the PB segment.  Build it with a
    # short attempt that does not finish, then run the same segment slower
    # than that best but faster than the PB.
    runtime_d, shot_d = make_runtime(tmp_path, 'g4-jkl')
    start(runtime_d, shot_d, t=BASE + 3000)
    observe(runtime_d, shot_d, [split_record(1)], t=BASE + 3000.1)
    observe(runtime_d, shot_d,
            run_extra={'terminal_reason': 'screen_stalled'}, t=BASE + 3000.2)
    assert store.load_records(runtime_d)['pb']['attempt_id'] == 'a0001-g1-abc'
    assert store.load_records(runtime_d)['gold_segments_ms'][0] == 100

    runtime_e, shot_e = make_runtime(tmp_path, 'g5-mno')
    start(runtime_e, shot_e, t=BASE + 4000)
    blue = observe(runtime_e, shot_e, [split_record(1)], t=BASE + 4000.7)
    assert blue['splits'][-1]['verdict'] == 'blue'
    assert blue['splits'][-1]['segment_ms'] == 700
    assert blue['splits'][-1]['delta_ms'] == -9_300


def test_merged_split_marks_skipped_chapters_and_skips_gold(tmp_path):
    runtime, shot = make_runtime(tmp_path)
    start(runtime, shot)
    merged = observe(runtime, shot, [split_record(3)], t=BASE + 100)
    assert merged['current_index'] == 3
    split = merged['splits'][0]
    assert split['index'] == 0
    assert split['merged'] is True
    assert split['source'] == 'chapter_end_save'
    assert split['verdict'] == 'white'
    assert split['delta_ms'] is None
    records = store.load_records(runtime)
    assert records['gold_segments_ms'][:3] == [None, None, None]

    fallback = observe(runtime, shot, [chapter_seen(6)], t=BASE + 200)
    assert fallback['current_index'] == 5
    split = fallback['splits'][-1]
    assert split['index'] == 3
    assert split['merged'] is True
    assert split['source'] == 'fallback'
    assert split['verdict'] == 'white'
    records = store.load_records(runtime)
    assert records['gold_segments_ms'][:5] == [None] * 5

    # The fallback already closed chapter 5; a repeated chapter_seen 6 is a
    # duplicate and must not add another split.
    repeated = observe(runtime, shot, [chapter_seen(6)], t=BASE + 300)
    assert repeated['current_index'] == 5
    assert len(repeated['splits']) == 2


def test_fallback_single_segment_and_repeated_chapter_signal(tmp_path):
    runtime, shot = make_runtime(tmp_path)
    start(runtime, shot)
    fallback = observe(runtime, shot, [chapter_seen(2)], t=BASE + 50)
    assert fallback['current_index'] == 1
    assert fallback['splits'][0]['merged'] is False
    assert fallback['splits'][0]['source'] == 'fallback'

    first = observe(runtime, shot, [split_record(2)], t=BASE + 100)
    again = observe(runtime, shot, [split_record(2)], t=BASE + 100)
    assert first['current_index'] == 2
    assert again['current_index'] == 2
    assert again['splits'] == first['splits']


def test_dnf_game_over_stalled_and_stopped_keep_gold_but_not_pb(tmp_path):
    runtime, shot = make_runtime(tmp_path)
    full_run(runtime, shot)
    pb = store.load_records(runtime)['pb']
    assert pb['total_ms'] == 120_000

    runtime_b, shot_b = make_runtime(tmp_path, 'g2-def')
    start(runtime_b, shot_b, t=BASE + 1000)
    observe(runtime_b, shot_b, [split_record(1)], t=BASE + 1001)
    dnf = observe(runtime_b, shot_b,
                  run_extra={'terminal_reason': 'input_stalled'},
                  t=BASE + 1002)
    assert dnf['status'] == 'dnf'
    assert dnf['dnf_reason'] == 'stalled'
    assert dnf['pb_updated'] is False
    records = store.load_records(runtime_b)
    assert records['pb']['total_ms'] == 120_000
    assert records['gold_segments_ms'][0] == 1_000
    row = store.load_attempts(runtime_b)[-1]
    assert row['status'] == 'dnf'
    assert row['dnf_reason'] == 'stalled'

    runtime_c, shot_c = make_runtime(tmp_path, 'g3-ghi')
    start(runtime_c, shot_c, t=BASE + 2000)
    observe(runtime_c, shot_c, [split_record(1)], t=BASE + 2001)
    timer_before = store.load_timer(runtime_c)
    stopped = store.finalize_attempt(runtime_c, reason='operator_stop')
    assert stopped['status'] == 'dnf'
    assert stopped['dnf_reason'] == 'stopped'
    assert stopped['reason'] == 'operator_stop'
    assert store.load_records(runtime_c)['pb'] == records['pb']
    assert timer_before['status'] == 'running'
    assert store.load_attempts(runtime_c)[-1]['dnf_reason'] == 'stopped'


def test_recovery_closes_a_stale_running_attempt(tmp_path):
    runtime_a, shot_a = make_runtime(tmp_path, 'g1-abc')
    start(runtime_a, shot_a)
    observe(runtime_a, shot_a, [split_record(1)], t=BASE + 10)
    runtime_b, shot_b = make_runtime(tmp_path, 'g2-def')
    new = start(runtime_b, shot_b, t=BASE + 100)
    assert new['status'] == 'running'
    assert new['attempt_id'] == 'a0002-g2-def'
    # The recovered attempt is history; the running one is not written until
    # it ends (the timer is its only durable state).
    attempts = store.load_attempts(runtime_b)
    assert [row['attempt_id'] for row in attempts] == ['a0001-g1-abc']
    assert attempts[0]['status'] == 'dnf'
    assert attempts[0]['dnf_reason'] == 'stopped'
    assert attempts[0]['reason'] == 'recovered'
    # The stale timer was replaced by the running attempt of the new runtime.
    assert store.load_timer(runtime_b)['attempt_id'] == 'a0002-g2-def'


def test_same_runtime_name_screen_closes_a_stale_attempt(tmp_path):
    runtime, shot = make_runtime(tmp_path)
    start(runtime, shot)
    observe(runtime, shot, [split_record(1)], t=BASE + 10)
    # The game is back at name entry although gameplay had started: this is a
    # restart in the same runtime generation, not the original name screen.
    restarted = observe(
        runtime, shot,
        run_extra={'name_entered': True, 'phase': 'name',
                   'gameplay_seen': True},
        t=BASE + 100)
    assert restarted['status'] == 'running'
    assert restarted['attempt_id'] == 'a0002-g1-abc'
    rows = store.load_attempts(runtime)
    assert len(rows) == 1
    assert rows[0]['status'] == 'dnf'
    assert rows[0]['reason'] == 'recovered'


def test_idempotent_observation_and_monotonic_time(tmp_path):
    runtime, shot = make_runtime(tmp_path)
    start(runtime, shot)
    record = split_record(1)
    first = observe(runtime, shot, [record], t=BASE + 10)
    second = observe(runtime, shot, [record], t=BASE + 10)
    assert first['splits'] == second['splits']
    assert first['heartbeat_at_ms'] == second['heartbeat_at_ms']

    # The clock went backwards: the previous observation time is reused, so
    # the next segment cannot have a negative or rewound duration.
    rewound = observe(runtime, shot, [split_record(2)], t=BASE + 5)
    assert rewound['heartbeat_at_ms'] == BASE * 1000 + 10_000
    assert rewound['splits'][-1]['segment_ms'] == 0
    assert rewound['splits'][-1]['cumulative_ms'] == 10_000

    # An observation with nothing to do still refreshes only the heartbeat.
    idle = observe(runtime, shot, t=BASE + 20)
    assert idle['heartbeat_at_ms'] == BASE * 1000 + 20_000
    assert len(idle['splits']) == 2


def test_corrupt_records_are_quarantined_and_recording_continues(tmp_path):
    runtime, shot = make_runtime(tmp_path)
    directory = store.data_dir(runtime)
    directory.mkdir(parents=True)
    (directory / 'records.json').write_text('{not json', encoding='utf-8')
    final = full_run(runtime, shot)
    assert final['status'] == 'finished'
    corrupt = list(directory.glob('records.json.corrupt-*'))
    assert corrupt
    assert corrupt[0].read_text(encoding='utf-8') == '{not json'
    assert store.load_records(runtime)['pb']['total_ms'] == 120_000


def test_observe_never_raises_on_bad_input():
    assert hook.observe(None, None, None, None, screenshot=None) is None
    assert hook.observe(Path('/proc/1/no-such-runtime'), object(), object(),
                        {'hanjuku': object()}, screenshot=object()) is None
    assert timer.normalize_records({'pb': 'broken', 'gold_segments': 5})
