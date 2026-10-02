"""Process-crash and concurrent delivery regression tests (no live output)."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import webui


CRASH = r"""
import os, sys
from pathlib import Path
from docich import webui
root, phase = Path(sys.argv[1]), sys.argv[2]
source = sys.argv[3] if len(sys.argv) > 3 else "crypto_paper"
original = os.replace
def replace(src, dst):
    publishing = Path(dst).name.startswith("comment_announce_")
    if phase == "before_publish" and publishing:
        os._exit(81)
    result = original(src, dst)
    if phase == "prepared" and Path(dst).parent.name == "audio_delivery_dedup":
        os._exit(81)
    if phase == "published" and publishing:
        Path(dst).unlink()  # consumer finishes before ACK
        os._exit(81)
    return result
webui.os.replace = replace
webui._enqueue_audio_text(root, "PAPER 模擬通知", source, delivery_key="crash-event")
"""


def child_env():
    return dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))


@pytest.mark.parametrize("phase", ["prepared", "before_publish", "published"])
def test_process_death_recovers_or_deduplicates(tmp_path, monkeypatch, phase):
    child = subprocess.run([sys.executable, "-c", CRASH, str(tmp_path), phase], env=child_env(), timeout=10)
    assert child.returncode == 81
    monkeypatch.setattr(webui.time, "time", lambda: 2000000000)
    result = webui._enqueue_audio_text(tmp_path, "PAPER 模擬通知", "crypto_paper", delivery_key="crash-event")
    assert result["dedup"] is (phase == "published")
    queue = tmp_path / "tmp/.comment_queue"
    assert len(list(queue.glob("comment_announce_*.txt"))) == (0 if phase == "published" else 1)
    assert webui._enqueue_audio_text(tmp_path, "PAPER 模擬通知", "crypto_paper", delivery_key="crash-event")["dedup"]


@pytest.mark.parametrize("phase", ["prepared", "before_publish", "published"])
def test_hanjuku_terminal_receipt_recovers_after_process_death(tmp_path, monkeypatch, phase):
    child = subprocess.run(
        [sys.executable, "-c", CRASH, str(tmp_path), phase, "hanjuku_terminal"],
        env=child_env(), timeout=10,
    )
    assert child.returncode == 81
    monkeypatch.setattr(webui.time, "time", lambda: 2000000000)
    result = webui._enqueue_audio_text(
        tmp_path, "PAPER 模擬通知", "hanjuku_terminal", delivery_key="crash-event"
    )
    assert result["dedup"] is (phase == "published")
    queue = tmp_path / "tmp/.comment_queue"
    assert len(list(queue.glob("comment_announce_*_hanjuku_terminal.txt"))) == (
        0 if phase == "published" else 1
    )


def test_concurrent_same_event_publishes_one_file(tmp_path):
    code = "from pathlib import Path; from docich import webui; import sys; webui._enqueue_audio_text(Path(sys.argv[1]), 'PAPER 模擬通知', 'crypto_paper', delivery_key='concurrent-event')"
    children = [subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], env=child_env()) for _ in range(8)]
    try:
        assert [child.wait(timeout=15) for child in children] == [0] * 8
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()
    assert len(list((tmp_path / "tmp/.comment_queue").glob("comment_announce_*.txt"))) == 1


def test_legacy_marker_does_not_silently_acknowledge(tmp_path):
    marker = webui._comment_audio_delivery_dir(tmp_path) / hashlib.sha256(b"legacy").hexdigest()
    marker.mkdir(parents=True)
    (marker / "event_id").write_text("legacy\n")
    with pytest.raises(RuntimeError, match="ambiguous"):
        webui._enqueue_audio_text(tmp_path, "PAPER 模擬通知", "crypto_paper", delivery_key="legacy")
    assert not list((tmp_path / "tmp/.comment_queue").glob("comment_announce_*.txt"))


def test_distinct_events_with_same_clock_do_not_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(webui.time, "time_ns", lambda: 123456789)
    for key in ("first", "second"):
        webui._enqueue_audio_text(tmp_path, "PAPER " + key, "crypto_paper", delivery_key=key)
    assert {p.read_text().strip() for p in (tmp_path / "tmp/.comment_queue").glob("comment_announce_*.txt")} == {"PAPER first", "PAPER second"}


def test_delivery_speaker_sidecar_selects_voice_without_breaking_dedupe(tmp_path):
    voiced = webui._enqueue_audio_text(
        tmp_path, "声つき通知", "crypto_paper", speaker="14", delivery_key="voiced-event"
    )
    assert voiced["ok"] is True
    sidecar = Path(str(tmp_path / "tmp/.comment_queue" / voiced["filename"]) + ".speaker")
    assert sidecar.read_text(encoding="utf-8") == "14"
    plain = webui._enqueue_audio_text(
        tmp_path, "声なし通知", "crypto_paper", delivery_key="plain-event"
    )
    assert not Path(str(tmp_path / "tmp/.comment_queue" / plain["filename"]) + ".speaker").exists()
    # Same event redelivers nothing, regardless of speaker.
    assert webui._enqueue_audio_text(
        tmp_path, "声つき通知", "crypto_paper", speaker="14", delivery_key="voiced-event"
    )["dedup"] is True
    with pytest.raises(ValueError, match="durable audio sources"):
        webui._enqueue_audio_text(tmp_path, "他ソース", "webui_test", delivery_key="other-source")


def test_hanjuku_terminal_has_a_dedicated_durable_source_and_nonblocking_lock(tmp_path):
    import fcntl

    text = "確認記録から作った終了の振り返りです。" * 8
    delivery_key = "hanjuku-terminal:run-identity-hash"
    first = webui._enqueue_audio_text(
        tmp_path, text, "hanjuku_terminal", delivery_key=delivery_key
    )
    assert first["ok"] is True and first["dedup"] is False
    assert first["filename"].endswith("_hanjuku_terminal.txt")
    assert webui._enqueue_audio_text(
        tmp_path, text, "hanjuku_terminal", delivery_key=delivery_key
    )["dedup"] is True
    paper = webui._enqueue_audio_text(
        tmp_path, text, "crypto_paper", delivery_key=delivery_key
    )
    assert paper["ok"] is True and paper["dedup"] is False
    assert paper["filename"].endswith("_crypto_paper.txt")
    with pytest.raises(ValueError, match="hanjuku_terminal requires"):
        webui._enqueue_audio_text(tmp_path, text, "hanjuku_terminal")

    receipts = webui._comment_audio_delivery_dir(tmp_path)
    with (receipts / ".publish.lock").open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            webui._enqueue_audio_text(
                tmp_path, "retry later", "hanjuku_terminal",
                delivery_key="hanjuku-terminal:another-run",
            )
    with pytest.raises(ValueError, match="durable audio sources"):
        webui._enqueue_audio_text(
            tmp_path, "not allowed", "webui_test", delivery_key="fixed-key"
        )


def test_hanjuku_terminal_outbox_file_is_consumed_by_real_comment_consumer(tmp_path, monkeypatch):
    """Join docich's writer to Soren's consumer with a stub audio player."""
    repo = Path(__file__).resolve().parents[1]
    soren_root = Path(os.environ.get("DOCICH_TEST_SOREN_ROOT", repo / "games/soviet_now"))
    if not (soren_root / "broadcast/comment_lib.sh").is_file():
        pytest.skip("Soren submodule is not initialized in this checkout")

    queue = tmp_path / "comment_queue"
    monkeypatch.setenv("COMMENT_QUEUE_DIR", str(queue))
    message = "終了要約の一度だけの配送を確認します。"
    result = webui._enqueue_audio_text(
        soren_root, message, "hanjuku_terminal",
        delivery_key="hanjuku-terminal:consumer-integration-run",
    )
    queued, = queue.glob("*_hanjuku_terminal.txt")
    assert result["filename"] == queued.name

    # Make the ordinary content-hash guard think this exact line was already
    # spoken. The run-keyed terminal source must still reach the player.
    played_hashes = tmp_path / "played_hashes.txt"
    played_hashes.write_text(hashlib.md5((message + "\n").encode()).hexdigest() + "\n")
    spoken = tmp_path / "spoken.txt"
    player = tmp_path / "say_enqueue.sh"
    player.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$CONSUMED"\n')
    player.chmod(0o755)
    env = dict(os.environ,
               ELOOP_LIB_DIR=str(soren_root), COMMENT_QUEUE_DIR=str(queue),
               COMMENT_PLAYED_HASHES_FILE=str(played_hashes),
               COMMENT_VIEWER_MEMORY_ENABLED="0", COMMENT_SPOKEN_HISTORY_DIR=str(tmp_path / "history"),
               COMMENT_SPOKEN_HISTORY_MAX_FILES="10", CONSUMED=str(spoken))
    script = r'''
set -u
mkdir -p tmp/.say_queue tmp/.comment_queue
_cp_my_pid=test
RADIO_SAY_RATE=""
log() { :; }
_clean_comment_talk() { cat; }
_sanitize_onair_text() { cat; }
_broadcast_read_expected_mode() { :; }
_broadcast_host_mode() { printf '%s' main; }
_broadcast_clear_expected_mode() { :; }
_play_deferred_radio_queue_once() { :; }
source "$ELOOP_LIB_DIR/broadcast/comment.sh"
source "$ELOOP_LIB_DIR/broadcast/comment_lib.sh"
test "$(_comment_playback_context_label "$TERMINAL_QUEUE_ITEM")" = hanjuku_terminal
test "$(_comment_playback_overlay_title hanjuku_terminal)" = "半熟英雄終了結果 playback"
COMMENT_PLAYED_HASHES_FILE="$PLAYED_HASHES"
_play_comment_queue
'''
    env["PLAYED_HASHES"] = str(played_hashes)
    env["TERMINAL_QUEUE_ITEM"] = str(queued)
    proc = subprocess.run(["bash", "-c", script], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    assert spoken.exists(), "consumer should pass the terminal event to the stub player"
    assert "--no-preempt" in spoken.read_text()
    assert not queued.exists(), "consumer should claim and remove the queue file"
