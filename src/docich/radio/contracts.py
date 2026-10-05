"""Typed material contracts for the native docich radio pipeline."""
from __future__ import annotations

from dataclasses import dataclass
import re


_KIND_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class MaterialQuery:
    """One public-information query selected by trusted radio orchestration."""

    kind: str
    query: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not _KIND_RE.fullmatch(self.kind):
            raise ValueError("invalid material kind")
        if (not isinstance(self.query, str) or not self.query.strip()
                or len(self.query) > 256 or any(ord(c) < 32 for c in self.query)):
            raise ValueError("invalid material query")


@dataclass(frozen=True)
class WebMaterial:
    """Bounded, fetched public-page material; never an instruction surface."""

    kind: str
    url: str
    sha256: str
    excerpt: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not _KIND_RE.fullmatch(self.kind):
            raise ValueError("invalid material kind")
        if not isinstance(self.url, str) or not self.url.startswith("https://"):
            raise ValueError("invalid material URL")
        if not isinstance(self.sha256, str) or not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError("invalid material digest")
        if not isinstance(self.excerpt, str) or not 1 <= len(self.excerpt) <= 1200:
            raise ValueError("invalid material excerpt")

    def wire(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "url": self.url,
            "sha256": self.sha256,
            "excerpt": self.excerpt,
        }
