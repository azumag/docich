"""Owner-only enable gate.  Reads one file; writes nothing; never raises."""
from __future__ import annotations

import datetime as dt
import json
import os
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .canonical import is_sha256_hex, sha256_hex
from .paths import live_layout

MAX_ENABLE_BYTES = 4096
_KEYS = {"schema_version", "kind", "mode", "policy_set_digest", "code_revision",
         "created_at", "expires_at", "note"}
_REQUIRED = _KEYS - {"note"}


class EnableReason(str, Enum):
    FILE_MISSING = "file_missing"
    ROOT_INVALID = "root_invalid"
    OWNER_DIR_INVALID = "owner_dir_invalid"
    NOT_REGULAR = "not_regular"
    OWNER_MISMATCH = "owner_mismatch"
    MODE_NOT_0600 = "mode_not_0600"
    TOO_LARGE = "too_large"
    UNREADABLE = "unreadable"
    CORRUPT_JSON = "corrupt_json"
    SCHEMA_INVALID = "schema_invalid"
    UNKNOWN_KEYS = "unknown_keys"
    EXPIRED = "expired"
    NOT_YET_VALID = "not_yet_valid"
    POLICY_SET_DIGEST_MISMATCH = "policy_set_digest_mismatch"
    CODE_REVISION_MISMATCH = "code_revision_mismatch"
    CHECK_ERROR = "check_error"


@dataclass(frozen=True)
class EnableResult:
    enabled: bool
    reasons: tuple[EnableReason, ...]
    mode: str | None = None
    file_sha256: str | None = None
    expires_at: float | None = None
    policy_set_digest: str | None = None
    code_revision: str | None = None

    @property
    def allows_orders(self) -> bool:
        return self.enabled and self.mode == "trade"


def _no(*reasons: EnableReason) -> EnableResult:
    return EnableResult(False, tuple(reasons))


def _aware_epoch(text: object) -> float | None:
    if not isinstance(text, str):
        return None
    try:
        moment = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.timestamp()


def _dir_ok(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return (stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
            and (info.st_mode & 0o777) == 0o700)


def live_enabled(state_dir, *, policy_set_digest: str, code_revision: str,
                 now: float) -> EnableResult:
    try:
        layout = live_layout(state_dir)
        if not _dir_ok(layout.root):
            return _no(EnableReason.ROOT_INVALID if layout.root.exists() else EnableReason.FILE_MISSING)
        if not _dir_ok(layout.owner_dir):
            return _no(EnableReason.OWNER_DIR_INVALID if layout.owner_dir.exists()
                       else EnableReason.FILE_MISSING)
        try:
            fd = os.open(layout.enable_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return _no(EnableReason.FILE_MISSING)
        except OSError:
            return _no(EnableReason.NOT_REGULAR)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                return _no(EnableReason.NOT_REGULAR)
            if info.st_uid != os.geteuid():
                return _no(EnableReason.OWNER_MISMATCH)
            if (info.st_mode & 0o777) != 0o600:
                return _no(EnableReason.MODE_NOT_0600)
            if info.st_size > MAX_ENABLE_BYTES:
                return _no(EnableReason.TOO_LARGE)
            raw = os.read(fd, MAX_ENABLE_BYTES + 1)
        finally:
            os.close(fd)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return _no(EnableReason.CORRUPT_JSON)
        if not isinstance(data, dict):
            return _no(EnableReason.SCHEMA_INVALID)
        if set(data) - _KEYS:
            return _no(EnableReason.UNKNOWN_KEYS)
        created = _aware_epoch(data.get("created_at"))
        expires = _aware_epoch(data.get("expires_at"))
        note = data.get("note", "")
        if (not _REQUIRED <= set(data) or data["schema_version"] != 1
                or type(data["schema_version"]) is not int
                or data["kind"] != "docich-live-enable" or data["mode"] not in ("shadow", "trade")
                or not is_sha256_hex(data["policy_set_digest"])
                or not (isinstance(data["code_revision"], str) and len(data["code_revision"]) == 40
                        and all(c in "0123456789abcdef" for c in data["code_revision"]))
                or created is None or expires is None or expires <= created
                or not isinstance(note, str) or len(note) > 200):
            return _no(EnableReason.SCHEMA_INVALID)
        reasons = []
        if now >= expires:
            reasons.append(EnableReason.EXPIRED)
        if now < created:
            reasons.append(EnableReason.NOT_YET_VALID)
        if data["policy_set_digest"] != policy_set_digest:
            reasons.append(EnableReason.POLICY_SET_DIGEST_MISMATCH)
        if data["code_revision"] != code_revision:
            reasons.append(EnableReason.CODE_REVISION_MISMATCH)
        if reasons:
            return _no(*reasons)
        return EnableResult(True, (), mode=data["mode"], file_sha256=sha256_hex(raw),
                            expires_at=expires, policy_set_digest=data["policy_set_digest"],
                            code_revision=data["code_revision"])
    except Exception:  # fail closed, never raise
        return _no(EnableReason.CHECK_ERROR)
