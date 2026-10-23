"""Bounded primary-source adapter for the research-paper corner.

This slice bridges URL-only discovery to the structured draft generator without
using search snippets as facts. It currently supports modern arXiv abstract
pages only. The selected page is fetched through ``WebBroker`` and converted to
trusted metadata plus ``VerifiedWebBundle`` material derived from the same
receipt. PDF/MIME/SSRF boundaries remain unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import hashlib
import math
from pathlib import Path
import re
import tempfile
import time
from collections.abc import Mapping, Sequence
from typing import Callable
from urllib.parse import urlsplit

from .research_paper_discovery import PaperCandidate
from .reply_research_web import Receipt, WebBroker, canonical_url
from .web_material import MAX_EXCERPT_BYTES, VerifiedWebBundle, VerifiedWebMaterial

ENABLE_ENV = "DOCICH_RESEARCH_PAPER_PRIMARY_ENABLED"
MAX_TIMEOUT_SEC = 20.0
MAX_TEXT_BYTES = 16384
MAX_QUERIES = 3
MAX_AUTHORS = 24
_ARXIV_PATH_RE = re.compile(r"^/abs/\d{4}\.\d{4,5}(?:v\d+)?$", re.IGNORECASE)
_V1_DATE_RE = re.compile(
    r"\[v1\]\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s+"
    r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})\b"
)
_DOI_RE = re.compile(r"\bDOI:\s*(10\.\d{4,9}/[^\s]+)", re.IGNORECASE)
_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class PrimaryPaperResult:
    status: str
    metadata: Mapping[str, object] | None = field(default=None, repr=False)
    bundle: VerifiedWebBundle | None = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.metadata is not None and self.bundle is not None


def _clean(value: str, *, maximum: int) -> str:
    text = " ".join(value.split())
    if (not 1 <= len(text) <= maximum
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in text)):
        raise ValueError("invalid_source")
    return text


def _segment(text: str, start: str, stops: Sequence[str], *, maximum: int) -> str:
    begin = text.find(start)
    if begin < 0:
        raise ValueError("invalid_source")
    begin += len(start)
    end = len(text)
    for stop in stops:
        index = text.find(stop, begin)
        if index >= 0:
            end = min(end, index)
    if end <= begin:
        raise ValueError("invalid_source")
    return _clean(text[begin:end], maximum=maximum)


def _queries_used(values: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= MAX_QUERIES:
        raise ValueError("invalid_input")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("invalid_input")
        text = " ".join(value.split())
        if (not 1 <= len(text) <= 256
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in text)):
            raise ValueError("invalid_input")
        result.append(text)
    return tuple(result)


def _candidate(value: object, queries: tuple[str, ...]) -> PaperCandidate:
    if not isinstance(value, PaperCandidate):
        raise ValueError("invalid_input")
    url = canonical_url(value.url)
    if url is None or url != value.url or value.source_kind != "preprint":
        raise ValueError("unsupported_source")
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if (host != "arxiv.org" or parsed.query or parsed.fragment
            or not _ARXIV_PATH_RE.fullmatch(parsed.path)):
        raise ValueError("unsupported_source")
    if (type(value.rank) is not int or value.rank < 1
            or not value.query_indexes
            or any(type(index) is not int or not 0 <= index < len(queries)
                   for index in value.query_indexes)):
        raise ValueError("invalid_input")
    if tuple(sorted(set(value.query_indexes))) != value.query_indexes:
        raise ValueError("invalid_input")
    return value


def _receipt(value: object, url: str) -> Receipt:
    if not isinstance(value, Receipt) or value.url != url:
        raise ValueError("invalid_source")
    text = value.text
    if (not isinstance(text, str) or not text
            or len(text.encode("utf-8")) > MAX_TEXT_BYTES
            or not isinstance(value.sha256, str) or not _HEX64_RE.fullmatch(value.sha256)
            or not isinstance(value.text_sha256, str) or not _HEX64_RE.fullmatch(value.text_sha256)
            or hashlib.sha256(text.encode("utf-8")).hexdigest() != value.text_sha256):
        raise ValueError("invalid_source")
    return value


def _published_at(text: str) -> str:
    match = _V1_DATE_RE.search(text)
    if match is None:
        raise ValueError("invalid_source")
    day, month, year = match.groups()
    try:
        return date(int(year), _MONTHS[month], int(day)).isoformat()
    except (KeyError, ValueError) as exc:
        raise ValueError("invalid_source") from exc


def _authors(text: str) -> list[str]:
    raw = _segment(
        text,
        "Authors:",
        ("View a PDF", "Download a PDF", "Abstract:", "Comments:", "Subjects:"),
        maximum=2400,
    )
    names = [part.strip() for part in re.split(r",|\s+and\s+", raw) if part.strip()]
    if not 1 <= len(names) <= MAX_AUTHORS:
        raise ValueError("invalid_source")
    result: list[str] = []
    for name in names:
        clean = _clean(name, maximum=160)
        if clean not in result:
            result.append(clean)
    if not result:
        raise ValueError("invalid_source")
    return result


def _metadata_from_arxiv(text: str, url: str) -> dict[str, object]:
    title = _segment(text, "Title:", ("Authors:",), maximum=500)
    authors = _authors(text)
    abstract = _segment(
        text,
        "Abstract:",
        ("Comments:", "Subjects:", "Cite as:", "Journal reference:", "DOI:", "Submission history"),
        maximum=8000,
    )
    # Parsing the abstract is deliberate even though it is not duplicated into
    # metadata: failure prevents a title-only/metadata-only page from becoming
    # a trusted paper input. The verified receipt carries the abstract onward.
    if not abstract:
        raise ValueError("invalid_source")
    subjects = _segment(
        text,
        "Subjects:",
        ("Cite as:", "Journal reference:", "DOI:", "Report number:", "Submission history"),
        maximum=1200,
    )
    field = _clean(subjects.split(";", 1)[0], maximum=160)
    metadata: dict[str, object] = {
        "title": title,
        "authors": authors,
        "affiliations": [],
        "field": field,
        "publishedAt": _published_at(text),
        "reviewStatus": "preprint",
        "url": url,
        "primarySources": [url],
    }
    doi = _DOI_RE.search(text)
    if doi is not None:
        value = doi.group(1).rstrip(".,;)")
        metadata["doi"] = _clean(value, maximum=160)
    return metadata


def _material(receipt: Receipt, query_indexes: tuple[int, ...]) -> VerifiedWebMaterial:
    raw = receipt.text.encode("utf-8")
    excerpt = (
        receipt.text if len(raw) <= MAX_EXCERPT_BYTES
        else raw[:MAX_EXCERPT_BYTES].decode("utf-8", errors="ignore").rstrip()
    )
    if not excerpt:
        raise ValueError("invalid_source")
    return VerifiedWebMaterial(
        url=receipt.url,
        body_sha256=receipt.sha256,
        text_sha256=receipt.text_sha256,
        excerpt_sha256=hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
        excerpt=excerpt,
        query_indexes=query_indexes,
    )


def fetch_primary_paper(
    candidate: PaperCandidate,
    *,
    queries_used: Sequence[str],
    env: Mapping[str, str],
    timeout_sec: float = MAX_TIMEOUT_SEC,
    broker=None,
    clock: Callable[[], float] = time.monotonic,
) -> PrimaryPaperResult:
    """Fetch one selected arXiv candidate and build #2689-compatible inputs."""
    if not isinstance(env, Mapping):
        return PrimaryPaperResult("invalid_config")
    flag = env.get(ENABLE_ENV, "0")
    if flag == "0":
        return PrimaryPaperResult("disabled")
    if (flag != "1" or type(timeout_sec) not in (int, float)
            or not math.isfinite(timeout_sec) or not 0 < float(timeout_sec) <= MAX_TIMEOUT_SEC):
        return PrimaryPaperResult("invalid_config")
    try:
        queries = _queries_used(queries_used)
        selected = _candidate(candidate, queries)
    except ValueError as exc:
        status = "unsupported_source" if str(exc) == "unsupported_source" else "invalid_input"
        return PrimaryPaperResult(status)

    deadline = clock() + float(timeout_sec)

    def run(active_broker) -> PrimaryPaperResult:
        try:
            active_broker.authorize([selected.url])
            fetched = active_broker.fetch(selected.url)
            receipt = _receipt(fetched, selected.url)
            metadata = _metadata_from_arxiv(receipt.text, selected.url)
            material = _material(receipt, selected.query_indexes)
        except Exception:
            return PrimaryPaperResult("unavailable")
        bundle = VerifiedWebBundle("ok", (material,), queries)
        return PrimaryPaperResult("ok", metadata, bundle)

    if broker is not None:
        return run(broker)

    try:
        with tempfile.TemporaryDirectory(prefix="docich-paper-primary-") as directory:
            active = WebBroker(Path(directory) / "unused.sock", deadline)
            return run(active)
    except Exception:
        return PrimaryPaperResult("unavailable")
