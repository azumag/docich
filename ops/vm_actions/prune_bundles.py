#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import time
from pathlib import Path

SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
MAX_ENTRIES = 4096
DEFAULT_MIN_AGE_SECONDS = 7 * 24 * 60 * 60
DEFAULT_KEEP_UNREFERENCED = 8


class UnsafeRetentionState(RuntimeError):
    pass


def _validate_sha(value, *, required=False):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise UnsafeRetentionState("invalid reference")
    return value


def load_config(path: Path):
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
        raise UnsafeRetentionState("untrusted config")
    cfg = json.loads(path.read_text())
    state = Path(cfg["state"])
    if not state.is_absolute() or state.is_symlink() or not state.is_dir():
        raise UnsafeRetentionState("untrusted state root")
    repos = cfg.get("repos")
    if not isinstance(repos, dict):
        raise UnsafeRetentionState("invalid repo config")
    return cfg


def _scan_bundles(state: Path, repo: str, now: float, max_entries: int):
    root = state / "bundles" / repo
    if root.is_symlink():
        raise UnsafeRetentionState("bundle root symlink")
    if not root.exists():
        return root, {}
    if not root.is_dir():
        raise UnsafeRetentionState("bundle root invalid")

    bundles = {}
    seen = 0
    try:
        entries = os.scandir(root)
    except OSError as exc:
        raise UnsafeRetentionState("bundle scan failed") from exc
    with entries:
        for entry in entries:
            seen += 1
            if seen > max_entries:
                raise UnsafeRetentionState("bundle scan bound exceeded")
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise UnsafeRetentionState("bundle stat failed") from exc
            name = entry.name
            if entry.is_symlink() or not stat.S_ISREG(st.st_mode) or not name.endswith(".bundle"):
                raise UnsafeRetentionState("unexpected bundle entry")
            sha = name[:-7]
            if not SHA_RE.fullmatch(sha):
                raise UnsafeRetentionState("unexpected bundle entry")
            bundles[sha] = {
                "path": Path(entry.path),
                "bytes": max(0, int(st.st_size)),
                "mtime_ns": int(st.st_mtime_ns),
                "age": max(0, int(now - st.st_mtime)),
                "dev": int(st.st_dev),
                "ino": int(st.st_ino),
            }
    return root, bundles


def _read_current_references(state: Path, repo: str):
    path = state / "current" / f"{repo}.json"
    if path.is_symlink() or not path.is_file():
        raise UnsafeRetentionState("current state unavailable")
    try:
        current = json.loads(path.read_text())
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise UnsafeRetentionState("current state invalid") from exc
    if not isinstance(current, dict):
        raise UnsafeRetentionState("current state invalid")

    refs = {_validate_sha(current.get("sha"), required=True)}
    previous = _validate_sha(current.get("previous_head"))
    if previous:
        refs.add(previous)

    intent = current.get("deployment_intent")
    if intent is not None:
        if not isinstance(intent, dict):
            raise UnsafeRetentionState("deployment intent invalid")
        refs.add(_validate_sha(intent.get("from"), required=True))
        refs.add(_validate_sha(intent.get("to"), required=True))

    repairs = current.get("pending_repairs", [])
    if not isinstance(repairs, list):
        raise UnsafeRetentionState("pending repairs invalid")
    for repair in repairs:
        if not isinstance(repair, dict):
            raise UnsafeRetentionState("pending repair invalid")
        refs.add(_validate_sha(repair.get("candidate_sha"), required=True))
    return refs


def _read_preview_references(state: Path, repo: str, max_entries: int):
    root = state / "releases" / repo
    if root.is_symlink():
        raise UnsafeRetentionState("release root symlink")
    if not root.exists():
        return set()
    if not root.is_dir():
        raise UnsafeRetentionState("release root invalid")

    refs = set()
    seen = 0
    try:
        entries = os.scandir(root)
    except OSError as exc:
        raise UnsafeRetentionState("release scan failed") from exc
    with entries:
        for entry in entries:
            seen += 1
            if seen > max_entries:
                raise UnsafeRetentionState("release scan bound exceeded")
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise UnsafeRetentionState("release stat failed") from exc
            if entry.is_symlink() or not stat.S_ISDIR(st.st_mode) or not SHA_RE.fullmatch(entry.name):
                raise UnsafeRetentionState("unexpected release entry")
            refs.add(entry.name)
    return refs


def _fingerprint_matches(item):
    try:
        st = item["path"].lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(st.st_mode)
        and not item["path"].is_symlink()
        and int(st.st_dev) == item["dev"]
        and int(st.st_ino) == item["ino"]
        and int(st.st_size) == item["bytes"]
        and int(st.st_mtime_ns) == item["mtime_ns"]
    )


def rotate_bundles(
    cfg,
    repo="docich",
    *,
    now=None,
    min_age_seconds=DEFAULT_MIN_AGE_SECONDS,
    keep_unreferenced=DEFAULT_KEEP_UNREFERENCED,
    max_entries=MAX_ENTRIES,
    dry_run=False,
):
    if not isinstance(min_age_seconds, int) or min_age_seconds < 0:
        raise ValueError("invalid minimum age")
    if not isinstance(keep_unreferenced, int) or keep_unreferenced < 0:
        raise ValueError("invalid retention count")
    if not isinstance(max_entries, int) or max_entries < 1:
        raise ValueError("invalid scan bound")

    state = Path(cfg["state"])
    if not state.is_absolute() or state.is_symlink() or not state.is_dir():
        raise UnsafeRetentionState("untrusted state root")
    if repo not in cfg.get("repos", {}):
        raise UnsafeRetentionState("unknown repo")

    now = time.time() if now is None else float(now)
    bundle_root, bundles = _scan_bundles(state, repo, now, max_entries)
    referenced = _read_current_references(state, repo)
    referenced.update(_read_preview_references(state, repo, max_entries))

    unreferenced = [sha for sha in bundles if sha not in referenced]
    unreferenced.sort(key=lambda sha: (bundles[sha]["mtime_ns"], sha), reverse=True)
    generation_keep = set(unreferenced[:keep_unreferenced])
    candidates = [
        sha
        for sha in unreferenced
        if sha not in generation_keep and bundles[sha]["age"] >= min_age_seconds
    ]
    candidates.sort(key=lambda sha: (bundles[sha]["mtime_ns"], sha))

    # Preflight every candidate before deleting anything. The gateway exec operation
    # holds vm-operations.lock around this script, so this also prevents racing
    # upload/deploy/preview operations that use the same owner-only control plane.
    if any(not _fingerprint_matches(bundles[sha]) for sha in candidates):
        raise UnsafeRetentionState("bundle changed during scan")

    deleted_bytes = sum(bundles[sha]["bytes"] for sha in candidates)
    if not dry_run and candidates:
        for sha in candidates:
            try:
                bundles[sha]["path"].unlink()
            except OSError as exc:
                raise UnsafeRetentionState("bundle delete failed") from exc
        try:
            directory_fd = os.open(bundle_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise UnsafeRetentionState("bundle directory sync failed") from exc

    recent_unreferenced = sum(1 for sha in unreferenced if bundles[sha]["age"] < min_age_seconds)
    return {
        "status": "dry_run" if dry_run else "rotated",
        "bundle_count": len(bundles),
        "referenced_count": len(set(bundles).intersection(referenced)),
        "unreferenced_count": len(unreferenced),
        "retained_recent_count": recent_unreferenced,
        "retained_generation_cap": keep_unreferenced,
        "deleted_count": len(candidates),
        "deleted_bytes": deleted_bytes,
        "min_age_seconds": min_age_seconds,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fail-closed Git bundle rotation for the owner-only VM control plane")
    parser.add_argument("config", type=Path)
    parser.add_argument("--repo", default="docich")
    parser.add_argument("--min-age-seconds", type=int, default=DEFAULT_MIN_AGE_SECONDS)
    parser.add_argument("--keep-unreferenced", type=int, default=DEFAULT_KEEP_UNREFERENCED)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
        result = rotate_bundles(
            cfg,
            args.repo,
            min_age_seconds=args.min_age_seconds,
            keep_unreferenced=args.keep_unreferenced,
            dry_run=args.dry_run,
        )
    except (UnsafeRetentionState, OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        print("bundle retention refused", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print(
            "bundle retention complete: "
            f"deleted={result['deleted_count']} deleted_bytes={result['deleted_bytes']} "
            f"bundles={result['bundle_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
