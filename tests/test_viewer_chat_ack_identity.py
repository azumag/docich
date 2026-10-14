"""Viewer chat pending-ack identity contract, enforced from docich (#829).

Every viewer chat source keeps provider-original rows in ``<CHAT_DIR>/pending.log``
(``id=<message-id>\\t...\\t<text>``) while the model-facing OUTFILE is NFKC
normalized by ``lib/comment_viewer_memory.py emit-batch``.  An acknowledgement
that only compares bytes therefore removes nothing, the row stays pending
forever, and once ``COMMENT_PROCESSED_LINES_TTL`` (1800s) expires the same
comment is generated and spoken again - measured on the production VM
2026-10-01: one YouTube comment answered 10 times, 30 minutes apart.

``twitch_chat.sh`` and ``kick_chat.sh`` already acknowledged by message id with
an NFKC text fallback; ``youtube_chat.sh`` was the last holdout and is fixed in
soviet_now #553.  These tests run the real scripts so a future source cannot
reintroduce a byte-exact ack in silence.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOVIET_NOW = ROOT / "games/soviet_now"

# script -> (chat dir env var, acknowledged message id, a second id that is not a
# substring of the first so "kept" assertions cannot pass by accident)
SOURCES = {
    "twitch_chat.sh": ("TWITCH_CHAT_DIR", "msg-1", "msg-2"),
    "youtube_chat.sh": (
        "YOUTUBE_CHAT_DIR",
        "LCC.EhwKGkNOMnNpZXU2bUpjREZYSEE1d01kajBZOUJB",
        "LCC.Zm9vYmFyQmF6cXV4OTg3NjU0MzIxMGFiY2RlZmdoaWo",
    ),
    "kick_chat.sh": ("KICK_CHAT_DIR", "msg-1", "msg-2"),
}


def _row(message_id: str, display: str, text: str) -> str:
    return (
        f"id={message_id}\tuser-id=uid-1\tlogin=alice\tdisplay={display}\tflags=\t{display}: {text}\n"
    )


def _ack(script: str, chat_dir: Path, batch: Path, extra_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env_name = SOURCES[script][0]
    env = os.environ.copy()
    env.update({env_name: str(chat_dir), "CHAT_INGEST_OVERLAY_NOTIFY": "0", **extra_env})
    return subprocess.run(
        ["bash", str(SOVIET_NOW / script), "ack-batch", str(batch)],
        cwd=SOVIET_NOW,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _require_submodule() -> None:
    if not (SOVIET_NOW / "youtube_chat.sh").is_file():
        pytest.skip("games/soviet_now not checked out")


@pytest.mark.parametrize("script", sorted(SOURCES))
def test_plain_ack_normalizes_full_width_punctuation(script, tmp_path):
    """The production regression: a normalized batch must still drain the row."""
    _require_submodule()
    message_id = SOURCES[script][1]
    chat_dir = tmp_path / "chat"
    chat_dir.mkdir()
    pending = chat_dir / "pending.log"
    pending.write_text(_row(message_id, "Alice", "わ～！（5回目）"), encoding="utf-8")
    batch = tmp_path / "batch.txt"
    batch.write_text("Alice: わ~!(5回目)\n", encoding="utf-8")

    result = _ack(script, chat_dir, batch, {})
    assert result.returncode == 0, result.stderr
    assert pending.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("script", sorted(SOURCES))
def test_message_id_ack_removes_only_the_acknowledged_row(script, tmp_path):
    """Two viewers posting the same text must not be acked as one."""
    _require_submodule()
    message_id, other_id = SOURCES[script][1], SOURCES[script][2]
    chat_dir = tmp_path / "chat"
    chat_dir.mkdir()
    pending = chat_dir / "pending.log"
    pending.write_text(
        _row(message_id, "Alice", "わ～！（5回目）") + _row(other_id, "Bob", "わ～！（5回目）"),
        encoding="utf-8",
    )
    batch = tmp_path / "batch.txt"
    batch.write_text(f"id={message_id}\tAlice: わ~!(5回目)\n", encoding="utf-8")

    result = _ack(script, chat_dir, batch, {})
    assert result.returncode == 0, result.stderr
    kept = pending.read_text(encoding="utf-8")
    assert message_id not in kept
    assert other_id in kept


@pytest.mark.parametrize("script", sorted(SOURCES))
def test_unmatched_batch_keeps_pending(script, tmp_path):
    """An unrelated batch is a no-op, never a silent drop of unread comments."""
    _require_submodule()
    message_id = SOURCES[script][1]
    chat_dir = tmp_path / "chat"
    chat_dir.mkdir()
    pending = chat_dir / "pending.log"
    original = _row(message_id, "Alice", "別のコメント")
    pending.write_text(original, encoding="utf-8")
    batch = tmp_path / "batch.txt"
    batch.write_text("Alice: まったく別のコメント\n", encoding="utf-8")

    result = _ack(script, chat_dir, batch, {})
    assert result.returncode == 0, result.stderr
    assert pending.read_text(encoding="utf-8") == original
