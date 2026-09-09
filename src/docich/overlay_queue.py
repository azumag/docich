"""Shared Soren event-overlay queue primitives used by Web UI and workers."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Any, Iterator, Mapping

OVERLAY_CATEGORIES = {"game", "worker", "chat", "radio", "prediction", "rollback", "system", "deadline"}
OVERLAY_TITLE_LIMIT = 120
OVERLAY_BODY_LIMIT = 500
LOCK_TIMEOUT_SECONDS = 5.0
SOURCE_ID_RE = re.compile(r"[A-Za-z0-9._:/-]{1,200}")


class OverlayQueueError(ValueError):
    """Raised when the shared overlay queue cannot be safely read or changed."""


class OverlayQueueBusyError(OverlayQueueError):
    """The shared writer lock remained busy until its bounded deadline."""


def _sanitize_text(value: str, limit: int) -> str:
    if not isinstance(value, str):
        raise OverlayQueueError("文字列である必要があります")
    if any(ord(c) < 32 and c not in ("\n", "\r", "\t") for c in value):
        raise OverlayQueueError("制御文字は使用できません")
    text = value.strip()
    if len(text) > limit:
        raise OverlayQueueError(f"{limit}文字以内である必要があります")
    return text


def validate_event(event: Mapping[str, Any], *, now: int | None = None) -> dict[str, Any]:
    if not isinstance(event, Mapping):
        raise OverlayQueueError("eventはオブジェクトである必要があります")
    category = str(event.get("category", "")).strip()
    if category not in OVERLAY_CATEGORIES:
        raise OverlayQueueError(f"categoryは {sorted(OVERLAY_CATEGORIES)} のいずれかである必要があります")
    title = _sanitize_text(str(event.get("title", "")), OVERLAY_TITLE_LIMIT)
    if not title:
        raise OverlayQueueError("titleは必須です")
    if "\n" in title or "\r" in title:
        raise OverlayQueueError("titleに改行は使用できません")
    body = _sanitize_text(str(event.get("body", "")), OVERLAY_BODY_LIMIT)
    level = str(event.get("level", "info")).strip() or "info"
    if level not in ("info", "warn", "error"):
        level = "info"
    current = int(time.time()) if now is None else int(now)
    raw_ts = event.get("ts")
    try:
        ts = int(raw_ts) if raw_ts is not None else current
    except Exception:
        ts = current
    if ts > current + 60 or ts < current - 7 * 86400:
        ts = current
    result = {"ts": ts, "category": category, "title": title, "body": body, "level": level}
    if "source_id" in event:
        source_id = str(event.get("source_id") or "").strip()
        if SOURCE_ID_RE.fullmatch(source_id) is None:
            raise OverlayQueueError("source_idが不正です")
        result["source_id"] = source_id
    return result


def _env_path(root: Path, name: str, default: str) -> Path:
    raw = os.environ.get(name, "")
    if raw:
        value = Path(raw)
        return value if value.is_absolute() else root / value
    return root / default


def overlay_events_path(soren_root: Path) -> Path:
    return _env_path(Path(soren_root), "EVENT_OVERLAY_EVENTS_FILE", "tmp/state/overlay_events.jsonl")


def overlay_html_path(soren_root: Path) -> Path:
    return _env_path(Path(soren_root), "EVENT_OVERLAY_HTML_FILE", "tmp/state/event_overlay.html")


def work_indicator_path(soren_root: Path) -> Path:
    return _env_path(Path(soren_root), "CODEX_WORK_OVERLAY_STATE_FILE", "tmp/state/codex_work_indicator.json")


def comment_gen_state_path(soren_root: Path) -> Path:
    # Preserve the legacy worker-state override used by Web UI. The overlay
    # generator receives this resolved path through EVENT_OVERLAY_* below.
    return _env_path(Path(soren_root), "COMMENT_GEN_STATE_FILE", "tmp/state/.comment_gen_state")


def radio_state_path(soren_root: Path) -> Path:
    return _env_path(Path(soren_root), "RADIO_STATE_FILE", "tmp/state/.radio_state")


def _read_dotenv(root: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = (root / ".env").read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is None:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values.setdefault(key, value)
    return values


def get_keep_visible(soren_root: Path) -> tuple[int, int]:
    root = Path(soren_root)
    dotenv = _read_dotenv(root)
    def number(name: str, default: int) -> int:
        raw = os.environ.get(name) or dotenv.get(name, "")
        try:
            return int(raw) if raw else default
        except Exception:
            return default
    return max(1, number("EVENT_OVERLAY_KEEP_EVENTS", 180)), max(1, number("EVENT_OVERLAY_VISIBLE_SEC", 18))


def load_events(soren_root: Path, *, keep: int | None = None, strict: bool = False) -> list[dict[str, Any]]:
    path = overlay_events_path(Path(soren_root))
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except FileNotFoundError:
        return []
    except Exception as exc:
        if strict:
            raise OverlayQueueError("overlay event queueを読み込めません") from exc
        return []
    events: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except Exception as exc:
            if strict:
                raise OverlayQueueError("overlay event queueが壊れています") from exc
            continue
        if not isinstance(item, dict):
            if strict:
                raise OverlayQueueError("overlay event queueが壊れています")
            continue
        if strict:
            item = validate_event(item)
        events.append(item)
    if keep is not None:
        return events[-max(0, int(keep)):]
    return events


@contextmanager
def _overlay_lock(soren_root: Path) -> Iterator[None]:
    """Share the native Soren writer's permanent, kernel-owned flock inode."""
    events = overlay_events_path(Path(soren_root))
    events.parent.mkdir(parents=True, exist_ok=True)
    lock_path = events.parent.resolve() / (events.name + ".lock")
    # Never unlink or steal by mtime: the OS releases this lock on process exit.
    with lock_path.open("a") as lock:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise OverlayQueueBusyError("another overlay edit in progress") from exc
                time.sleep(0.01)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _atomic_write_unlocked(path: Path, data: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".overlay.")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.chmod(mode)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def atomic_write(soren_root: Path, path: Path, data: str, mode: int = 0o644) -> None:
    with _overlay_lock(Path(soren_root)):
        _atomic_write_unlocked(Path(path), data, mode)


def delete_event(soren_root: Path, index: int) -> int:
    """Delete a Web UI row without overwriting a concurrent native append."""
    root = Path(soren_root)
    with _overlay_lock(root):
        events = load_events(root)
        if index < 0 or index >= len(events):
            raise IndexError(index)
        events.pop(index)
        content = "\n".join(json.dumps(item, ensure_ascii=False) for item in events)
        _atomic_write_unlocked(overlay_events_path(root), content + ("\n" if events else ""), 0o644)
        return len(events)


def regenerate_overlay(soren_root: Path) -> bool:
    root = Path(soren_root)
    try:
        generator = root / "generate_event_overlay.py"
        if not generator.is_file():
            candidate = Path(__file__).resolve().parents[2] / "games/soviet_now/generate_event_overlay.py"
            if candidate.is_file():
                generator = candidate
            else:
                return False
        keep, visible = get_keep_visible(root)
        env = os.environ.copy()
        env["EVENT_OVERLAY_STATE_BASE"] = str(root)
        env.setdefault("EVENT_OVERLAY_COMMENT_GEN_STATE", str(comment_gen_state_path(root)))
        env.setdefault("EVENT_OVERLAY_RADIO_STATE", str(radio_state_path(root)))
        result = subprocess.run(
            ["python3", str(generator), str(overlay_events_path(root)), str(overlay_html_path(root)),
             str(keep), str(visible), str(work_indicator_path(root))],
            cwd=str(root), env=env, timeout=5,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception:
        return False


def append_event(
    soren_root: Path,
    event: Mapping[str, Any],
    *,
    keep: int | None = None,
    strict: bool = True,
    regenerate: bool = True,
) -> bool:
    root = Path(soren_root)
    normalized = validate_event(event)
    keep_count = get_keep_visible(root)[0] if keep is None else int(keep)
    if keep_count <= 0:
        raise OverlayQueueError("keepは1以上である必要があります")
    with _overlay_lock(root):
        # Pre-existing lines load leniently: one poisoned line from another
        # writer must never brick all future appends (2026-09-10 production
        # incident: a category='improve' line broke every strict append).
        # Invalid existing lines are dropped by the rewrite below
        # (self-healing). The NEW payload above stays strictly validated, so
        # a bad writer still fails closed on its own data.
        existing = load_events(root, strict=False)
        normalized_existing: list[dict[str, Any]] = []
        for item in existing:
            try:
                normalized_existing.append(validate_event(item))
            except OverlayQueueError:
                continue
        source_id = normalized.get("source_id")
        if source_id is not None:
            matched = [item for item in normalized_existing if item.get("source_id") == source_id]
            if matched and matched[-1] == normalized:
                return False
            if matched:
                normalized_existing = [
                    item for item in normalized_existing if item.get("source_id") != source_id
                ]
        elif normalized in normalized_existing:
            return False
        records = (normalized_existing + [normalized])[-keep_count:]
        content = "\n".join(json.dumps(item, ensure_ascii=False) for item in records)
        if records:
            content += "\n"
        _atomic_write_unlocked(overlay_events_path(root), content, 0o644)
    if regenerate:
        regenerate_overlay(root)
    return True
