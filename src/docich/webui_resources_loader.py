"""Bounded, data-only WebUI refresh with an immutable last-good snapshot.

The manifest binds HTML (including inline JS/CSS) and defaults into one release.
No timestamp is used as a cache key and no Python source is evaluated.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping


LIMITS = {"manifest.json": 4096, "index.html": 1_000_000, "defaults.json": 64_000}
FILES = ("index.html", "defaults.json")


class ResourceError(ValueError):
    """A fixed public error code, never file contents or exception messages."""


def _read(path: Path, limit: int) -> bytes:
    # Nonblocking open prevents a malformed deployment's FIFO from hanging GET.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ResourceError("not_regular_file")
        if before.st_size > limit:
            raise ResourceError("too_large")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if len(data) > limit:
        raise ResourceError("too_large")
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size, after.st_mtime_ns, after.st_ctime_ns
    ) or len(data) != after.st_size:
        raise ResourceError("changed_during_read")
    return data


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ResourceError("duplicate_json_key")
        result[key] = value
    return result


def _json(data: bytes):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ResourceError("invalid_json") from None


class _ShellParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.shell = []
        self.raw_tags = []

    def handle_starttag(self, tag, attrs):
        if tag in ("html", "head", "body"):
            self.shell.append(tag)
        if tag in ("script", "style"):
            self.raw_tags.append(tag)

    def handle_endtag(self, tag):
        if tag in ("html", "head", "body"):
            self.shell.append("/" + tag)
        if tag in ("script", "style"):
            if not self.raw_tags or self.raw_tags.pop() != tag:
                raise ResourceError("invalid_html")


@dataclass(frozen=True)
class Snapshot:
    html: bytes
    defaults: Mapping[str, str]
    revision: str


class ResourceLoader:
    def __init__(self, root: Path, keys: set[str], validate: Callable[[str, str], None]):
        self.root = root
        self.keys = frozenset(keys)
        self.validate = validate
        self._lock = threading.Lock()
        self._snapshot: Snapshot | None = None
        self._attempt: str | None = None
        self._attempt_error: str | None = None
        self._error: str | None = None
        self.refresh()  # No last-good snapshot at startup: fail closed.

    def _validate(self, raw: dict[str, bytes], revision: str) -> Snapshot:
        manifest = _json(raw["manifest.json"])
        if not isinstance(manifest, dict) or set(manifest) != {"schema", "files"} or type(manifest["schema"]) is not int or manifest["schema"] != 1:
            raise ResourceError("invalid_manifest")
        files = manifest["files"]
        if not isinstance(files, dict) or set(files) != set(FILES):
            raise ResourceError("invalid_manifest")
        for name in FILES:
            if files[name] != hashlib.sha256(raw[name]).hexdigest():
                raise ResourceError("digest_mismatch")
        try:
            html = raw["index.html"].decode("utf-8")
        except UnicodeError:
            raise ResourceError("invalid_html") from None
        # Structural/truncation checks; JS semantics remain a reviewed CI concern.
        if not html.lstrip().lower().startswith("<!doctype html>") or not html.rstrip().lower().endswith("</html>") or "</body>" not in html.lower() or "\x00" in html:
            raise ResourceError("invalid_html")
        parser = _ShellParser()
        parser.feed(html)
        parser.close()
        if parser.shell != ["html", "head", "/head", "body", "/body", "/html"] or parser.raw_tags:
            raise ResourceError("invalid_html")
        defaults = _json(raw["defaults.json"])
        if not isinstance(defaults, dict) or set(defaults) != self.keys:
            raise ResourceError("invalid_defaults_keys")
        try:
            for key, value in defaults.items():
                self.validate(key, value)
        except (ValueError, TypeError):
            raise ResourceError("invalid_default_value") from None
        return Snapshot(raw["index.html"], MappingProxyType(defaults), revision)

    def refresh(self) -> tuple[Snapshot, dict]:
        with self._lock:
            try:
                raw = {name: _read(self.root / name, limit) for name, limit in LIMITS.items()}
                # Reject a manifest replacement during the multi-file read.
                if raw["manifest.json"] != _read(self.root / "manifest.json", LIMITS["manifest.json"]):
                    raise ResourceError("changed_during_read")
                revision = hashlib.sha256(b"".join(hashlib.sha256(raw[name]).digest() for name in LIMITS)).hexdigest()
                if revision != self._attempt:
                    self._attempt = revision
                    try:
                        candidate = self._validate(raw, revision)
                    except ResourceError as exc:
                        self._attempt_error = str(exc)
                    else:
                        self._snapshot = candidate
                        self._attempt_error = None
                self._error = self._attempt_error
            except ResourceError as exc:
                self._error = str(exc)
            except OSError:
                self._error = "read_failed"
            if self._snapshot is None:
                raise ResourceError(self._error or "unavailable")
            return self._snapshot, {
                "status": "last_good" if self._error else "current",
                "revision": self._snapshot.revision,
                "error": self._error,
            }
