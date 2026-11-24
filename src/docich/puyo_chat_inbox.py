"""Twitch chat → live puyo slot ``chat_inbox.jsonl`` producer (docich#3372).

The consumer contract is owned by this repository (spec:
``docs/superpowers/specs/2026-10-18-puyo-puyo-clone-corner-design.md``):
the chat router appends JSON lines ``{"user_id","display_name","text"}``
(text ≤200 chars) to ``<runtime_dir>/chat_inbox.jsonl`` of the *currently
running* puyo-view slot.  ``docich.puyo_view --live --inbox-file`` drains
that file forward-only and applies ``puyo_corner.observe_chat`` to each
valid row.

This module is the writer half of that contract.  Soren's
``twitch_chat.sh fetch`` calls it once per sanitized PRIVMSG line at the
same point where the line is appended to ``pending.log`` (mirroring at the
single-writer moment, because pending.log is an ack-drained shared queue
and raw.log rotates — see issue #3372).

Fail-closed rules, mirroring ``trading/soren_output.py`` (docich owns the
contract schema and the writer):

* The destination is resolved from the canonical GameSwitch state only
  (``active.game == "puyo-view"``).  Any missing/corrupt state, a
  non-puyo active game, or a missing runtime directory resolves to
  ``skipped`` and nothing is written — chat from other corners never
  leaks into a stale inbox.
* Appends are a single ``O_APPEND`` write of one newline-terminated JSON
  line followed by ``fsync``: the caller is the fetch loop's single
  writer, so no interleaving and no torn lines can occur.
* ``--msg-id`` at-most-once delivery is kept in a bounded sidecar
  (``chat_inbox.seen``) next to the inbox: IRC reconnects re-deliver
  messages, and a retried mirror call must not apply the same chat twice.
* Raw chat text is never logged anywhere else: only fixed status
  identifiers are emitted on stdout.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .game_switch import (GameSwitchBusyError, GameSwitchLock, GameSwitchStore, StateCorruptError)
from .naming import NameValidationError, runtime_directory
from .puyo_corner import MAX_NAME_CHARS, MAX_USER_ID_CHARS
from .puyo_view import VIEW_NAME

INBOX_FILENAME = "chat_inbox.jsonl"
SEEN_FILENAME = "chat_inbox.seen"
# text cap of the consumer contract (puyo_view.MAX_INBOX_TEXT_CHARS is 200);
# the producer truncates so the line it writes is always inside the schema.
MAX_TEXT_CHARS = 200
# msg ids beyond this length are malformed upstream; bound the sidecar too.
MAX_MSG_ID_CHARS = 128
# The seen sidecar stays bounded: compact to the newest SEEN_KEEP ids once
# the file grows past SEEN_COMPACT_AT lines.  A puyo slot lives 2-14
# minutes, so a few hundred ids is far more than a slot ever sees.
SEEN_KEEP = 256
SEEN_COMPACT_AT = 1024

DEFAULT_CONFIG = "config/docich.soren-live.toml"


class ChatInboxError(RuntimeError):
    """A fixed, non-sensitive producer failure."""


def _clip(value, limit: int) -> str:
    """Coerce to a bounded string; non-str input becomes empty (fail-closed)."""
    if not isinstance(value, str):
        return ""
    return value[:limit]


def resolve_inbox_path(state_dir) -> Path | None:
    """Return the live puyo slot's inbox path, or ``None`` (fail-closed).

    Resolution trusts only the canonical GameSwitch state: the active
    runtime must be the puyo view and its runtime directory must exist.
    A corrupt canonical state, an idle switch, or a non-puyo slot all
    resolve to ``None`` so the caller writes nothing.
    """
    try:
        canonical, missing = GameSwitchStore(Path(state_dir)).canonical.load()
    except (StateCorruptError, OSError):
        return None
    if missing:
        return None
    active = canonical.get("active")
    if canonical.get("phase") != "ready" or not isinstance(active, dict) or active.get("game") != VIEW_NAME:
        return None
    runtime_id = active.get("runtime_id")
    if not isinstance(runtime_id, str) or not runtime_id:
        return None
    try:
        runtime_dir = runtime_directory(Path(state_dir), runtime_id)
    except (NameValidationError, ValueError):
        return None
    if not runtime_dir.is_dir():
        return None
    return runtime_dir / INBOX_FILENAME


def _read_seen(path: Path) -> list[str]:
    """Read the id sidecar as an ordered list, tolerating a missing file."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    # Any other read failure must stop delivery, not bypass deduplication.
    return [line for line in lines if line]


def _seen_append(path: Path, msg_id: str) -> None:
    """Record one id, compacting the sidecar when it outgrows its bound."""
    ids = _read_seen(path)
    if msg_id in ids:
        return
    ids.append(msg_id)
    if len(ids) > SEEN_COMPACT_AT:
        ids = ids[-SEEN_KEEP:]
        # Compaction replaces the file atomically so a reader never sees a
        # half-written id list (rename within the same directory).
        tmp = path.with_name(path.name + ".tmp")
        # Persist the replacement before publishing it, then persist the
        # directory entry. Atomic rename alone does not guarantee durability
        # after power loss.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            payload = ("\n".join(ids) + "\n").encode("utf-8")
            written = os.write(fd, payload)
            if written != len(payload):
                raise OSError("short chat seen compaction write")
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
        dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        original_size = os.fstat(fd).st_size
        try:
            encoded = (msg_id + "\n").encode("utf-8")
            written = os.write(fd, encoded)
            if written != len(encoded):
                raise OSError("short chat seen write")
            os.fsync(fd)
        except OSError:
            # A partial write leaves a newline-less torn tail; the next id
            # would concatenate onto it and silently lose its own dedup
            # record, which can turn a later mirror retry into a duplicate
            # inbox row. Roll the file back to its previous size -- the same
            # pattern as _append_line's JSONL rollback.
            os.ftruncate(fd, original_size)
            os.fsync(fd)
            raise
    finally:
        os.close(fd)


def _append_line(path: Path, line: bytes) -> None:
    """Append exactly one line atomically (single O_APPEND write + fsync)."""
    # The GameSwitch shared lock serializes switching, not other producers.
    # The fetch hook is contractually the sole writer for this inbox.
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        original_size = os.fstat(fd).st_size
        try:
            written = os.write(fd, line)
            if written != len(line):
                raise OSError("short chat inbox write")
            os.fsync(fd)
        except OSError:
            # A short write can leave a partial JSONL record. Without
            # rollback, the next append would concatenate two records.
            # Preserve all pre-existing bytes, including on fsync failure.
            os.ftruncate(fd, original_size)
            os.fsync(fd)
            raise
    finally:
        os.close(fd)


def append_chat(state_dir, *, user_id, display_name, text, msg_id=None) -> dict:
    """Mirror one sanitized chat line into the live puyo inbox.

    Returns a fixed ``{"status": ...}`` dict.  ``"appended"`` means one
    JSONL row was written; every ``"skipped"`` carries a fixed ``reason``
    and guarantees no bytes were written.  Raises ``ChatInboxError`` only
    when the inbox write itself fails.
    """
    user_id = _clip(user_id, MAX_USER_ID_CHARS)
    display_name = _clip(display_name, MAX_NAME_CHARS)
    text = _clip(text, MAX_TEXT_CHARS)
    msg_id = _clip(msg_id, MAX_MSG_ID_CHARS)
    if not user_id or not display_name or not text:
        return {"status": "skipped", "reason": "invalid_input"}

    # The coordinator takes the exclusive lock when switching slots. Keep a
    # shared lock across canonical resolution AND the append so a switch
    # cannot retire the resolved runtime between those two operations.
    # Never block the chat fetch loop on an in-progress switch.
    lock = GameSwitchLock(Path(state_dir))
    try:
        lock.acquire(exclusive=False, blocking=False)
    except (GameSwitchBusyError, OSError):
        return {"status": "skipped", "reason": "switch_busy"}
    try:
        inbox = resolve_inbox_path(state_dir)
        if inbox is None:
            return {"status": "skipped", "reason": "no_puyo_slot"}

        seen = inbox.parent / SEEN_FILENAME
        try:
            if msg_id and msg_id in _read_seen(seen):
                return {"status": "skipped", "reason": "duplicate"}
        except (OSError, UnicodeError) as exc:
            raise ChatInboxError("chat seen read failed") from exc

        line = json.dumps(
            {"user_id": user_id, "display_name": display_name, "text": text},
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8") + b"\n"
        try:
            # Reserve the id durably before writing to the consumer inbox.
            # A crash between these operations may lose the message, but
            # retrying must not apply it twice. Exactly-once requires a
            # transactional consumer acknowledgement protocol.
            if msg_id:
                _seen_append(seen, msg_id)
            _append_line(inbox, line)
        except (OSError, UnicodeError) as exc:
            raise ChatInboxError("chat_inbox append failed") from exc
    finally:
        lock.release()
    return {"status": "appended"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--msg-id", default="")
    parser.add_argument("--state-dir", default=None,
                        help="state_dir override (既定: config の paths.state_dir)")
    parser.add_argument("--config", default=None,
                        help=f"config TOML (既定: <repo>/{DEFAULT_CONFIG})")
    args = parser.parse_args(argv)

    if args.state_dir:
        state_dir = Path(args.state_dir)
    else:
        try:
            from .config import load_global

            root = Path(__file__).resolve().parents[2]
            config = Path(args.config) if args.config else root / DEFAULT_CONFIG
            state_dir = load_global(root, config).state_dir
        except Exception:
            # 宛先が解決できない状態では何も書かない (fail-closed)。
            print(json.dumps({"status": "skipped", "reason": "config_unavailable"}))
            return 0
    try:
        result = append_chat(
            state_dir,
            user_id=args.user_id,
            display_name=args.display_name,
            text=args.text,
            msg_id=args.msg_id,
        )
    except ChatInboxError:
        result = {"status": "error"}
        rc = 1
    else:
        rc = 0
    print(json.dumps(result, separators=(",", ":")))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
