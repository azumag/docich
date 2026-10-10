#!/usr/bin/env python3
"""Rebuild published reports/summary/metadata offline; saved raw is read-only.

    python3 bench/tools/reaggregate_ollama_results.py \
        bench/results/2026-10-09_rtx3060_jev
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import run_ollama_bench as runner


def validate_records(records, cases, metadata):
    """The recorded run/case order and gold must match the supplied suite."""
    expected = [(run, case, position < metadata["warmup"])
                for run in range(1, metadata["runs"] + 1)
                for position, case in enumerate(cases)]
    if len(records) != len(expected):
        raise ValueError("raw record count does not match runs and suite")
    for row, (run, case, warmup) in zip(records, expected):
        if (type(row["run"]) is not int or row["run"] != run
                or row["case_id"] != case["case_id"]
                or type(row["warmup"]) is not bool or row["warmup"] != warmup
                or row["category_expected"] != case["expected"]["category"]
                or set(row["tags"]) != set(case["tags"])):
            raise ValueError("raw run/case order, warmup, gold or tags do not match suite")


def regenerate(results_dir, suite_dir):
    results_dir = Path(results_dir)
    loaded = runner.jb.load_suite(suite_dir)
    metadata = json.loads((results_dir / "metadata.json").read_text(encoding="utf-8"))
    digest = loaded["manifest"]["corpus_digest"]
    if metadata["suite_digest"] != digest:
        raise ValueError("saved results do not match the supplied suite digest")
    suite_reference = runner.public_suite_reference(suite_dir, digest)
    rebuilt = []
    hashes = {}
    for raw_path in sorted((results_dir / "raw").glob("*.raw.jsonl")):
        name = raw_path.name.removesuffix(".raw.jsonl")
        report_path = results_dir / (name + ".report.json")
        previous = json.loads(report_path.read_text(encoding="utf-8"))
        records = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()
                   if line.strip()]
        validate_records(records, loaded["cases"], metadata)
        hashes[raw_path.name] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        entry = runner.rebuild_entry(records, loaded["cases"], previous, suite_dir)
        if runner.jb.slug(entry["model"]) != name:
            raise ValueError("saved model does not match raw filename")
        report = runner.public_report(entry["pooled"], suite_reference=suite_reference,
                                      run_reference=f"results:{runner.jb.slug(results_dir.name)}/{name}")
        rebuilt.append((report_path, report, runner.public_numbers(runner.aggregate_row(entry))))
    if not rebuilt or {r[2]["model"] for r in rebuilt} != set(metadata["models"]):
        raise ValueError("raw files must cover exactly the saved models")
    projected = runner.public_metadata(metadata, suite_reference=suite_reference)
    projected["reaggregation"] = {
        "method": "bench/tools/reaggregate_ollama_results.py",
        "inputs": "unchanged published raw; saved per-run VRAM retained by run ID",
        "raw_sha256": hashes,
        "validation": "suite digest; exact run/case count and order; warmup; gold and tags",
        "historical_stream_completion": "unverified: raw does not preserve terminal events",
    }
    # Validate everything before replacing any published artifact.
    for report_path, report, _ in rebuilt:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    rows_by_model = {r[2]["model"]: r[2] for r in rebuilt}
    runner.jb.write_csv(results_dir / "summary.csv", [rows_by_model[m] for m in metadata["models"]],
                        runner.AGG_FIELDS)
    (results_dir / "metadata.json").write_text(
        json.dumps(projected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results_dir", type=Path)
    parser.add_argument("--suite", type=Path, default=runner.REPO_ROOT / "bench" / "jev_eval_v1")
    args = parser.parse_args(argv)
    regenerate(args.results_dir, args.suite)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
