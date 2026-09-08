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
webui._enqueue_audio_text(root, "PAPER 模擬通知", "crypto_paper", delivery_key="crash-event")
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
