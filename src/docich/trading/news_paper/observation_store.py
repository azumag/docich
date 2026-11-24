"""Dedicated observation store for news input (N02, #778).

Layout under ``<state_dir>/news-paper/observations/`` (the only directory this
module writes):

* ``<identity>/<digest16>.json`` — one immutable version per content digest.
  Holds timing provenance, normalized title, bounded excerpt and digest.  The
  full body is never stored.
* ``<identity>/deliveries.jsonl`` — append-only re-delivery log.  It never
  changes a version's ``first_seen_at`` or ``available_at``.
* ``quarantine/<n>.json`` — fixed code plus source id only.  No body, URL or
  transport text.

Writes are no-overwrite (``os.link`` on a temp file).  Restarting the process
rereads the same files, so first-seen times survive a restart.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .provenance import Provenance, ProvenanceError
from .collectors.fixture_feed import CollectedItem, Quarantine
from .normalization import FIXTURE_MAX_AGE

STORE_VERSION = "fixture-observation-store-v1"
MODE = "paper"
LIVE_ELIGIBLE = False

CODE_CORRECTION_TARGET_UNKNOWN = "CORRECTION_TARGET_UNKNOWN"
CODE_STORE_CORRUPT = "STORE_CORRUPT"

_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class StoreError(ValueError):
    """Fixed-code store error."""


def identity_of(source_id: str, source_item_id: str) -> str:
    return hashlib.sha256(f"{source_id}\x1f{source_item_id}".encode("utf-8")).hexdigest()[:32]


def _fmt(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(_TS_FORMAT)


def _parse(text: str) -> datetime:
    return datetime.strptime(text, _TS_FORMAT).replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class VersionRecord:
    version_id: str
    identity: str
    source_id: str
    source_item_id: str
    canonical_url: str
    title: str
    excerpt: str
    content_digest: str
    provenance: Provenance
    mode: str = MODE
    live_eligible: bool = LIVE_ELIGIBLE

    def to_dict(self) -> dict:
        return {
            "version_id": self.version_id,
            "identity": self.identity,
            "source_id": self.source_id,
            "source_item_id": self.source_item_id,
            "canonical_url": self.canonical_url,
            "title": self.title,
            "excerpt": self.excerpt,
            "content_digest": self.content_digest,
            "origin_published_at": _fmt(self.provenance.origin_published_at),
            "fetched_at": _fmt(self.provenance.fetched_at),
            "available_at": _fmt(self.provenance.available_at),
            "first_seen_at": _fmt(self.provenance.first_seen_at),
            "revision_kind": self.provenance.revision_kind,
            "supersedes": self.provenance.supersedes,
            "mode": self.mode,
            "live_eligible": self.live_eligible,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VersionRecord":
        try:
            provenance = Provenance(
                source_id=data["source_id"],
                origin_published_at=_parse(data["origin_published_at"]),
                fetched_at=_parse(data["fetched_at"]),
                available_at=_parse(data["available_at"]),
                first_seen_at=_parse(data["first_seen_at"]),
                revision_kind=data["revision_kind"],
                supersedes=data["supersedes"],
            )
            return cls(
                version_id=data["version_id"], identity=data["identity"],
                source_id=data["source_id"], source_item_id=data["source_item_id"],
                canonical_url=data["canonical_url"], title=data["title"],
                excerpt=data["excerpt"], content_digest=data["content_digest"],
                provenance=provenance, mode=data["mode"], live_eligible=data["live_eligible"],
            )
        except (KeyError, ValueError, ProvenanceError, TypeError):
            raise StoreError(CODE_STORE_CORRUPT) from None


@dataclass(frozen=True)
class PersistResult:
    status: str  # "original" | "revision" | "correction" | "redelivery"
    record: VersionRecord


class ObservationStore:
    def __init__(self, state_dir: Path, clock: Callable[[], datetime]) -> None:
        self._root = Path(state_dir) / "news-paper" / "observations"
        self._clock = clock
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / "quarantine").mkdir(exist_ok=True)

    # ----- write path -------------------------------------------------------

    def persist(self, item: CollectedItem, *, correction_of: str | None = None) -> PersistResult:
        identity = identity_of(item.source_id, item.source_item_id)
        digest16 = item.content_digest[:16]
        directory = self._root / identity
        directory.mkdir(exist_ok=True)
        path = directory / f"{digest16}.json"

        if path.exists():
            existing = self._read_version(path)
            self._append_delivery(directory, item.fetched_at, digest16)
            return PersistResult("redelivery", existing)

        prior = self._versions(directory)
        if correction_of is not None:
            if correction_of not in {v.version_id for v in prior}:
                raise StoreError(CODE_CORRECTION_TARGET_UNKNOWN)
            revision_kind, supersedes = "correction", correction_of
        elif prior:
            latest = max(prior, key=lambda v: (v.provenance.available_at, v.version_id))
            revision_kind, supersedes = "revision", latest.version_id
        else:
            revision_kind, supersedes = "original", None

        available_at = max(self._clock(), item.fetched_at)
        provenance = Provenance(
            source_id=item.source_id,
            origin_published_at=item.origin_published_at,
            fetched_at=item.fetched_at,
            available_at=available_at,
            first_seen_at=available_at,
            revision_kind=revision_kind,
            supersedes=supersedes,
        )
        record = VersionRecord(
            version_id=f"{identity}:{digest16}",
            identity=identity,
            source_id=item.source_id,
            source_item_id=item.source_item_id,
            canonical_url=item.canonical_url,
            title=item.title,
            excerpt=item.excerpt,
            content_digest=item.content_digest,
            provenance=provenance,
        )
        self._write_once(path, record.to_dict())
        return PersistResult(revision_kind, record)

    def quarantine(self, quarantined: Quarantine) -> Path:
        """Store a fixed code and source id only. Nothing else from the item is kept."""
        qdir = self._root / "quarantine"
        path = qdir / f"{uuid.uuid4().hex}.json"
        payload = {
            "source_id": quarantined.source_id,
            "code": quarantined.code,
            "item_index": quarantined.item_index,
            "recorded_at": _fmt(self._clock()),
            "mode": MODE,
            "live_eligible": LIVE_ELIGIBLE,
        }
        self._write_once(path, payload)
        return path

    # ----- read path --------------------------------------------------------

    def versions(self, identity: str) -> list[VersionRecord]:
        return sorted(self._versions(self._root / identity),
                      key=lambda v: (v.provenance.available_at, v.version_id))

    def effective_version(self, identity: str, at: datetime) -> VersionRecord | None:
        """Latest version whose ``available_at`` is at or before ``at``.

        A correction is invisible before its own ``available_at``, and older
        versions are never deleted, so the original stays inspectable.
        """
        at = at.astimezone(timezone.utc)
        visible = [v for v in self.versions(identity) if v.provenance.available_at <= at]
        return visible[-1] if visible else None

    def ready(self, at: datetime) -> list[VersionRecord]:
        """Observations usable for decisions at ``at``.

        Requires an available_at at or before ``at`` and a publication time that
        is not stale at ``at``.  Quarantined items are never in this store's
        version files, so they cannot appear here.
        """
        at = at.astimezone(timezone.utc)
        out: list[VersionRecord] = []
        for identity_dir in sorted(p for p in self._root.iterdir() if p.is_dir() and p.name != "quarantine"):
            for version in self._versions(identity_dir):
                if version.provenance.available_at > at:
                    continue
                if at - version.provenance.origin_published_at > FIXTURE_MAX_AGE:
                    continue
                out.append(version)
        return out

    def quarantine_codes(self) -> list[str]:
        qdir = self._root / "quarantine"
        return sorted(json.loads(p.read_text(encoding="utf-8"))["code"] for p in qdir.glob("*.json"))

    # ----- helpers ----------------------------------------------------------

    def _versions(self, directory: Path) -> list[VersionRecord]:
        if not directory.is_dir():
            return []
        return [self._read_version(p) for p in sorted(directory.glob("*.json"))]

    def _read_version(self, path: Path) -> VersionRecord:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise StoreError(CODE_STORE_CORRUPT) from None
        return VersionRecord.from_dict(data)

    def _append_delivery(self, directory: Path, received_at: datetime, digest16: str) -> None:
        line = json.dumps({"received_at": _fmt(received_at), "digest16": digest16}, sort_keys=True)
        with open(directory / "deliveries.jsonl", "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _write_once(self, path: Path, payload: dict) -> None:
        """Atomic create. ``os.link`` fails if the target exists, so nothing is overwritten."""
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            raise StoreError("VERSION_EXISTS") from None
        finally:
            tmp.unlink(missing_ok=True)
