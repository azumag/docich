#!/usr/bin/env python3
"""Explicit, offline export of a completed Hanjuku run to an encrypted archive.

This is an operator CLI, NOT a new gateway/diagnostics permission. It neither
starts a game nor deploys, edits runtime state, uploads, or installs services.
Only the recipient certificate belongs on the VM; its private key stays with
the recipient. Errors printed by the CLI are fixed codes, never source data.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib

SCHEMA = 1
RUN_ID = re.compile(r"g([1-9][0-9]{0,17})-[0-9a-f]{8}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
FRAME_NAME = re.compile(r"(?:frame|decision)-[0-9]+\.png\Z")
MAX_FILE = 5 * 1024 * 1024  # 4 MiB rotation plus a bounded last record.
MAX_TOTAL = 32 * 1024 * 1024
MAX_FRAMES = 240
MAX_ENTRIES = 512
MAX_JSON = 1024 * 1024
MAX_RECORDS = 100000
MAX_LINE = 256 * 1024
MAX_PNG = 512 * 1024
JSON_FILES = (
    "hanjuku_run.json", "hanjuku_bot.json", "hanjuku_chart_review.json",
    "hanjuku_chart_adjusted.json", "hanjuku_chart_adjust_request.json",
)
LOG_STEMS = ("hanjuku_events", "hanjuku_decisions", "hanjuku_chart_history")
FILES = JSON_FILES + tuple(
    f"{stem}{suffix}.jsonl" for stem in LOG_STEMS for suffix in (".previous", "")
)
FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW
SECRET_KEY = re.compile(
    r"(?:prompt|api_?key|token|password|passwd|secret|authorization|cookie|private_?key)",
    re.IGNORECASE,
)


class EvidenceError(ValueError):
    """A fixed, source-content-free failure code."""


def _fail(code):
    raise EvidenceError(code)


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail("duplicate_json_key")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: _fail("nonfinite_json"),
                          parse_float=lambda x: float(x) if math.isfinite(float(x))
                          else _fail("nonfinite_json"))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        _fail("invalid_json")


def _dump(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode()


@contextmanager
def _directory(path):
    """Walk every component with no-follow dirfds (including the trusted root)."""
    path = Path(os.path.abspath(path))
    fd = os.open("/", FLAGS | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, FLAGS | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _stamp(st):
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def _read(parent, name, limit, *, optional=False):
    try:
        fd = os.open(name, FLAGS, dir_fd=parent)
    except FileNotFoundError:
        if optional:
            return None
        _fail("required_file_missing")
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            _fail("unsafe_file")
        if before.st_size > limit:
            _fail("file_too_large")
        with os.fdopen(os.dup(fd), "rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            _fail("file_too_large")
        after = os.fstat(fd)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if _stamp(before) != _stamp(after) or _stamp(after) != _stamp(current):
            _fail("source_changed")
        return data
    finally:
        os.close(fd)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def terminal_identity(run, runtime_id):
    """Conservative schema-1 mirror of hanjuku_run.terminal, without bot imports.

    A parity test against the native validator must accompany boundary changes.
    Legacy absent leases and manual/abnormal stops are intentionally rejected.
    """
    match = RUN_ID.fullmatch(runtime_id)
    if not isinstance(run, dict) or not match:
        _fail("invalid_identity")
    if (type(run.get("schema")) is not int or run["schema"] != 1
            or run.get("game") != "hanjuku-hero"
            or run.get("runtime_id") != runtime_id
            or type(run.get("generation")) is not int
            or run["generation"] != int(match[1])
            or not isinstance(run.get("lease_id"), str)
            or not 1 <= len(run["lease_id"]) <= 128):
        _fail("invalid_identity")
    if not isinstance(run.get("frame_sha256"), str) or not DIGEST.fullmatch(run["frame_sha256"]):
        _fail("invalid_terminal")
    for key in ("observations", "actions_sent", "battles_started", "battles_finished",
                "phase_transitions", "snapshots", "title_count"):
        value = run.get(key, 0)
        if type(value) is not int or value < 0:
            _fail("invalid_terminal")
    for key in ("observed_monotonic", "observed_at", "unchanged_since",
                "unchanged_seconds", "last_snapshot_at", "title_since"):
        if not _number(run.get(key, 0)):
            _fail("invalid_terminal")
    reason = run.get("terminal_reason")
    if reason == "game_over":
        if not (run.get("name_entered") is True and run.get("gameplay_seen") is True
                and run.get("phase") == "title" and run.get("title_count", 0) >= 3
                and run.get("observed_monotonic", 0) - run.get("title_since", 0) >= 2):
            _fail("invalid_terminal")
    elif reason == "screen_stalled":
        if run.get("unchanged_seconds", 0) < 300:
            _fail("invalid_terminal")
    else:
        _fail("not_completed")
    return {key: run[key] for key in ("game", "runtime_id", "generation", "lease_id")}


def _inactive(canonical, runtime_id):
    required = {"schema_version", "phase", "active", "candidate", "previous", "retiring",
                "operation", "request_id", "next_generation", "revision"}
    if (not isinstance(canonical, dict) or not required <= canonical.keys()
            or type(canonical["schema_version"]) is not int or canonical["schema_version"] != 2
            or canonical["phase"] not in ("idle", "ready")
            or canonical["operation"] is not None or canonical["request_id"] is not None
            or canonical["candidate"] is not None or canonical["previous"] is not None
            or not isinstance(canonical["retiring"], list)
            or type(canonical["revision"]) is not int or canonical["revision"] < 0
            or type(canonical["next_generation"]) is not int or canonical["next_generation"] < 1):
        _fail("canonical_unverified")
    active = canonical["active"]
    if canonical["phase"] == "idle" and active is not None:
        _fail("canonical_unverified")
    if canonical["phase"] == "ready" and not isinstance(active, dict):
        _fail("canonical_unverified")
    for runtime in ([active] if active is not None else []) + canonical["retiring"]:
        if (not isinstance(runtime, dict) or not isinstance(runtime.get("runtime_id"), str)
                or not RUN_ID.fullmatch(runtime["runtime_id"])
                or type(runtime.get("generation")) is not int
                or runtime["generation"] != int(RUN_ID.fullmatch(runtime["runtime_id"])[1])
                or canonical["next_generation"] <= runtime["generation"]):
            _fail("canonical_unverified")
        if runtime["runtime_id"] == runtime_id:
            _fail("runtime_active")


@contextmanager
def _child(parent, name):
    fd = os.open(name, FLAGS | os.O_DIRECTORY, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


def snapshot(state_dir, runtime_id):
    """Read an explicit completed generation under the existing shared switch lock.

    No lock is created, no exclusive lock is taken, and contention fails at once.
    Compression, PNG decoding and encryption happen AFTER releasing this lock.
    """
    if not RUN_ID.fullmatch(runtime_id):
        _fail("invalid_runtime_id")
    root = Path(state_dir)
    with _directory(root) as root_fd, _child(root_fd, "locks") as locks_fd:
        lock = os.open("game-switch.lock", FLAGS, dir_fd=locks_fd)
        try:
            lock_meta = os.fstat(lock)
            if not stat.S_ISREG(lock_meta.st_mode) or lock_meta.st_nlink != 1:
                _fail("unsafe_lock")
            try:
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                _fail("switch_busy")
            before = _read(root_fd, "game_switch.json", MAX_JSON)
            _inactive(_json(before), runtime_id)
            with _child(root_fd, "runtimes") as runtimes_fd, _child(runtimes_fd, runtime_id) as runtime_fd:
                run_raw = _read(runtime_fd, "hanjuku_run.json", MAX_JSON)
                identity = terminal_identity(_json(run_raw), runtime_id)
                data, missing = {}, []
                total = 0
                deadline = time.monotonic() + 3
                for name in FILES:
                    value = _read(runtime_fd, name, MAX_JSON if name.endswith(".json") else MAX_FILE,
                                  optional=name != "hanjuku_run.json")
                    if value is None:
                        missing.append(name)
                        continue
                    data[name] = value
                    total += len(value)
                    if total > MAX_TOTAL or time.monotonic() > deadline:
                        _fail("snapshot_budget_exceeded")
                frame_names = None
                try:
                    frames_fd = os.open("hanjuku_frames", FLAGS | os.O_DIRECTORY, dir_fd=runtime_fd)
                except FileNotFoundError:
                    frames_fd = None
                    missing.append("hanjuku_frames/")
                if frames_fd is not None:
                    try:
                        frame_names = []
                        with os.scandir(frames_fd) as entries:
                            for index, entry in enumerate(entries):
                                if index >= MAX_ENTRIES:
                                    _fail("frame_scan_limit")
                                if FRAME_NAME.fullmatch(entry.name):
                                    frame_names.append(entry.name)
                        if len(frame_names) > MAX_FRAMES:
                            _fail("frame_count_limit")
                        for name in sorted(frame_names):
                            value = _read(frames_fd, name, MAX_PNG)
                            total += len(value)
                            if total > MAX_TOTAL or time.monotonic() > deadline:
                                _fail("snapshot_budget_exceeded")
                            data[f"hanjuku_frames/{name}"] = value
                    finally:
                        os.close(frames_fd)
                # Catch writers which do not use the switch lock, including
                # post-game chart review jobs. Never publish a torn snapshot.
                # Membership matters as well as bytes: a file which was absent
                # on the first pass, or a frame created after the first directory
                # scan, must invalidate the whole snapshot rather than silently
                # exporting an older subset.
                for name in missing:
                    if name == "hanjuku_frames/":
                        continue
                    limit = MAX_JSON if name.endswith(".json") else MAX_FILE
                    if _read(runtime_fd, name, limit, optional=True) is not None:
                        _fail("source_changed")
                for name, value in data.items():
                    if "/" in name:
                        continue
                    if _read(runtime_fd, name, MAX_FILE) != value:
                        _fail("source_changed")
                if frame_names is None:
                    try:
                        os.stat("hanjuku_frames", dir_fd=runtime_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        _fail("source_changed")
                else:
                    with _child(runtime_fd, "hanjuku_frames") as check_fd:
                        current_names = []
                        with os.scandir(check_fd) as entries:
                            for index, entry in enumerate(entries):
                                if index >= MAX_ENTRIES:
                                    _fail("frame_scan_limit")
                                if FRAME_NAME.fullmatch(entry.name):
                                    current_names.append(entry.name)
                        if sorted(current_names) != sorted(frame_names):
                            _fail("source_changed")
                        for name, value in data.items():
                            if name.startswith("hanjuku_frames/"):
                                if _read(check_fd, name.split("/")[1], MAX_PNG) != value:
                                    _fail("source_changed")
                if _read(runtime_fd, "hanjuku_run.json", MAX_JSON) != run_raw:
                    _fail("source_changed")
                current = os.stat(runtime_id, dir_fd=runtimes_fd, follow_symlinks=False)
                opened = os.fstat(runtime_fd)
                if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                    _fail("source_changed")
                if time.monotonic() > deadline:
                    _fail("snapshot_budget_exceeded")
            if _read(root_fd, "game_switch.json", MAX_JSON) != before:
                _fail("source_changed")
            named_lock = os.stat("game-switch.lock", dir_fd=locks_fd, follow_symlinks=False)
            if (named_lock.st_dev, named_lock.st_ino) != (lock_meta.st_dev, lock_meta.st_ino):
                _fail("source_changed")
            return identity, data, missing
        finally:
            os.close(lock)


def _scrub(value, depth=0):
    if depth > 24:
        _fail("json_too_deep")
    if isinstance(value, dict):
        return {k: "<redacted>" if SECRET_KEY.search(k) else _scrub(v, depth + 1)
                for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, depth + 1) for v in value]
    return value


def _png(raw):
    """Accept only the exact normalized RGB format emitted by Frame.png_bytes().

    Bound inflation before decoding. Reject ancillary chunks to prevent metadata
    transport; unexpected formats fail closed rather than guessing normalization.
    """
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        _fail("invalid_frame")
    pos, kinds, chunks, compressed = 8, [], [], bytearray()
    while pos + 12 <= len(raw):
        size, kind = struct.unpack(">I4s", raw[pos:pos + 8])
        end = pos + 8 + size
        if end + 4 > len(raw):
            _fail("invalid_frame")
        payload = raw[pos + 8:end]
        if zlib.crc32(kind + payload) & 0xffffffff != struct.unpack(">I", raw[end:end + 4])[0]:
            _fail("invalid_frame")
        if kind == b"IHDR":
            if payload != struct.pack(">IIBBBBB", 256, 224, 8, 2, 0, 0, 0):
                _fail("uncalibrated_frame_format")
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind != b"IEND":
            _fail("unexpected_png_chunk")
        kinds.append(kind)
        chunks.append(raw[pos:end + 4])
        pos = end + 4
        if kind == b"IEND":
            if payload:
                _fail("invalid_frame")
            break
    if kinds != [b"IHDR", b"IDAT", b"IEND"] or pos != len(raw):
        _fail("invalid_frame")
    expected = 224 * (1 + 256 * 3)
    decoder = zlib.decompressobj()
    try:
        pixels = decoder.decompress(compressed, expected + 1)
    except zlib.error:
        _fail("invalid_frame")
    if len(pixels) != expected or not decoder.eof or decoder.unused_data:
        _fail("invalid_frame")
    if any(pixels[y * 769] != 0 for y in range(224)):
        _fail("unexpected_png_filter")
    rgb = b"".join(pixels[y * 769 + 1:(y + 1) * 769] for y in range(224))
    return hashlib.sha256(rgb).hexdigest()


def build_archive(identity, data, missing):
    """Preserve line numbers, input-vs-plan distinction, and both SHA domains."""
    files, notes, refs, frame_hashes, versions = {}, [], set(), {}, set()
    record_count = 0
    manifest = {
        "schema": SCHEMA, "identity": identity, "history_complete": False,
        "missing": sorted(missing), "files": {},
        "warnings": ["rotated_logs_and_frame_rings_are_not_full_history",
                     "action_plan_is_not_input_sent",
                     "free_text_may_contain_private_data_do_not_publish"],
    }
    for name, raw in data.items():
        meta = {"source_bytes": len(raw), "source_file_sha256": hashlib.sha256(raw).hexdigest()}
        if name.endswith(".png"):
            digest = _png(raw)
            frame_hashes.setdefault(digest, []).append(name)
            meta["rgb_sha256"] = digest
            meta["width"], meta["height"] = 256, 224
            clean = raw
        else:
            lines = [raw] if name.endswith(".json") else raw.splitlines()
            cleaned, invalid = [], []
            for number, line in enumerate(lines, 1):
                record_count += 1
                if record_count > MAX_RECORDS:
                    _fail("record_count_limit")
                try:
                    if len(line) > (MAX_JSON if name.endswith(".json") else MAX_LINE):
                        _fail("line_too_large")
                    record = _json(line)
                    if not isinstance(record, dict):
                        _fail("invalid_record")
                    # Identity fields are absent on some native event types;
                    # fields which ARE present must never name another lease.
                    for key, expected in identity.items():
                        if key in record and (type(record[key]) is not type(expected) or record[key] != expected):
                            _fail("record_identity_mismatch")
                    for key in ("frame_sha256", "decision_frame_sha256"):
                        digest = record.get(key)
                        if isinstance(digest, str) and DIGEST.fullmatch(digest):
                            refs.add(digest)
                    version = record.get("bot_version")
                    if isinstance(version, str) and len(version) <= 128:
                        versions.add(version)
                    cleaned.append(_dump(_scrub(record)))
                except EvidenceError as exc:
                    if name.endswith(".json") or str(exc) == "record_identity_mismatch":
                        raise
                    invalid.append(number)
                    cleaned.append(_dump({"export_error": str(exc), "source_line": number}))
            clean = b"".join(cleaned)
            meta["invalid_lines"] = invalid
            meta["trailing_newline"] = raw.endswith(b"\n")
            if invalid:
                notes.append({"file": name, "invalid_lines": invalid})
        meta["export_bytes"] = len(clean)
        meta["export_file_sha256"] = hashlib.sha256(clean).hexdigest()
        manifest["files"][name] = meta
        files[name] = clean
    manifest.update(bot_versions=sorted(versions), invalid_records=notes,
                    frame_index=frame_hashes,
                    missing_frame_sha256=sorted(refs - frame_hashes.keys()))
    files["manifest.json"] = _dump(manifest)
    if sum(map(len, files.values())) > MAX_TOTAL:
        _fail("archive_too_large")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
        for name, raw in sorted(files.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.external_attr = 0o100600 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, raw)
    return buffer.getvalue()


def _openssl(args, data=None):
    try:
        result = subprocess.run(["/usr/bin/openssl", *args], input=data, capture_output=True,
                                timeout=30, check=False,
                                env={"PATH": "/usr/bin:/bin", "LANG": "C"})
    except (OSError, subprocess.TimeoutExpired):
        _fail("crypto_unavailable")
    if result.returncode != 0:
        _fail("crypto_failed")
    if len(result.stdout) > MAX_TOTAL + 1024 * 1024:
        _fail("ciphertext_too_large")
    return result.stdout


def encrypt(archive, certificate):
    if len(archive) > MAX_TOTAL:
        _fail("archive_too_large")
    if (len(certificate) > 16384 or not certificate.startswith(b"-----BEGIN CERTIFICATE-----\n")
            or certificate.count(b"-----BEGIN CERTIFICATE-----") != 1
            or certificate.rstrip().splitlines()[-1] != b"-----END CERTIFICATE-----"):
        _fail("invalid_recipient")
    with tempfile.TemporaryDirectory(prefix="hanjuku-recipient-") as tmp:
        cert = Path(tmp) / "recipient.pem"
        cert.write_bytes(certificate)  # Public certificate, directory is 0700.
        return _openssl(["cms", "-encrypt", "-binary", "-aes-256-gcm", "-outform", "DER",
                         "-recip", str(cert), "-keyopt", "rsa_padding_mode:oaep",
                         "-keyopt", "rsa_oaep_md:sha256", "-keyopt", "rsa_mgf1_md:sha256"], archive)


def write_private(path, data):
    path = Path(path)
    with _directory(path.parent) as parent:
        metadata = os.fstat(parent)
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            _fail("output_directory_not_private")
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                     dir_fd=parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            os.unlink(path.name, dir_fd=parent)
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--runtime-id", required=True, help="Explicit completed generation, not latest/active")
    parser.add_argument("--recipient", type=Path, required=True, help="Recipient RSA certificate; never a private key")
    parser.add_argument("--output", type=Path, required=True, help="New .cms file in an existing owner-only directory")
    args = parser.parse_args(argv)
    try:
        with _directory(args.recipient.parent) as parent:
            cert = _read(parent, args.recipient.name, 16384)
        # Validate the certificate before taking any runtime snapshot.
        encrypt(b"recipient-validation", cert)
        identity, data, missing = snapshot(args.state_dir, args.runtime_id)
        ciphertext = encrypt(build_archive(identity, data, missing), cert)
        write_private(args.output, ciphertext)
    except EvidenceError as exc:
        print(f"evidence export rejected: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, RecursionError):
        print("evidence export rejected: io_or_format_error", file=sys.stderr)
        return 1
    print("encrypted evidence ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
