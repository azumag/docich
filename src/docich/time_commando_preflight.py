"""Read-only structural inspection of private Time Commando ZIP/DOSZ content.

Never extracts files, executes content, launches an emulator, or grants runtime
readiness. Only the small CUE descriptor is decompressed; game data stays opaque.
Output intentionally excludes host paths, content hashes and raw file contents.
"""
from __future__ import annotations

import argparse
import json
import re
import stat
from pathlib import Path, PurePosixPath
import zipfile
import zlib

MAX_MEMBERS = 4096
MAX_ARCHIVE_BYTES = 4 * 1024**3
MAX_TOTAL_BYTES = 4 * 1024**3
MAX_DESCRIPTOR_BYTES = 64 * 1024
COMPANIONS = ("TIMECO.BIN", "RESSOURC.HQR", "DOS4GW.EXE")
FILE_RE = re.compile(r'FILE\s+"([^"]+)"\s+BINARY', re.IGNORECASE)
TRACK_RE = re.compile(r"TRACK\s+(\d+)\s+(MODE1/2352|AUDIO)", re.IGNORECASE)
INDEX_RE = re.compile(r"INDEX\s+(\d+)\s+(\d+):(\d+):(\d+)", re.IGNORECASE)


class InspectionError(ValueError):
    """Unsafe, ambiguous, unsupported, or incomplete archive metadata."""


def _safe_name(name: str, *, directory: bool = False) -> str:
    if directory and name.endswith("/"):
        name = name[:-1]
    if (not name or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or "\\" in name or ":" in name or name.startswith("/")
            or any(part in ("", ".", "..") for part in name.split("/"))):
        raise InspectionError("unsafe_member_name")
    return name


def _members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS:
        raise InspectionError("member_limit")
    if sum(info.file_size for info in infos) > MAX_TOTAL_BYTES:
        raise InspectionError("uncompressed_size_limit")
    files: dict[str, zipfile.ZipInfo] = {}
    names: set[str] = set()
    prefixes: dict[str, tuple[str, bool]] = {}
    for info in infos:
        name = _safe_name(info.orig_filename, directory=info.is_dir())
        _safe_name(info.filename, directory=info.is_dir())
        if info.orig_filename != info.filename:
            raise InspectionError("member_name_metadata_mismatch")
        if name.casefold() in names:
            raise InspectionError("duplicate_or_case_collision")
        names.add(name.casefold())
        mode = stat.S_IFMT(info.external_attr >> 16)
        if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise InspectionError("non_regular_member")
        if ((mode == stat.S_IFDIR and not info.is_dir())
                or (mode == stat.S_IFREG and info.is_dir())):
            raise InspectionError("invalid_directory_metadata")
        if info.flag_bits & 1:
            raise InspectionError("encrypted_member")
        parts = name.split("/")
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            is_directory = index < len(parts) or info.is_dir()
            previous = prefixes.get(prefix.casefold())
            if previous is not None:
                if previous[0] != prefix:
                    raise InspectionError("duplicate_or_case_collision")
                if previous[1] != is_directory:
                    raise InspectionError("file_directory_collision")
            prefixes[prefix.casefold()] = (prefix, is_directory)
        if not info.is_dir():
            files[name] = info
    return files


def _cue(archive: zipfile.ZipFile, info: zipfile.ZipInfo,
         files: dict[str, zipfile.ZipInfo]) -> dict:
    if info.file_size > MAX_DESCRIPTOR_BYTES:
        raise InspectionError("descriptor_size_limit")
    with archive.open(info) as descriptor:
        content = descriptor.read(MAX_DESCRIPTOR_BYTES + 1)
    if len(content) > MAX_DESCRIPTOR_BYTES:
        raise InspectionError("descriptor_size_limit")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InspectionError("descriptor_encoding") from exc
    parent = PurePosixPath(info.filename).parent
    target = None
    track = None
    tracks: list[dict] = []
    references: dict[str, int] = {}
    positions: dict[str, int] = {}
    last_index01: dict[str, int] = {}
    current_file_tracks = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.upper().startswith("REM "):
            continue
        if match := FILE_RE.fullmatch(line):
            if target is not None and current_file_tracks == 0:
                raise InspectionError("file_without_track")
            relative = _safe_name(match.group(1))
            target = str(parent / relative)
            # CUE/host lookup must match exactly; case-insensitive DOS lookup
            # does not prove that a host-side CD reference will resolve.
            if target not in files:
                raise InspectionError("cd_reference_missing_or_case_mismatch")
            if not files[target].file_size:
                raise InspectionError("empty_cd_image")
            if target in references:
                raise InspectionError("duplicate_cd_reference")
            references[target] = 0
            current_file_tracks = 0
            track = None
        elif match := TRACK_RE.fullmatch(line):
            if target is None:
                raise InspectionError("track_without_file")
            number = int(match.group(1))
            if number != len(tracks) + 1:
                raise InspectionError("track_sequence")
            track = {"number": number, "mode": match.group(2).upper(),
                     "index01": False, "last_index": -1}
            tracks.append(track)
            current_file_tracks += 1
        elif match := INDEX_RE.fullmatch(line):
            if track is None or target is None:
                raise InspectionError("index_without_track")
            index, minutes, seconds, frames = map(int, match.groups())
            if index not in (0, 1) or seconds >= 60 or frames >= 75:
                raise InspectionError("invalid_index")
            if index <= track["last_index"]:
                raise InspectionError("duplicate_or_reversed_index")
            track["last_index"] = index
            sector = (minutes * 60 + seconds) * 75 + frames
            if sector < positions.get(target, -1):
                raise InspectionError("decreasing_index")
            if index == 0 and sector <= last_index01.get(target, -1):
                raise InspectionError("non_increasing_track_start")
            positions[target] = sector
            if index == 1:
                if sector <= last_index01.get(target, -1):
                    raise InspectionError("non_increasing_track_index01")
                last_index01[target] = sector
                track["index01"] = True
                references[target] = max(references[target], (sector + 1) * 2352)
        else:
            # This small inspector intentionally supports only the observed
            # descriptor grammar, rather than guessing at other CUE variants.
            raise InspectionError("unsupported_descriptor_line")
    if target is not None and current_file_tracks == 0:
        raise InspectionError("file_without_track")
    if not tracks or any(not t["index01"] for t in tracks):
        raise InspectionError("missing_track_index01")
    if tracks[0]["mode"] != "MODE1/2352" or any(
            t["mode"] != "AUDIO" for t in tracks[1:]):
        raise InspectionError("unexpected_track_layout")
    if len(tracks) != 3:
        raise InspectionError("unexpected_track_count")
    if any(files[name].file_size < minimum for name, minimum in references.items()):
        raise InspectionError("cd_image_truncated_before_index")
    return {"track_modes": [t["mode"] for t in tracks], "referenced_images": len(references)}


def inspect_archive(path: Path) -> dict:
    """Return metadata findings, always leaving real-game acceptance untested."""
    result = {
        "schema_version": 1,
        "mode": "offline_read_only",
        "archive_structure": "blocked",
        "runtime_acceptance": "not_run",
        "rights_acceptance": "not_assessed",
        "blockers": [],
        "warnings": ["metadata_is_not_content_integrity_or_runtime_compatibility"],
    }
    try:
        if path.suffix.lower() not in (".zip", ".dosz"):
            raise InspectionError("unsupported_archive_extension")
        if path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise InspectionError("archive_size_limit")
        with zipfile.ZipFile(path) as archive:
            files = _members(archive)
            result["file_count"] = len(files)
            entries = [name for name in files if PurePosixPath(name).name == "TIMECO.EXE"]
            if len(entries) != 1:
                raise InspectionError("missing_or_ambiguous_timeco_exe")
            entry = entries[0]
            parent = PurePosixPath(entry).parent
            if not files[entry].file_size:
                raise InspectionError("empty_timeco_exe")
            for name in COMPANIONS:
                sibling = str(parent / name)
                if sibling not in files or not files[sibling].file_size:
                    raise InspectionError("missing_or_empty_companion")
            result["entry_location"] = "archive_root" if str(parent) == "." else "nested_directory"
            cues = [name for name in files if name.lower().endswith(".cue")]
            observed_dat = str(parent / "GAME.DAT")
            if len(cues) > 1:
                raise InspectionError("ambiguous_cd_descriptor")
            if not cues:
                if observed_dat not in files:
                    raise InspectionError("missing_cd_descriptor")
                cue_name = observed_dat
                result["warnings"].append("dat_extension_is_not_verified_as_a_pure_cd_image")
            else:
                cue_name = cues[0]
            result["cd"] = _cue(archive, files[cue_name], files)
            if not cues:
                raise InspectionError("explicit_cue_content_required_before_runtime_test")
            if any(PurePosixPath(name).name.upper() == "DOSBOX.BAT" for name in files):
                raise InspectionError("autoexec_batch_requires_separate_review")
            result["archive_structure"] = "metadata_checks_passed"
    except InspectionError as exc:
        result["blockers"].append(str(exc))
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError,
            UnicodeError, EOFError, zlib.error, ValueError):
        # Do not include private filenames or library exception text in reports.
        result["blockers"].append("archive_read_failed")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="private external ZIP/DOSZ, read only")
    args = parser.parse_args(argv)
    result = inspect_archive(args.archive)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    return 0 if result["archive_structure"] == "metadata_checks_passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
