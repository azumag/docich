"""The shared comment/audio queue path and backlog contract (#2236)."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich.comment_queue import backlog_counts, comment_queue_dir, is_quiet


def test_queue_dir_follows_the_soren_root_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv('COMMENT_QUEUE_DIR', raising=False)
    assert comment_queue_dir(tmp_path) == tmp_path / 'tmp' / '.comment_queue'


def test_queue_dir_honors_an_absolute_and_a_relative_override(tmp_path, monkeypatch):
    absolute = tmp_path / 'elsewhere' / 'queue'
    monkeypatch.setenv('COMMENT_QUEUE_DIR', str(absolute))
    assert comment_queue_dir(tmp_path) == absolute
    # The shell sink runs with the Soren root as cwd, so a relative value is
    # resolved below it.
    monkeypatch.setenv('COMMENT_QUEUE_DIR', 'tmp/custom_queue')
    assert comment_queue_dir(tmp_path) == tmp_path / 'tmp' / 'custom_queue'


def test_queue_dir_reads_the_soren_env_like_the_shell_sink(tmp_path, monkeypatch):
    monkeypatch.delenv('COMMENT_QUEUE_DIR', raising=False)
    (tmp_path / '.env').write_text(
        '# comment\nSECRET_KEY=DO-NOT-USE\nCOMMENT_QUEUE_DIR="tmp/env_queue" # trailing\n')
    assert comment_queue_dir(tmp_path) == tmp_path / 'tmp' / 'env_queue'


def test_environment_wins_over_the_soren_env(tmp_path, monkeypatch):
    (tmp_path / '.env').write_text('COMMENT_QUEUE_DIR=tmp/env_queue\n')
    monkeypatch.setenv('COMMENT_QUEUE_DIR', 'tmp/env_queue_override')
    assert comment_queue_dir(tmp_path) == tmp_path / 'tmp' / 'env_queue_override'


def test_missing_queue_is_quiet(tmp_path, monkeypatch):
    monkeypatch.delenv('COMMENT_QUEUE_DIR', raising=False)
    queue = tmp_path / 'tmp' / '.comment_queue'
    assert backlog_counts(queue) is None
    assert is_quiet(queue)


def test_backlog_counts_only_queue_items(tmp_path):
    queue = tmp_path / 'queue'
    queue.mkdir()
    (queue / 'comment_announce_1_2_3_chat.txt').write_text('viewer comment')
    (queue / 'comment_announce_1_2_3_chat.txt.speaker').write_text('speaker')
    (queue / 'comment_announce_4_5_6_yosou:intro.playing').write_text('intro')
    (queue / 'comment_announce_4_5_6_yosou:intro.mode').write_text('mode')
    (queue / 'radio_1_2_news_3.txt').write_text('radio lives in its own queue')
    (queue / 'processed_batch_hashes.log').write_text('log')
    (queue / 'spoken_history').mkdir()
    (queue / 'comment_announce_7_8_9_chat.txt').mkdir()
    assert backlog_counts(queue) == (1, 1)
    assert not is_quiet(queue)


def test_backlog_counts_ignores_a_broken_queue(tmp_path):
    queue = tmp_path / 'queue'
    queue.write_text('not a directory')
    assert backlog_counts(queue) is None
    assert is_quiet(queue)
