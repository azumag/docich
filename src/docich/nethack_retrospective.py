"""Deterministic NetHack post-run retrospective and candidate lesson memory (P5a).

The terminal run/xlog record remains the source of truth.  This module adds
postmortem evidence (recent advisory decisions, repeated death signatures, and
bounded dumplog tail evidence) without changing gameplay policy or actions.
Candidate lessons are persisted for later review; they are never promoted to
policy automatically in P5a.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import sys
import uuid
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import ConfigError, GlobalConfig, load_global
from .game_switch import atomic_write_json
from .nethack_run import (
    SCHEMA_VERSION as RUN_SCHEMA_VERSION,
    TERMINAL_STATUSES,
    NethackRunError,
    load_nethack_persistence_settings,
)
from .nethack_strategist import safe_dispatch_error_kind

RETROSPECTIVE_SCHEMA_VERSION = 1
LESSON_MEMORY_SCHEMA_VERSION = 1
SOURCE_NAME = "p5a_retrospective"
_MAX_ADVISORY_BYTES = 32 * 1024 * 1024
_MAX_DUMP_TAIL_BYTES = 32 * 1024
_MAX_DUMP_SIZE = 128 * 1024 * 1024
_MAX_RECENT_ADVISORY = 20


class NethackRetrospectiveError(RuntimeError):
    """Retrospective evidence cannot be read or committed safely."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise NethackRetrospectiveError(f"retrospective inputを読めません: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise NethackRetrospectiveError(f"run JSONが壊れています: {path.name}") from exc
    if not isinstance(payload, dict):
        raise NethackRetrospectiveError(f"run JSON rootが不正です: {path.name}")
    return payload


def _validate_run(payload: dict[str, object], path: Path) -> None:
    if payload.get("schema_version") != RUN_SCHEMA_VERSION:
        raise NethackRetrospectiveError(f"run schemaが不正です: {path.name}")
    run_id = payload.get("run_id")
    if not isinstance(run_id, str):
        raise NethackRetrospectiveError(f"run_idが不正です: {path.name}")
    try:
        canonical = str(uuid.UUID(run_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise NethackRetrospectiveError(f"run_idが不正です: {path.name}") from exc
    if canonical != run_id or path.name != f"{run_id}.json":
        raise NethackRetrospectiveError(f"run_idとfilenameが一致しません: {path.name}")
    expedition = payload.get("expedition")
    if type(expedition) is not int or expedition < 1:
        raise NethackRetrospectiveError(f"expeditionが不正です: {path.name}")


def normalize_death_signature(reason: object) -> str | None:
    """Create a conservative repeat-detection key from the recorded xlog cause."""
    if not isinstance(reason, str) or not reason.strip():
        return None
    text = re.sub(r"\s+", " ", reason.strip().casefold())
    prefixes = (
        ("killed by ", "killed_by"),
        ("poisoned by ", "poisoned_by"),
        ("choked on ", "choked_on"),
        ("drowned in ", "drowned_in"),
        ("burned by ", "burned_by"),
    )
    for prefix, kind in prefixes:
        if text.startswith(prefix):
            subject = text[len(prefix):].strip()
            subject = re.sub(r"^(?:a|an|the)\s+", "", subject)
            return f"{kind}:{subject}"[:240]
    if "starv" in text:
        return "starvation"
    return text[:240]


def _terminal_end_epoch(run: dict[str, object]) -> float | None:
    terminal = run.get("terminal")
    if isinstance(terminal, dict):
        value = terminal.get("endtime")
        if type(value) is int and value >= 0:
            return float(value)
    value = run.get("last_finished_at")
    if isinstance(value, str):
        try:
            return dt.datetime.fromisoformat(value).timestamp()
        except ValueError:
            return None
    return None


def _event_public_summary(event: dict[str, object]) -> dict[str, object]:
    proposal = event.get("proposal")
    evaluation = event.get("evaluation")
    raw_error_kind = event.get("error_kind")
    return {
        "ts": event.get("ts"),
        "intent": event.get("intent"),
        "reason": event.get("reason"),
        "status": event.get("status"),
        "proposal_kind": proposal.get("kind") if isinstance(proposal, dict) else None,
        "evaluation_status": evaluation.get("status") if isinstance(evaluation, dict) else None,
        "evaluation_reason": evaluation.get("reason") if isinstance(evaluation, dict) else None,
        # Do not re-expose raw ``error`` text from pre-fix advisory logs.
        "error_kind": (
            safe_dispatch_error_kind(raw_error_kind)
            if raw_error_kind is not None
            else None
        ),
        "narrated": event.get("narrated") is True,
    }


def _advisory_evidence(root: Path, run: dict[str, object]) -> dict[str, object]:
    path = root / "strategist" / "advisory.jsonl"
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return {"status": "missing", "event_count": 0, "recent": []}
    except OSError as exc:
        return {"status": "error", "event_count": 0, "recent": [], "error": str(exc)[:200]}
    if size > _MAX_ADVISORY_BYTES:
        return {"status": "too_large", "event_count": 0, "recent": []}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return {"status": "error", "event_count": 0, "recent": [], "error": str(exc)[:200]}

    start = run.get("started_epoch")
    start_epoch = float(start) if type(start) is int and start >= 0 else None
    end_epoch = _terminal_end_epoch(run)
    events: list[dict[str, object]] = []
    malformed = 0
    for line in lines:
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            malformed += 1
            continue
        ts = raw.get("ts")
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            malformed += 1
            continue
        value = float(ts)
        if start_epoch is not None and value < start_epoch - 5.0:
            continue
        if end_epoch is not None and value > end_epoch + 5.0:
            continue
        events.append(_event_public_summary(raw))

    recent = events[-_MAX_RECENT_ADVISORY:]
    intents = Counter(str(item.get("intent")) for item in events if item.get("intent"))
    proposals = Counter(str(item.get("proposal_kind")) for item in events if item.get("proposal_kind"))
    rejected = sum(1 for item in events if item.get("evaluation_status") == "rejected")
    return {
        "status": "ok",
        "event_count": len(events),
        "malformed_lines": malformed,
        "intent_counts": dict(sorted(intents.items())),
        "proposal_counts": dict(sorted(proposals.items())),
        "rejected_evaluations": rejected,
        "recent": recent,
    }


def _dump_evidence(dump_dir: Path | None, run: dict[str, object]) -> dict[str, object]:
    name = run.get("dump_file")
    if not isinstance(name, str) or not name:
        return {"status": "not_recorded"}
    if Path(name).name != name:
        return {"status": "invalid_name", "file": name[:200]}
    if dump_dir is None:
        return {"status": "unavailable", "file": name}
    path = dump_dir / name
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return {"status": "missing", "file": name}
    except OSError as exc:
        return {"status": "error", "file": name, "error": str(exc)[:200]}
    if size > _MAX_DUMP_SIZE:
        return {"status": "too_large", "file": name, "size_bytes": size}
    try:
        with path.open("rb") as stream:
            stream.seek(max(0, size - _MAX_DUMP_TAIL_BYTES))
            tail = stream.read(_MAX_DUMP_TAIL_BYTES)
    except OSError as exc:
        return {"status": "error", "file": name, "error": str(exc)[:200]}
    decoded = tail.decode("utf-8", errors="replace")
    nonempty = [line.strip()[:240] for line in decoded.splitlines() if line.strip()]
    excerpt = nonempty[-12:]
    return {
        "status": "ok",
        "file": name,
        "size_bytes": size,
        "tail_sha256": hashlib.sha256(tail).hexdigest(),
        "tail_excerpt": excerpt,
    }


def _lesson(
    *,
    key_material: str,
    category: str,
    text: str,
    expedition: int,
    evidence: dict[str, object],
) -> dict[str, object]:
    digest = hashlib.sha256(f"{category}\0{key_material}".encode("utf-8")).hexdigest()[:20]
    return {
        "lesson_key": digest,
        "source": SOURCE_NAME,
        "status": "candidate",
        "category": category,
        "text": text[:500],
        "expedition": expedition,
        "evidence": evidence,
        "policy_effect": "none",
    }


def _candidate_lessons(
    run: dict[str, object],
    *,
    signature: str | None,
    prior_same: list[dict[str, object]],
    advisory: dict[str, object],
) -> list[dict[str, object]]:
    expedition = int(run["expedition"])
    status = run.get("status")
    lessons: list[dict[str, object]] = []

    if status == "ended_unknown":
        lessons.append(
            _lesson(
                key_material="terminal_evidence_missing",
                category="terminal_evidence",
                text="終了理由をxlogfileから確定できていない。死因を推測して戦略へ反映せず、まずterminal evidenceの欠落を確認する。",
                expedition=expedition,
                evidence={"terminal": run.get("terminal")},
            )
        )
        return lessons

    if status != "dead":
        return lessons

    if signature is not None and prior_same:
        total = len(prior_same) + 1
        previous = [item.get("expedition") for item in prior_same[-8:]]
        lessons.append(
            _lesson(
                key_material=f"repeat:{signature}",
                category="repeated_death",
                text=(
                    f"同じ死因signature「{signature}」が今回を含めて{total}回記録されている。"
                    "次回はこの死因に至る局面を独立した回帰ケースとして扱う。"
                ),
                expedition=expedition,
                evidence={"death_signature": signature, "prior_expeditions": previous, "total": total},
            )
        )

    recent = advisory.get("recent")
    recent_list = recent if isinstance(recent, list) else []
    intents = [item.get("intent") for item in recent_list if isinstance(item, dict)]
    if "survival_emergency" in intents:
        lessons.append(
            _lesson(
                key_material=f"survival:{signature or 'unknown'}",
                category="survival_signal",
                text=(
                    "死亡前のadvisory履歴に survival_emergency が記録されている。"
                    "危険検知後の回復・退避手段を実行可能にする前に、この局面を回帰テストへ追加する。"
                ),
                expedition=expedition,
                evidence={"death_signature": signature, "recent_intents": intents[-8:]},
            )
        )

    if signature == "starvation" and "food_emergency" in intents:
        lessons.append(
            _lesson(
                key_material="food_emergency_to_starvation",
                category="food_survival",
                text=(
                    "food_emergency を検知した遠征が飢餓系の死因で終了している。"
                    "食料判断を実行へ昇格する際の必須回帰ケースとして保持する。"
                ),
                expedition=expedition,
                evidence={"death_signature": signature, "recent_intents": intents[-8:]},
            )
        )

    rejected = advisory.get("rejected_evaluations")
    if type(rejected) is int and rejected > 0:
        lessons.append(
            _lesson(
                key_material=f"rejected:{signature or 'unknown'}",
                category="proposal_drift",
                text=(
                    f"この遠征ではstrategist proposalのfresh-state再検証rejectが{rejected}件あった。"
                    "action surfaceを広げる前に、state drift・遅延・proposal品質を確認する。"
                ),
                expedition=expedition,
                evidence={"rejected_evaluations": rejected, "death_signature": signature},
            )
        )

    event_count = advisory.get("event_count")
    if event_count == 0:
        lessons.append(
            _lesson(
                key_material=f"no_advisory:{signature or 'unknown'}",
                category="evidence_gap",
                text=(
                    "死亡run内にstrategist advisory履歴がない。"
                    "戦略上の原因を推定する前に、観測・advisory・ログのcoverageを確認する。"
                ),
                expedition=expedition,
                evidence={"advisory_status": advisory.get("status"), "death_signature": signature},
            )
        )
    return lessons


class NethackRetrospectiveEngine:
    """Lock-compatible retrospective writer over durable NetHack run files."""

    def __init__(self, g: GlobalConfig):
        self.g = g
        self.root = Path(g.state_dir) / "nethack"
        self.runs_dir = self.root / "runs"
        self.lock_path = self.root / ".lock"
        self.lessons_path = self.root / "lessons.json"
        settings = load_nethack_persistence_settings(g)
        self.dump_dir = settings.dump_dir if settings is not None else None

    def _ensure_dirs(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.runs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.runs_dir, 0o700)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._ensure_dirs()
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            os.chmod(self.lock_path, 0o600)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _runs_unlocked(self) -> list[dict[str, object]]:
        runs: list[dict[str, object]] = []
        try:
            paths = tuple(self.runs_dir.glob("*.json"))
        except OSError as exc:
            raise NethackRetrospectiveError("run history一覧を取得できません") from exc
        for path in paths:
            payload = _read_json(path)
            _validate_run(payload, path)
            runs.append(payload)
        runs.sort(key=lambda item: int(item["expedition"]))
        return runs

    def _select_run_unlocked(self, runs: list[dict[str, object]], run_id: str | None) -> dict[str, object]:
        candidates = [item for item in runs if item.get("status") in TERMINAL_STATUSES]
        if run_id is None:
            if not candidates:
                raise NethackRetrospectiveError("terminal NetHack runがありません")
            return dict(candidates[-1])
        try:
            canonical = str(uuid.UUID(run_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise NethackRetrospectiveError("run_idが不正です") from exc
        if canonical != run_id:
            raise NethackRetrospectiveError("run_idが標準UUID形式ではありません")
        for item in candidates:
            if item.get("run_id") == run_id:
                return dict(item)
        raise NethackRetrospectiveError("指定run_idのterminal runがありません")

    def _write_run_unlocked(self, run: dict[str, object]) -> None:
        run_id = run.get("run_id")
        if not isinstance(run_id, str):
            raise NethackRetrospectiveError("run_idが不正です")
        atomic_write_json(self.runs_dir / f"{run_id}.json", run)

    def _rebuild_lessons_unlocked(self, runs: list[dict[str, object]], *, now: dt.datetime) -> dict[str, object]:
        buckets: dict[str, dict[str, object]] = {}
        for run in runs:
            lessons = run.get("lessons")
            if not isinstance(lessons, list):
                continue
            expedition = run.get("expedition")
            run_id = run.get("run_id")
            if type(expedition) is not int or not isinstance(run_id, str):
                continue
            for lesson in lessons:
                if not isinstance(lesson, dict) or lesson.get("source") != SOURCE_NAME:
                    continue
                key = lesson.get("lesson_key")
                if not isinstance(key, str) or not key:
                    continue
                bucket = buckets.get(key)
                if bucket is None:
                    bucket = {
                        "lesson_key": key,
                        "source": SOURCE_NAME,
                        "status": "candidate",
                        "category": lesson.get("category"),
                        "text": lesson.get("text"),
                        "evidence_count": 0,
                        "first_expedition": expedition,
                        "last_expedition": expedition,
                        "run_ids": [],
                        "policy_effect": "none",
                    }
                    buckets[key] = bucket
                bucket["evidence_count"] = int(bucket["evidence_count"]) + 1
                bucket["first_expedition"] = min(int(bucket["first_expedition"]), expedition)
                bucket["last_expedition"] = max(int(bucket["last_expedition"]), expedition)
                run_ids = bucket["run_ids"]
                if isinstance(run_ids, list) and run_id not in run_ids:
                    run_ids.append(run_id)
                    del run_ids[:-20]

        payload = {
            "schema_version": LESSON_MEMORY_SCHEMA_VERSION,
            "updated_at": now.isoformat(),
            "lessons": [buckets[key] for key in sorted(buckets)],
            "policy_effect": "none",
        }
        atomic_write_json(self.lessons_path, payload)
        return payload

    def generate(
        self,
        *,
        run_id: str | None = None,
        now: dt.datetime | None = None,
    ) -> dict[str, object]:
        timestamp = now or dt.datetime.now(dt.timezone.utc)
        if timestamp.tzinfo is None:
            raise NethackRetrospectiveError("now must be timezone-aware")
        with self._locked():
            runs = self._runs_unlocked()
            run = self._select_run_unlocked(runs, run_id)
            signature = normalize_death_signature(run.get("death_reason")) if run.get("status") == "dead" else None
            prior_same = [
                item
                for item in runs
                if int(item["expedition"]) < int(run["expedition"])
                and item.get("status") == "dead"
                and normalize_death_signature(item.get("death_reason")) == signature
                and signature is not None
            ]
            advisory = _advisory_evidence(self.root, run)
            dump = _dump_evidence(self.dump_dir, run)
            lessons = _candidate_lessons(
                run,
                signature=signature,
                prior_same=prior_same,
                advisory=advisory,
            )
            fingerprint_payload = {
                "run_id": run.get("run_id"),
                "status": run.get("status"),
                "death_reason": run.get("death_reason"),
                "score": run.get("score"),
                "turns": run.get("turns"),
                "max_depth": run.get("max_depth"),
                "advisory": advisory,
                "dump_tail_sha256": dump.get("tail_sha256"),
            }
            evidence_fingerprint = hashlib.sha256(
                json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            retrospective = {
                "schema_version": RETROSPECTIVE_SCHEMA_VERSION,
                "source": SOURCE_NAME,
                "generated_at": timestamp.isoformat(),
                "run_id": run.get("run_id"),
                "expedition": run.get("expedition"),
                "terminal_status": run.get("status"),
                "death_reason": run.get("death_reason"),
                "death_signature": signature,
                "same_death_prior_count": len(prior_same),
                "same_death_total_count": len(prior_same) + 1 if signature is not None else 0,
                "same_death_prior_expeditions": [item.get("expedition") for item in prior_same[-20:]],
                "score": run.get("score"),
                "turns": run.get("turns"),
                "max_depth": run.get("max_depth"),
                "got_amulet": run.get("got_amulet") is True,
                "advisory_evidence": advisory,
                "dump_evidence": dump,
                "evidence_fingerprint": evidence_fingerprint,
                "candidate_lessons": lessons,
                "policy_effect": "none",
            }

            current_lessons = run.get("lessons")
            if current_lessons is None:
                current_lessons = []
            if not isinstance(current_lessons, list):
                raise NethackRetrospectiveError("run lessons fieldが不正です")
            preserved = [
                item
                for item in current_lessons
                if not (isinstance(item, dict) and item.get("source") == SOURCE_NAME)
            ]
            run["retrospective"] = retrospective
            run["lessons"] = preserved + lessons
            self._write_run_unlocked(run)

            # Replace the in-memory copy before rebuilding aggregate memory.
            updated_runs = [run if item.get("run_id") == run.get("run_id") else item for item in runs]
            memory = self._rebuild_lessons_unlocked(updated_runs, now=timestamp)
            retrospective["lesson_memory_count"] = len(memory["lessons"])
            return retrospective


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich-nethack-retrospective")
    parser.add_argument("--config", metavar="PATH")
    parser.add_argument("--run-id", metavar="UUID")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        g = load_global(_repo_root(), Path(args.config) if args.config else None)
        result = NethackRetrospectiveEngine(g).generate(run_id=args.run_id)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (ConfigError, NethackRunError, NethackRetrospectiveError, ValueError) as exc:
        print(f"docich: エラー: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
