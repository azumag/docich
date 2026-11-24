"""Source registry and provenance records for news observations (N02, #778).

Contract sources (#775 frozen design v2 §1–§3/§5, v3 §1–§3, v4 §2; #776 N01):

* A source is recorded with its original publisher and, separately, any
  aggregator that delivered it.  The aggregator is never counted as the
  origin, and no LLM assigns source trust.
* ``fetched_at`` is the time the response was fully received; ``available_at``
  is the time the observation was validated and persisted.  ``first_seen_at``
  and the version identity are immutable once written.
* Only ``status == "fixture"`` sources exist in this module.  The registry has
  no live entries, and adding one is a separate approval (#778 condition 1).

Every numeric value here is a fixture-only placeholder.  None of them is an
approved operating value (TTL, quota, capacity, cadence), and they must not be
read as production settings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .classification import canonical, digest

PROVENANCE_VERSION = "fixture-provenance-v1"
REGISTRY_VERSION = "fixture-source-registry-v1"

ORIGIN_KINDS = frozenset({"primary", "aggregator"})
TIME_PRECISIONS = frozenset({"second", "minute", "day"})
SOURCE_STATUSES = frozenset({"fixture"})  # live is not a valid status in this module
REVISION_KINDS = frozenset({"original", "revision", "correction"})

CODE_SOURCE_UNKNOWN = "SOURCE_UNKNOWN"
CODE_SOURCE_INVALID = "SOURCE_INVALID"
CODE_SOURCE_DUPLICATE = "SOURCE_DUPLICATE"
CODE_PROVENANCE_INVALID = "PROVENANCE_INVALID"
CODE_TIME_ORDER = "TIME_ORDER_INVALID"
CODE_TIMEZONE_MISSING = "TIMEZONE_MISSING"

_SOURCE_ID_RE = re.compile(r"\A[a-z][a-z0-9_]{1,47}\Z")
_HOST_RE = re.compile(r"\A(?=.{1,253}\Z)[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?)+\Z")


class ProvenanceError(ValueError):
    """Fixed-code error. The message is a code only, never raw input."""


def _aware(moment: datetime, code: str = CODE_TIMEZONE_MISSING) -> datetime:
    if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
        raise ProvenanceError(code)
    return moment.astimezone(timezone.utc)


@dataclass(frozen=True)
class SourceSpec:
    """Fixture source schema. Describes what may be read; it grants no access."""

    source_id: str
    origin_kind: str
    delivered_by: str | None
    allowed_hosts: tuple[str, ...]
    allowed_path_prefixes: tuple[str, ...]
    allowed_query_keys: tuple[str, ...]
    time_field: str
    time_precision: str
    reliability_label: str
    asset_mapping_version: str
    usage_terms_ref: str
    status: str = "fixture"

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not _SOURCE_ID_RE.match(self.source_id):
            raise ProvenanceError(CODE_SOURCE_INVALID)
        if self.origin_kind not in ORIGIN_KINDS:
            raise ProvenanceError(CODE_SOURCE_INVALID)
        if self.origin_kind == "aggregator" and not self.delivered_by:
            raise ProvenanceError(CODE_SOURCE_INVALID)
        if self.origin_kind == "primary" and self.delivered_by is not None:
            raise ProvenanceError(CODE_SOURCE_INVALID)
        if self.status not in SOURCE_STATUSES:
            raise ProvenanceError(CODE_SOURCE_INVALID)
        if self.time_precision not in TIME_PRECISIONS:
            raise ProvenanceError(CODE_SOURCE_INVALID)
        if not self.allowed_hosts:
            raise ProvenanceError(CODE_SOURCE_INVALID)
        hosts = tuple(host.lower() for host in self.allowed_hosts)
        if any(not _HOST_RE.match(host) for host in hosts):
            raise ProvenanceError(CODE_SOURCE_INVALID)
        for prefix in self.allowed_path_prefixes:
            if not prefix.startswith("/") or ".." in prefix:
                raise ProvenanceError(CODE_SOURCE_INVALID)
        object.__setattr__(self, "allowed_hosts", hosts)
        object.__setattr__(self, "allowed_path_prefixes", tuple(self.allowed_path_prefixes))
        object.__setattr__(self, "allowed_query_keys", tuple(sorted(self.allowed_query_keys)))

    def identity_digest(self) -> str:
        return digest({"source": self.source_id, "version": REGISTRY_VERSION,
                       "hosts": self.allowed_hosts, "paths": self.allowed_path_prefixes})


class SourceRegistry:
    """Immutable map of fixture sources. Lookups fail closed on unknown ids."""

    def __init__(self, specs: Iterable[SourceSpec]) -> None:
        table: dict[str, SourceSpec] = {}
        for spec in specs:
            if not isinstance(spec, SourceSpec):
                raise ProvenanceError(CODE_SOURCE_INVALID)
            if spec.source_id in table:
                raise ProvenanceError(CODE_SOURCE_DUPLICATE)
            table[spec.source_id] = spec
        self._table = dict(table)

    def get(self, source_id: str) -> SourceSpec:
        spec = self._table.get(source_id)
        if spec is None:
            raise ProvenanceError(CODE_SOURCE_UNKNOWN)
        return spec

    def __contains__(self, source_id: object) -> bool:
        return source_id in self._table

    def __len__(self) -> int:
        return len(self._table)

    @property
    def live_source_count(self) -> int:
        """Always 0: no live source is registered in this fixture-only version."""
        return sum(1 for spec in self._table.values() if spec.status != "fixture")


@dataclass(frozen=True)
class Provenance:
    """Timing and origin for one stored observation version. Write-once."""

    source_id: str
    origin_published_at: datetime
    fetched_at: datetime
    available_at: datetime
    first_seen_at: datetime
    revision_kind: str
    supersedes: str | None

    def __post_init__(self) -> None:
        for name in ("origin_published_at", "fetched_at", "available_at", "first_seen_at"):
            object.__setattr__(self, name, _aware(getattr(self, name)))
        if self.revision_kind not in REVISION_KINDS:
            raise ProvenanceError(CODE_PROVENANCE_INVALID)
        if self.revision_kind == "original" and self.supersedes is not None:
            raise ProvenanceError(CODE_PROVENANCE_INVALID)
        if self.revision_kind != "original" and not self.supersedes:
            raise ProvenanceError(CODE_PROVENANCE_INVALID)
        # Receipt cannot precede publication, and persistence cannot precede receipt.
        if not (self.origin_published_at <= self.fetched_at <= self.available_at):
            raise ProvenanceError(CODE_TIME_ORDER)
        if self.first_seen_at > self.available_at:
            raise ProvenanceError(CODE_TIME_ORDER)

    def to_dict(self) -> dict[str, str | None]:
        return {
            "source_id": self.source_id,
            "origin_published_at": _fmt(self.origin_published_at),
            "fetched_at": _fmt(self.fetched_at),
            "available_at": _fmt(self.available_at),
            "first_seen_at": _fmt(self.first_seen_at),
            "revision_kind": self.revision_kind,
            "supersedes": self.supersedes,
            "provenance_version": PROVENANCE_VERSION,
        }


def _fmt(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def timing_fingerprint(provenance: Provenance) -> bytes:
    """Stable bytes for comparing immutable fields across restarts and re-deliveries."""
    return canonical({
        "source_id": provenance.source_id,
        "origin_published_at": _fmt(provenance.origin_published_at),
        "first_seen_at": _fmt(provenance.first_seen_at),
    })
