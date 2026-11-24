"""Fixture-only news collector (N02, #778).

The collector never opens a socket.  All I/O goes through an injected
``Transport`` and ``Resolver``; this module imports no network library, and
tests prove it structurally.  The real transport is out of scope and needs a
separate approval (#778 condition 1).

Checks, in order: source allowlist → canonical https URL → DNS resolve and
public-IP check on every address → one GET pinned to the validated IP → no
redirects → size limits (raw and decompressed) → DTD/entity refusal → item
limit → per-item normalization.  Failures carry fixed codes only; raw URLs,
bodies and transport messages are never logged or returned.
"""
from __future__ import annotations

import email.utils
import ipaddress
import re
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Mapping, Protocol, Sequence

from ..normalization import (
    NormalizationError,
    canonical_url,
    content_digest,
    excerpt,
    normalize_body,
    normalize_title,
    parse_published_at,
    secret_suspected,
)
from ..provenance import SourceRegistry

COLLECTOR_VERSION = "fixture-collector-v1"

# Fixture-only values (not approved operating settings).
FIXTURE_CONNECT_TIMEOUT_S = 3.0
FIXTURE_READ_TIMEOUT_S = 5.0
FIXTURE_TOTAL_TIMEOUT_S = 10.0
FIXTURE_MAX_BODY_BYTES = 262_144
FIXTURE_MAX_EXPANDED_BYTES = 262_144
FIXTURE_MAX_ITEMS = 50
FIXTURE_RETRY_AFTER_CAP_S = 300
FIXTURE_CYCLE_REQUEST_BUDGET = 10

CODE_SOURCE_UNKNOWN = "SOURCE_UNKNOWN"
CODE_URL_NOT_ALLOWED = "URL_NOT_ALLOWED"
CODE_DNS_NON_PUBLIC = "DNS_NON_PUBLIC"
CODE_TIMEOUT = "TIMEOUT"
CODE_TRANSPORT_FAILED = "TRANSPORT_FAILED"
CODE_REDIRECT_REFUSED = "REDIRECT_REFUSED"
CODE_HTTP_RETRY = "HTTP_RETRY"
CODE_HTTP_STATUS_UNEXPECTED = "HTTP_STATUS_UNEXPECTED"
CODE_PAYLOAD_LIMIT = "PAYLOAD_LIMIT"
CODE_ENCODING_UNSUPPORTED = "ENCODING_UNSUPPORTED"
CODE_XML_DTD_REFUSED = "XML_DTD_REFUSED"
CODE_XML_INVALID = "XML_INVALID"
CODE_ITEM_LIMIT = "ITEM_LIMIT"
CODE_ITEM_ID_MISSING = "ITEM_ID_MISSING"
CODE_ITEM_INVALID = "ITEM_INVALID"
CODE_TIME_MISSING = "TIME_MISSING"
CODE_BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
CODE_SECRET_SUSPECTED = "SECRET_SUSPECTED"

_BLOCKED_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "100.64.0.0/10",     # shared / CGNAT, includes tailnet ranges
    "192.0.0.0/24",
    "198.18.0.0/15",     # benchmarking
    "192.88.99.0/24",
    "169.254.169.254/32",  # cloud metadata (also link-local)
))
_DTD_RE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


def is_public_ip(text: str) -> bool:
    """Reject private, loopback, link-local, metadata, shared and reserved ranges."""
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_public_ip(str(ip.ipv4_mapped))
        if ip.sixtofour is not None or ip.teredo is not None:
            return False  # tunnelled forms can point at private hosts; refuse
    if not ip.is_global:
        return False
    if any(ip in net for net in _BLOCKED_NETWORKS if ip.version == net.version):
        return False
    return True


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes


class TransportTimeout(Exception):
    """Raised by a transport on connect/read/total timeout. Message is never used."""


class TransportFailure(Exception):
    """Raised by a transport on any other failure. Message is never used."""


class Transport(Protocol):
    def get(self, url: str, *, pinned_ip: str, headers: Mapping[str, str],
            connect_timeout: float, read_timeout: float, total_timeout: float,
            max_bytes: int) -> Response: ...


class Resolver(Protocol):
    def resolve(self, host: str) -> Sequence[str]: ...


@dataclass(frozen=True)
class CollectedItem:
    source_id: str
    source_item_id: str
    canonical_url: str
    title: str
    excerpt: str
    content_digest: str
    origin_published_at: datetime
    fetched_at: datetime


@dataclass(frozen=True)
class Quarantine:
    source_id: str
    code: str
    item_index: int | None = None


@dataclass
class FetchOutcome:
    source_id: str
    code: str | None = None          # fetch-level failure code; None when the response was processed
    backoff_seconds: int = 0
    items: list[CollectedItem] = field(default_factory=list)
    quarantined: list[Quarantine] = field(default_factory=list)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


def _decode_body(response: Response) -> bytes:
    if len(response.body) > FIXTURE_MAX_BODY_BYTES:
        raise NormalizationError(CODE_PAYLOAD_LIMIT)
    encoding = (_header(response.headers, "content-encoding") or "identity").strip().lower()
    if encoding in ("", "identity"):
        return response.body
    if encoding != "gzip":
        raise NormalizationError(CODE_ENCODING_UNSUPPORTED)
    # Bounded inflate: stop at the expanded limit, never allocate past it.
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = inflater.decompress(response.body, FIXTURE_MAX_EXPANDED_BYTES + 1)
    except zlib.error:
        raise NormalizationError(CODE_ENCODING_UNSUPPORTED) from None
    if len(out) > FIXTURE_MAX_EXPANDED_BYTES or inflater.unconsumed_tail:
        raise NormalizationError(CODE_PAYLOAD_LIMIT)
    return out


def _parse_pubdate(raw: str | None, now: datetime) -> datetime:
    if not raw:
        raise NormalizationError(CODE_TIME_MISSING)
    try:
        parsed = email.utils.parsedate_to_datetime(raw.strip())
    except (TypeError, ValueError):
        raise NormalizationError("TIME_INVALID") from None
    if parsed.tzinfo is None:
        raise NormalizationError("TIMEZONE_MISSING")
    return parse_published_at(parsed.isoformat(), now=now)


class FixtureCollector:
    def __init__(self, registry: SourceRegistry, transport: Transport,
                 resolver: Resolver, clock: Callable[[], datetime]) -> None:
        self._registry = registry
        self._transport = transport
        self._resolver = resolver
        self._clock = clock

    def fetch(self, source_id: str, url: str) -> FetchOutcome:
        outcome = FetchOutcome(source_id=source_id)
        try:
            spec = self._registry.get(source_id)
        except Exception:
            outcome.code = CODE_SOURCE_UNKNOWN
            return outcome
        try:
            target = canonical_url(url, allowed_query_keys=spec.allowed_query_keys)
        except NormalizationError as exc:
            outcome.code = str(exc)
            return outcome
        host, path = _split_target(target)
        if host not in spec.allowed_hosts or not any(
                path.startswith(prefix) for prefix in spec.allowed_path_prefixes):
            outcome.code = CODE_URL_NOT_ALLOWED
            return outcome

        try:
            addresses = tuple(self._resolver.resolve(host))
        except Exception:
            outcome.code = CODE_DNS_NON_PUBLIC
            return outcome
        if not addresses or not all(is_public_ip(a) for a in addresses):
            outcome.code = CODE_DNS_NON_PUBLIC
            return outcome

        try:
            response = self._transport.get(
                target, pinned_ip=addresses[0],
                headers={"Accept": "application/rss+xml, application/xml;q=0.9", "Accept-Encoding": "gzip"},
                connect_timeout=FIXTURE_CONNECT_TIMEOUT_S,
                read_timeout=FIXTURE_READ_TIMEOUT_S,
                total_timeout=FIXTURE_TOTAL_TIMEOUT_S,
                max_bytes=FIXTURE_MAX_BODY_BYTES,
            )
        except TransportTimeout:
            outcome.code = CODE_TIMEOUT
            return outcome
        except Exception:
            outcome.code = CODE_TRANSPORT_FAILED
            return outcome
        # Receipt is complete here; fetched_at is taken after the body is fully read.
        fetched_at = self._clock()

        if 300 <= response.status < 400:
            outcome.code = CODE_REDIRECT_REFUSED
            return outcome
        if response.status == 429 or 500 <= response.status < 600:
            outcome.code = CODE_HTTP_RETRY
            outcome.backoff_seconds = _retry_after(response.headers)
            return outcome
        if response.status != 200:
            outcome.code = CODE_HTTP_STATUS_UNEXPECTED
            return outcome

        try:
            body = _decode_body(response)
        except NormalizationError as exc:
            outcome.code = str(exc)
            return outcome
        if _DTD_RE.search(body):
            outcome.code = CODE_XML_DTD_REFUSED
            return outcome
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            outcome.code = CODE_XML_INVALID
            return outcome

        items = root.findall(".//item")
        if len(items) > FIXTURE_MAX_ITEMS:
            outcome.quarantined.append(Quarantine(source_id, CODE_ITEM_LIMIT))
            items = items[:FIXTURE_MAX_ITEMS]
        for index, node in enumerate(items):
            try:
                outcome.items.append(self._item(spec.source_id, spec.allowed_query_keys, node, fetched_at))
            except NormalizationError as exc:
                outcome.quarantined.append(Quarantine(source_id, str(exc), index))
        return outcome

    def _item(self, source_id: str, allowed_keys: tuple[str, ...], node: ET.Element,
              fetched_at: datetime) -> CollectedItem:
        def text(tag: str) -> str | None:
            found = node.find(tag)
            return found.text if found is not None and found.text is not None else None

        guid = (text("guid") or "").strip()
        link_raw = (text("link") or "").strip()
        if not link_raw:
            raise NormalizationError(CODE_ITEM_INVALID)
        link = canonical_url(link_raw, allowed_query_keys=allowed_keys)
        # Without a publisher id the canonical URL is the only identity; it is never inferred from the title.
        item_id = guid or link
        if len(item_id) > 512:
            raise NormalizationError(CODE_ITEM_ID_MISSING)
        title = normalize_title(text("title") or "")
        body = normalize_body(text("description") or "")
        if secret_suspected(title) or secret_suspected(body):
            raise NormalizationError(CODE_SECRET_SUSPECTED)
        published = _parse_pubdate(text("pubDate"), fetched_at)
        if published > fetched_at:
            raise NormalizationError("TIME_FUTURE")
        return CollectedItem(
            source_id=source_id,
            source_item_id=item_id,
            canonical_url=link,
            title=title,
            excerpt=excerpt(body),
            content_digest=content_digest(body),
            origin_published_at=published,
            fetched_at=fetched_at,
        )


def _split_target(target: str) -> tuple[str, str]:
    rest = target[len("https://"):]
    host, _, tail = rest.partition("/")
    return host, "/" + tail.split("?", 1)[0]


def _retry_after(headers: Mapping[str, str]) -> int:
    raw = _header(headers, "retry-after")
    if raw is None or not raw.strip().isdigit():
        return 0
    return min(int(raw.strip()), FIXTURE_RETRY_AFTER_CAP_S)


def run_cycle(collector: FixtureCollector,
              jobs: Sequence[tuple[str, str]],
              *, budget: int = FIXTURE_CYCLE_REQUEST_BUDGET) -> list[FetchOutcome]:
    """Sequential fixture cycle. One source failing does not stop the others.

    Requests run one at a time, so concurrency is 1 in this fixture version.
    Jobs past ``budget`` are reported as ``BUDGET_EXHAUSTED`` and never fetched.
    """
    results: list[FetchOutcome] = []
    for index, (source_id, url) in enumerate(jobs):
        if index >= budget:
            results.append(FetchOutcome(source_id=source_id, code=CODE_BUDGET_EXHAUSTED))
            continue
        results.append(collector.fetch(source_id, url))
    return results
