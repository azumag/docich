"""構造的改善ジョブ: 昇格ゲートの統計・LLM出力の抽出・静的/スモーク検査・昇格と据え置き。

実ゲームは使わない: arena_eval と llm を差し替えて、ジョブの分岐 (promoted / kept /
rejected / skipped / dry-run) と fail closed を決定的に検証する。
"""
import json
import math
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import docich.corner_improve as corner_improve  # noqa: E402
from docich.ninvaders import arena, improve  # noqa: E402
from docich.ninvaders.sandbox import policy_sha  # noqa: E402
from docich.ninvaders.store import PolicyStore  # noqa: E402
from test_corner_improve_dispatch import _G, _setup_completed  # noqa: E402

BASE = "def decide(obs, state):\n    return ['Space']\n"
GOOD = "# CHANGE: lead the target\n# GOOD\ndef decide(obs, state):\n    return ['Left', 'Space']\n"


def summary_of(scores, faults=0, ticks=1000):
    matches = [{"end": "title", "score": s, "ticks": ticks // max(len(scores), 1),
                "policy": {"timeouts": faults, "errors": 0}} for s in scores]
    return arena.summarize(matches)


def fake_arena(base_scores, good_scores, calls=None):
    def evaluate(path, matches, parallel=1, max_seconds=1.0):
        if calls is not None:
            calls.append({"path": str(path), "matches": matches, "parallel": parallel})
        scores = good_scores if "GOOD" in Path(path).read_text(encoding="utf-8") else base_scores
        ms = [{"end": "title", "score": s, "ticks": 900, "cause": "invasion",
               "last_frames": ["Level: 01 Score: 0000000\nfake frame"], "policy": {"timeouts": 0, "errors": 0}}
              for s in scores]
        return {"summary": arena.summarize(ms), "matches": ms}
    return evaluate


def make_store(tmp_path):
    baseline = tmp_path / "baseline.py"
    baseline.write_text(BASE, encoding="utf-8")
    return PolicyStore(tmp_path / "pd", baseline)


def fenced(src):
    return f"Here is the new policy.\n```python\n{src}```\n"


# ---------------------------------------------------------------- statistics

def test_perfectly_separated_six_vs_six_has_the_exact_p_one_in_924():
    assert improve.permutation_p_value([1, 2, 3, 4, 5, 6], [10, 11, 12, 13, 14, 15]) == pytest.approx(1 / math.comb(12, 6))


def test_identical_samples_are_never_significant():
    assert improve.permutation_p_value([5, 5, 5, 5], [5, 5, 5, 5]) == 1.0


def test_worse_candidate_gets_a_p_value_near_one():
    assert improve.permutation_p_value([10, 11, 12, 13, 14, 15], [1, 2, 3, 4, 5, 6]) > 0.99


def test_empty_inputs_fail_closed():
    assert improve.permutation_p_value([], [1, 2]) == 1.0


def test_large_samples_use_monte_carlo_and_stay_deterministic():
    a, b = list(range(100, 130)), list(range(130, 160))
    p1, p2 = improve.permutation_p_value(a, b), improve.permutation_p_value(a, b)
    assert p1 == p2 and p1 < 0.01


# ---------------------------------------------------------------- promotion gate

def decide(inc, cand, matches=6, **kw):
    return improve.promotion_decision(summary_of(inc), summary_of(cand), matches=matches, **kw)


def test_clear_improvement_promotes():
    d = decide([5000, 5200, 4800, 5100, 4900, 5000], [7000, 7200, 6800, 7100, 6900, 7000])
    assert d["promote"] and d["p_value"] <= 0.01 and d["reasons"] == []


def test_noise_sized_gain_is_kept_out_even_if_the_mean_is_higher():
    d = decide([3000, 8000, 4000, 7000, 5000, 6000], [3500, 7500, 4500, 6500, 5500, 6600])
    assert not d["promote"] and any("significant" in r or "margin" in r for r in d["reasons"])


def test_significant_but_below_margin_is_kept_out():
    d = decide([5000, 5001, 5002, 5003, 5004, 5005], [5100, 5101, 5102, 5103, 5104, 5105], margin_pct=10.0)
    assert not d["promote"] and any("margin" in r for r in d["reasons"])


def test_worse_candidate_is_never_promoted():
    assert not decide([7000] * 6, [5000] * 6)["promote"]


def test_too_few_completed_matches_fails_closed():
    d = improve.promotion_decision(summary_of([5000, 5000]), summary_of([9000, 9000, 9000, 9000, 9000, 9000]), matches=6)
    assert not d["promote"] and any("too few" in r for r in d["reasons"])


def test_policy_faults_block_promotion():
    d = improve.promotion_decision(summary_of([5000] * 6), summary_of([9000] * 6, faults=50, ticks=600), matches=6)
    assert not d["promote"] and any("faults" in r for r in d["reasons"])


def test_zero_incumbent_needs_a_real_gain_and_significance():
    d = decide([0, 0, 0, 0, 0, 0], [500, 600, 700, 800, 900, 1000])
    assert d["promote"]


# ---------------------------------------------------------------- extraction / prompt

def test_extract_prefers_the_longest_fenced_block_and_reads_the_change_note():
    text = "```python\nx = 1\n```\nand\n```python\n# CHANGE: track alien speed\ndef decide(obs, state):\n    return []\n```"
    src, change = improve.extract_policy(text)
    assert "def decide" in src and change == "track alien speed"


def test_extract_accepts_bare_code_and_untagged_fences():
    assert "def decide" in improve.extract_policy("def decide(obs, state):\n    return []\n")[0]
    assert "def decide" in improve.extract_policy("```\ndef decide(obs, state):\n    return []\n```")[0]


@pytest.mark.parametrize("bad", ["", "no code here", "```python\nx = 1\n```"])
def test_extract_rejects_output_without_decide(bad):
    with pytest.raises(improve.ImproveError):
        improve.extract_policy(bad)


def test_extract_rejects_oversized_output():
    with pytest.raises(improve.ImproveError):
        improve.extract_policy("def decide(o, s):\n    return []\n# " + "x" * improve.MAX_LLM_SOURCE_BYTES)


def test_prompt_carries_facts_source_evaluation_and_history():
    matches = [{"score": 4000, "cause": "invasion", "last_frames": ["Level: 01 Score: 0004000\nlast frame"]}]
    text = improve.build_prompt(
        incumbent_source=BASE, incumbent_summary=summary_of([4000, 6000]), incumbent_matches=matches,
        attempts=[{"event": "kept", "change": "wider dodge", "incumbent_mean": 5000, "candidate_mean": 5100, "p_value": 0.4}],
        live_stats={"n": 3, "mean": 3000.0, "best": 3500})
    for needle in ("bombs are ':'", "Your missile is '!'", "GAME OVER", "def decide(obs, state)", "wider dodge",
                   "mean 5000", "invasion", "last frame", "3 matches, mean 3000", "# CHANGE:"):
        assert needle in text, needle


def test_smoke_test_accepts_a_working_policy_and_rejects_broken_ones(tmp_path):
    ok = tmp_path / "ok.py"; ok.write_text(BASE, encoding="utf-8")
    improve.smoke_test(ok)
    bad = tmp_path / "bad.py"; bad.write_text("def decide(o, s):\n    return 1 / 0\n", encoding="utf-8")
    with pytest.raises(improve.ImproveError):
        improve.smoke_test(bad)


# ---------------------------------------------------------------- the job

def run_job(store, llm, evaluator, **kw):
    return improve.improve_once(store, llm=llm, arena_eval=evaluator, matches=6, **kw)


def test_better_candidate_is_promoted_and_becomes_current(tmp_path):
    store, calls = make_store(tmp_path), []
    seen = {}
    def llm(prompt):
        seen["prompt"] = prompt
        return fenced(GOOD)
    result = run_job(store, llm, fake_arena([5000, 5200, 4800, 5100, 4900, 5000], [7000, 7200, 6800, 7100, 6900, 7000], calls))
    assert result["status"] == "promoted" and result["change"] == "lead the target"
    assert store.current()["sha"] == policy_sha(GOOD) == result["current"]
    assert len(calls) == 2 and all(c["matches"] == 6 for c in calls)  # incumbent, then candidate
    assert "def decide(obs, state)" in seen["prompt"]


def test_equal_candidate_is_kept_out_and_current_is_unchanged(tmp_path):
    store = make_store(tmp_path)
    result = run_job(store, lambda p: fenced(GOOD), fake_arena([5000] * 6, [5000] * 6))
    assert result["status"] == "kept" and store.current()["origin"] == "baseline"
    assert store.recent_attempts(1)[0]["event"] == "kept"


def test_statically_illegal_candidate_never_reaches_the_arena(tmp_path):
    store, calls = make_store(tmp_path), []
    evil = "import os\ndef decide(obs, state):\n    return ['Space']\n"
    result = run_job(store, lambda p: fenced(evil), fake_arena([5000] * 6, [9000] * 6, calls))
    assert result["status"] == "rejected" and "static-gate" in result["reason"]
    assert len(calls) == 1  # only the incumbent was evaluated


def test_candidate_that_crashes_is_rejected_by_the_smoke_test(tmp_path):
    store, calls = make_store(tmp_path), []
    crashy = "# CHANGE: boom\n# GOOD\ndef decide(obs, state):\n    return 1 / 0\n"
    result = run_job(store, lambda p: fenced(crashy), fake_arena([5000] * 6, [9000] * 6, calls))
    assert result["status"] == "rejected" and "スモーク" in result["reason"]
    assert len(calls) == 1 and store.current()["origin"] == "baseline"


def test_llm_returning_the_incumbent_unchanged_is_rejected(tmp_path):
    store = make_store(tmp_path)
    result = run_job(store, lambda p: fenced(BASE), fake_arena([5000] * 6, [9000] * 6))
    assert result["status"] == "rejected" and result["reason"] == "identical-to-incumbent"


def test_llm_output_without_code_is_rejected(tmp_path):
    store = make_store(tmp_path)
    result = run_job(store, lambda p: "I cannot help with that.", fake_arena([5000] * 6, [9000] * 6))
    assert result["status"] == "rejected"


def test_incomplete_incumbent_evaluation_skips_without_calling_the_llm(tmp_path):
    store = make_store(tmp_path)
    def llm(prompt):
        raise AssertionError("must not spend an LLM call on an unmeasurable incumbent")
    result = run_job(store, llm, fake_arena([5000, 5000], [9000] * 6))
    assert result["status"] == "skipped" and result["reason"] == "incumbent-eval-incomplete"


def test_dry_run_calls_neither_the_llm_nor_the_arena(tmp_path):
    store = make_store(tmp_path)
    def boom(*a, **k):
        raise AssertionError("dry run must not evaluate")
    result = improve.improve_once(store, llm=boom, arena_eval=boom, dry_run=True)
    assert result["status"] == "dry-run" and result["prompt_chars"] > 1000


def test_llm_failure_propagates_so_the_caller_can_report_it(tmp_path):
    store = make_store(tmp_path)
    def llm(prompt):
        raise RuntimeError("provider down")
    with pytest.raises(RuntimeError):
        run_job(store, llm, fake_arena([5000] * 6, [9000] * 6))
    assert store.current()["origin"] == "baseline"


# ---------------------------------------------------------------- corner_improve wiring

def _corner(tmp_path):
    state_dir = _setup_completed(tmp_path, "ninvaders", [3000, 3200, 2800])
    return state_dir, _G(state_dir)


def test_run_corner_improve_routes_ninvaders_to_code_improvement(tmp_path, monkeypatch):
    state_dir, g = _corner(tmp_path)
    calls = []
    monkeypatch.setattr(
        improve._arena,
        "evaluate",
        fake_arena([5000, 5200, 4800, 5100, 4900, 5000],
                   [7000, 7200, 6800, 7100, 6900, 7000], calls),
    )
    result = corner_improve.run_corner_improve(
        g, game="ninvaders", date_str="2026-09-10", agents="a", matches=2,
        llm=lambda p: fenced(GOOD))
    assert result["status"] == "promoted" and result["reason_code"] == "policy-promoted"
    assert all(c["matches"] == improve.CORNER_EVAL_MATCHES for c in calls)
    current = json.loads((state_dir / "resolver" / "ninvaders" / "current.json").read_text(encoding="utf-8"))
    assert current["sha"] == policy_sha(GOOD)
    log = (state_dir / "resolver" / "ninvaders" / "history.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(log[-1])["event"] == "promoted"


def test_run_corner_improve_code_route_keeps_the_existing_guards(tmp_path, monkeypatch):
    state_dir, g = _corner(tmp_path)
    assert corner_improve.run_corner_improve(g, game="ninvaders", date_str="2026-01-01", agents="a")["reason"] == "wrong-date"
    dry = corner_improve.run_corner_improve(g, game="ninvaders", date_str="2026-09-10", agents="a", dry_run=True)
    assert dry["status"] == "dry-run" and "incumbent" in dry
