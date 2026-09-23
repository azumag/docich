#!/usr/bin/env python3
"""Bounded per-viewer memory for comment replies.

Only exchanges whose reply reached the playback-success path are committed.
The prompt context is limited to viewers in the current comment batch, so one
viewer's history is never exposed as another viewer's memory.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


VERSION = 1
VALID_SOURCES = {"twitch", "youtube", "kick"}
VALID_MODES = {"main", "soren91"}
EVENT_PREFIX_RE = re.compile(r"^(?:\[(?:BITS|SUB|視聴記録)\]\s*)+", re.IGNORECASE)
CARD_ACQUIRED_RE = re.compile(
    r"^(?P<viewer>[^:\n]{1,80}?)\s*が\s*(?P<card>【[^】]{1,80}】.{1,240}?)\s*を獲得しました(?P<detail>.*)$"
)
WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ViewerComment:
    key: str
    fallback_key: str
    source: str
    display_name: str
    normalized_name: str
    comment: str
    kind: str = "comment"
    stable_id: str = ""
    message_id: str = ""
    rememberable: bool = True


def _collapse(value: str, limit: int) -> str:
    text = WHITESPACE_RE.sub(" ", unicodedata.normalize("NFKC", value or "")).strip()
    if len(text) > limit:
        text = text[: max(1, limit - 1)].rstrip() + "…"
    return text


def normalize_name(value: str) -> str:
    value = EVENT_PREFIX_RE.sub("", value or "")
    value = _collapse(value, 80).lstrip("@").casefold()
    return value


def _source(value: str) -> str:
    value = (value or "").strip().casefold()
    return value if value in VALID_SOURCES else "unknown"


def _mode(value: str) -> str:
    return value if value in VALID_MODES else "main"


def viewer_key(source: str, normalized_name: str, stable_id: str = "") -> str:
    identity = f"id\0{_collapse(stable_id, 160)}" if stable_id else f"name\0{normalized_name}"
    digest = hashlib.sha256(f"{_source(source)}\0{identity}".encode("utf-8")).hexdigest()[:24]
    return f"{_source(source)}:{digest}"


def parse_excluded(raw: str) -> set[str]:
    return {
        normalize_name(item)
        for item in re.split(r"[,\s]+", raw or "")
        if normalize_name(item)
    }


def _clean_metadata_token(value: Any, limit: int = 160) -> str:
    return re.sub(r"[^0-9A-Za-z_.:@-]", "", str(value or ""))[:limit]


def parse_pending_envelope(raw: str, source: str) -> dict[str, Any]:
    """Parse a raw/pending row while retaining backwards compatibility.

    New rows use tab-separated key/value fields followed by the exact plain
    comment line.  Keeping identity on the same physical row avoids the race
    and same-display-name ambiguity of a separate latest-name lookup table.
    """
    parts = (raw or "").rstrip("\r\n").split("\t")
    metadata: dict[str, Any] = {
        "source": _source(source),
        "message_id": "",
        "stable_id": "",
        "login": "",
        "display_name": "",
        "flags": [],
        "line": "",
    }
    if len(parts) == 1:
        metadata["line"] = _collapse(parts[0], 800)
        return metadata

    cursor = 0
    known = {"id", "user-id", "login", "display", "flags"}
    while cursor < len(parts) - 1 and "=" in parts[cursor]:
        key, value = parts[cursor].split("=", 1)
        if key not in known:
            break
        if key == "id":
            metadata["message_id"] = _clean_metadata_token(value)
        elif key == "user-id":
            metadata["stable_id"] = _clean_metadata_token(value)
        elif key == "login":
            metadata["login"] = _clean_metadata_token(value, 80)
        elif key == "display":
            metadata["display_name"] = _collapse(value, 80)
        elif key == "flags":
            metadata["flags"] = [
                flag for flag in (_clean_metadata_token(item, 40) for item in value.split(",")) if flag
            ]
        cursor += 1
    metadata["line"] = _collapse(" ".join(parts[cursor:]), 800)
    return metadata


def encode_pending_envelope(
    *,
    line: str,
    message_id: str = "",
    stable_id: str = "",
    login: str = "",
    display_name: str = "",
    flags: list[str] | None = None,
) -> str:
    safe_flags = ",".join(
        flag for flag in (_clean_metadata_token(item, 40) for item in (flags or [])) if flag
    )
    return "\t".join(
        [
            f"id={_clean_metadata_token(message_id)}",
            f"user-id={_clean_metadata_token(stable_id)}",
            f"login={_clean_metadata_token(login, 80)}",
            f"display={_collapse(display_name, 80)}",
            f"flags={safe_flags}",
            _collapse(line, 800),
        ]
    )


def _metadata_sidecar_path(batch_path: str) -> Path:
    return Path(f"{batch_path}.viewer_meta.jsonl")


def _atomic_text_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def emit_batch(*, pending_path: str, out_path: str, source: str, limit: int = 10) -> int:
    try:
        rows = Path(pending_path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        rows = []
    entries: list[dict[str, Any]] = []
    plain_lines: list[str] = []
    for raw in rows[: max(1, limit)]:
        entry = parse_pending_envelope(raw, source)
        line = _collapse(str(entry.get("line") or ""), 800)
        if not line:
            continue
        entry["line_index"] = len(entries)
        entry["line"] = line
        entries.append(entry)
        plain_lines.append(line)
    out = Path(out_path)
    if not plain_lines:
        for target in (out, _metadata_sidecar_path(out_path)):
            try:
                target.unlink()
            except OSError:
                pass
        return 0
    _atomic_text_write(out, "\n".join(plain_lines) + "\n")
    metadata_text = "".join(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n" for entry in entries)
    _atomic_text_write(_metadata_sidecar_path(out_path), metadata_text)
    return len(entries)


def load_batch_metadata(path: str) -> list[dict[str, Any]]:
    if not path:
        return []
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        return []
    entries: list[dict[str, Any]] = []
    for expected_index, line in enumerate(lines[:100]):
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            return []
        if not isinstance(raw, dict):
            return []
        try:
            actual_index = int(raw.get("line_index"))
        except (TypeError, ValueError):
            return []
        if actual_index != expected_index or actual_index < 0:
            return []
        raw_flags = raw.get("flags") or []
        if not isinstance(raw_flags, list):
            return []
        entries.append(
            {
                "line_index": expected_index,
                "line": _collapse(str(raw.get("line") or ""), 800),
                "source": _source(str(raw.get("source") or "")),
                "message_id": _clean_metadata_token(raw.get("message_id")),
                "stable_id": _clean_metadata_token(raw.get("stable_id")),
                "login": _clean_metadata_token(raw.get("login"), 80),
                "display_name": _collapse(str(raw.get("display_name") or ""), 80),
                "flags": [
                    flag
                    for flag in (_clean_metadata_token(item, 40) for item in raw_flags)
                    if flag
                ],
            }
        )
    return entries


def select_metadata(*, metadata_path: str, batch_path: str, out_path: str) -> int:
    """Select metadata for a filtered plain-text batch, preserving order."""
    entries = load_batch_metadata(metadata_path)
    try:
        wanted = Path(batch_path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        wanted = []
    selected: list[dict[str, Any]] = []
    cursor = 0
    for wanted_line in wanted:
        wanted_clean = _collapse(wanted_line, 800)
        if not wanted_clean:
            continue
        match: dict[str, Any] | None = None
        while cursor < len(entries):
            candidate = entries[cursor]
            cursor += 1
            if candidate.get("line") == wanted_clean:
                match = dict(candidate)
                break
        if match is None:
            match = {
                "line": wanted_clean,
                "source": "unknown",
                "message_id": "",
                "stable_id": "",
                "login": "",
                "display_name": "",
                "flags": [],
            }
        match["line_index"] = len(selected)
        selected.append(match)
    text = "".join(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n" for entry in selected)
    _atomic_text_write(Path(out_path), text)
    return len(selected)


def emit_ack_batch(*, metadata_path: str, batch_path: str, out_path: str) -> int:
    """Encode a plain Twitch batch with message IDs for precise acknowledgement.

    The model-facing batch is NFKC-normalized, while the pending envelope retains
    the provider's original punctuation.  A text-only acknowledgement can
    therefore miss the pending row.  Preserve positional identity from the
    sidecar and let ``twitch_chat.sh ack-batch`` remove the exact provider
    message.  Lines without trustworthy metadata remain plain-text fallbacks.
    """
    entries = load_batch_metadata(metadata_path)
    try:
        lines = Path(batch_path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        lines = []

    encoded: list[str] = []
    identified = 0
    for index, raw_line in enumerate(lines):
        line = _collapse(raw_line, 800)
        if not line:
            continue
        message_id = ""
        if index < len(entries) and entries[index].get("line") == line:
            message_id = _clean_metadata_token(entries[index].get("message_id"))
        if message_id:
            encoded.append(f"id={message_id}\t{line}")
            identified += 1
        else:
            encoded.append(line)

    _atomic_text_write(Path(out_path), "\n".join(encoded) + ("\n" if encoded else ""))
    return identified


def parse_batch(
    path: str,
    source: str,
    excluded: set[str],
    metadata_path: str = "",
    preserve_positions: bool = False,
) -> list[ViewerComment]:
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []

    parsed: list[ViewerComment] = []
    metadata = load_batch_metadata(metadata_path or str(_metadata_sidecar_path(path)))
    for line_index, raw_line in enumerate(lines):
        line = _collapse(raw_line, 800)
        entry = metadata[line_index] if line_index < len(metadata) else {}
        entry_source = _source(str(entry.get("source") or source))
        if entry_source not in {_source(source), "unknown"}:
            entry = {}
        raw_name = ""
        raw_comment = line
        metadata_display = _collapse(str(entry.get("display_name") or ""), 80)
        line_without_event = EVENT_PREFIX_RE.sub("", line)
        if metadata_display and line_without_event.startswith(f"{metadata_display}: "):
            raw_name = metadata_display
            raw_comment = line_without_event[len(metadata_display) + 2 :]
        elif ": " in line:
            raw_name, raw_comment = line.split(": ", 1)

        # Dociai is not a remembered viewer, but a collector-authenticated
        # card notification carries a real viewer's acquisition.  Text alone
        # is never enough to mark a human comment as such.
        sender_normalized = normalize_name(raw_name)
        metadata_sender_normalized = normalize_name(metadata_display or str(entry.get("login") or ""))
        card_match = CARD_ACQUIRED_RE.match(raw_comment)
        trusted_card = "trusted-card" in (entry.get("flags") or [])
        if card_match and trusted_card and metadata_sender_normalized in excluded:
            display_name = _collapse(card_match.group("viewer"), 80)
            normalized_name = normalize_name(display_name)
            card = _collapse(card_match.group("card"), 320)
            detail = _collapse(card_match.group("detail"), 200)
            if display_name and normalized_name and normalized_name not in excluded:
                card_memory = f"{card}を獲得しました"
                if detail:
                    card_memory += f" {detail}"
                parsed.append(
                    ViewerComment(
                        key=viewer_key(source, normalized_name),
                        fallback_key=viewer_key(source, normalized_name),
                        source=_source(source),
                        display_name=display_name,
                        normalized_name=normalized_name,
                        comment=_collapse(card_memory, 600),
                        kind="card_acquired",
                        stable_id="",
                        message_id=_clean_metadata_token(entry.get("message_id")),
                    )
                )
            continue

        if not raw_name:
            if preserve_positions and raw_comment:
                parsed.append(
                    ViewerComment(
                        key="",
                        fallback_key="",
                        source=_source(source),
                        display_name="",
                        normalized_name="",
                        comment=_collapse(raw_comment, 600),
                        rememberable=False,
                    )
                )
            continue
        display_name = _collapse(EVENT_PREFIX_RE.sub("", raw_name), 80)
        normalized_name = normalize_name(display_name)
        stable_id = ""
        message_id = ""
        if entry:
            metadata_name = metadata_display or raw_name
            if normalize_name(metadata_name) == normalized_name:
                stable_id = _clean_metadata_token(entry.get("stable_id"))
                message_id = _clean_metadata_token(entry.get("message_id"))
        comment = _collapse(raw_comment, 600)
        if not display_name or not normalized_name or not comment:
            continue
        memory_eligible = normalized_name not in excluded and normalized_name not in {"不明", "unknown"}
        # A display name is mutable and not unique.  Normal viewer exchanges
        # are remembered only when the collector supplied a provider-stable
        # identity on this exact message row.  Trusted card notifications are
        # the sole name-keyed exception because the bot payload has no viewer ID.
        if not stable_id:
            memory_eligible = False
        if not memory_eligible and not preserve_positions:
            continue
        parsed.append(
            ViewerComment(
                key=viewer_key(source, normalized_name, stable_id) if memory_eligible else "",
                fallback_key=viewer_key(source, normalized_name) if memory_eligible else "",
                source=_source(source),
                display_name=display_name,
                normalized_name=normalized_name,
                comment=comment,
                kind="comment",
                stable_id=stable_id,
                message_id=message_id,
                rememberable=memory_eligible,
            )
        )
    return parsed


def _reply_paragraphs(text: str) -> list[str]:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    return [_collapse(part, 1200) for part in re.split(r"\n\s*\n+", text) if _collapse(part, 1200)]


def build_staged_payload(
    *,
    batch_path: str,
    reply_path: str,
    source: str,
    mode: str,
    excluded: set[str],
    metadata_path: str = "",
    batch_hash: str = "",
    now: int | None = None,
) -> dict[str, Any]:
    comments = parse_batch(batch_path, source, excluded, metadata_path, preserve_positions=True)
    try:
        reply_text = Path(reply_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        reply_text = ""
    paragraphs = _reply_paragraphs(reply_text)
    remembered_comments = [item for item in comments if item.rememberable]
    unique_keys = {item.key for item in remembered_comments}

    events: list[dict[str, str]] = []
    if len(paragraphs) == len(comments):
        for item, reply in zip(comments, paragraphs):
            if item.rememberable:
                events.append(_event_from_comment(item, reply))
    elif len(unique_keys) == 1 and remembered_comments and len(remembered_comments) == len(comments):
        combined_comment = " / ".join(item.comment for item in remembered_comments)
        event = _event_from_comment(remembered_comments[0], _collapse(reply_text, 1600))
        event["comment"] = _collapse(combined_comment, 1200)
        events.append(event)
    else:
        # Do not attribute a mixed-batch reply to the wrong viewer when the
        # model did not preserve the requested one-paragraph-per-comment shape.
        events.extend(_event_from_comment(item, "") for item in remembered_comments)

    staged_at = int(now if now is not None else time.time())
    payload = {
        "version": VERSION,
        "staged_at": staged_at,
        "source": _source(source),
        "mode": _mode(mode),
        "batch_hash": _collapse(batch_hash, 160),
        "events": events,
    }
    return payload


def _event_from_comment(item: ViewerComment, reply: str) -> dict[str, str]:
    return {
        "key": item.key,
        "fallback_key": item.fallback_key,
        "source": item.source,
        "display_name": item.display_name,
        "normalized_name": item.normalized_name,
        "kind": item.kind,
        "stable_id": item.stable_id,
        "message_id": item.message_id,
        "comment": item.comment,
        "reply": _collapse(reply, 1600),
    }


def _validate_staged(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("version") != VERSION:
        raise ValueError("unsupported viewer-memory sidecar")
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError("viewer-memory events must be a list")
    payload["source"] = _source(str(payload.get("source") or ""))
    payload["mode"] = _mode(str(payload.get("mode") or ""))
    return payload


def _load_state(path: Path, *, strict: bool) -> dict[str, Any]:
    if not path.exists():
        return {"version": VERSION, "updated_at": 0, "users": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if strict:
            raise ValueError(f"invalid viewer-memory state: {exc}") from exc
        return {"version": VERSION, "updated_at": 0, "users": {}}
    if not isinstance(payload, dict) or payload.get("version") != VERSION or not isinstance(payload.get("users"), dict):
        if strict:
            raise ValueError("invalid viewer-memory state schema")
        return {"version": VERSION, "updated_at": 0, "users": {}}
    return payload


@contextmanager
def _state_lock(path: Path, *, exclusive: bool) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = Path(str(path) + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def stage_sidecar(path: str, payload: dict[str, Any]) -> None:
    _atomic_json_write(Path(path), payload)


def _safe_epoch(raw: Any) -> int:
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _event_id(event: dict[str, Any], mode: str, batch_hash: str, staged_at: int) -> str:
    source_message_id = _clean_metadata_token(event.get("message_id"))
    if source_message_id:
        material = "\0".join([str(event.get("source") or ""), source_message_id, mode])
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    material = "\0".join(
        [
            str(event.get("key") or ""),
            mode,
            batch_hash,
            str(staged_at),
            str(event.get("kind") or "comment"),
            str(event.get("comment") or ""),
            str(event.get("reply") or ""),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def commit_sidecar(
    *,
    state_path: str,
    sidecar_path: str,
    max_users: int,
    max_exchanges: int,
    ttl_days: int,
    now: int | None = None,
) -> int:
    sidecar = Path(sidecar_path)
    try:
        staged = _validate_staged(json.loads(sidecar.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError):
        return 0

    current_time = int(now if now is not None else time.time())
    cutoff = current_time - max(1, ttl_days) * 86400
    state_file = Path(state_path)
    committed = 0
    with _state_lock(state_file, exclusive=True):
        state = _load_state(state_file, strict=True)
        users = state["users"]
        _prune_users(users, cutoff=cutoff, max_users=max_users)

        for raw_event in staged["events"]:
            if not isinstance(raw_event, dict):
                continue
            key = str(raw_event.get("key") or "")
            name = _collapse(str(raw_event.get("display_name") or ""), 80)
            normalized = normalize_name(str(raw_event.get("normalized_name") or name))
            stable_id = _collapse(str(raw_event.get("stable_id") or ""), 160)
            comment = _collapse(str(raw_event.get("comment") or ""), 1200)
            reply = _collapse(str(raw_event.get("reply") or ""), 1600)
            kind = str(raw_event.get("kind") or "comment")
            if kind not in {"comment", "card_acquired"}:
                kind = "comment"
            if not key or not name or not normalized or not comment:
                continue
            event_source = str(raw_event.get("source") or staged["source"])
            expected_key = viewer_key(event_source, normalized, stable_id)
            if key != expected_key:
                continue
            fallback_key = viewer_key(event_source, normalized)
            if str(raw_event.get("fallback_key") or fallback_key) != fallback_key:
                continue
            event_id = _event_id(
                raw_event,
                staged["mode"],
                str(staged.get("batch_hash") or ""),
                _safe_epoch(staged.get("staged_at")),
            )
            user = users.setdefault(
                key,
                {
                    "source": staged["source"],
                    "normalized_name": normalized,
                    "stable_id": stable_id,
                    "display_name": name,
                    "first_seen_at": current_time,
                    "last_seen_at": current_time,
                    "interaction_count": 0,
                    "exchanges": [],
                },
            )
            exchanges = user.get("exchanges")
            if not isinstance(exchanges, list):
                exchanges = []
            exchanges = [item for item in exchanges if isinstance(item, dict) and _safe_epoch(item.get("played_at")) >= cutoff]
            if any(str(item.get("event_id") or "") == event_id for item in exchanges):
                user["exchanges"] = exchanges[-max(1, max_exchanges) :]
                continue
            exchanges.append(
                {
                    "event_id": event_id,
                    "played_at": current_time,
                    "mode": staged["mode"],
                    "kind": kind,
                    "comment": comment,
                    "reply": reply,
                }
            )
            user.update(
                {
                    "source": staged["source"],
                    "normalized_name": normalized,
                    "stable_id": stable_id,
                    "display_name": name,
                    "last_seen_at": current_time,
                    "interaction_count": max(0, _safe_epoch(user.get("interaction_count"))) + 1,
                    "exchanges": exchanges[-max(1, max_exchanges) :],
                }
            )
            committed += 1

        _prune_users(users, cutoff=cutoff, max_users=max_users)
        state["updated_at"] = current_time
        _atomic_json_write(state_file, state)
    return committed


def _prune_users(users: dict[str, Any], *, cutoff: int, max_users: int) -> None:
    for key in list(users):
        user = users.get(key)
        if not isinstance(user, dict):
            users.pop(key, None)
            continue
        exchanges = user.get("exchanges")
        if not isinstance(exchanges, list):
            users.pop(key, None)
            continue
        exchanges = [item for item in exchanges if isinstance(item, dict) and _safe_epoch(item.get("played_at")) >= cutoff]
        if not exchanges:
            users.pop(key, None)
            continue
        user["exchanges"] = exchanges
        user["last_seen_at"] = max(_safe_epoch(item.get("played_at")) for item in exchanges)

    keep = max(1, max_users)
    if len(users) <= keep:
        return
    newest = sorted(users, key=lambda key: _safe_epoch(users[key].get("last_seen_at")), reverse=True)[:keep]
    newest_set = set(newest)
    for key in list(users):
        if key not in newest_set:
            users.pop(key, None)


def build_context(
    *,
    state_path: str,
    batch_path: str,
    source: str,
    mode: str,
    excluded: set[str],
    items_per_user: int,
    max_chars: int,
    comment_max_chars: int,
    reply_max_chars: int,
    ttl_days: int,
    metadata_path: str = "",
    now: int | None = None,
) -> str:
    current = parse_batch(batch_path, source, excluded, metadata_path)
    ordered: list[ViewerComment] = []
    seen: set[str] = set()
    for item in current:
        if item.key not in seen:
            ordered.append(item)
            seen.add(item.key)
    if not ordered:
        return "（該当する投稿者別メモなし）"

    current_time = int(now if now is not None else time.time())
    cutoff = current_time - max(1, ttl_days) * 86400
    state_file = Path(state_path)
    with _state_lock(state_file, exclusive=False):
        state = _load_state(state_file, strict=False)
    users = state.get("users") or {}

    blocks: list[str] = []
    per_user_budget = max(180, max(200, max_chars) // max(1, len(ordered)))
    for item in ordered:
        candidate_exchanges: list[dict[str, Any]] = []
        user = users.get(item.key)
        if isinstance(user, dict):
            candidate_exchanges.extend(entry for entry in user.get("exchanges") or [] if isinstance(entry, dict))
        # Card notices are emitted by the trusted bot without the recipient's
        # provider ID.  Add only those name-keyed facts; never fall back to
        # ordinary name-keyed conversation for a stable-ID viewer.
        if item.fallback_key != item.key:
            fallback_user = users.get(item.fallback_key)
            if isinstance(fallback_user, dict):
                candidate_exchanges.extend(
                    entry
                    for entry in fallback_user.get("exchanges") or []
                    if isinstance(entry, dict) and entry.get("kind") == "card_acquired"
                )
        exchanges = [
            entry
            for entry in candidate_exchanges
            if entry.get("mode") == _mode(mode) and _safe_epoch(entry.get("played_at")) >= cutoff
        ]
        if not exchanges:
            continue
        exchanges = sorted(exchanges, key=lambda entry: _safe_epoch(entry.get("played_at")), reverse=True)[
            : max(1, items_per_user)
        ]
        source_label = {"twitch": "Twitch", "youtube": "YouTube", "kick": "Kick"}.get(item.source, item.source)
        header = f"- {item.display_name}（{source_label}、過去の再生済み会話 {len(exchanges)}件）"
        lines = [header]
        used = len(header)
        for entry in exchanges:
            stamp = datetime.fromtimestamp(_safe_epoch(entry.get("played_at"))).strftime("%Y-%m-%d")
            comment = _collapse(str(entry.get("comment") or ""), max(40, comment_max_chars))
            reply = _collapse(str(entry.get("reply") or ""), max(40, reply_max_chars))
            event_label = "カード獲得" if entry.get("kind") == "card_acquired" else "コメント"
            line = f"  - {stamp} {event_label}「{comment}」"
            if reply:
                line += f" / 返信「{reply}」"
            if used + len(line) + 1 > per_user_budget:
                break
            lines.append(line)
            used += len(line) + 1
        if len(lines) == 1:
            continue
        blocks.append("\n".join(lines))

    if not blocks:
        return "（該当する投稿者別メモなし）"
    output = "\n".join(blocks)
    limit = max(200, max_chars)
    if len(output) > limit:
        output = output[: max(1, limit - 1)].rstrip() + "…"
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    stage = commands.add_parser("stage")
    stage.add_argument("--sidecar", required=True)
    stage.add_argument("--batch", required=True)
    stage.add_argument("--reply", required=True)
    stage.add_argument("--source", required=True)
    stage.add_argument("--mode", default="main")
    stage.add_argument("--exclude", default="")
    stage.add_argument("--metadata", default="")
    stage.add_argument("--batch-hash", default="")

    commit = commands.add_parser("commit")
    commit.add_argument("--state", required=True)
    commit.add_argument("--sidecar", required=True)
    commit.add_argument("--max-users", type=int, default=500)
    commit.add_argument("--max-exchanges", type=int, default=24)
    commit.add_argument("--ttl-days", type=int, default=365)

    context = commands.add_parser("context")
    context.add_argument("--state", required=True)
    context.add_argument("--batch", required=True)
    context.add_argument("--source", required=True)
    context.add_argument("--mode", default="main")
    context.add_argument("--exclude", default="")
    context.add_argument("--metadata", default="")
    context.add_argument("--items", type=int, default=4)
    context.add_argument("--max-chars", type=int, default=2200)
    context.add_argument("--comment-max-chars", type=int, default=240)
    context.add_argument("--reply-max-chars", type=int, default=320)
    context.add_argument("--ttl-days", type=int, default=365)

    emit = commands.add_parser("emit-batch")
    emit.add_argument("--pending", required=True)
    emit.add_argument("--out", required=True)
    emit.add_argument("--source", required=True)
    emit.add_argument("--limit", type=int, default=10)

    select = commands.add_parser("select-metadata")
    select.add_argument("--metadata", required=True)
    select.add_argument("--batch", required=True)
    select.add_argument("--out", required=True)

    ack = commands.add_parser("emit-ack-batch")
    ack.add_argument("--metadata", required=True)
    ack.add_argument("--batch", required=True)
    ack.add_argument("--out", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "stage":
        payload = build_staged_payload(
            batch_path=args.batch,
            reply_path=args.reply,
            source=args.source,
            mode=args.mode,
            excluded=parse_excluded(args.exclude),
            metadata_path=args.metadata,
            batch_hash=args.batch_hash,
        )
        stage_sidecar(args.sidecar, payload)
        print(len(payload["events"]))
        return 0
    if args.command == "commit":
        print(
            commit_sidecar(
                state_path=args.state,
                sidecar_path=args.sidecar,
                max_users=args.max_users,
                max_exchanges=args.max_exchanges,
                ttl_days=args.ttl_days,
            )
        )
        return 0
    if args.command == "context":
        print(
            build_context(
                state_path=args.state,
                batch_path=args.batch,
                source=args.source,
                mode=args.mode,
                excluded=parse_excluded(args.exclude),
                items_per_user=args.items,
                max_chars=args.max_chars,
                comment_max_chars=args.comment_max_chars,
                reply_max_chars=args.reply_max_chars,
                ttl_days=args.ttl_days,
                metadata_path=args.metadata,
            )
        )
        return 0
    if args.command == "emit-batch":
        print(emit_batch(pending_path=args.pending, out_path=args.out, source=args.source, limit=args.limit))
        return 0
    if args.command == "select-metadata":
        print(select_metadata(metadata_path=args.metadata, batch_path=args.batch, out_path=args.out))
        return 0
    if args.command == "emit-ack-batch":
        print(emit_ack_batch(metadata_path=args.metadata, batch_path=args.batch, out_path=args.out))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
