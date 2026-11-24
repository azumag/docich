"""Single-publisher, atomic, schema/size-checked envelope publication."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .contract import Envelope, decode_envelope, encode_envelope

DEFAULT_MAX_BYTES = 256 * 1024


def publish_atomic(state_dir: Path, name: str, envelope: Envelope, *,
                   max_bytes: int = DEFAULT_MAX_BYTES) -> Path:
    """Write one envelope via same-FS temp file + fsync + os.replace."""
    if not name or "/" in name or "\\" in name:
        raise ValueError("invalid_publish_name")
    payload = json.dumps(encode_envelope(envelope), sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    if len(payload) > max_bytes:
        raise ValueError("payload_too_large")
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".mbridge-", suffix=".tmp", dir=str(directory))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, directory / name)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return directory / name


def load_published(path: Path) -> Envelope | None:
    """Read a published envelope; any malformed content is treated as absent."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    if len(raw) > DEFAULT_MAX_BYTES * 2:
        return None
    try:
        return decode_envelope(json.loads(raw))
    except (ValueError, TypeError):
        return None
