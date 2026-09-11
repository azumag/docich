#!/usr/bin/env python3
import hashlib
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
ALLOWED_SUBMODULES = {"games/soviet_now"}
LIVE_PROJECTION = Path("/home/ubuntu/soren")

# Numeric refusal reasons only; exec output is withheld. All codes <128.
REASON_INVALID_ROOT = 40
REASON_UNAPPROVED_SUBMODULE = 41
REASON_INVALID_SHA = 42
REASON_NO_ADVANCE = 43
REASON_ROOT_MOVED = 50
REASON_ROOT_DRIFT = 51
REASON_OLD_GITLINK_MISMATCH = 52
REASON_SUBMODULE_MISSING = 53
REASON_SUBMODULE_HEAD_MISMATCH = 54
REASON_SUBMODULE_DRIFT = 55
REASON_OLD_OBJECT_MISSING = 56
REASON_NEW_OBJECT_MISSING = 57
REASON_NOT_DESCENDANT = 58
REASON_MUTATION_FAILED = 59
REASON_POSTVERIFY_FAILED = 60
REASON_GIT_VERIFICATION_FAILED = 61
REASON_PROJECTION_UNSUPPORTED = 62
REASON_PROJECTION_UNKNOWN_STATE = 63
REASON_PROJECTION_MUTATION_FAILED = 64
REASON_PROJECTION_POSTVERIFY_FAILED = 65

# 71..85: mask of unknown paths in 2-4 path diffs. Else generic 63.
REASON_PROJECTION_UNKNOWN_MASK_BASE = 70
PROJECTION_UNKNOWN_MASK_MAX_PATHS = 4

# 90+bit*3+class for one unknown: 0 absent, 1 unreviewed length, 2 same length.
# Pruned .github/ files heal to old; other unknowns stay fail-closed.
REPO_ONLY_PREFIXES = (".github/",)
# Opt-in skew recovery (argv "lineage"); reviewed intermediates converge.
_LINEAGE = False
# Reviewed-commit attestation argv triples: exact byte+mode match only.
_ATTESTED = []
ATTEST_SHA_RE = re.compile(r"[a-f0-9]{64}\Z")
ATTEST_PATH_RE = re.compile(r"[A-Za-z0-9._/-]+\Z")
ATTEST_MODES = {"100644": 0o644, "100755": 0o755}
ATTEST_MAX = 16
REASON_PROJECTION_UNKNOWN_CLASS_BASE = 90
PROJECTION_UNKNOWN_CLASS_PER_PATH = 3
PROJECTION_UNKNOWN_CLASS_ABSENT = 0
PROJECTION_UNKNOWN_CLASS_RESIZED = 1
PROJECTION_UNKNOWN_CLASS_SAME_LENGTH = 2


class ReconcileError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _gb(root):
    return ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null"]


def _git(root, *args, reason=REASON_GIT_VERIFICATION_FAILED, raw=False):
    try:
        out = subprocess.check_output(_gb(root) + list(args), stderr=subprocess.DEVNULL, text=not raw)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise ReconcileError(reason, "git failed") from exc
    return out if raw else out.strip()


def _git_run(root, *args):
    try:
        subprocess.run(_gb(root) + list(args), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=60)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ReconcileError(REASON_MUTATION_FAILED, "mutation failed") from exc


def _is_ancestor(root, ancestor, descendant):
    try:
        result = subprocess.run(_gb(root) + ["merge-base", "--is-ancestor", ancestor, descendant], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ReconcileError(REASON_GIT_VERIFICATION_FAILED, "ancestry failed") from exc
    if result.returncode not in {0, 1}:
        raise ReconcileError(REASON_GIT_VERIFICATION_FAILED, "ancestry failed")
    return result.returncode == 0


def _gitlink_at(root, commit, path):
    line = _git(root, "ls-tree", commit, "--", path)
    fields = line.split()
    if len(fields) < 3 or fields[0] != "160000" or fields[1] != "commit" or not SHA_RE.fullmatch(fields[2]):
        raise ReconcileError(REASON_OLD_GITLINK_MISMATCH, "gitlink missing")
    return fields[2]


def _changed_paths(repo, old, new):
    raw = _git(repo, "diff", "--name-only", "-z", "--no-renames", old, new, "--", raw=True)
    return [item.decode("utf-8", "strict") for item in raw.split(b"\0") if item]


def _projection_entry(repo, commit, rel):
    raw = _git(repo, "ls-tree", "-z", commit, "--", rel, raw=True)
    if not raw:
        return None
    entries = [item for item in raw.split(b"\0") if item]
    if len(entries) != 1:
        raise ReconcileError(REASON_PROJECTION_UNSUPPORTED, "ambiguous entry")
    meta, raw_path = entries[0].split(b"\t", 1)
    mode, kind, obj = meta.decode("ascii").split()
    if raw_path.decode("utf-8", "strict") != rel or kind != "blob" or mode not in {"100644", "100755"}:
        raise ReconcileError(REASON_PROJECTION_UNSUPPORTED, "regular files only")
    return {"mode": 0o755 if mode == "100755" else 0o644, "object": obj}


def _projection_expected(repo, entry):
    if entry is None:
        return None
    data = _git(repo, "cat-file", "blob", entry["object"], raw=True)
    if len(data) > 32 * 1024 * 1024:
        raise ReconcileError(REASON_PROJECTION_UNSUPPORTED, "file too large")
    return {"sha256": hashlib.sha256(data).hexdigest(), "mode": entry["mode"], "data": data}


def _safe_projection_path(destination, rel):
    part = PurePosixPath(rel)
    if part.is_absolute() or not part.parts or any(p in {"", ".", ".."} for p in part.parts):
        raise ReconcileError(REASON_PROJECTION_UNSUPPORTED, "unsafe path")
    if destination.is_symlink() or not destination.is_dir():
        raise ReconcileError(REASON_PROJECTION_UNSUPPORTED, "unsafe root")
    current = destination
    for component in part.parts:
        current = current / component
        if current.exists() and current.is_symlink():
            raise ReconcileError(REASON_PROJECTION_UNSUPPORTED, "symlink in path")
    return current


def _projection_live(path):
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ReconcileError(REASON_PROJECTION_UNSUPPORTED, "not a regular file")
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "mode": stat.S_IMODE(path.stat().st_mode), "data": data}


def _projection_same(actual, expected):
    if actual is None or expected is None:
        return actual is None and expected is None
    return actual["sha256"] == expected["sha256"] and actual["mode"] == expected["mode"]


def _projection_content_state(actual, old_expected, new_expected):
    live = actual["sha256"] if actual is not None else None
    if live == (old_expected["sha256"] if old_expected is not None else None):
        return 0
    return 1 if live == (new_expected["sha256"] if new_expected is not None else None) else 2


def _projection_unknown_reason(states, unknowns=None):
    # Mask/generic mapping; a lone unknown refines to its class.
    if len(states) < 2 or len(states) > PROJECTION_UNKNOWN_MASK_MAX_PATHS:
        return REASON_PROJECTION_UNKNOWN_STATE
    mask = sum(1 << index for index, state in enumerate(states) if state == 2)
    if mask == 0 or unknowns is None or mask & (mask - 1) or not unknowns:
        if mask == 0:
            return REASON_PROJECTION_UNKNOWN_STATE
        return REASON_PROJECTION_UNKNOWN_MASK_BASE + mask
    bit, live, old_expected, new_expected = unknowns[0]
    if live is None:
        klass = PROJECTION_UNKNOWN_CLASS_ABSENT
    else:
        known_lens = {len(e["data"]) for e in (old_expected, new_expected) if e is not None}
        klass = PROJECTION_UNKNOWN_CLASS_SAME_LENGTH if len(live["data"]) in known_lens else PROJECTION_UNKNOWN_CLASS_RESIZED
    return REASON_PROJECTION_UNKNOWN_CLASS_BASE + bit * PROJECTION_UNKNOWN_CLASS_PER_PATH + klass


def _atomic_projection_write(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".v-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _reviewed_lineage(repo, rel, old_sub, new_sub, live_sha):
    # True only if live bytes equal a reviewed blob of rel in old..new.
    try:
        commits = _git(repo, "rev-list", f"{old_sub}..{new_sub}", "--", rel).split()
    except ReconcileError:
        return False
    if len(commits) > 64:
        return False
    for commit in commits:
        try:
            entry = _projection_entry(repo, commit, rel)
            if entry is None:
                continue
            data = _git(repo, "cat-file", "blob", entry["object"], raw=True)
        except ReconcileError:
            continue
        if hashlib.sha256(data).hexdigest() == live_sha:
            return True
    return False


def _attestations(tokens):
    if len(tokens) % 3 or len(tokens) // 3 > ATTEST_MAX:
        raise ReconcileError(REASON_INVALID_SHA, "invalid attestation list")
    attested = []
    for index in range(0, len(tokens), 3):
        path, digest, mode = tokens[index], tokens[index + 1], tokens[index + 2]
        if not ATTEST_PATH_RE.fullmatch(path) or path.startswith("/") or ".." in path.split("/"):
            raise ReconcileError(REASON_INVALID_SHA, "invalid attestation path")
        if not ATTEST_SHA_RE.fullmatch(digest) or mode not in ATTEST_MODES:
            raise ReconcileError(REASON_INVALID_SHA, "invalid attestation blob")
        attested.append((path, digest, ATTEST_MODES[mode]))
    return attested


def _reviewed_attestation(rel, live):
    return live is not None and any(p == rel and d == live["sha256"] and m == live["mode"] for p, d, m in _ATTESTED)


def _set_projection(path, target):
    if target is None:
        if path.exists():
            path.unlink()
    else:
        _atomic_projection_write(path, target["data"], target["mode"])


def _normalize_projection(repo, old_sub, new_sub, destination):
    plans = []
    verifies = []
    states = []
    unknowns = []
    changed = _changed_paths(repo, old_sub, new_sub)
    for index, rel in enumerate(changed):
        path = _safe_projection_path(destination, rel)
        old_meta = _projection_expected(repo, _projection_entry(repo, old_sub, rel))
        new_meta = _projection_expected(repo, _projection_entry(repo, new_sub, rel))
        live_meta = _projection_live(path)
        verifies.append((path, old_meta))
        state = _projection_content_state(live_meta, old_meta, new_meta)
        if state == 2 and live_meta is None and old_meta is not None and rel.startswith(REPO_ONLY_PREFIXES):
            plans.append((path, old_meta, None))
            states.append(0)
            continue
        if state == 2 and _LINEAGE and live_meta is not None and (
                _reviewed_lineage(repo, rel, old_sub, new_sub, live_meta["sha256"])
                or _reviewed_attestation(rel, live_meta)):
            plans.append((path, old_meta, live_meta))
            states.append(0)
            continue
        states.append(state)
        if state == 2:
            unknowns.append((index, live_meta, old_meta, new_meta))
            continue
        if _projection_same(live_meta, old_meta):
            continue
        plans.append((path, old_meta, live_meta))

    if 2 in states:
        raise ReconcileError(_projection_unknown_reason(states, unknowns), "unknown projection content")

    applied = []
    try:
        for path, old_meta, original_live in plans:
            if not _projection_same(_projection_live(path), original_live):
                raise ReconcileError(REASON_PROJECTION_UNKNOWN_STATE, "concurrent drift")
            _set_projection(path, old_meta)
            applied.append((path, old_meta, original_live))
        for path, old_meta in verifies:
            if not _projection_same(_projection_live(path), old_meta):
                raise ReconcileError(REASON_PROJECTION_POSTVERIFY_FAILED, "postverify failed")
    except Exception as exc:
        rollback_failed = False
        for path, old_meta, original_live in reversed(applied):
            try:
                if _projection_same(_projection_live(path), old_meta):
                    _set_projection(path, original_live)
                elif not _projection_same(_projection_live(path), original_live):
                    rollback_failed = True
            except Exception:
                rollback_failed = True
        if rollback_failed:
            raise ReconcileError(REASON_PROJECTION_MUTATION_FAILED, "rollback incomplete") from exc
        if isinstance(exc, ReconcileError):
            raise
        raise ReconcileError(REASON_PROJECTION_MUTATION_FAILED, "normalization failed") from exc


def reconcile(root, old_parent, old_sub, new_sub, sub_path, projection_destination=None):
    if not root.is_absolute() or not root.is_dir():
        raise ReconcileError(REASON_INVALID_ROOT, "invalid root")
    if sub_path not in ALLOWED_SUBMODULES:
        raise ReconcileError(REASON_UNAPPROVED_SUBMODULE, "submodule not eligible")
    if not all(SHA_RE.fullmatch(value) for value in (old_parent, old_sub, new_sub)):
        raise ReconcileError(REASON_INVALID_SHA, "invalid SHA")
    if old_sub == new_sub:
        raise ReconcileError(REASON_NO_ADVANCE, "no advance")

    if _git(root, "rev-parse", "HEAD", reason=REASON_ROOT_MOVED) != old_parent:
        raise ReconcileError(REASON_ROOT_MOVED, "root moved")
    if _git(root, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all", reason=REASON_ROOT_DRIFT):
        raise ReconcileError(REASON_ROOT_DRIFT, "root drift")
    if _gitlink_at(root, old_parent, sub_path) != old_sub:
        raise ReconcileError(REASON_OLD_GITLINK_MISMATCH, "gitlink mismatch")

    sub = root / sub_path
    if not sub.is_dir():
        raise ReconcileError(REASON_SUBMODULE_MISSING, "checkout missing")

    current_sub = _git(sub, "rev-parse", "HEAD", reason=REASON_SUBMODULE_HEAD_MISMATCH)
    if current_sub not in {old_sub, new_sub}:
        current_tree = _git(sub, "rev-parse", f"{current_sub}^{{tree}}", reason=REASON_SUBMODULE_HEAD_MISMATCH)
        reviewed_tree = _git(sub, "rev-parse", f"{new_sub}^{{tree}}", reason=REASON_NEW_OBJECT_MISSING)
        if current_tree != reviewed_tree or not _is_ancestor(sub, current_sub, new_sub):
            raise ReconcileError(REASON_SUBMODULE_HEAD_MISMATCH, "not at accepted state")

    if _git(sub, "status", "--porcelain", "--untracked-files=no", reason=REASON_SUBMODULE_DRIFT):
        raise ReconcileError(REASON_SUBMODULE_DRIFT, "tracked drift")

    _git(sub, "cat-file", "-e", f"{old_sub}^{{commit}}", reason=REASON_OLD_OBJECT_MISSING)
    _git(sub, "cat-file", "-e", f"{new_sub}^{{commit}}", reason=REASON_NEW_OBJECT_MISSING)
    if not _is_ancestor(sub, old_sub, new_sub):
        raise ReconcileError(REASON_NOT_DESCENDANT, "not descendant")

    if current_sub != old_sub:
        _git_run(sub, "checkout", "--detach", "--quiet", old_sub)

    if _git(sub, "rev-parse", "HEAD", reason=REASON_POSTVERIFY_FAILED) != old_sub:
        raise ReconcileError(REASON_POSTVERIFY_FAILED, "postverify failed")
    if _git(sub, "status", "--porcelain", "--untracked-files=no", reason=REASON_POSTVERIFY_FAILED):
        raise ReconcileError(REASON_POSTVERIFY_FAILED, "postverify failed")

    if projection_destination is not None:
        _normalize_projection(sub, old_sub, new_sub, projection_destination)


def main(argv):
    global _LINEAGE, _ATTESTED
    if len(argv) < 6 or len(argv) > 7 + ATTEST_MAX * 3:
        raise ReconcileError(REASON_INVALID_SHA, "invalid reconcile arguments")
    rest = argv[6:]
    if rest and rest[0] != "lineage":
        raise ReconcileError(REASON_INVALID_SHA, "invalid reconcile arguments")
    _LINEAGE = bool(rest)
    _ATTESTED = _attestations(rest[1:])
    reconcile(Path(argv[1]), argv[2], argv[3], argv[4], argv[5], LIVE_PROJECTION)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except ReconcileError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        raise SystemExit(exc.code)
