"""Read-only API key loading.  Fail closed; never print, log or raise a key or a path's content.

The key lives in a 0600 file inside the owner-only directory of the LIVE state dir (or, when asked
explicitly, in two environment variables).  This module never creates files and never writes.
"""
from __future__ import annotations

import json
import os
import re
import stat
from enum import Enum
from pathlib import Path
from typing import MutableMapping

from ...live.paths import live_layout

KEY_FILENAME = "bitbank_readonly_key.json"
ENV_KEY, ENV_SECRET = "DOCICH_BITBANK_RO_API_KEY", "DOCICH_BITBANK_RO_API_SECRET"
MAX_BYTES = 2048
_VALUE_RE = re.compile(r"^[\x21-\x7e]{8,256}$")
_KEYS = {"schema_version", "kind", "api_key", "api_secret", "note"}
_REQUIRED = {"schema_version", "kind", "api_key", "api_secret"}


class CredentialReason(str, Enum):
    ROOT_INVALID = "root_invalid"
    OWNER_DIR_INVALID = "owner_dir_invalid"
    FILE_MISSING = "file_missing"
    NOT_REGULAR = "not_regular"
    OWNER_MISMATCH = "owner_mismatch"
    MODE_NOT_0600 = "mode_not_0600"
    TOO_LARGE = "too_large"
    CORRUPT_JSON = "corrupt_json"
    SCHEMA_INVALID = "schema_invalid"
    UNKNOWN_KEYS = "unknown_keys"
    VALUE_INVALID = "value_invalid"
    ENV_MISSING = "env_missing"
    CHECK_ERROR = "check_error"


class CredentialsUnavailable(RuntimeError):
    """The message is only the reason code (never a value, never a path)."""

    def __init__(self, reason: CredentialReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


class Credentials:
    """Holds the key pair; repr, str, pickle and comparison reveal nothing."""
    __slots__ = ("_key", "_secret")

    def __init__(self, key: str, secret: str) -> None:
        object.__setattr__(self, "_key", key)
        object.__setattr__(self, "_secret", secret)

    def reveal(self) -> tuple[str, str]:
        """For the transport only."""
        return self._key, self._secret

    def __setattr__(self, name, value):
        raise AttributeError("immutable")

    def __repr__(self) -> str:
        return "Credentials(<redacted>)"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("not picklable")

    def __eq__(self, other):
        return NotImplemented

    __hash__ = None


def _dir_ok(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and (info.st_mode & 0o777) == 0o700


def _valid(key: object, secret: object) -> bool:
    return (isinstance(key, str) and isinstance(secret, str) and bool(_VALUE_RE.match(key))
            and bool(_VALUE_RE.match(secret)))


def load_credentials(state_dir, *, source: str = "file",
                     environ: MutableMapping[str, str] | None = None) -> Credentials:
    try:
        if source == "env":
            env = os.environ if environ is None else environ
            key, secret = env.pop(ENV_KEY, None), env.pop(ENV_SECRET, None)   # not inherited by children
            if key is None or secret is None:
                raise CredentialsUnavailable(CredentialReason.ENV_MISSING)
            if not _valid(key, secret):
                raise CredentialsUnavailable(CredentialReason.VALUE_INVALID)
            return Credentials(key, secret)
        if source != "file":
            raise CredentialsUnavailable(CredentialReason.CHECK_ERROR)
        layout = live_layout(state_dir)
        if not _dir_ok(layout.root):
            raise CredentialsUnavailable(CredentialReason.ROOT_INVALID if layout.root.exists()
                                         else CredentialReason.FILE_MISSING)
        if not _dir_ok(layout.owner_dir):
            raise CredentialsUnavailable(CredentialReason.OWNER_DIR_INVALID if layout.owner_dir.exists()
                                         else CredentialReason.FILE_MISSING)
        try:
            fd = os.open(layout.owner_dir / KEY_FILENAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            raise CredentialsUnavailable(CredentialReason.FILE_MISSING) from None
        except OSError:
            raise CredentialsUnavailable(CredentialReason.NOT_REGULAR) from None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise CredentialsUnavailable(CredentialReason.NOT_REGULAR)
            if info.st_uid != os.geteuid():
                raise CredentialsUnavailable(CredentialReason.OWNER_MISMATCH)
            if (info.st_mode & 0o777) != 0o600:
                raise CredentialsUnavailable(CredentialReason.MODE_NOT_0600)
            if info.st_size > MAX_BYTES:
                raise CredentialsUnavailable(CredentialReason.TOO_LARGE)
            raw = os.read(fd, MAX_BYTES + 1)
        finally:
            os.close(fd)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise CredentialsUnavailable(CredentialReason.CORRUPT_JSON) from None
        if not isinstance(data, dict):
            raise CredentialsUnavailable(CredentialReason.SCHEMA_INVALID)
        if set(data) - _KEYS:
            raise CredentialsUnavailable(CredentialReason.UNKNOWN_KEYS)
        if (not _REQUIRED <= set(data) or data["schema_version"] != 1 or type(data["schema_version"]) is not int
                or data["kind"] != "docich-bitbank-readonly-key"):
            raise CredentialsUnavailable(CredentialReason.SCHEMA_INVALID)
        if not _valid(data["api_key"], data["api_secret"]):
            raise CredentialsUnavailable(CredentialReason.VALUE_INVALID)
        return Credentials(data["api_key"], data["api_secret"])
    except CredentialsUnavailable:
        raise
    except Exception:
        raise CredentialsUnavailable(CredentialReason.CHECK_ERROR) from None
