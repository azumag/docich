"""The reading of the owner's LIVE switch: ``EnableReason`` and ``EnableResult`` only.

The file itself is read and written by ``control.py``.  These types are kept here because the
ledger, cycle, executor and owner CLI all speak in terms of one "gate reading".
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


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
    SWITCHED_OFF = "switched_off"
    CHECK_ERROR = "check_error"


@dataclass(frozen=True)
class EnableResult:
    enabled: bool
    reasons: tuple[EnableReason, ...]
    mode: str | None = None
    file_sha256: str | None = None

    @property
    def allows_orders(self) -> bool:
        return self.enabled and self.mode == "trade"
