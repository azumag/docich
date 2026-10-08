"""Train-leakage checker for candidate prompt mutations (#1308 section 5)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.eval import contracts, leakage  # noqa: E402


def train_cases():
    return [
        contracts.validate_case({"case_id": "c-secret", "group_id": "thread-zz",
                                 "input": {"comment": "右上の赤いやつは敵のボスなので近づかないほうがいいとおもう"},
                                 "expected": {}, "tags": []}),
    ]


def test_general_rule_text_is_allowed():
    result = leakage.check(
        "質問には現在のゲーム文脈を確認してから答える。画面を見たと断定しない。", train_cases())
    assert result["ok"] and result["violations"] == []


def test_verbatim_case_body_is_flagged():
    result = leakage.check("右上の赤いやつは敵のボスなので近づかないほうがいいとおもう と聞かれたらこう返す。",
                           train_cases())
    assert not result["ok"]
    assert "verbatim_substring" in result["violations"]


def test_case_and_group_id_echo_is_flagged():
    result = leakage.check("rule for c-secret applied", train_cases())
    assert "case_id_echo" in result["violations"]
    result = leakage.check("see thread-zz for detail " + "あ" * 40, train_cases())
    assert "group_id_echo" in result["violations"]


def test_empty_mutation_is_refused():
    assert leakage.check("   ", train_cases())["violations"] == ["empty_mutation"]


def test_longest_common_substring_is_normalised():
    assert leakage.longest_common_substring("ＡＢＣ", "abc") == 3
