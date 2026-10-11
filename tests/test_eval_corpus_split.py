"""Suite loading, group-aware split and private-corpus projection (#1308 PR-1)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.eval import contracts, corpus, load_suite, split  # noqa: E402

SUITE = ROOT / "evals/comment/v1"


def test_public_suite_loads_and_validates():
    loaded = corpus.load_public_cases(SUITE)
    ids = [case["case_id"] for case in loaded["cases"]]
    assert len(ids) == len(set(ids))
    assert loaded["critical_ids"]
    assert loaded["manifest"]["corpus_digest"].startswith("sha256:")


def test_split_is_group_aware_and_deterministic():
    loaded = corpus.load_public_cases(SUITE)
    first = split.split_cases(loaded["cases"], seed=33, critical_ids=loaded["critical_ids"])
    again = split.split_cases(loaded["cases"], seed=33, critical_ids=loaded["critical_ids"])
    other = split.split_cases(loaded["cases"], seed=34, critical_ids=loaded["critical_ids"])
    split.assert_group_integrity(loaded["cases"], first["assignments"])
    assert first["manifest"]["split_manifest_hash"] == again["manifest"]["split_manifest_hash"]
    # Every group stays inside one split even when several cases share a group.
    by_group = {}
    for case in loaded["cases"]:
        if case["case_id"] in first["assignments"]:
            by_group.setdefault(case["group_id"], set()).add(first["assignments"][case["case_id"]])
    assert all(len(splits) == 1 for splits in by_group.values())
    assert first["manifest"]["counts"] != {} and other["manifest"]["seed"] == 34


def test_sealed_is_a_separate_bucket_from_critical():
    suite = load_suite(SUITE)
    sealed = {case["case_id"] for case in suite["by_split"]["sealed_test"]}
    critical = {case["case_id"] for case in suite["critical"]}
    train_val = {case["case_id"] for case in suite["by_split"]["train"] + suite["by_split"]["validation"]}
    assert sealed and critical and not (sealed & critical) and not (sealed & train_val)
    assert suite["manifest"]["split"]["critical_count"] == len(critical)


def test_bucket_for_group_is_stable_and_rejects_bad_ratios():
    assert split.bucket_for_group("thread-1", seed=33) == split.bucket_for_group("thread-1", seed=33)
    with pytest.raises(contracts.ContractError):
        split.bucket_for_group("thread-1", seed=1, ratios={"train": 1.0, "validation": 0.0})


def test_private_corpus_projection_removes_raw_identity(tmp_path):
    env = {"DOCICH_STATE_DIR": str(tmp_path)}
    directory = tmp_path / "eval/corpora/comment-v1"
    directory.mkdir(parents=True)
    raw = {
        "case_id": "p-1", "group_id": "pthread-1",
        "input": {"comment": "viewer: bob が https://example.com で /home/ubuntu/x を消した",
                  "user": "bob"},
        "expected": {"category": "stream_bug_report", "screen_need": "not_required"},
        "tags": [],
    }
    (directory / "cases.jsonl").write_text(json.dumps(raw) + "\n", encoding="utf-8")
    cases = corpus.load_private_cases("comment-v1", env=env, salt="pepper")
    body = cases[0]["input"]["comment"]
    assert "bob" not in body and "example.com" not in body and "/home/ubuntu" not in body
    assert any(tag.startswith("user_token:tok-") for tag in cases[0]["tags"])
    # Salted token is irreversible and never equal to the raw handle.
    assert corpus.tokenize_user("bob", salt="pepper") != "bob"


def test_private_corpus_requires_state_dir_and_salt(tmp_path):
    with pytest.raises(contracts.ContractError):
        corpus.load_private_cases("comment-v1", env={}, salt="x")
    with pytest.raises(contracts.ContractError):
        corpus.load_private_cases("comment-v1", env={"DOCICH_STATE_DIR": str(tmp_path)}, salt="")


def test_public_corpus_cannot_carry_a_production_secret():
    with pytest.raises(contracts.ContractError):
        contracts.assert_public_safe(
            contracts.validate_case({"case_id": "c-1", "group_id": "t-1",
                                     "input": {"comment": "Authorization: Bearer abcdefghijklmnop"},
                                     "expected": {}, "tags": []}))
