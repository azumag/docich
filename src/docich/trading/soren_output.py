"""Narrow adapters from paper notifications to existing Soren viewer queues."""
from __future__ import annotations

from pathlib import Path

from ..config import GlobalConfig
from ..overlay_queue import append_event


class SorenOutputError(RuntimeError):
    """Raised when an existing Soren viewer-output queue cannot accept output."""


def resolve_soren_root(g: GlobalConfig) -> Path:
    raw = (g.webui.soren_root or "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = g.repo_root / candidate
        return candidate.resolve()
    candidate = g.repo_root / "games" / "soviet_now"
    if (candidate / "eloop_lib.sh").is_file():
        return candidate.resolve()
    cwd = Path.cwd()
    if (cwd / "eloop_lib.sh").is_file():
        return cwd.resolve()
    return candidate.resolve()


def send_overlay(g: GlobalConfig, payload: dict[str, object]) -> None:
    try:
        append_event(resolve_soren_root(g), payload, strict=True, regenerate=True)
    except Exception as exc:
        raise SorenOutputError("Soren overlay queue delivery failed") from exc


def enqueue_speech(g: GlobalConfig, text: str) -> None:
    # Reuse the same production Soren comment-audio queue used by Web UI.  The
    # import stays lazy so disabled notifications do not load the large Web UI
    # module or touch Soren runtime paths.
    try:
        from .. import webui
        result = webui._enqueue_audio_text(resolve_soren_root(g), text, "crypto_paper")
    except Exception as exc:
        raise SorenOutputError("Soren audio queue delivery failed") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise SorenOutputError("Soren audio queue rejected notification")
