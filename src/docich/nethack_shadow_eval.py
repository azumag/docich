"""Offline evaluator for P4 shadow comparison telemetry.

The evaluator summarizes agreement; it never promotes a structured source or
changes gameplay configuration.  ``eligible_for_review`` means only that the
sample crossed explicitly configured evidence thresholds and can be reviewed by
a human/next-stage PR.

Evidence integrity is global and fail-closed.  A malformed comparison line, an
unknown event status, or an invalid event whose source cannot be attributed may
hide a producer/parser failure from an otherwise healthy source bucket.  Such
events therefore make every source in the report ineligible for review.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError, load_global


@dataclass(frozen=True)
class ReadinessCriteria:
    min_comparable: int = 100
    min_observation_match_rate: float = 0.99
    min_map_cell_match_rate: float = 0.999
    max_stale_rate: float = 0.01

    def __post_init__(self) -> None:
        if type(self.min_comparable) is not int or self.min_comparable < 1:
            raise ValueError("min_comparable must be a positive integer")
        for name, value in (
            ("min_observation_match_rate", self.min_observation_match_rate),
            ("min_map_cell_match_rate", self.min_map_cell_match_rate),
            ("max_stale_rate", self.max_stale_rate),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= float(value) <= 1:
                raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True)
class SourceMetrics:
    source: str
    total_events: int
    comparable: int
    matches: int
    mismatches: int
    stale: int
    invalid: int
    map_cells_compared: int
    map_cells_mismatched: int
    mismatch_fields: tuple[tuple[str, int], ...]
    observation_match_rate: float | None
    map_cell_match_rate: float | None
    stale_rate: float | None
    eligible_for_review: bool
    readiness_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "total_events": self.total_events,
            "comparable": self.comparable,
            "matches": self.matches,
            "mismatches": self.mismatches,
            "stale": self.stale,
            "invalid": self.invalid,
            "map_cells_compared": self.map_cells_compared,
            "map_cells_mismatched": self.map_cells_mismatched,
            "mismatch_fields": {key: value for key, value in self.mismatch_fields},
            "observation_match_rate": self.observation_match_rate,
            "map_cell_match_rate": self.map_cell_match_rate,
            "stale_rate": self.stale_rate,
            "eligible_for_review": self.eligible_for_review,
            "readiness_reasons": list(self.readiness_reasons),
        }


@dataclass(frozen=True)
class ShadowEvaluationReport:
    sources: tuple[SourceMetrics, ...]
    malformed_lines: int
    ignored_events: int
    unattributed_invalid_events: int
    criteria: ReadinessCriteria

    @property
    def evidence_integrity_passed(self) -> bool:
        return (
            self.malformed_lines == 0
            and self.ignored_events == 0
            and self.unattributed_invalid_events == 0
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "malformed_lines": self.malformed_lines,
            "ignored_events": self.ignored_events,
            "unattributed_invalid_events": self.unattributed_invalid_events,
            "evidence_integrity_passed": self.evidence_integrity_passed,
            "criteria": {
                "min_comparable": self.criteria.min_comparable,
                "min_observation_match_rate": self.criteria.min_observation_match_rate,
                "min_map_cell_match_rate": self.criteria.min_map_cell_match_rate,
                "max_stale_rate": self.criteria.max_stale_rate,
            },
            "sources": [source.to_dict() for source in self.sources],
            "policy_effect": "none",
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class _MutableMetrics:
    def __init__(self, source: str) -> None:
        self.source = source
        self.total_events = 0
        self.comparable = 0
        self.matches = 0
        self.mismatches = 0
        self.stale = 0
        self.invalid = 0
        self.map_cells_compared = 0
        self.map_cells_mismatched = 0
        self.mismatch_fields: Counter[str] = Counter()

    def freeze(
        self,
        criteria: ReadinessCriteria,
        *,
        global_integrity_reasons: tuple[str, ...] = (),
    ) -> SourceMetrics:
        observation_rate = self.matches / self.comparable if self.comparable else None
        map_rate = (
            (self.map_cells_compared - self.map_cells_mismatched) / self.map_cells_compared
            if self.map_cells_compared
            else None
        )
        freshness_denominator = self.comparable + self.stale
        stale_rate = self.stale / freshness_denominator if freshness_denominator else None

        reasons: list[str] = []
        if self.comparable < criteria.min_comparable:
            reasons.append(f"comparable {self.comparable} < {criteria.min_comparable}")
        if observation_rate is None or observation_rate < criteria.min_observation_match_rate:
            reasons.append("observation match rate below threshold")
        if map_rate is None or map_rate < criteria.min_map_cell_match_rate:
            reasons.append("map cell match rate below threshold")
        if stale_rate is None or stale_rate > criteria.max_stale_rate:
            reasons.append("stale rate above threshold or unavailable")
        if self.invalid:
            reasons.append("invalid shadow events present")
        reasons.extend(global_integrity_reasons)

        return SourceMetrics(
            source=self.source,
            total_events=self.total_events,
            comparable=self.comparable,
            matches=self.matches,
            mismatches=self.mismatches,
            stale=self.stale,
            invalid=self.invalid,
            map_cells_compared=self.map_cells_compared,
            map_cells_mismatched=self.map_cells_mismatched,
            mismatch_fields=tuple(sorted(self.mismatch_fields.items())),
            observation_match_rate=observation_rate,
            map_cell_match_rate=map_rate,
            stale_rate=stale_rate,
            eligible_for_review=not reasons,
            readiness_reasons=tuple(reasons),
        )


def evaluate_jsonl_lines(
    lines: list[str] | tuple[str, ...],
    *,
    criteria: ReadinessCriteria | None = None,
) -> ShadowEvaluationReport:
    criteria = criteria or ReadinessCriteria()
    metrics: dict[str, _MutableMetrics] = {}
    malformed = 0
    ignored = 0
    unattributed_invalid = 0

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if len(line.encode("utf-8")) > 1_048_576:
            malformed += 1
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if not isinstance(event, dict) or event.get("schema_version") != 1:
            malformed += 1
            continue

        status = event.get("status")
        if status not in {"match", "mismatch", "stale", "invalid"}:
            ignored += 1
            continue
        source_raw = event.get("source")
        source = source_raw.strip() if isinstance(source_raw, str) else ""
        if not source or source == "<unknown>":
            # Invalid evidence with no stable source attribution is a global
            # integrity failure: assigning it to a harmless pseudo-source would
            # let real source buckets remain eligible despite missing failures.
            if status == "invalid":
                unattributed_invalid += 1
            else:
                malformed += 1
            continue
        bucket = metrics.setdefault(source, _MutableMetrics(source))
        bucket.total_events += 1

        if status in {"match", "mismatch"}:
            bucket.comparable += 1
            if status == "match":
                bucket.matches += 1
            else:
                bucket.mismatches += 1
            compared = event.get("map_cells_compared", 0)
            mismatched = event.get("map_cells_mismatched", 0)
            if type(compared) is int and compared >= 0:
                bucket.map_cells_compared += compared
                if type(mismatched) is int and 0 <= mismatched <= compared:
                    bucket.map_cells_mismatched += mismatched
            fields = event.get("mismatches", [])
            if isinstance(fields, list):
                for field in fields:
                    if isinstance(field, str) and field and len(field) <= 120:
                        bucket.mismatch_fields[field] += 1
        elif status == "stale":
            bucket.stale += 1
        else:
            bucket.invalid += 1

    integrity_reasons: list[str] = []
    if malformed:
        integrity_reasons.append("malformed or unattributed comparison evidence present")
    if ignored:
        integrity_reasons.append("unknown comparison event status present")
    if unattributed_invalid:
        integrity_reasons.append("unattributed producer/parser failure evidence present")
    global_reasons = tuple(integrity_reasons)

    sources = tuple(
        metrics[name].freeze(criteria, global_integrity_reasons=global_reasons)
        for name in sorted(metrics)
    )
    return ShadowEvaluationReport(
        sources=sources,
        malformed_lines=malformed,
        ignored_events=ignored,
        unattributed_invalid_events=unattributed_invalid,
        criteria=criteria,
    )


def evaluate_jsonl_file(
    path: Path,
    *,
    criteria: ReadinessCriteria | None = None,
    max_bytes: int = 64 * 1024 * 1024,
) -> ShadowEvaluationReport:
    path = Path(path)
    if type(max_bytes) is not int or not 1024 <= max_bytes <= 512 * 1024 * 1024:
        raise ValueError("max_bytes is out of range")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValueError(f"cannot read shadow comparison log: {exc}") from exc
    if size > max_bytes:
        raise ValueError("shadow comparison log exceeds size limit")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read shadow comparison log: {exc}") from exc
    return evaluate_jsonl_lines(lines, criteria=criteria)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich-nethack-shadow-evaluate")
    parser.add_argument("--config", metavar="PATH")
    parser.add_argument("--input", metavar="PATH")
    parser.add_argument("--min-comparable", type=int, default=100)
    parser.add_argument("--min-observation-match-rate", type=float, default=0.99)
    parser.add_argument("--min-map-cell-match-rate", type=float, default=0.999)
    parser.add_argument("--max-stale-rate", type=float, default=0.01)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        g = load_global(_repo_root(), Path(args.config) if args.config else None)
        path = (
            Path(args.input)
            if args.input
            else Path(g.state_dir) / "nethack" / "shadow" / "comparisons.jsonl"
        )
        criteria = ReadinessCriteria(
            min_comparable=args.min_comparable,
            min_observation_match_rate=args.min_observation_match_rate,
            min_map_cell_match_rate=args.min_map_cell_match_rate,
            max_stale_rate=args.max_stale_rate,
        )
        report = evaluate_jsonl_file(path, criteria=criteria)
        print(report.to_json())
        return 0
    except (ConfigError, ValueError) as exc:
        print(f"docich: エラー: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
