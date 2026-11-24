"""Real Python-bridge -> real Bash-sink regressions for TRPG narration (#2548).

PR #2552's first independent review (5576015382) found that argument-forwarding
tests can pass while the real sink refuses every TRPG line.  These regressions
run the production bridge (``soren_output.enqueue_audio_text``) against the
real ``lib/outbound_queue.sh`` sink with an isolated queue directory: the
normal accept, the refusal cases, and the scoped same-text contract (a
same-occurrence retry is suppressed while the same text in a later scene or
run is accepted).
"""
import json
import os
from pathlib import Path
import shutil
import time

import pytest

from docich import config
from docich.trading import soren_output

REPO = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("SOREN_TRPG_SINK_SOURCE", REPO / "games/soviet_now"))


def _bridge_root(tmp_path, monkeypatch):
    queue_helper = SOURCE / 'lib' / 'outbound_queue.sh'
    fence_helper = SOURCE / 'lib' / 'runtime_audio_fence.py'
    if not queue_helper.is_file() or not fence_helper.is_file():
        pytest.skip(
            f"native sink helpers が不在のため読み飛ばし: {SOURCE} "
            "(git submodule 未取得の hermetic checkout では正常。取得時は "
            "SOREN_TRPG_SINK_SOURCE で上書き可)"
        )
    sink = tmp_path / 'sink'
    (sink / 'lib').mkdir(parents=True)
    shutil.copyfile(queue_helper, sink / 'lib' / 'outbound_queue.sh')
    shutil.copyfile(fence_helper, sink / 'lib' / 'runtime_audio_fence.py')
    queue = tmp_path / 'queue'
    queue.mkdir()
    monkeypatch.setenv('COMMENT_QUEUE_DIR', str(queue))
    monkeypatch.setenv('COMMENT_AUDIO_DEDUP_TTL_SEC', '120')
    cfg = tmp_path / 'docich.toml'
    cfg.write_text(f'[webui]\nsoren_root = "{sink}"\n', encoding='utf-8')
    g = config.load_global(tmp_path, config_path=cfg)
    return sink, queue, g


def _fence():
    return {'game': 'trpg-view', 'runtime_id': 'g7-abcdef12', 'generation': 4,
            'lease_id': 'lease-4', 'expires_at': time.time() + 60}


def _items(queue):
    return sorted(queue.glob('*.txt'))


def test_real_bridge_enqueues_a_fenced_trpg_line(tmp_path, monkeypatch):
    _sink, queue, g = _bridge_root(tmp_path, monkeypatch)
    soren_output.enqueue_audio_text(
        g, 'シーン「城の地下」です。', context='trpg:scene', speaker='7',
        runtime_fence=_fence(), dedup_scope='trpg:g7-abcdef12:4:SCENE:123')
    items = _items(queue)
    assert len(items) == 1
    assert items[0].read_text(encoding='utf-8').strip() == 'シーン「城の地下」です。'
    base = items[0].with_suffix('')
    fence = json.loads(Path(f'{base}.runtime_fence.json').read_text())
    assert fence['game'] == 'trpg-view' and fence['generation'] == 4
    dedup_key = Path(f'{base}.dedup_key')
    assert dedup_key.is_file() and dedup_key.read_text().strip()
    assert Path(f'{items[0]}.speaker').read_text() == '7'


def test_real_bridge_rejects_unfenced_and_wrong_namespace(tmp_path, monkeypatch):
    _sink, queue, g = _bridge_root(tmp_path, monkeypatch)
    # A TRPG context is runtime-bound: without a fence the sink refuses the line.
    with pytest.raises(soren_output.SorenOutputError):
        soren_output.enqueue_audio_text(g, 'x', context='trpg:scene')
    # A fence from another game's namespace is refused as well.
    with pytest.raises(soren_output.SorenOutputError):
        soren_output.enqueue_audio_text(
            g, 'x', context='trpg:scene',
            runtime_fence={**_fence(), 'game': 'hanjuku-hero'})
    assert _items(queue) == []


def test_real_bridge_keeps_same_occurrence_retry_and_other_scene(tmp_path, monkeypatch):
    _sink, queue, g = _bridge_root(tmp_path, monkeypatch)
    fence = _fence()
    text = '同じ文'
    # The same occurrence retried stays one queue item; the same text in a
    # later scene (a different scope identity, as a later run would also
    # produce) is accepted instead of dropped by the global text hash.
    for scope in ('trpg:g7-abcdef12:4:SCENE:1',
                  'trpg:g7-abcdef12:4:SCENE:1',
                  'trpg:g7-abcdef12:4:CHOICE:2'):
        soren_output.enqueue_audio_text(
            g, text, context='trpg:scene',
            runtime_fence=fence, dedup_scope=scope)
    items = _items(queue)
    assert len(items) == 2
    keys = {Path(f'{item.with_suffix("")}.dedup_key').read_text().strip()
            for item in items}
    assert len(keys) == 2
