"""Durable, replay-safe delivery of paper trading events to viewer outputs."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Callable, Iterator, Mapping

from ..config import GlobalConfig
from .events import PublicEventError, read_public_events
from .performance import realized_pnl_for_fill
from .presentation import PresentationError, read_presentation, render_notification
from .soren_output import enqueue_speech, send_overlay


class NotificationError(RuntimeError):
    """Raised when source/delivery state is corrupt or cannot be persisted."""


@contextmanager
def _delivery_lock(state_dir: Path) -> Iterator[None]:
    """Non-stealable process lock for the full source->output->ACK transaction."""
    base = Path(state_dir)
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    lock_path = base / ".notification_delivery.lock"
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise NotificationError("notification delivery lock could not be opened") from exc
    try:
        try:
            os.fchmod(fd, 0o600)
        except OSError:
            pass
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            raise NotificationError("notification delivery already in progress") from exc
        try:
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        os.close(fd)


@dataclass(frozen=True)
class NotificationDeliveryResult:
    enabled: bool
    bootstrapped: bool
    presentation_mode: str
    source_count: int
    overlay_sent: int
    speech_sent: int
    overlay_pending: int
    speech_pending: int
    error_codes: tuple[str, ...]


@dataclass
class _DeliveryState:
    overlay_ids: list[str]
    speech_ids: list[str]
    updated_at: float


def _trading_state_dir(g: GlobalConfig, override: Path | None = None) -> Path:
    return Path(override) if override is not None else g.state_dir / "trading"


def _state_path(g: GlobalConfig, state_dir: Path | None = None) -> Path:
    return _trading_state_dir(g, state_dir) / "notification_delivery.json"


def _presentation_path(g: GlobalConfig, state_dir: Path | None = None) -> Path:
    return _trading_state_dir(g, state_dir) / "presentation.json"


def _source_path(g: GlobalConfig, state_dir: Path | None = None) -> Path:
    return _trading_state_dir(g, state_dir) / "events.jsonl"


def _safe_status(g: GlobalConfig, state_dir: Path | None = None) -> dict[str, object] | None:
    path = _trading_state_dir(g, state_dir) / "status.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict) or raw.get("mode") != "paper":
        return None
    result = {"schema_version": raw.get("schema_version"), "mode": "paper"}
    for key in ("capital_reference", "deployed_reference"):
        value = raw.get(key)
        if value is not None:
            result[key] = value
    return result


def _load_state(path: Path) -> _DeliveryState | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NotificationError("notification delivery state is corrupt") from exc
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version", "overlay_delivered_ids", "speech_delivered_ids", "updated_at"
    } or raw.get("schema_version") != 1:
        raise NotificationError("notification delivery state schema is invalid")
    overlay = raw.get("overlay_delivered_ids")
    speech = raw.get("speech_delivered_ids")
    if not isinstance(overlay, list) or not all(isinstance(item, str) and item for item in overlay):
        raise NotificationError("notification overlay delivery state is invalid")
    if not isinstance(speech, list) or not all(isinstance(item, str) and item for item in speech):
        raise NotificationError("notification speech delivery state is invalid")
    if len(overlay) != len(set(overlay)) or len(speech) != len(set(speech)):
        raise NotificationError("notification delivery state contains duplicate ids")
    try:
        updated = float(raw.get("updated_at"))
    except (TypeError, ValueError) as exc:
        raise NotificationError("notification delivery timestamp is invalid") from exc
    if not math.isfinite(updated):
        raise NotificationError("notification delivery timestamp is invalid")
    return _DeliveryState(list(overlay), list(speech), updated)


def _write_state(path: Path, state: _DeliveryState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({
                "schema_version": 1,
                "overlay_delivered_ids": state.overlay_ids,
                "speech_delivered_ids": state.speech_ids,
                "updated_at": state.updated_at,
            }, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception as exc:
        try:
            os.close(fd)
        except OSError:
            pass
        raise NotificationError("notification delivery state could not be written") from exc
    finally:
        tmp.unlink(missing_ok=True)


def _event_ids(events: list[dict[str, object]]) -> list[str]:
    return [str(item["event_id"]) for item in events]


def _bounded(ids: list[str], source_ids: list[str]) -> list[str]:
    present = set(ids)
    return [event_id for event_id in source_ids if event_id in present]


_OVERLAY_ONLY_EVENT_TYPES = frozenset({"paper_fill", "multileg_settlement"})


def _is_overlay_only_event(item: Mapping[str, object]) -> bool:
    """Return whether this PAPER flash is intentionally visual-only."""
    return str(item.get("event_type") or "") in _OVERLAY_ONLY_EVENT_TYPES


def _render_event_with_local_facts(
    item: Mapping[str, object], *, mode: str, status: Mapping[str, object] | None,
    display_at: float, trading_dir: Path,
):
    enriched = dict(item)
    if enriched.get("event_type") == "paper_fill" and enriched.get("side") == "sell":
        event_id = str(enriched.get("event_id") or "")
        fill_id = event_id.removeprefix("fill:")
        pnl = realized_pnl_for_fill(trading_dir / "paper.sqlite3", fill_id)
        if pnl is not None:
            enriched["realized_pnl_reference"] = str(pnl)
    return render_notification(enriched, mode=mode, status=status, display_at=display_at)


def _deliver_pending_notifications_unlocked(
    g: GlobalConfig,
    *,
    overlay_sender: Callable[[GlobalConfig, dict[str, object]], None] | None = None,
    speech_sender: Callable[[GlobalConfig, str], None] | None = None,
    now: float,
    state_dir: Path | None = None,
) -> NotificationDeliveryResult:
    """Deliver new durable PAPER events without generating any trading activity."""
    if not g.trading.notifications_enabled:
        return NotificationDeliveryResult(False, False, "compact", 0, 0, 0, 0, 0, ())
    try:
        presentation = read_presentation(_presentation_path(g, state_dir))
        events = read_public_events(_source_path(g, state_dir))
    except (PresentationError, PublicEventError, OSError) as exc:
        raise NotificationError("notification source state is unsafe") from exc
    timestamp = float(now)
    if not math.isfinite(timestamp):
        raise NotificationError("notification time is invalid")
    source_ids = _event_ids(events)
    state_path = _state_path(g, state_dir)
    state = _load_state(state_path)
    if state is None:
        state = _DeliveryState(list(source_ids), list(source_ids), timestamp)
        _write_state(state_path, state)
        return NotificationDeliveryResult(
            True, True, presentation.mode, len(events), 0, 0, 0, 0, ()
        )

    state.overlay_ids = _bounded(state.overlay_ids, source_ids)
    state.speech_ids = _bounded(state.speech_ids, source_ids)
    status = _safe_status(g, state_dir)
    trading_dir = _trading_state_dir(g, state_dir)
    render_cache: dict[str, object] = {}
    overlay_fn = overlay_sender or send_overlay
    if speech_sender is None:
        def speech_fn(config: GlobalConfig, text: str, event_id: str) -> None:
            enqueue_speech(config, text, event_id=event_id)
    else:
        def speech_fn(config: GlobalConfig, text: str, event_id: str) -> None:
            speech_sender(config, text)
    overlay_sent = 0
    speech_sent = 0
    errors: list[str] = []
    overlay_blocked = False
    speech_blocked = False

    def rendered(item: Mapping[str, object]):
        event_id = str(item["event_id"])
        if event_id not in render_cache:
            try:
                render_cache[event_id] = _render_event_with_local_facts(
                    item, mode=presentation.mode, status=status,
                    display_at=timestamp, trading_dir=trading_dir,
                )
            except PresentationError as exc:
                raise NotificationError("notification event could not be rendered") from exc
        return render_cache[event_id]

    for item in events:
        event_id = str(item["event_id"])
        if event_id not in state.overlay_ids and not overlay_blocked:
            try:
                overlay_fn(g, rendered(item).overlay_event)
            except Exception:
                errors.append("overlay_delivery_error")
                overlay_blocked = True
            else:
                state.overlay_ids.append(event_id)
                state.overlay_ids = _bounded(state.overlay_ids, source_ids)
                state.updated_at = timestamp
                _write_state(state_path, state)
                overlay_sent += 1

    if not g.trading.notification_speech_enabled:
        state.speech_ids = list(source_ids)
        state.updated_at = timestamp
        _write_state(state_path, state)
    else:
        for item in events:
            event_id = str(item["event_id"])
            if event_id in state.speech_ids:
                continue
            if _is_overlay_only_event(item):
                # Trade flashes belong in the lower overlay only. Mark them as
                # intentionally handled so enabling speech later never replays
                # historical fills/settlements into the audio queue.
                state.speech_ids.append(event_id)
                state.speech_ids = _bounded(state.speech_ids, source_ids)
                state.updated_at = timestamp
                _write_state(state_path, state)
                continue
            if speech_blocked:
                continue
            try:
                speech_fn(g, rendered(item).speech_text, event_id)
            except Exception:
                errors.append("speech_delivery_error")
                speech_blocked = True
            else:
                state.speech_ids.append(event_id)
                state.speech_ids = _bounded(state.speech_ids, source_ids)
                state.updated_at = timestamp
                _write_state(state_path, state)
                speech_sent += 1

    state.overlay_ids = _bounded(state.overlay_ids, source_ids)
    state.speech_ids = _bounded(state.speech_ids, source_ids)
    state.updated_at = timestamp
    _write_state(state_path, state)
    overlay_pending = sum(1 for event_id in source_ids if event_id not in set(state.overlay_ids))
    speech_pending = sum(1 for event_id in source_ids if event_id not in set(state.speech_ids))
    return NotificationDeliveryResult(
        True, False, presentation.mode, len(events), overlay_sent, speech_sent,
        overlay_pending, speech_pending, tuple(dict.fromkeys(errors)),
    )


def deliver_pending_notifications(
    g: GlobalConfig,
    *,
    overlay_sender: Callable[[GlobalConfig, dict[str, object]], None] | None = None,
    speech_sender: Callable[[GlobalConfig, str], None] | None = None,
    now: float,
    state_dir: Path | None = None,
) -> NotificationDeliveryResult:
    """Serialize one complete source-read -> output -> ACK transaction."""
    if not g.trading.notifications_enabled:
        return _deliver_pending_notifications_unlocked(
            g, overlay_sender=overlay_sender, speech_sender=speech_sender,
            now=now, state_dir=state_dir,
        )
    base = _trading_state_dir(g, state_dir)
    with _delivery_lock(base):
        return _deliver_pending_notifications_unlocked(
            g, overlay_sender=overlay_sender, speech_sender=speech_sender,
            now=now, state_dir=state_dir,
        )
