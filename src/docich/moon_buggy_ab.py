"""Durable live A/B gate for Moon Buggy command-brain weights.

Candidates stay out of the live weights file until one complete ABBA block
has compared them with the incumbent in real matches. The wrapper activates
one immutable snapshot at each game boundary and records that snapshot's hash
with the resulting score.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import time
import uuid

from .game_switch import atomic_write_json

SCHEMA_VERSION = 1
PATTERN = "ABBA"
ARMS = {"A": "baseline", "B": "candidate"}
PENDING_STATUSES = frozenset({"staged", "running"})
TERMINAL_STATUSES = frozenset({"promoted", "kept"})


class MoonBuggyABError(ValueError):
    """Invalid or incomplete Moon Buggy A/B evidence."""


def state_path(state_dir) -> Path:
    return Path(state_dir) / "moon_buggy_ab.json"


def active_path(state_dir) -> Path:
    return Path(state_dir) / "moon_buggy_ab_active.json"


def _lock_path(path: Path) -> Path:
    return path.parent / "locks" / "moon-buggy-ab.lock"


@contextmanager
def _locked(path: Path):
    lock_path = _lock_path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _validated_weights(value) -> dict:
    if not isinstance(value, dict) or set(value) != {"laser_period"}:
        raise MoonBuggyABError("invalid Moon Buggy A/B weights")
    period = value["laser_period"]
    if type(period) is int:
        valid = 2 <= period <= 100
    elif type(period) is float:
        valid = math.isfinite(period) and 2 <= period <= 100
    else:
        valid = False
    if not valid:
        raise MoonBuggyABError("invalid Moon Buggy A/B weights")
    return {"laser_period": period}


def weights_sha256(value) -> str:
    weights = _validated_weights(value)
    raw = json.dumps(weights, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                raise MoonBuggyABError("Moon Buggy A/B state is invalid")
            raw = stream.read(65537)
            if len(raw) > 65536:
                raise MoonBuggyABError("Moon Buggy A/B state is invalid")
        value = json.loads(raw)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RecursionError) as exc:
        raise MoonBuggyABError("Moon Buggy A/B state is unreadable") from exc
    if not isinstance(value, dict):
        raise MoonBuggyABError("Moon Buggy A/B state is invalid")
    return value


def _validate_experiment(value: dict) -> dict:
    if type(value.get("schema_version")) is not int or value.get("schema_version") != SCHEMA_VERSION:
        raise MoonBuggyABError("Moon Buggy A/B state is invalid")
    status = value.get("status")
    if (not isinstance(status, str)
            or status not in PENDING_STATUSES | TERMINAL_STATUSES | {"completed"}):
        raise MoonBuggyABError("Moon Buggy A/B state is invalid")
    if value.get("pattern") != PATTERN:
        raise MoonBuggyABError("Moon Buggy A/B state is invalid")
    exp_id = value.get("experiment_id")
    if not isinstance(exp_id, str) or not exp_id:
        raise MoonBuggyABError("Moon Buggy A/B state is invalid")
    results = value.get("results")
    if not isinstance(results, list) or len(results) > len(PATTERN):
        raise MoonBuggyABError("Moon Buggy A/B state is invalid")
    if status in PENDING_STATUSES | {"completed"}:
        baseline = _validated_weights(value.get("baseline"))
        candidate = _validated_weights(value.get("candidate"))
        if (weights_sha256(baseline) != value.get("baseline_sha256")
                or weights_sha256(candidate) != value.get("candidate_sha256")):
            raise MoonBuggyABError("Moon Buggy A/B state is invalid")
    else:
        baseline = candidate = None
    if status == "completed" and len(results) != len(PATTERN):
        raise MoonBuggyABError("Moon Buggy A/B state is invalid")
    if ((status == "staged" and results)
            or (status == "running" and len(results) >= len(PATTERN))):
        raise MoonBuggyABError("Moon Buggy A/B state is invalid")

    for index, item in enumerate(results):
        if not isinstance(item, dict):
            raise MoonBuggyABError("Moon Buggy A/B state is invalid")
        arm = PATTERN[index]
        expected_hash = value.get(f"{ARMS[arm]}_sha256")
        if (type(item.get("index")) is not int or item.get("index") != index
                or item.get("arm") != arm
                or item.get("weights_sha256") != expected_hash
                or item.get("match_id") != f"{exp_id}:{index}"
                or type(item.get("score")) is not int or item["score"] < 0):
            raise MoonBuggyABError("Moon Buggy A/B state is invalid")

    if status == "completed":
        scores = {arm: [r["score"] for r in results if r["arm"] == arm] for arm in "AB"}
        means = {arm: sum(scores[arm]) / len(scores[arm]) for arm in "AB"}
        winner = "B" if means["B"] > means["A"] else "A"
        if (value.get("means") != means or value.get("winner") != winner):
            raise MoonBuggyABError("Moon Buggy A/B state is invalid")
    return value


def read_experiment(state_dir) -> dict | None:
    path = state_path(state_dir)
    value = _read_json(path)
    return _validate_experiment(value) if value is not None else None


def pending_matches(state_dir) -> int | None:
    experiment = read_experiment(state_dir)
    if not experiment or experiment["status"] not in PENDING_STATUSES:
        return None
    return len(PATTERN) - len(experiment["results"])


def stage(state_dir, baseline, candidate, *, source_date: str,
          headless_baseline_mean: float, headless_candidate_mean: float) -> dict:
    """Persist a candidate without changing live weights."""
    path = state_path(state_dir)
    base = _validated_weights(baseline)
    proposed = _validated_weights(candidate)
    base_hash, candidate_hash = weights_sha256(base), weights_sha256(proposed)
    if (not math.isfinite(float(headless_baseline_mean))
            or not math.isfinite(float(headless_candidate_mean))):
        raise MoonBuggyABError("invalid Moon Buggy A/B evaluation")
    if base_hash == candidate_hash:
        raise MoonBuggyABError("identical Moon Buggy A/B candidates")
    with _locked(path):
        previous = _read_json(path)
        if previous is not None:
            previous = _validate_experiment(previous)
            if previous["status"] not in TERMINAL_STATUSES:
                raise MoonBuggyABError("Moon Buggy A/B experiment already pending")
        now = time.time()
        experiment = {
            "schema_version": SCHEMA_VERSION,
            "status": "staged",
            "experiment_id": str(uuid.uuid4()),
            "created_at": now,
            "source_date": str(source_date)[:10],
            "pattern": PATTERN,
            "baseline": base,
            "baseline_sha256": base_hash,
            "candidate": proposed,
            "candidate_sha256": candidate_hash,
            "headless_baseline_mean": float(headless_baseline_mean),
            "headless_candidate_mean": float(headless_candidate_mean),
            "results": [],
        }
        atomic_write_json(path, experiment)
        return experiment


def select_arm(state_dir, request_id: str | None = None) -> dict:
    """Pin the next ABBA arm to the active snapshot consumed by the brain."""
    canonical_id = None
    if request_id is not None:
        try:
            canonical_id = str(uuid.UUID(request_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise MoonBuggyABError("invalid Moon Buggy A/B request") from exc
        if canonical_id != request_id:
            raise MoonBuggyABError("invalid Moon Buggy A/B request")
    path = state_path(state_dir)
    active = active_path(state_dir)
    with _locked(path):
        experiment = _read_json(path)
        if experiment is None:
            raise MoonBuggyABError("Moon Buggy A/B experiment is missing")
        experiment = _validate_experiment(experiment)
        if experiment["status"] not in PENDING_STATUSES:
            raise MoonBuggyABError("Moon Buggy A/B experiment is not running")
        index = len(experiment["results"])
        if index >= len(PATTERN):
            raise MoonBuggyABError("Moon Buggy A/B experiment is complete")
        arm = PATTERN[index]
        weights = experiment["baseline"] if arm == "A" else experiment["candidate"]
        selected = {
            "schema_version": SCHEMA_VERSION,
            "experiment_id": experiment["experiment_id"],
            "match_id": f"{experiment['experiment_id']}:{index}",
            "index": index,
            "arm": arm,
            "weights_sha256": experiment[f"{ARMS[arm]}_sha256"],
            "weights": weights,
        }
        # Publish the immutable weights before exposing status=running to the
        # agent. A brain can never observe a newly running experiment with the
        # previous experiment's active snapshot.
        atomic_write_json(active, selected)
        if experiment["status"] == "staged":
            experiment["status"] = "running"
            experiment["started_at"] = time.time()
        if canonical_id is not None:
            experiment["rotation_request_id"] = canonical_id
        atomic_write_json(path, experiment)
        return selected


def _scorelog_has_match(path: Path, selected: dict, score: int) -> bool:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return False
    except (OSError, UnicodeError) as exc:
        raise MoonBuggyABError("Moon Buggy A/B score log is unavailable") from exc
    for line in lines:
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(row, dict) and row.get("ab_match_id") == selected["match_id"]:
            expected = (row.get("score") == score
                        and row.get("ab_experiment_id") == selected["experiment_id"]
                        and row.get("ab_arm") == selected["arm"]
                        and row.get("weights_sha256") == selected["weights_sha256"])
            if not expected:
                raise MoonBuggyABError("Moon Buggy A/B score log conflicts")
            return True
    return False


def _ensure_scorelog_match(path: Path, selected: dict, score: int) -> None:
    if _scorelog_has_match(path, selected, score):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": int(time.time()),
        "game": "moon-buggy",
        "score": score,
        "source": "wrapper",
        "ab_experiment_id": selected["experiment_id"],
        "ab_match_id": selected["match_id"],
        "ab_arm": selected["arm"],
        "weights_sha256": selected["weights_sha256"],
    }
    try:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise MoonBuggyABError("Moon Buggy A/B score log is unavailable") from exc


def record_score(state_dir, scorelog, score: int) -> dict:
    """Durably associate one completed match score with its pinned arm."""
    if type(score) is not int or score < 0:
        raise MoonBuggyABError("invalid Moon Buggy A/B score")
    path = state_path(state_dir)
    active = active_path(state_dir)
    log = Path(scorelog)
    with _locked(path):
        selected = _read_json(active)
        experiment = _read_json(path)
        if experiment is None:
            raise MoonBuggyABError("Moon Buggy A/B experiment is missing")
        experiment = _validate_experiment(experiment)
        if selected is None:
            # Recover a retry if the process died after the durable result and
            # scorelog append but just after unlinking the active snapshot.
            if not experiment["results"] or experiment["results"][-1].get("score") != score:
                raise MoonBuggyABError("Moon Buggy A/B active arm is missing")
            last = experiment["results"][-1]
            selected = {
                "experiment_id": experiment["experiment_id"],
                "match_id": last["match_id"],
                "arm": last["arm"],
                "weights_sha256": last["weights_sha256"],
            }
            _ensure_scorelog_match(log, selected, score)
            return experiment
        if (type(selected.get("schema_version")) is not int
                or selected.get("schema_version") != SCHEMA_VERSION
                or not isinstance(selected.get("arm"), str)
                or selected.get("arm") not in ARMS
                or not isinstance(selected.get("experiment_id"), str)
                or type(selected.get("index")) is not int
                or not 0 <= selected["index"] < len(PATTERN)
                or selected.get("match_id") != f"{selected.get('experiment_id')}:{selected.get('index')}"
                or selected.get("weights_sha256") != weights_sha256(selected.get("weights"))):
            raise MoonBuggyABError("Moon Buggy A/B active arm is invalid")
        if experiment["experiment_id"] != selected["experiment_id"]:
            raise MoonBuggyABError("Moon Buggy A/B active experiment changed")

        index = selected["index"]
        expected_arm = PATTERN[index]
        if (selected["arm"] != expected_arm
                or selected["weights_sha256"]
                != experiment[f"{ARMS[expected_arm]}_sha256"]):
            raise MoonBuggyABError("Moon Buggy A/B active arm is invalid")
        results = list(experiment["results"])
        if index < len(results):
            result = results[index]
            if result.get("match_id") != selected["match_id"] or result.get("score") != score:
                raise MoonBuggyABError("Moon Buggy A/B match conflicts")
        elif (index == len(results) and experiment["status"] == "running"
              and index < len(PATTERN) and selected["arm"] == PATTERN[index]
              and selected["weights_sha256"] == experiment[f"{ARMS[PATTERN[index]]}_sha256"]):
            result = {
                "index": index,
                "match_id": selected["match_id"],
                "arm": selected["arm"],
                "weights_sha256": selected["weights_sha256"],
                "score": score,
                "ts": time.time(),
            }
            results.append(result)
            experiment["results"] = results
            if len(results) == len(PATTERN):
                scores = {arm: [r["score"] for r in results if r["arm"] == arm] for arm in "AB"}
                means = {arm: sum(scores[arm]) / len(scores[arm]) for arm in "AB"}
                experiment.update(
                    status="completed",
                    means=means,
                    winner="B" if means["B"] > means["A"] else "A",
                    completed_at=time.time(),
                )
            atomic_write_json(path, experiment)
        else:
            raise MoonBuggyABError("Moon Buggy A/B match order is invalid")

        _ensure_scorelog_match(log, selected, score)

        latest = _read_json(active)
        if latest and latest.get("match_id") == selected["match_id"]:
            try:
                active.unlink()
            except FileNotFoundError:
                pass
        return experiment


def finish(state_dir, *, status: str) -> dict:
    """Resolve a completed experiment after its winner was handled."""
    if status not in TERMINAL_STATUSES:
        raise MoonBuggyABError("invalid Moon Buggy A/B result")
    path = state_path(state_dir)
    with _locked(path):
        experiment = _read_json(path)
        if experiment is None:
            raise MoonBuggyABError("Moon Buggy A/B experiment is missing")
        experiment = _validate_experiment(experiment)
        if experiment["status"] == status:
            return experiment
        if experiment["status"] != "completed":
            raise MoonBuggyABError("Moon Buggy A/B experiment is not complete")
        experiment["status"] = status
        experiment.pop("baseline", None)
        experiment.pop("candidate", None)
        atomic_write_json(path, experiment)
        return experiment


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    select = subparsers.add_parser("select")
    select.add_argument("--request-id")
    record = subparsers.add_parser("record-score")
    record.add_argument("--score", required=True, type=int)
    record.add_argument("--scorelog", required=True)
    args = parser.parse_args(argv)
    state_file = os.environ.get("DOCICH_MOON_BUGGY_AB_STATE")
    if not state_file:
        print("Moon Buggy A/B state is unavailable", file=sys.stderr)
        return 2
    directory = Path(state_file).parent
    try:
        if args.command == "select":
            active_file = os.environ.get("DOCICH_MOON_BUGGY_AB_ACTIVE")
            if not active_file:
                raise MoonBuggyABError("Moon Buggy A/B active path is unavailable")
            # Runtime paths are siblings of the canonical state path.
            if Path(active_file) != active_path(directory):
                raise MoonBuggyABError("Moon Buggy A/B active path is invalid")
            select_arm(directory, request_id=args.request_id)
            return 0
        record_score(directory, args.scorelog, args.score)
        return 0
    except (MoonBuggyABError, OSError):
        print("Moon Buggy A/B transition failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
