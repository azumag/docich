#!/usr/bin/env python3
"""Bound the private docich handoff and archive the overflow losslessly.

The canonical operations handoff (`handoff.md`) lives in the approved private shared
area and is referenced by `DOCICH_HANDOFF_PATH` (or `--handoff`); this tool never
infers a checkout-local copy. Left alone it grows without limit: every session
prepends a section and nothing is ever removed, so reading it whole at session start
costs more context than the recent work it describes.

This tool keeps a bounded, newest-first window in the canonical file and moves the
older sections into monthly archive files next to it (`handoff-archive/YYYY-MM.md`,
in the same private area). It is deliberately conservative:

- It never prints section headings. Only counts and byte sizes leave the tool, so
  it is safe to run where output may be captured.
- Archive updates are atomic (temp file + ``os.replace``) and are verified before
  the canonical file is rewritten.
- It re-reads the canonical file immediately before writing and aborts if another
  agent changed it, so a concurrent append is never clobbered.
- A rotation is only recorded after the canonical file has been written, and every
  archived section's SHA-256 is recorded so ``verify`` can re-check it later.
- A run interrupted after the archive write but before the canonical rewrite can
  leave a duplicated archived section on retry. Duplicates are harmless and never a
  loss, so no digest-based dedupe is used: it could drop a genuine repeated entry.

Operations::

    handoff_rotate.py rotate   # archive everything past the window (defaults below)
    handoff_rotate.py check    # exit 1 when a rotation is due; read-only
    handoff_rotate.py verify   # re-check that recorded sections are still archived

The source is `--handoff` when given, otherwise `DOCICH_HANDOFF_PATH`; both must be
an absolute path to a regular `handoff.md`. The archive is created next to it.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

HANDOFF_NAME = "handoff.md"
HANDOFF_ENV = "DOCICH_HANDOFF_PATH"
ARCHIVE_DIR_NAME = "handoff-archive"
INDEX_NAME = "INDEX.jsonl"
LOCK_NAME = ".lock"
ARCHIVE_HEADER = "# docich handoff archive — {month} (private, gitignored; do not commit)\n\n"
DEFAULT_KEEP = 30
DEFAULT_WITHIN_HOURS = 0.0
DEFAULT_WARN_BYTES = 256 * 1024
MAX_SOURCE = 8 * 1024 * 1024
HEADING_RE = re.compile(r"^## (?=\S)")
HEADING_DATE_RE = re.compile(r"^##\s*(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?")
FENCE_RE = re.compile(r"^(`{3,}|~{3,})")


class RotationError(RuntimeError):
    """A precondition failed; nothing was written."""


@dataclass(frozen=True)
class Section:
    lines: tuple[str, ...]
    heading: str
    instant: datetime | None
    digest: str

    @property
    def text(self) -> str:
        return "".join(self.lines)

    @property
    def date_key(self) -> date | None:
        return self.instant.date() if self.instant is not None else None


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _section_instant(heading: str) -> datetime | None:
    match = HEADING_DATE_RE.match(heading)
    if match is None:
        return None
    year, month, day, hour, minute = match.groups()
    try:
        return datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0))
    except ValueError:
        return None


def split_document(text: str) -> tuple[tuple[str, ...], list[Section]]:
    """Split into a preamble plus ``## `` sections, ignoring fenced code blocks."""
    lines = text.splitlines(keepends=True)
    starts: list[int] = []
    fenced: str | None = None
    for index, raw in enumerate(lines):
        stripped = raw.strip()
        fence = FENCE_RE.match(stripped)
        if fence is not None:
            token = fence.group(1)
            if fenced is None:
                fenced = token
            elif token[0] == fenced[0] and len(token) >= len(fenced):
                fenced = None
            continue
        if fenced is None and HEADING_RE.match(raw):
            starts.append(index)
    if not starts:
        return tuple(lines), []
    preamble = tuple(lines[: starts[0]])
    starts.append(len(lines))
    sections: list[Section] = []
    for position in range(len(starts) - 1):
        block = tuple(lines[starts[position] : starts[position + 1]])
        heading = block[0].rstrip("\n")
        sections.append(
            Section(
                lines=block,
                heading=heading,
                instant=_section_instant(heading),
                digest=digest("".join(block).encode("utf-8")),
            )
        )
    return preamble, sections


def select_prefix(sections: list[Section], keep: int, within_hours: float) -> int:
    """Return how many leading sections to keep (always a prefix, at least one).

    The window is the newest ``keep`` sections. ``within_hours`` > 0 additionally
    keeps every section whose heading timestamp is within that many hours of the
    newest known timestamp, so a busy stretch is never cut mid-flight. The result is
    rounded up to a prefix so a window boundary never splits the file's order.
    """
    if not sections:
        return 0
    cut = min(keep, len(sections))
    reference = max((s.instant for s in sections if s.instant is not None), default=None)
    if within_hours > 0 and reference is not None:
        floor = reference - timedelta(hours=within_hours)
        for index, section in enumerate(sections):
            if section.instant is not None and section.instant >= floor:
                cut = max(cut, index + 1)
    return max(1, min(cut, len(sections)))


def resolve_handoff(explicit: Path | None) -> Path:
    """Pick the shared source explicitly; never infer a checkout-local copy."""
    path = explicit
    if path is None:
        configured = os.environ.get(HANDOFF_ENV)
        if not configured:
            raise RotationError("shared handoff source not configured")
        path = Path(configured)
    if not path.is_absolute() or path.name != HANDOFF_NAME:
        raise RotationError(f"absolute {HANDOFF_NAME} path required")
    if path.is_symlink():
        raise RotationError("refusing a symlinked handoff")
    if not path.is_file():
        raise RotationError("handoff is missing")
    return path


def read_source(path: Path) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(MAX_SOURCE + 1)
    if len(data) > MAX_SOURCE:
        raise RotationError("handoff is larger than the supported limit")
    return data


def decode(data: bytes) -> str:
    try:
        return data.decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise RotationError("handoff is not valid UTF-8") from error


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.rotate-")
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    except BaseException:
        try:
            temp.unlink()
        except OSError:
            pass
        raise
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def append_jsonl(path: Path, entry: dict) -> None:
    existing = path.read_bytes() if path.exists() else b""
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
    atomic_write(path, existing + line, 0o600)


def archive_groups(sections: list[Section], fallback: date) -> dict[str, list[Section]]:
    groups: dict[str, list[Section]] = {}
    for section in sections:
        month = (section.date_key or fallback).strftime("%Y-%m")
        groups.setdefault(month, []).append(section)
    for month, items in groups.items():
        items.sort(key=lambda s: (s.instant or datetime.min, s.digest))
    return dict(sorted(groups.items()))


def parse_archive(data: bytes) -> list[Section]:
    _, sections = split_document(decode(data))
    return sections


def digest_counts(sections: list[Section]) -> dict[str, int]:
    """Count archived sections by digest, so ``verify`` is duplicate-aware."""
    counts: dict[str, int] = {}
    for section in sections:
        counts[section.digest] = counts.get(section.digest, 0) + 1
    return counts


def ensure_archive_is_not_committable(directory: Path) -> None:
    """Refuse an archive directory that Git inside a work tree could commit.

    The archive holds the same private records as the canonical handoff. Placing it
    in a checkout without an ignore rule is how private operations text would leak
    into the public repository, so this fails closed and asks for an ignored path or
    one outside the checkout.
    """
    probe = directory
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        inside = subprocess.run(["git", "-C", str(probe), "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, text=True)
    except OSError:
        return
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return
    for candidate in (str(directory), str(directory) + os.sep):
        ignored = subprocess.run(["git", "-C", str(probe), "check-ignore", "-q", candidate],
                                 capture_output=True, text=True)
        if ignored.returncode == 0:
            return
    raise RotationError("archive directory would be committable; use an ignored path "
                        "or --archive-dir outside the checkout")


def rotate(args: argparse.Namespace) -> int:
    path = resolve_handoff(args.handoff)
    archive_dir = args.archive_dir if args.archive_dir is not None else path.parent / ARCHIVE_DIR_NAME
    if archive_dir.is_symlink():
        raise RotationError("refusing a symlinked archive directory")
    ensure_archive_is_not_committable(archive_dir)

    original = read_source(path)
    before = digest(original)
    preamble, sections = split_document(decode(original))
    cut = select_prefix(sections, args.keep, args.within_hours)
    removed = sections[cut:]
    kept = sections[:cut]
    if not removed:
        print(f"handoff rotate: ok (nothing to archive, {len(sections)} sections, "
              f"{len(original)} bytes)")
        return 0
    if args.dry_run:
        print(f"handoff rotate: dry-run ({len(kept)} kept, {len(removed)} to archive, "
              f"{sum(len(s.text.encode('utf-8')) for s in removed)} bytes)")
        return 0

    archive_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = archive_dir / LOCK_NAME
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            _rotate_locked(path, archive_dir, before, preamble, kept, removed)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    archived_bytes = sum(len(section.text.encode("utf-8")) for section in removed)
    print(f"handoff rotate: ok ({len(kept)} kept, {len(removed)} archived, "
          f"{archived_bytes} bytes moved)")
    return 0


def _rotate_locked(path: Path, archive_dir: Path, before: str,
                   preamble: tuple[str, ...], kept: list[Section],
                   removed: list[Section]) -> None:
    if digest(read_source(path)) != before:
        raise RotationError("handoff changed during rotation; nothing was written")

    mode = 0o600
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        pass

    fallback = datetime.now().date()
    records: list[dict] = []
    for month, items in archive_groups(removed, fallback).items():
        target = archive_dir / f"{month}.md"
        existing = target.read_bytes() if target.exists() else ARCHIVE_HEADER.format(month=month).encode("utf-8")
        atomic_write(target, existing + "".join(section.text for section in items).encode("utf-8"), 0o600)
        counts = digest_counts(parse_archive(target.read_bytes()))
        for section in items:
            if counts.get(section.digest, 0) < 1:
                raise RotationError("archive verification failed; handoff left unchanged")
            records.append({"heading": section.heading, "sha256": section.digest, "archive": target.name})

    if digest(read_source(path)) != before:
        raise RotationError("handoff changed during rotation; handoff left unchanged")

    rewritten = ("".join(preamble) + "".join(section.text for section in kept)).encode("utf-8")
    atomic_write(path, rewritten, mode)

    _, check = split_document(decode(read_source(path)))
    if [section.digest for section in check] != [section.digest for section in kept]:
        raise RotationError("handoff verification failed after rewrite")

    append_jsonl(archive_dir / INDEX_NAME, {
        "archived_at": datetime.now().isoformat(timespec="seconds"),
        "handoff": path.name,
        "kept_sections": len(kept),
        "kept_bytes": len(rewritten),
        "removed_bytes": sum(len(section.text.encode("utf-8")) for section in removed),
        "removed_sha256": digest("".join(section.text for section in removed).encode("utf-8")),
        "sections": records,
    })


def check(args: argparse.Namespace) -> int:
    path = resolve_handoff(args.handoff)
    original = read_source(path)
    _, sections = split_document(decode(original))
    cut = select_prefix(sections, args.keep, args.within_hours)
    if cut < len(sections):
        print(f"handoff check: rotate required ({cut} kept, {len(sections) - cut} to archive, "
              f"{len(original)} bytes)")
        return 1
    note = ""
    if len(original) > args.warn_bytes:
        note = f" (warning: above {args.warn_bytes} bytes)"
    print(f"handoff check: ok ({len(sections)} sections, {len(original)} bytes){note}")
    return 0


def verify(args: argparse.Namespace) -> int:
    path = resolve_handoff(args.handoff)
    archive_dir = args.archive_dir if args.archive_dir is not None else path.parent / ARCHIVE_DIR_NAME
    index = archive_dir / INDEX_NAME
    if not index.is_file() or index.is_symlink():
        raise RotationError("archive index is missing")
    entries = 0
    expected: dict[str, dict[str, int]] = {}
    for raw in index.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        entry = json.loads(raw)
        entries += 1
        for record in entry["sections"]:
            expected.setdefault(record["archive"], {})
            counts = expected[record["archive"]]
            counts[record["sha256"]] = counts.get(record["sha256"], 0) + 1
    missing = 0
    for name, wanted in expected.items():
        target = archive_dir / name
        if not target.is_file() or target.is_symlink():
            missing += sum(wanted.values())
            continue
        available = digest_counts(parse_archive(target.read_bytes()))
        for section_digest, count in wanted.items():
            if available.get(section_digest, 0) < count:
                missing += count - available.get(section_digest, 0)
    if missing:
        print(f"handoff verify: failed ({missing} archived sections missing)")
        return 1
    print(f"handoff verify: ok ({entries} rotations, "
          f"{sum(sum(c.values()) for c in expected.values())} sections)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("operation", choices=("rotate", "check", "verify"))
    parser.add_argument("--handoff", type=Path,
                        help=f"absolute shared handoff.md (overrides {HANDOFF_ENV})")
    parser.add_argument("--archive-dir", type=Path, default=None)
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP,
                        help=f"newest sections always kept (default: {DEFAULT_KEEP})")
    parser.add_argument("--within-hours", type=float, default=DEFAULT_WITHIN_HOURS,
                        help="also keep sections newer than this (0 disables it)")
    parser.add_argument("--warn-bytes", type=int, default=DEFAULT_WARN_BYTES)
    parser.add_argument("--dry-run", action="store_true", help="rotate: report only")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.keep < 1:
        print("handoff rotate: --keep must be positive", file=sys.stderr)
        return 2
    try:
        if args.operation == "rotate":
            return rotate(args)
        if args.operation == "check":
            return check(args)
        return verify(args)
    except RotationError as error:
        print(f"handoff {args.operation}: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"handoff {args.operation}: failed ({type(error).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
