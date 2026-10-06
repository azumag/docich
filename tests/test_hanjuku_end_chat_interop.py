"""Keep repeated recap passages intact through the real shared shell queue."""
import datetime as dt
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from docich import config
from docich.retro_corner import RetroCornerConfig, RetroCornerManager, _end_result_chat_parts

REPO = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("SOREN_CHAT_QUEUE_SOURCE", REPO / "games/soviet_now"))


@pytest.mark.parametrize('exclude_mirror', [False, True])
def test_repeated_end_passages_keep_run_part_identity_and_full_text(tmp_path, monkeypatch, exclude_mirror):
    # Only copy the queue helper and use synthetic senders. No network sender,
    # chat worker, or live queue is started; everything stays in this temp root.
    sink = tmp_path / 'sink'
    (sink / 'lib').mkdir(parents=True)
    shutil.copyfile(SOURCE / 'lib/outbound_queue.sh',
                    sink / 'lib/outbound_queue.sh')
    queue = sink / 'queue'
    monkeypatch.setenv('OUTBOUND_CHAT_QUEUE_DIR', str(queue))
    monkeypatch.setenv('OUTBOUND_CHAT_ENQUEUE_DEDUP_TTL_SEC', '300')
    monkeypatch.setenv('OUTBOUND_CHAT_PAUSE_MARKER', str(sink / 'not-paused'))
    monkeypatch.setenv('OUTBOUND_CHAT_YOUTUBE_MIRROR_ENABLED', '1')
    monkeypatch.setenv('OUTBOUND_CHAT_YOUTUBE_MIRROR_EXCLUDE_SOURCES',
                       'other retro-corner' if exclude_mirror else '')
    monkeypatch.setenv('YOUTUBE_OAUTH_CLIENT_ID', 'synthetic-client')
    monkeypatch.setenv('YOUTUBE_OAUTH_CLIENT_SECRET', 'synthetic-secret')
    monkeypatch.setenv('YOUTUBE_OAUTH_REFRESH_TOKEN', 'synthetic-refresh')
    for destination in ('twitch', 'youtube'):
        script = sink / f'{destination}_chat.sh'
        script.write_text(f'#!/bin/bash\n[ "$1" = send ] || exit 1\nprintf "%s\\n" "$2" >> {destination}.log\n')
        script.chmod(0o755)
    cfg = tmp_path / 'docich.toml'
    cfg.write_text(f'[webui]\nsoren_root = "{sink}"\n', encoding='utf-8')
    g = config.load_global(tmp_path, config_path=cfg)
    manager = RetroCornerManager(g, config=RetroCornerConfig(), coordinator=SimpleNamespace())
    text = 'どうし将軍のジョンリギ城への出撃が確認されました。' * 12
    expected = _end_result_chat_parts(text)
    assert len(expected) == 3 and expected[0] == expected[1]
    monkeypatch.setattr(manager, '_end_result_text', lambda state, completed_at: text)
    now = dt.datetime(2026, 10, 6, tzinfo=dt.timezone.utc)
    state = {
        'game': 'hanjuku-hero', 'started_at': now.isoformat(),
        'bot_identity': {'game': 'hanjuku-hero', 'runtime_id': 'g1-abcdef',
                         'generation': 1, 'lease_id': 'lease-1'},
    }
    manager._announce_end_result_locked(state, now)
    posts = sorted((queue / 'pending').glob('*.msg'))
    assert all(p.name.endswith('_retro-corner_5.msg') for p in posts)
    assert [p.read_text(encoding='utf-8').removesuffix('\n') for p in posts] == expected
    assert ''.join(p.read_text(encoding='utf-8').removesuffix('\n') for p in posts) == text
    assert all(len(p.read_text().removesuffix('\n').encode('utf-8')) <= 430 for p in posts)
    assert state['end_announced'] and 'end_announce_error' not in state

    # A replay whose state receipt was lost still reuses the same queue keys.
    state.pop('end_announced')
    manager._announce_end_result_locked(state, now)
    assert sorted((queue / 'pending').glob('*.msg')) == posts

    # Another run must not lose its identical story to the previous run's TTL.
    next_state = {**state, 'end_announced': False,
                  'bot_identity': {**state['bot_identity'], 'generation': 2,
                                   'runtime_id': 'g2-abcdef', 'lease_id': 'lease-2'}}
    manager._announce_end_result_locked(next_state, now)
    all_posts = sorted((queue / 'pending').glob('*.msg'))
    assert len(all_posts) == 6
    assert [p.read_text(encoding='utf-8').removesuffix('\n') for p in all_posts] == expected * 2
    consumed = subprocess.run(
        ['bash', '-c', 'source lib/outbound_queue.sh; '
         'for n in 1 2 3 4 5 6; do outbound_queue_consume_once || exit $?; done'],
        cwd=sink, capture_output=True, text=True, timeout=10,
    )
    assert consumed.returncode == 0, consumed.stderr
    assert (sink / 'twitch.log').read_text(encoding='utf-8').splitlines() == expected * 2
    if exclude_mirror:
        assert not (sink / 'youtube.log').exists()
    else:
        mirrored = (sink / 'youtube.log').read_text(encoding='utf-8').splitlines()
        assert ''.join(mirrored) == text * 2
        assert all(len(part.encode('utf-8')) <= 200 for part in mirrored)
