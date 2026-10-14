#!/usr/bin/env python3
"""Reviewed public projection -> runtime markdown.

The projection may be derived from either the private shared handoff
(``handoff.md``, referenced by ``DOCICH_HANDOFF_PATH`` and deliberately not in Git)
or a small tracked source (``ops/runtime_context/ops_brief_source.md``). The tracked
source exists so an ordinary handoff edit cannot churn the committed projection and
collide with concurrent branches; it stays public-safe by construction.

Never print source/projection text. check-source proves provenance locally (and in
clean CI once the tracked source is the input); check-artifact works in clean CI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ARTIFACT = "ops/runtime_context/ops_brief.json"
PROJECTION = "games/soviet_now"
DESTINATION = "prompts/ops_brief.md"
CAPABILITY = "parent_ops_brief_v1"
CAPABILITY_TRACKED_SOURCE = "parent_ops_brief_tracked_source_v1"
SOURCE_LEGACY = "handoff.md"
SOURCE_TRACKED = "ops/runtime_context/ops_brief_source.md"
ALLOWED_SOURCES = (SOURCE_LEGACY, SOURCE_TRACKED)
DEFAULT_SOURCE = SOURCE_LEGACY
MAX_SOURCE = 4 * 1024 * 1024
MAX_ARTIFACT = 4096
HEADER = "# 直近の裏側の改修 (docich handoff.md から自動生成。手で編集しない)\n"
HANDOFF_ENV = "DOCICH_HANDOFF_PATH"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def markdown(topics: list[str]) -> bytes:
    return (HEADER + "".join(f"- {topic}\n" for topic in topics)).encode("utf-8")


def extract_topics(source: bytes) -> list[str]:
    if not source or len(source) > MAX_SOURCE:
        raise ValueError("invalid handoff size")
    topics = []
    fenced = None
    for raw in source.decode("utf-8", "strict").splitlines():
        line = raw.strip()
        fence = re.match(r"^(`{3,}|~{3,})", line)
        if fence:
            token = fence.group(1)
            if fenced is None:
                fenced = token
            elif token[0] == fenced[0] and len(token) >= len(fenced):
                fenced = None
            continue
        if fenced or not line.startswith("## "):
            continue
        topic = line[3:].strip()
        stripped = re.sub(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\s.*?[—–]\s*", "", topic).strip()
        if stripped == topic:
            stripped = re.sub(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[0-9:x\s\-]*(?:JST)?\s*", "", topic).strip()
        topic = re.sub(r"(?i)\b(?:issue|docich|pr)\s*#?\s*-?\d+(?![0-9a-z_])", "", stripped)
        topic = re.sub(r"^[\s:：,、。\-–—]+", "", topic)
        topic = re.sub(r"\s+", " ", topic).strip()
        if not topic:
            continue
        if len(topic) > 70:
            topic = topic[:69].rstrip("、。 ,.") + "…"
        topics.append(topic)
        if len(topics) == 3:
            break
    if not topics:
        raise ValueError("handoff has no topics")
    return topics


def encode(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def build(source: bytes, source_name: str = SOURCE_LEGACY) -> bytes:
    if source_name not in ALLOWED_SOURCES:
        raise ValueError("unknown ops brief source")
    topics = extract_topics(source)
    data = encode({"schema": 1, "source": source_name, "source_sha256": digest(source),
                   "topics": topics, "output_sha256": digest(markdown(topics))})
    validate(data)
    return data


def validate(data: bytes) -> dict:
    if not data or len(data) > MAX_ARTIFACT:
        raise ValueError("invalid ops brief artifact")
    value = json.loads(data)
    if not isinstance(value, dict) or set(value) != {"schema", "source", "source_sha256", "topics", "output_sha256"}:
        raise ValueError("invalid ops brief schema")
    if type(value["schema"]) is not int or value["schema"] != 1 or value["source"] not in ALLOWED_SOURCES:
        raise ValueError("invalid ops brief source")
    for field in ("source_sha256", "output_sha256"):
        if not isinstance(value[field], str) or not re.fullmatch(r"[a-f0-9]{64}", value[field]):
            raise ValueError("invalid ops brief hash")
    topics = value["topics"]
    if not isinstance(topics, list) or not 1 <= len(topics) <= 3:
        raise ValueError("invalid ops brief topics")
    for topic in topics:
        if not isinstance(topic, str) or not 1 <= len(topic) <= 70 or topic != topic.strip():
            raise ValueError("invalid ops brief topic")
        if any(ord(c) < 32 or ord(c) == 127 for c in topic):
            raise ValueError("invalid ops brief topic")
    if digest(markdown(topics)) != value["output_sha256"] or encode(value) != data:
        raise ValueError("stale ops brief artifact")
    return value


def render(data: bytes) -> bytes:
    return markdown(validate(data)["topics"])


def read_regular(path: Path, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("required regular file missing")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("input too large")
    return data


def handoff_path(explicit: Path | None) -> Path:
    """Legacy shared handoff; never infer a checkout-local copy."""
    if explicit is None:
        configured = os.environ.get(HANDOFF_ENV)
        if not configured:
            raise ValueError("shared handoff source not configured")
        explicit = Path(configured)
    if not explicit.is_absolute() or explicit.name != SOURCE_LEGACY:
        raise ValueError(f"explicit absolute {SOURCE_LEGACY} path required")
    return explicit


def classify_source(path: Path, root: Path) -> str:
    """Map a source path to the identity stored in the artifact."""
    if path.name == SOURCE_LEGACY and path.is_absolute():
        return SOURCE_LEGACY
    try:
        if path.resolve() == (root / SOURCE_TRACKED).resolve():
            return SOURCE_TRACKED
    except OSError:
        pass
    raise ValueError("unsupported ops brief source path")


def source_path(root: Path, explicit: Path | None, required: str | None = None) -> tuple[Path, str]:
    """Resolve the source file and identity, failing closed on a mismatch.

    ``required`` is the identity the artifact demands; a different file is never
    silently verified in its place.
    """
    if explicit is not None:
        path = explicit
    elif required == SOURCE_TRACKED or (required is None and DEFAULT_SOURCE == SOURCE_TRACKED):
        path = root / SOURCE_TRACKED
    else:
        path = handoff_path(None)
    identity = classify_source(path, root)
    if required is not None and identity != required:
        raise ValueError("ops brief source does not match the artifact")
    return path, identity


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("build", "check-source", "check-artifact", "materialize"))
    parser.add_argument("--source", "--handoff", dest="source", type=Path,
                        help=f"explicit source file (default: {DEFAULT_SOURCE})")
    parser.add_argument("--artifact", type=Path, default=root / ARTIFACT)
    parser.add_argument("--output", type=Path, help="explicit local runtime markdown output")
    args = parser.parse_args()
    try:
        if args.operation in {"build", "check-source"}:
            current = None
            required = None
            if args.operation == "check-source":
                current = read_regular(args.artifact, MAX_ARTIFACT)
                required = validate(current)["source"]
            path, identity = source_path(root, args.source, required)
            expected = build(read_regular(path, MAX_SOURCE), identity)
            if args.operation == "check-source":
                if current != expected:
                    raise ValueError("stale ops brief source")
            else:
                if args.artifact.is_symlink():
                    raise ValueError("artifact is a symlink")
                args.artifact.parent.mkdir(parents=True, exist_ok=True)
                args.artifact.write_bytes(expected)
        else:
            data = read_regular(args.artifact, MAX_ARTIFACT)
            validate(data)
            if args.operation == "materialize":
                if args.output is None or args.output.is_symlink():
                    raise ValueError("explicit regular output required")
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_bytes(render(data))
    except (ValueError, OSError, UnicodeError):
        print("ops brief verification failed", file=sys.stderr)
        return 1
    print("ops brief " + args.operation + ": ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
