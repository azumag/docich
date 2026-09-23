#!/usr/bin/env python3
"""Private parent handoff -> reviewed public projection -> runtime markdown.

Never print source/projection text. The private handoff is deliberately not in
Git; check-source proves provenance locally, check-artifact works in clean CI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ARTIFACT = "ops/runtime_context/ops_brief.json"
PROJECTION = "games/soviet_now"
DESTINATION = "prompts/ops_brief.md"
CAPABILITY = "parent_ops_brief_v1"
MAX_SOURCE = 4 * 1024 * 1024
MAX_ARTIFACT = 4096
HEADER = "# 直近の裏側の改修 (docich handoff.md から自動生成。手で編集しない)\n"


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


def build(source: bytes) -> bytes:
    topics = extract_topics(source)
    data = encode({"schema": 1, "source": "handoff.md", "source_sha256": digest(source),
                   "topics": topics, "output_sha256": digest(markdown(topics))})
    validate(data)
    return data


def validate(data: bytes) -> dict:
    if not data or len(data) > MAX_ARTIFACT:
        raise ValueError("invalid ops brief artifact")
    value = json.loads(data)
    if not isinstance(value, dict) or set(value) != {"schema", "source", "source_sha256", "topics", "output_sha256"}:
        raise ValueError("invalid ops brief schema")
    if type(value["schema"]) is not int or value["schema"] != 1 or value["source"] != "handoff.md":
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


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("build", "check-source", "check-artifact", "materialize"))
    parser.add_argument("--handoff", type=Path, default=root / "handoff.md")
    parser.add_argument("--artifact", type=Path, default=root / ARTIFACT)
    parser.add_argument("--output", type=Path, help="explicit local runtime markdown output")
    args = parser.parse_args()
    try:
        if args.operation in {"build", "check-source"}:
            if args.handoff.name != "handoff.md":
                raise ValueError("parent handoff.md required")
            expected = build(read_regular(args.handoff, MAX_SOURCE))
            if args.operation == "check-source":
                if read_regular(args.artifact, MAX_ARTIFACT) != expected:
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
