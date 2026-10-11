#!/usr/bin/env python3
"""One fixed Soren91 PNG diagnostic run and private, UUID-scoped frame handoff.

The `start` action launches only the existing five-minute manual probe. The two
capture overrides are placed in that child process environment and are never
written to an EnvironmentFile. `export UUID` reads at most three validated PNG
frames and fixed-schema sidecars from the single selected run directory and
writes a bounded tar stream to stdout for an owner-operated SSH transfer.
`receive` validates that stream and stores its contents in a new private local
directory. No workflow, public artifact, endpoint, key, or gateway operation is
added here.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from typing import BinaryIO
import zlib

DOCICH_ROOT = Path("/home/ubuntu/docich")
RUNTIME_ROOT = Path("/home/ubuntu/soren")
MANUAL_COMMAND = DOCICH_ROOT / "bin/docich-soren91-corner-manual"
MANUAL_CONFIG = DOCICH_ROOT / "config/docich.soren-live.toml"
MANUAL_DURATION_MINUTES = 5
DIAGNOSTIC_PROFILE = "rejected_png_v1"
DIAGNOSTIC_RELATIVE_ROOT = Path("tmp/rejected_frame_diagnostics")
MAX_FRAMES = 3
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_METADATA_BYTES = 16 * 1024
MAX_BUNDLE_BYTES = MAX_FRAMES * (MAX_IMAGE_BYTES + MAX_METADATA_BYTES) + 64 * 1024
ELIGIBLE_REASONS = {"non-move", "unknown-current", "confirm-frame"}
UUID_RE = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\Z", re.I)
FRAME_DIR_RE = re.compile(r"frame_(\d{2})\Z")
IMAGE_RE = re.compile(r"frame_(\d{2})\.png\Z")
METADATA_RE = re.compile(r"frame_(\d{2})\.json\Z")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
METADATA_KEYS = {
    "schema", "game", "turn", "sessionId", "reason", "imageFormat",
    "boardConfidence", "currentPieceConfidence", "image", "fileMtimeMs",
    "recordedAtMs", "capture",
}
CAPTURE_GEOMETRY_KEYS = {
    "x", "y", "width", "height", "scrollX", "scrollY", "dpr",
    "viewportWidth", "viewportHeight", "viewportScale",
}


class HandoffError(ValueError):
    """A fixed, safe-to-report rejection of a private evidence bundle."""


def manual_command(root: Path = DOCICH_ROOT) -> list[str]:
    """Build the fixed five-minute command; no caller-provided args are used."""
    return [
        str(root / "bin/docich-soren91-corner-manual"),
        "--config", str(root / "config/docich.soren-live.toml"),
        "start", "--duration-minutes", str(MANUAL_DURATION_MINUTES),
        "--capture-profile", DIAGNOSTIC_PROFILE,
    ]


def start_png_diagnostic(*, root: Path = DOCICH_ROOT, popen=subprocess.Popen,
                         sleep=time.sleep) -> dict[str, object]:
    """Launch one bounded probe with capture flags isolated to its child."""
    if Path.cwd().resolve() != root.resolve():
        raise HandoffError("fixed_docich_root_required")
    # This intent is persisted in the one manual reservation so it survives
    # common-rotation queue dispatch. The effective capture flags are scoped
    # by the Soren adapter and the Mac renderer child, never this environment.
    child_env = os.environ.copy()
    with open(os.devnull, "wb") as sink:
        proc = popen(
            manual_command(root), cwd=str(root), env=child_env,
            stdin=subprocess.DEVNULL, stdout=sink, stderr=subprocess.STDOUT,
            start_new_session=True, close_fds=True,
        )
    sleep(1.0)
    returncode = proc.poll()
    # A zero exit can mean the normal manual request was durably queued.
    if returncode is not None and returncode != 0:
        raise HandoffError("manual_probe_exited_with_error")
    return {"status": "dispatched", "duration_minutes": MANUAL_DURATION_MINUTES,
            "capture_profile": DIAGNOSTIC_PROFILE}


def _open_dir_chain(path: Path) -> int:
    """Open every directory component without following any symlink."""
    if not path.is_absolute():
        raise HandoffError("invalid_runtime_root")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open("/", flags)
    try:
        for part in path.parts[1:]:
            if part in ("", ".", ".."):
                raise HandoffError("invalid_runtime_root")
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except Exception:
        os.close(fd)
        raise


def _private_dir(fd: int) -> os.stat_result:
    info = os.fstat(fd)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077):
        raise HandoffError("unsafe_private_directory")
    return info


def _open_child_dir(parent_fd: int, name: str, *, require_private: bool = True) -> int:
    if not name or name in (".", "..") or "/" in name:
        raise HandoffError("invalid_directory_name")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(name, flags, dir_fd=parent_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise HandoffError("unsafe_directory")
        if require_private and info.st_mode & 0o077:
            raise HandoffError("unsafe_private_directory")
        return fd
    except Exception:
        os.close(fd)
        raise


def _read_stable_file(dir_fd: int, name: str, maximum: int) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(name, flags, dir_fd=dir_fd)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or before.st_mode & 0o077 or before.st_nlink != 1
                or before.st_size <= 0 or before.st_size > maximum):
            raise HandoffError("unsafe_or_oversize_file")
        chunks: list[bytes] = []
        total = 0
        while True:
            block = os.read(fd, min(65536, maximum + 1 - total))
            if not block:
                break
            chunks.append(block)
            total += len(block)
            if total > maximum:
                raise HandoffError("oversize_file")
        after = os.fstat(fd)
        identity_before = (before.st_dev, before.st_ino, before.st_mode, before.st_nlink,
                           before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
                          after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        if identity_before != identity_after or total != before.st_size:
            raise HandoffError("file_changed_during_read")
        return b"".join(chunks), after
    finally:
        os.close(fd)


def _parse_metadata(data: bytes, *, session_id: str,
                    image_stat: os.stat_result | None = None) -> dict[str, object]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HandoffError("invalid_frame_metadata") from None
    if not isinstance(value, dict) or set(value) != METADATA_KEYS:
        raise HandoffError("invalid_frame_metadata")
    image = value.get("image")
    capture = value.get("capture")
    valid_int = lambda item, minimum: type(item) is int and item >= minimum
    valid_number = lambda item, minimum: type(item) in (int, float) and minimum <= item < float("inf")
    if (value.get("schema") != 1 or not valid_int(value.get("game"), 1)
            or not valid_int(value.get("turn"), 0) or value.get("sessionId") != session_id
            or value.get("reason") not in ELIGIBLE_REASONS
            or value.get("imageFormat") != "png"
            or not isinstance(image, dict) or set(image) != {"width", "height"}
            or not valid_int(image.get("width"), 1) or image["width"] > 16_384
            or not valid_int(image.get("height"), 1) or image["height"] > 16_384
            or not valid_int(value.get("fileMtimeMs"), 0)
            or not valid_int(value.get("recordedAtMs"), 0)
            or not isinstance(capture, dict) or set(capture) != {"capturedAtMs", "captureMs", "geometry"}
            or not valid_number(capture.get("capturedAtMs"), 0)
            or not valid_number(capture.get("captureMs"), 0)
            or not isinstance(capture.get("geometry"), dict)
            or set(capture["geometry"]) != CAPTURE_GEOMETRY_KEYS
            or any(not valid_number(number, 0) for number in capture["geometry"].values())):
        raise HandoffError("invalid_frame_metadata")
    for key in ("boardConfidence", "currentPieceConfidence"):
        confidence = value.get(key)
        if confidence is not None and (type(confidence) not in (int, float) or not 0 <= confidence <= 1):
            raise HandoffError("invalid_frame_metadata")
    if image_stat is not None:
        image_mtime = int(image_stat.st_mtime_ns / 1_000_000 + 0.5)
        if (value["fileMtimeMs"] != image_mtime
                or abs(value["recordedAtMs"] - image_mtime) > 30_000):
            raise HandoffError("frame_time_mismatch")
    return value


def _validate_png(data: bytes, metadata: dict[str, object]) -> None:
    if len(data) < 45 or not data.startswith(PNG_SIGNATURE):
        raise HandoffError("invalid_png")
    offset = 8
    width = height = None
    saw_idat = False
    ended = False
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset:offset + 4], "big")
        kind = data[offset + 4:offset + 8]
        chunk_end = offset + 12 + length
        if length > MAX_IMAGE_BYTES or chunk_end > len(data) or kind not in {b"IHDR", b"IDAT", b"IEND"}:
            raise HandoffError("invalid_png")
        chunk_data = data[offset + 8:offset + 8 + length]
        expected_crc = int.from_bytes(data[offset + 8 + length:chunk_end], "big")
        if zlib.crc32(data[offset + 4:offset + 8 + length]) & 0xffffffff != expected_crc:
            raise HandoffError("invalid_png")
        if width is None:
            if kind != b"IHDR" or length != 13:
                raise HandoffError("invalid_png")
            width = int.from_bytes(chunk_data[0:4], "big")
            height = int.from_bytes(chunk_data[4:8], "big")
            if (chunk_data[8] != 8 or chunk_data[9] not in (2, 6)
                    or chunk_data[10:13] != b"\x00\x00\x00"
                    or width <= 0 or height <= 0 or width > 16_384 or height > 16_384
                    or width * height > 16_777_216):
                raise HandoffError("invalid_png")
        elif kind == b"IHDR":
            raise HandoffError("invalid_png")
        elif kind == b"IDAT":
            saw_idat = True
        elif kind == b"IEND":
            if length != 0 or not saw_idat or chunk_end != len(data):
                raise HandoffError("invalid_png")
            ended = True
            break
        offset = chunk_end
    if not ended:
        raise HandoffError("invalid_png")
    image = metadata["image"]
    assert isinstance(image, dict)
    if width != image["width"] or height != image["height"]:
        raise HandoffError("frame_dimensions_mismatch")


def read_selected_run(runtime_root: Path, session_id: str) -> tuple[dict[str, object], list[tuple[str, bytes, bytes, dict[str, object]]]]:
    """Read one private run directory, using only dirfd/no-follow opens."""
    if not UUID_RE.fullmatch(session_id):
        raise HandoffError("invalid_session_id")
    root_fd = _open_dir_chain(runtime_root)
    tmp_fd = diag_fd = run_fd = None
    try:
        tmp_fd = _open_child_dir(root_fd, "tmp", require_private=False)
        diag_fd = _open_child_dir(tmp_fd, "rejected_frame_diagnostics")
        run_fd = _open_child_dir(diag_fd, f"run_{session_id}")
        names = os.listdir(run_fd)
        if not names or len(names) > MAX_FRAMES:
            raise HandoffError("invalid_frame_set")
        if any(not FRAME_DIR_RE.fullmatch(name) for name in names):
            raise HandoffError("unexpected_run_entry")
        image_indexes = sorted(int(match.group(1)) for name in names
                               if (match := FRAME_DIR_RE.fullmatch(name)))
        if not image_indexes or len(image_indexes) > MAX_FRAMES or image_indexes != list(range(len(image_indexes))):
            raise HandoffError("incomplete_frame_set")

        manifest_files: list[dict[str, object]] = []
        frames: list[tuple[str, bytes, bytes, dict[str, object]]] = []
        seen_reasons: set[str] = set()
        for index in image_indexes:
            pair_name = f"frame_{index:02d}"
            pair_fd = _open_child_dir(run_fd, pair_name)
            try:
                pair_names = os.listdir(pair_fd)
                if sorted(pair_names) != ["image.png", "metadata.json"]:
                    raise HandoffError("incomplete_frame_pair")
                image_data, image_stat = _read_stable_file(pair_fd, "image.png", MAX_IMAGE_BYTES)
                metadata_data, _ = _read_stable_file(pair_fd, "metadata.json", MAX_METADATA_BYTES)
            finally:
                os.close(pair_fd)
            metadata = _parse_metadata(metadata_data, session_id=session_id, image_stat=image_stat)
            _validate_png(image_data, metadata)
            reason = str(metadata["reason"])
            if reason in seen_reasons:
                raise HandoffError("duplicate_reason")
            seen_reasons.add(reason)
            frames.append((f"frame_{index:02d}", image_data, metadata_data, metadata))
            manifest_files.append({
                "image": f"{pair_name}/image.png",
                "imageSha256": hashlib.sha256(image_data).hexdigest(),
                "metadata": f"{pair_name}/metadata.json",
                "metadataSha256": hashlib.sha256(metadata_data).hexdigest(),
            })
        manifest: dict[str, object] = {"schema": 1, "sessionId": session_id,
                                      "files": manifest_files}
        return manifest, frames
    finally:
        for fd in (run_fd, diag_fd, tmp_fd, root_fd):
            if fd is not None:
                os.close(fd)


def export_run(runtime_root: Path, session_id: str, output: BinaryIO) -> None:
    manifest, frames = read_selected_run(runtime_root, session_id)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        manifest_data = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
        entries: list[tuple[str, bytes]] = [("manifest.json", manifest_data)]
        for index, (_name, image_data, metadata_data, _metadata) in enumerate(frames):
            entries.append((f"run_{session_id}/frame_{index:02d}/image.png", image_data))
            entries.append((f"run_{session_id}/frame_{index:02d}/metadata.json", metadata_data))
        for name, data in entries:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o600
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(data))
    payload = buffer.getvalue()
    if len(payload) > MAX_BUNDLE_BYTES:
        raise HandoffError("bundle_size_limit")
    output.write(payload)
    output.flush()


def _safe_tar_members(payload: bytes, session_id: str) -> dict[str, bytes]:
    if len(payload) <= 0 or len(payload) > MAX_BUNDLE_BYTES:
        raise HandoffError("invalid_bundle_size")
    expected_root = f"run_{session_id}/"
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            members = archive.getmembers()
            if not 2 <= len(members) <= MAX_FRAMES * 2 + 1:
                raise HandoffError("invalid_bundle_entries")
            files: dict[str, bytes] = {}
            for member in members:
                name = member.name
                if (name in files or not member.isfile() or member.issym() or member.islnk()
                        or member.size <= 0 or member.size > MAX_IMAGE_BYTES
                        or name.startswith("/") or ".." in Path(name).parts):
                    raise HandoffError("unsafe_bundle_entry")
                if name != "manifest.json" and not name.startswith(expected_root):
                    raise HandoffError("unexpected_bundle_path")
                stream = archive.extractfile(member)
                if stream is None:
                    raise HandoffError("invalid_bundle_entry")
                data = stream.read(member.size + 1)
                if len(data) != member.size:
                    raise HandoffError("invalid_bundle_entry_size")
                files[name] = data
    except (tarfile.TarError, OSError):
        raise HandoffError("invalid_bundle") from None
    return files


def verify_bundle(payload: bytes, session_id: str) -> dict[str, bytes]:
    if not UUID_RE.fullmatch(session_id):
        raise HandoffError("invalid_session_id")
    files = _safe_tar_members(payload, session_id)
    try:
        manifest = json.loads(files["manifest.json"])
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
        raise HandoffError("invalid_manifest") from None
    if not isinstance(manifest, dict) or set(manifest) != {"schema", "sessionId", "files"}:
        raise HandoffError("invalid_manifest")
    if manifest.get("schema") != 1 or manifest.get("sessionId") != session_id:
        raise HandoffError("invalid_manifest")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_FRAMES:
        raise HandoffError("invalid_manifest")
    expected: set[str] = set()
    for index, item in enumerate(entries):
        if (not isinstance(item, dict) or set(item) != {
                "image", "imageSha256", "metadata", "metadataSha256"}):
            raise HandoffError("invalid_manifest")
        pair_name = f"frame_{index:02d}"
        image_name = f"{pair_name}/image.png"
        sidecar_name = f"{pair_name}/metadata.json"
        if item.get("image") != image_name or item.get("metadata") != sidecar_name:
            raise HandoffError("invalid_manifest")
        image_path = f"run_{session_id}/{image_name}"
        sidecar_path = f"run_{session_id}/{sidecar_name}"
        expected.update((image_path, sidecar_path))
        image_data, sidecar_data = files.get(image_path), files.get(sidecar_path)
        if image_data is None or sidecar_data is None:
            raise HandoffError("incomplete_bundle")
        if (hashlib.sha256(image_data).hexdigest() != item.get("imageSha256")
                or hashlib.sha256(sidecar_data).hexdigest() != item.get("metadataSha256")):
            raise HandoffError("bundle_digest_mismatch")
        metadata = _parse_received_metadata(sidecar_data, session_id)
        _validate_png(image_data, metadata)
    if set(files) - {"manifest.json"} != expected:
        raise HandoffError("unexpected_bundle_entry")
    return files


def _parse_received_metadata(data: bytes, session_id: str) -> dict[str, object]:
    return _parse_metadata(data, session_id=session_id)


def receive_bundle(payload: bytes, session_id: str, output_dir: Path) -> dict[str, object]:
    files = verify_bundle(payload, session_id)
    if output_dir.exists() or output_dir.is_symlink():
        raise HandoffError("output_must_be_new")
    parent = output_dir.parent
    if not parent.is_dir() or parent.is_symlink():
        raise HandoffError("private_output_parent_required")
    parent_info = parent.stat()
    if parent_info.st_uid != os.geteuid() or parent_info.st_mode & 0o077:
        raise HandoffError("private_output_parent_required")
    output_dir.mkdir(mode=0o700)
    try:
        for archive_name, data in files.items():
            relative = Path(archive_name)
            destination = output_dir / relative
            parent = output_dir
            for part in relative.parts[:-1]:
                parent = parent / part
                parent.mkdir(mode=0o700, exist_ok=True)
                parent_info = parent.lstat()
                if not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode):
                    raise HandoffError("unsafe_bundle_directory")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(destination, flags, 0o600)
            try:
                with os.fdopen(fd, "wb", closefd=False) as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(fd)
            finally:
                os.close(fd)
        os.chmod(output_dir, 0o700)
    except Exception:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
    return {"status": "verified", "session_id": session_id,
            "files": len(files), "directory": str(output_dir)}


def read_local_archive(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or before.st_mode & 0o077 or before.st_size <= 0
                or before.st_size > MAX_BUNDLE_BYTES):
            raise HandoffError("unsafe_archive_path")
        chunks: list[bytes] = []
        total = 0
        while True:
            block = os.read(fd, min(65536, MAX_BUNDLE_BYTES + 1 - total))
            if not block:
                break
            total += len(block)
            if total > MAX_BUNDLE_BYTES:
                raise HandoffError("unsafe_archive_path")
            chunks.append(block)
        after = os.fstat(fd)
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or total != before.st_size):
            raise HandoffError("archive_changed_during_read")
        return b"".join(chunks)
    finally:
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    sub.add_parser("start", help="start one fixed five-minute PNG diagnostic probe")
    export_parser = sub.add_parser("export", help="write one selected run's private tar stream")
    export_parser.add_argument("session_id")
    receive_parser = sub.add_parser("receive", help="verify a private tar and save into a new private dir")
    receive_parser.add_argument("session_id")
    receive_parser.add_argument("--archive", required=True, type=Path)
    receive_parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.operation == "start":
            result = start_png_diagnostic()
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        elif args.operation == "export":
            export_run(RUNTIME_ROOT, args.session_id, sys.stdout.buffer)
        else:
            payload = read_local_archive(args.archive)
            result = receive_bundle(payload, args.session_id, args.output)
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (HandoffError, OSError) as error:
        message = str(error) if isinstance(error, HandoffError) else "operation_failed"
        print(f"Soren91 private frame handoff failed: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
