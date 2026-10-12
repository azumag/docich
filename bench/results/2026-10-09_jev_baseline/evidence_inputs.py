"""Strict, offline input identity checks for the saved #1974 benchmark."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

PUBLIC_SHA256 = "d750a4361f7b7acffd46a192b1f7f5e873e8a2692cf45bbae78ce97240a46c37"
CRITICAL_SHA256 = "3dc691d401b1bbb7d2f6cdc23f686ee8c0521bb24b6fa0b250996b0c903a7d3a"
WARMUP_IDS = {"jev-0001", "jev-0002", "jev-0003"}
CRITICAL_IDS = {"jev-0093", "jev-0094"}
RUNS = {1, 2, 3}
NOTIFICATIONS = {"card_gacha", "raid", "subscription", "stream_goal", "bits"}
RESULT_PATH = Path("bench/results/2026-10-09_jev_baseline")
LLAMA_PATH = Path("bench/results/2026-10-09_rtx3060_jev/raw/llama3.1-8b-instruct-q4_K_M.raw.jsonl")


def jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source(path, repo):
    path = Path(path).resolve()
    try:
        ref = path.relative_to(Path(repo).resolve()).as_posix()
    except ValueError:
        ref = "sha256:" + sha256(path)
    return {"path": ref, "sha256": sha256(path)}


def ids_scope(ids):
    ids = list(ids)
    return {"n": len(ids), "case_ids": ids,
            "case_ids_sha256": hashlib.sha256(("\n".join(ids) + "\n").encode()).hexdigest()}


def indexed(rows, expected_ids, name):
    ids = [r.get("case_id") for r in rows]
    if any(not isinstance(i, str) or not i for i in ids):
        raise ValueError(f"{name}: missing case ID")
    if len(set(ids)) != len(ids):
        raise ValueError(f"{name}: duplicate case ID")
    if set(ids) != set(expected_ids) or len(ids) != len(expected_ids):
        raise ValueError(f"{name}: case membership/count mismatch")
    return dict(zip(ids, rows))


def suite(path, expected_hash, expected_n, repo):
    if sha256(path) != expected_hash:
        raise ValueError("suite SHA-256 mismatch")
    rows = jsonl(path)
    if len(rows) != expected_n:
        raise ValueError("suite count mismatch")
    ids = [r.get("case_id") for r in rows]
    indexed(rows, ids, "suite")
    metadata = {**source(path, repo), **ids_scope(ids),
                "label_counts": dict(sorted(Counter(r["expected"]["category"] for r in rows).items())),
                "intent_family_counts": dict(sorted(Counter(r["expected"]["intent_family"] for r in rows).items()))}
    return rows, metadata


def pipeline_input(path, expected_ids):
    rows = jsonl(path)
    if rows and all("case_id" not in r for r in rows):
        # Random batch_id and positional order cannot prove case identity.
        return None, {"state": "unavailable", "events": len(rows),
                      "reason": "Legacy raw has no case IDs; batch/latency mapping was not published. Positional scores are historical and unverified."}
    by_id = indexed(rows, expected_ids, "pipeline")
    for row in rows:
        event = row.get("event", {})
        if event.get("batch_size") != 1 or len(event.get("rows", [])) != 1:
            raise ValueError("pipeline: expected exactly one row per case")
        if not isinstance(row.get("pass"), int) or row["pass"] < 1:
            raise ValueError("pipeline: invalid pass")
    return by_id, {"state": "verified_case_ids", "events": len(rows)}


def load(repo, pipeline_path=None):
    repo = Path(repo)
    result = repo / RESULT_PATH
    cases, public_meta = suite(repo / "bench/jev_eval_v1/public_cases.jsonl", PUBLIC_SHA256, 106, repo)
    critical, critical_meta = suite(repo / "bench/jev_eval_v1/critical_cases.jsonl", CRITICAL_SHA256, 2, repo)
    all_ids = [r["case_id"] for r in cases]
    if {r["case_id"] for r in critical} != CRITICAL_IDS or set(all_ids) & CRITICAL_IDS:
        raise ValueError("critical/public membership mismatch")
    gold = {r["case_id"]: r["expected"]["category"] for r in cases + critical}
    tags = {r["case_id"]: set(r.get("tags", [])) for r in cases + critical}
    labels = set(gold.values())
    ungated_path = result / "ungated_results.jsonl"
    ungated = indexed(jsonl(ungated_path), all_ids, "ungated")
    for cid, row in ungated.items():
        if row.get("expected") != gold[cid]:
            raise ValueError("ungated: stored gold mismatch")
        if row.get("category") is not None and row["category"] not in labels:
            raise ValueError("ungated: unknown prediction label")
    llama_path = repo / LLAMA_PATH
    raw = jsonl(llama_path)
    if {r.get("run") for r in raw} != RUNS:
        raise ValueError("llama: run membership mismatch")
    measured_ids = sorted((set(all_ids) - WARMUP_IDS) | CRITICAL_IDS)
    by_run = {}
    for run in sorted(RUNS):
        rows = [r for r in raw if r["run"] == run]
        indexed(rows, sorted(set(all_ids) | CRITICAL_IDS), f"llama run {run}")
        if {r["case_id"] for r in rows if r.get("warmup") is True} != WARMUP_IDS:
            raise ValueError("llama: warmup membership mismatch")
        if any(not isinstance(r.get("warmup"), bool) for r in rows):
            raise ValueError("llama: invalid warmup flag")
        scored = [r for r in rows if not r["warmup"]]
        by_run[run] = indexed(scored, measured_ids, f"llama scored run {run}")
        for row in rows:
            if row.get("category_expected") != gold[row["case_id"]]:
                raise ValueError("llama: stored gold mismatch")
            if not isinstance(row.get("parse_ok"), bool):
                raise ValueError("llama: invalid parse flag")
            if row["parse_ok"] and row.get("choice") not in labels:
                raise ValueError("llama: unknown/missing parsed label")
    matched = [cid for cid in all_ids if cid not in WARMUP_IDS]
    pipeline_path = Path(pipeline_path) if pipeline_path else result / "pipeline_metrics.jsonl"
    pipeline, pipeline_state = pipeline_input(pipeline_path, all_ids)
    return {"cases": cases, "gold": gold, "tags": tags, "all_ids": all_ids,
            "matched": matched, "measured_ids": measured_ids,
            "ungated": ungated, "by_run": by_run, "pipeline": pipeline,
            "metadata": {"suite": public_meta, "critical_suite": critical_meta,
                         "matched_public": ids_scope(matched),
                         "llama_measured": ids_scope(measured_ids),
                         "sources": {"ungated": source(ungated_path, repo),
                                     "pipeline": source(pipeline_path, repo),
                                     "llama": source(llama_path, repo)},
                         "pipeline_reproduction": pipeline_state}}
