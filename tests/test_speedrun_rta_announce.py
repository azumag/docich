"""Measured split announcements for the hanjuku-speedrun RTA records."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import time

import pytest

from docich.hanjuku_speedrun.rta import announce, hook, store, timer

BASE_MS = 1_700_000_000_000
ROOT = Path(__file__).resolve().parents[1]
IDENTITY = {
    'game': 'hanjuku-speedrun',
    'runtime_id': 'g2-def456',
    'generation': 2,
    'lease_id': 'lease-2',
}


# ---------------------------------------------------------------------------
# real timer states -> sentences
# ---------------------------------------------------------------------------


def records_with(*, pb_segments=None, first_gold=None):
    raw = timer.default_records(BASE_MS)
    if first_gold is not None:
        raw['gold_segments'][0] = {
            'segment_ms': first_gold, 'attempt_id': 'a0001-g1-abc',
            'at_ms': BASE_MS,
        }
    if pb_segments is not None:
        raw['pb'] = {
            'attempt_id': 'a0001-g1-abc', 'attempt_no': 1,
            'total_ms': sum(pb_segments), 'segments_ms': list(pb_segments),
            'cumulative_ms': [], 'merged_ranges': [],
            'bot_version': 'b1', 'chart_edition': 'c1', 'at_ms': BASE_MS,
        }
    return timer.normalize_records(raw)


def running(records, *, runtime_id='g2-def456', attempt_no=2, start_ms=BASE_MS):
    value = timer.new_timer(runtime_id, records, start_ms)
    value, _ = timer.start_attempt(value, records, start_ms, attempt_no=attempt_no)
    return value


def confirmed_split(records, segment_ms, *, end=0, start_index=0, start_ms=BASE_MS):
    value = running(records, start_ms=start_ms)
    value['current_index'] = start_index
    value, _ = timer.confirm_split(
        value, records, start_ms + segment_ms, end, 'chapter_end_save')
    return value['splits'][-1]


@pytest.mark.parametrize(
    ('pb_segments', 'first_gold', 'segment_ms', 'verdict', 'needle'),
    [
        ([60_000], 50_000, 40_000, 'gold',
         '第1話を0分40秒で通過。区間ベスト更新です。PBより20秒先行です。'),
        ([60_000], 30_000, 40_000, 'blue',
         '第1話を0分40秒で通過。PBより20秒先行です。'),
        ([50_000], 30_000, 60_000, 'red',
         '第1話を1分00秒で通過。PBより10秒遅れです。'),
        ([50_000], 30_000, 50_000, 'white',
         '第1話を0分50秒で通過。PBと同じタイムです。'),
    ],
)
def test_split_sentences_use_the_measured_verdict(pb_segments, first_gold,
                                                  segment_ms, verdict, needle):
    split = confirmed_split(
        records_with(pb_segments=pb_segments, first_gold=first_gold),
        segment_ms)
    assert split['verdict'] == verdict
    assert split['delta_ms'] == segment_ms - pb_segments[0]
    assert announce.compose_split(split) == needle


def test_no_pb_never_invents_a_difference():
    # A DNF may already hold a segment best without any PB.
    split = confirmed_split(records_with(first_gold=10_000), 20_000)
    assert split['verdict'] == 'white'
    assert split['delta_ms'] is None
    assert announce.compose_split(split) == '第1話を0分20秒で通過。'


def test_merged_split_names_the_range_and_says_no_best():
    records = records_with(pb_segments=[60_000] * 5, first_gold=10_000)
    value = running(records)
    value['current_index'] = 2
    value, _ = timer.confirm_split(
        value, records, BASE_MS + 130_000, 4, 'chapter_end_save')
    split = value['splits'][-1]
    assert split['merged'] is True
    assert split['verdict'] == 'blue'
    assert announce.compose_split(split) == (
        '第3〜5話をまとめて2分10秒で通過。PBより50秒先行です。')


def test_finish_reports_clear_time_and_pb_update():
    records = timer.default_records(BASE_MS)
    value = running(records, runtime_id='g1-abc', attempt_no=1)
    for chapter in range(12):
        value, records = timer.confirm_split(
            value, records, BASE_MS + (chapter + 1) * 10_000, chapter,
            'chapter_end_save')
    value, _ = timer.finish(value, records, BASE_MS + 120_000)
    assert value['status'] == 'finished'
    assert announce.compose_finish(value) == (
        '12話を完走。クリアタイムは2分00秒。PBを更新しました。')


def test_finish_without_a_pb_update_says_so():
    records = records_with(pb_segments=[1_000] * 12)
    records['pb']['total_ms'] = 12_000
    records = timer.normalize_records(records)
    value = running(records)
    for chapter in range(12):
        value, _ = timer.confirm_split(
            value, records, BASE_MS + (chapter + 1) * 5_000, chapter,
            'chapter_end_save')
    value, _ = timer.finish(value, records, BASE_MS + 60_000)
    assert value['pb_updated'] is False
    assert announce.compose_finish(value) == (
        '12話を完走。クリアタイムは1分00秒。PB更新はありません。')


def test_dnf_names_the_running_chapter_and_a_fixed_reason():
    records = timer.default_records(BASE_MS)
    value = running(records, runtime_id='g1-abc', attempt_no=1)
    for chapter in range(3):
        value, records = timer.confirm_split(
            value, records, BASE_MS + (chapter + 1) * 10_000, chapter,
            'chapter_end_save')
    value, _ = timer.dnf(value, records, BASE_MS + 40_000, 'game_over',
                         reason='game_over')
    assert announce.compose_dnf(value) == (
        '第4話の途中で終了。理由はゲームオーバーです。')


# ---------------------------------------------------------------------------
# durable queue
# ---------------------------------------------------------------------------


def runtime_dir(tmp_path):
    path = Path(tmp_path) / 'run-soren-live' / 'runtimes' / 'g2-def456'
    path.mkdir(parents=True)
    return path


def queue_split(runtime, *, segment_ms=40_000, index=0, attempt_id='a0002-g2-def456',
                at_ms=None, now=None):
    split = {
        'index': index, 'end_index': index, 'segment_ms': segment_ms,
        'verdict': 'red', 'delta_ms': 10_000, 'merged': False,
    }
    return announce.split_event(
        runtime, split, attempt_id=attempt_id, identity=IDENTITY,
        at_ms=at_ms, now=now)


def test_same_split_is_spoken_once_even_after_a_restart(tmp_path, monkeypatch):
    runtime = runtime_dir(tmp_path)
    assert queue_split(runtime) is True
    assert queue_split(runtime) is False

    calls = []
    monkeypatch.setattr(announce, 'launch_pending', lambda *a, **k: calls.append(1) or True)
    status = announce.deliver_pending(
        runtime, now=time.time(),
        enqueue=lambda g, text, **kwargs: calls.append(text))
    assert status == 'enqueued'
    assert calls == ['第1話を0分40秒で通過。PBより10秒遅れです。']

    # A later bot process rereads the receipt instead of relying on memory.
    importlib.reload(announce)
    assert queue_split(runtime) is False
    assert announce.pending(runtime) == []


def test_a_stale_notification_is_dropped(tmp_path):
    runtime = runtime_dir(tmp_path)
    now = time.time()
    assert queue_split(runtime, at_ms=int(now * 1000) - 1_000) is True
    calls = []
    status = announce.deliver_pending(
        runtime, now=now + announce.MAX_AGE_S + 1,
        enqueue=lambda g, text, **kwargs: calls.append(text))
    assert status == 'idle'
    assert calls == []
    assert announce.pending(runtime) == []


def test_notifications_keep_the_existing_interval(tmp_path):
    runtime = runtime_dir(tmp_path)
    now = time.time()
    assert queue_split(runtime, index=0, at_ms=int(now * 1000), now=now) is True
    calls = []
    enqueue = lambda g, text, **kwargs: calls.append(text)
    assert announce.deliver_pending(runtime, now=now, enqueue=enqueue) == 'enqueued'
    assert queue_split(runtime, index=1, at_ms=int((now + 1) * 1000),
                       now=now + 1) is True
    assert announce.deliver_pending(runtime, now=now + 1, enqueue=enqueue) == 'cooldown'
    assert len(calls) == 1
    later = now + announce._cooldown_seconds() + 1
    assert announce.deliver_pending(runtime, now=later, enqueue=enqueue) == 'enqueued'
    assert len(calls) == 2


def test_a_stale_event_is_not_queued(tmp_path):
    runtime = runtime_dir(tmp_path)
    now = time.time()
    assert queue_split(runtime, at_ms=int((now - announce.MAX_AGE_S - 1) * 1000),
                       now=now) is False
    assert announce.pending(runtime) == []


def test_the_pending_queue_is_bounded(tmp_path):
    runtime = runtime_dir(tmp_path)
    now = time.time()
    for index in range(12):
        assert queue_split(runtime, index=index, at_ms=int(now * 1000)) is True
    items = announce.pending(runtime)
    assert len(items) == announce.MAX_PENDING
    # The newest events win; the four oldest must not clog the voice queue.
    assert items[0]['key'].endswith(':split:4:4')
    assert items[-1]['key'].endswith(':split:11:11')


def test_enqueue_failure_never_escapes(tmp_path):
    runtime = runtime_dir(tmp_path)
    assert queue_split(runtime) is True

    def broken(*args, **kwargs):
        raise RuntimeError('queue down')

    status = announce.deliver_pending(runtime, enqueue=broken)
    assert status == 'delivery_failed'
    # A transient failure is retried later, but the age bound still removes it.
    assert len(announce.pending(runtime)) == 1


def test_delivery_uses_the_copied_corner_narration_sink(tmp_path, monkeypatch):
    runtime = runtime_dir(tmp_path)
    assert queue_split(runtime) is True
    from docich.hanjuku_speedrun import hanjuku_narration

    calls = []

    def fake_deliver(g, runtime_path, item, speaker, enqueue, max_age):
        calls.append((g, runtime_path, item, speaker, enqueue, max_age))
        return 'enqueued'

    monkeypatch.setattr(hanjuku_narration, '_deliver', fake_deliver)
    g = SimpleNamespace(state_dir=runtime.parent.parent, repo_root=ROOT,
                        games_dir=ROOT / 'config' / 'games')
    status = announce.deliver_pending(
        runtime, now=time.time(), g=g, enqueue=None)
    assert status == 'enqueued'
    assert len(calls) == 1
    _, runtime_path, item, _, enqueue, max_age = calls[0]
    assert runtime_path == runtime
    assert item['text'] == '第1話を0分40秒で通過。PBより10秒遅れです。'
    assert item['game'] == 'hanjuku-speedrun'
    assert item['terminal_recap'] is True
    assert item['at'] > 0
    assert item['key'].endswith(':split:0:0')
    assert callable(enqueue)
    assert max_age == announce.MAX_AGE_S


def test_launcher_failure_never_escapes(tmp_path, monkeypatch):
    runtime = runtime_dir(tmp_path)
    assert queue_split(runtime) is True

    def broken(*args, **kwargs):
        raise OSError('no process')

    monkeypatch.setattr(announce.subprocess, 'Popen', broken)
    assert announce.launch_pending(runtime) is False


# ---------------------------------------------------------------------------
# hook integration
# ---------------------------------------------------------------------------


def make_hook_runtime(tmp_path):
    state_dir = tmp_path / 'run-soren-live'
    runtime = state_dir / 'runtimes' / 'g2-def456'
    runtime.mkdir(parents=True)
    shot = runtime / 'screenshots' / 'latest.png'
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b'frame')
    return runtime, shot


def hook_observe(runtime, shot, records=None, *, at, run_extra=None):
    os.utime(shot, (at, at))
    run = {
        'game': 'hanjuku-speedrun', 'runtime_id': 'g2-def456',
        'generation': 2, 'lease_id': 'lease-2',
        'name_entered': False, 'clear_ending_seen': False,
        'terminal_reason': None, 'phase': 'title', 'gameplay_seen': False,
    }
    run.update(run_extra or {})
    return hook.observe(
        runtime, records or [], {}, {'hanjuku': run,
                                     'terminal_reason': run.get('terminal_reason')},
        screenshot=shot)


def test_hook_records_a_confirmed_split_and_launches_delivery(tmp_path, monkeypatch):
    runtime, shot = make_hook_runtime(tmp_path)
    launches = []
    monkeypatch.setattr(announce, 'launch_pending',
                        lambda *a, **k: launches.append(1) or True)
    base = time.time() - 20
    hook_observe(runtime, shot, at=base,
                 run_extra={'name_entered': True, 'phase': 'name'})
    hook_observe(
        runtime, shot,
        [{'decision': 'prompt', 'strategy_variant': 'chapter_end_save',
          'chapter': 1}],
        at=base + 10)
    items = announce.pending(runtime)
    assert [item['text'] for item in items] == [
        '第1話を0分10秒で通過。区間ベスト更新です。']
    assert launches


def test_hook_records_finish_and_dnf(tmp_path, monkeypatch):
    runtime, shot = make_hook_runtime(tmp_path)
    monkeypatch.setattr(announce, 'launch_pending', lambda *a, **k: True)
    base = time.time() - 20
    hook_observe(runtime, shot, at=base,
                 run_extra={'name_entered': True, 'phase': 'name'})
    for chapter in range(1, 12):
        hook_observe(
            runtime, shot,
            [{'decision': 'prompt', 'strategy_variant': 'chapter_end_save',
              'chapter': chapter}],
            at=base + chapter)
    hook_observe(runtime, shot, at=base + 13,
                 run_extra={'clear_ending_seen': True})
    texts = [item['text'] for item in announce.pending(runtime)]
    # The terminal sentence is queued ahead of the final segment so a quick
    # teardown cannot defer the run result behind segment detail.
    finish_at = next(i for i, text in enumerate(texts) if text.startswith('12話を完走。'))
    final_split_at = next(i for i, text in enumerate(texts) if text.startswith('第12話を'))
    assert finish_at < final_split_at

    runtime2, shot2 = make_hook_runtime(tmp_path / 'dnf')
    hook_observe(runtime2, shot2, at=base,
                 run_extra={'name_entered': True, 'phase': 'name'})
    hook_observe(
        runtime2, shot2,
        [{'decision': 'prompt', 'strategy_variant': 'chapter_end_save',
          'chapter': 1}],
        at=base + 10)
    hook_observe(runtime2, shot2, at=base + 11,
                 run_extra={'terminal_reason': 'game_over'})
    texts = [item['text'] for item in announce.pending(runtime2)]
    assert '第2話の途中で終了。理由はゲームオーバーです。' in texts


def test_teardown_finalize_queues_the_terminal_sentence(tmp_path, monkeypatch):
    """A terminal the bot never observed still gets its DNF sentence."""
    monkeypatch.setattr(announce, 'launch_pending', lambda *a, **k: True)
    start = int(time.time() * 1000) - 20_000
    data = tmp_path / 'run-local' / 'speedrun' / 'hanjuku-speedrun'
    data.mkdir(parents=True)
    monkeypatch.setenv('DOCICH_SPEEDRUN_DATA_DIR', str(data))
    monkeypatch.setenv('DOCICH_SPEEDRUN_ENV', 'local')
    runtime = tmp_path / 'run-local' / 'runtimes' / 'g2-def456'
    runtime.mkdir(parents=True)
    records = timer.default_records(start)
    value = running(records, runtime_id='g2-def456', start_ms=start)
    value['heartbeat_at_ms'] = start + 10_000
    store.save_timer(runtime, value)
    store.save_records(runtime, records)
    (runtime / 'hanjuku_run.json').write_text(json.dumps({
        **IDENTITY, 'observed_at_ms': start + 11_000, 'phase': 'field',
        'terminal_reason': 'game_over',
    }), encoding='utf-8')

    saved = store.finalize_attempt(runtime)

    assert saved['status'] == 'dnf'
    assert saved['dnf_reason'] == 'game_over'
    assert saved['ended_at_ms'] == start + 11_000
    assert [item['text'] for item in announce.pending(runtime)] == [
        '第1話の途中で終了。理由はゲームオーバーです。']
    # A repeated finalize (teardown retried) must not queue it twice.
    store.finalize_attempt(runtime)
    assert len(announce.pending(runtime)) == 1


def test_hook_never_fails_when_announcement_delivery_breaks(tmp_path, monkeypatch):
    runtime, shot = make_hook_runtime(tmp_path)

    def broken(*args, **kwargs):
        raise RuntimeError('queue down')

    monkeypatch.setattr(announce, 'split_event', broken)
    monkeypatch.setattr(announce, 'launch_pending', broken)
    base = time.time() - 20
    hook_observe(runtime, shot, at=base,
                 run_extra={'name_entered': True, 'phase': 'name'})
    result = hook_observe(
        runtime, shot,
        [{'decision': 'prompt', 'strategy_variant': 'chapter_end_save',
          'chapter': 1}],
        at=base + 10)
    # The timer record still commits; only the side channel is missing.
    assert result['current_index'] == 1
    assert result['status'] == 'running'


def test_default_config_follows_the_runtime_owner_state_dir():
    # The production corner is started with an explicit profile; the bot
    # process does not inherit that argument, so resolve by state dir.
    runtime = ROOT / 'run-soren-live' / 'runtimes' / 'g1-abc123'
    g = announce._default_config(runtime)
    assert g is not None
    assert Path(g.state_dir).resolve() == (ROOT / 'run-soren-live').resolve()


def test_original_narration_never_imports_the_speedrun_rta_package():
    code = (
        'import sys\n'
        'import docich.hanjuku_narration\n'
        'assert not [name for name in sys.modules '
        "if name.startswith('docich.hanjuku_speedrun.rta')]\n"
    )
    env = dict(os.environ)
    env['PYTHONPATH'] = str(ROOT / 'src')
    proc = subprocess.run([sys.executable, '-c', code], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-1000:]
