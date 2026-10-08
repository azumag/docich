"""Eval contracts: case schema, hashing, public-fixture safety (#1308 PR-1)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.eval import contracts  # noqa: E402


def make_case(**overrides):
    case = {
        "case_id": "c-1",
        "group_id": "thread-1",
        "input": {"comment": "そこに置くんじゃない？"},
        "expected": {"intent_family": "game", "category": "strategy_advice",
                     "screen_need": "required"},
        "tags": ["deictic"],
    }
    case.update(overrides)
    return case


def test_validate_case_defaults_and_normalisation():
    case = contracts.validate_case(make_case())
    assert case["schema"] == contracts.CASE_SCHEMA
    assert case["input"]["host_mode"] == "main"
    assert case["input"]["image_attached"] is False
    assert case["expected"]["must_use_game_context"] is False
    assert case["expected"]["response_must_include"] == []
    assert case["expected"]["must_not_claim_screen_seen_without_image"] is False
    # NFKC + whitespace collapse makes the body canonical.
    assert case["input"]["comment"] == "そこに置くんじゃない?"


@pytest.mark.parametrize("bad", [
    {"case_id": ""},
    {"group_id": "has space"},
    {"input": {"comment": "   "}},
    {"input": {"comment": "x" * (contracts.MAX_COMMENT_CHARS + 1)}},
    {"input": {"comment": "ok", "host_mode": "unknown"}},
    {"expected": {"intent_family": "not-a-family"}},
    {"expected": {"screen_need": "maybe"}},
    {"expected": {"category": "has space"}},
])
def test_validate_case_rejects_bad_input(bad):
    with pytest.raises(contracts.ContractError):
        contracts.validate_case(make_case(**bad))


def test_canonical_and_digest_are_stable_across_key_order():
    a = {"b": 1, "a": [2, 3]}
    b = {"a": [2, 3], "b": 1}
    assert contracts.canonical(a) == contracts.canonical(b)
    assert contracts.digest(a) == contracts.digest(b)
    assert contracts.digest(a).startswith("sha256:")


def test_case_fingerprint_is_group_stable():
    one = contracts.validate_case(make_case(case_id="c-1"))
    two = contracts.validate_case(make_case(case_id="c-2"))
    assert contracts.case_fingerprint(one) == contracts.case_fingerprint(two)
    three = contracts.validate_case(make_case(case_id="c-3", group_id="thread-2"))
    assert contracts.case_fingerprint(one) != contracts.case_fingerprint(three)


@pytest.mark.parametrize("body,pattern", [
    ("token: abcdef123456", "assigned_secret"),
    ("Authorization: Bearer abcdefghijklmnopqrst", "bearer_token"),
    ("/home/ubuntu/soren/handoff.md", "private_path"),
    ("10.1.2.3 に接続", "private_ip"),
])
def test_looks_private_flags_production_shapes(body, pattern):
    assert pattern in contracts.looks_private(body)


def test_assert_public_safe_rejects_private_fixture():
    case = contracts.validate_case(make_case(input={"comment": "私のtoken: abcdefghijkl"}))
    with pytest.raises(contracts.ContractError):
        contracts.assert_public_safe(case)


def test_validate_manifest_requires_valid_ratios():
    manifest = {
        "schema": contracts.SUITE_SCHEMA, "suite": "comment-v1",
        "seed": 1, "ratios": {"train": 0.5, "validation": 0.2, "sealed_test": 0.2},
    }
    with pytest.raises(contracts.ContractError):
        contracts.validate_manifest(manifest)
    ok = contracts.validate_manifest({**manifest, "ratios": contracts.DEFAULT_RATIOS})
    assert ok["suite"] == "comment-v1"
    assert ok["grader_versions"]["deterministic"] == "deterministic-v1"
