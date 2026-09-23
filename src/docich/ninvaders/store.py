"""Versioned store for nInvaders policies (baseline seed + promoted versions).

Layout under ``<state_dir>/resolver/ninvaders/``::

    versions/<sha12>.py      immutable policy source (sha of the content)
    versions/<sha12>.json    metadata: parent, origin, evaluation, change note
    current.json             {"sha", "parent", "promoted_at"}  (atomic replace)
    history.jsonl            every attempt (promoted / kept / rejected) + rollbacks

``current()`` is what the live player and the evaluator's incumbent read.  A
missing, unreadable or hash-mismatching pointer falls back to the tracked
baseline seed, so a broken store can never leave the game without a player.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path

from .sandbox import policy_sha

POLICY_SHA_RE = re.compile(r"^[0-9a-f]{12}$")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def baseline_policy_path() -> Path:
    return repo_root() / "brains" / "ninvaders" / "policy.py"


def default_policy_dir(state_dir) -> Path:
    return Path(state_dir) / "resolver" / "ninvaders"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class PolicyStore:
    def __init__(self, policy_dir, baseline_path=None):
        self.dir = Path(policy_dir)
        self.baseline = Path(baseline_path) if baseline_path else baseline_policy_path()

    @property
    def versions_dir(self) -> Path:
        return self.dir / "versions"

    def baseline_entry(self) -> dict:
        source = self.baseline.read_text(encoding="utf-8")
        return {"sha": policy_sha(source), "path": str(self.baseline), "origin": "baseline",
                "parent": None}

    def current(self) -> dict:
        """The active policy: {"sha", "path", "origin", "parent"}."""
        try:
            pointer = json.loads((self.dir / "current.json").read_text(encoding="utf-8"))
            sha = str(pointer["sha"])
            if not POLICY_SHA_RE.fullmatch(sha):
                return self.baseline_entry()
            path = self.versions_dir / f"{sha}.py"
            if policy_sha(path.read_text(encoding="utf-8")) == sha:
                return {"sha": sha, "path": str(path), "origin": "promoted",
                        "parent": pointer.get("parent")}
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return self.baseline_entry()

    def source_of(self, sha: str) -> str:
        if sha == self.baseline_entry()["sha"]:
            return self.baseline.read_text(encoding="utf-8")
        if not isinstance(sha, str) or not POLICY_SHA_RE.fullmatch(sha):
            raise ValueError("invalid policy SHA")
        return (self.versions_dir / f"{sha}.py").read_text(encoding="utf-8")

    def add_version(self, source: str, meta: dict) -> str:
        sha = policy_sha(source)
        target = self.versions_dir / f"{sha}.py"
        if not target.exists():
            _atomic_write(target, source)
        _atomic_write(self.versions_dir / f"{sha}.json",
                      json.dumps({**meta, "sha": sha}, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return sha

    def log(self, entry: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / "history.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": int(time.time()), **entry}, ensure_ascii=False) + "\n")

    def promote(self, source: str, meta: dict) -> dict:
        previous = self.current()
        sha = self.add_version(source, {**meta, "parent": previous["sha"]})
        _atomic_write(self.dir / "current.json", json.dumps(
            {"sha": sha, "parent": previous["sha"], "promoted_at": int(time.time())}) + "\n")
        self.log({"event": "promoted", "sha": sha, "parent": previous["sha"], **{
            k: v for k, v in meta.items() if k in ("change", "incumbent_mean", "candidate_mean", "p_value")}})
        return self.current()

    def rollback(self) -> dict:
        """Point ``current`` back at the parent (or the baseline when none)."""
        cur = self.current()
        parent = cur.get("parent")
        if cur["origin"] == "baseline":
            return cur
        if parent and parent != self.baseline_entry()["sha"] and (self.versions_dir / f"{parent}.py").exists():
            _atomic_write(self.dir / "current.json", json.dumps(
                {"sha": parent, "parent": None, "promoted_at": int(time.time())}) + "\n")
        else:
            try:
                (self.dir / "current.json").unlink()
            except OSError:
                pass
        self.log({"event": "rollback", "from": cur["sha"], "to": self.current()["sha"]})
        return self.current()

    def recent_attempts(self, n: int = 5) -> list[dict]:
        try:
            lines = (self.dir / "history.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict] = []
        for line in lines[-n:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out
