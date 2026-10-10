#!/usr/bin/env python3
"""Rebuild published reports/summary/metadata offline; saved raw is read-only.

    python3 bench/tools/reaggregate_ollama_results.py \
        bench/results/2026-10-09_rtx3060_jev
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import run_ollama_bench as runner


def regenerate(results_dir, suite_dir):
    results_dir = Path(results_dir)
    loaded = runner.jb.load_suite(suite_dir)
    metadata = json.loads((results_dir / "metadata.json").read_text(encoding="utf-8"))
    digest = loaded["manifest"]["corpus_digest"]
    if metadata["suite_digest"] != digest:
        raise ValueError("saved results do not match the supplied suite digest")
    suite_reference = runner.public_suite_reference(suite_dir, digest)
    rebuilt = []
    for raw_path in sorted((results_dir / "raw").glob("*.raw.jsonl")):
        name = raw_path.name.removesuffix(".raw.jsonl")
        report_path = results_dir / (name + ".report.json")
        previous = json.loads(report_path.read_text(encoding="utf-8"))
        records = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()
                   if line.strip()]
        entry = runner.rebuild_entry(records, loaded["cases"], previous, suite_dir)
        if runner.jb.slug(entry["model"]) != name:
            raise ValueError("saved model does not match raw filename")
        report = runner.public_report(entry["pooled"], suite_reference=suite_reference,
                                      run_reference=f"results:{runner.jb.slug(results_dir.name)}/{name}")
        rebuilt.append((report_path, report, runner.aggregate_row(entry)))
    if not rebuilt or {r[2]["model"] for r in rebuilt} != set(metadata["models"]):
        raise ValueError("raw files must cover exactly the saved models")
    projected = runner.public_metadata(metadata, suite_reference=suite_reference)
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
