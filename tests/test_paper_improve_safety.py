"""Legacy policy adoption safety: bounded delta, shadow evaluation, rollback (docich#267)."""
import json
from types import SimpleNamespace

import pytest

from docich.trading import paper_improve
from docich.trading.strategies import StrategyPolicy, check_policy_bounds
from docich.trading.strategy_store import (
    POLICY_MAX_REVISIONS,
    adopt_strategy_policy,
    archive_strategy_policy,
    list_policy_revisions,
    load_strategy_policy,
    rollback_strategy_policy,
    save_strategy_policy,
)


def _g(tmp_path):
    return SimpleNamespace(state_dir=tmp_path / "state")


def _trading(tmp_path):
    trading = tmp_path / "state" / "trading"
    trading.mkdir(parents=True, exist_ok=True)
    return trading


def _policy_json(**overrides):
    base = {
        "momentum_lookback": 6,
        "momentum_threshold_bps": "300",
        "mean_reversion_lookback": 10,
        "mean_reversion_z": "-1.5",
        "max_notional_fraction": "0.15",
    }
    base.update(overrides)
    return json.dumps(base)


def _llm_with(text):
    def fake_llm(prompt_text):
        return text

    return fake_llm


def _seed_cache(trading, closes):
    (trading / "market_cache.json").write_text(
        json.dumps({"symbols": {symbol: {"closes": values} for symbol, values in closes.items()}}),
        encoding="utf-8",
    )


def _rising(n, start=100.0, step=0.01):
    values = []
    price = start
    for _ in range(n):
        values.append(price)
        price *= 1.0 + step
    return values


@pytest.fixture()
def live_facts(monkeypatch):
    monkeypatch.setattr(
        paper_improve, "build_facts", lambda target, now=None, policy=None: {"capital_jpy": "1000000"}
    )


class TestBounds:
    def test_defaults_pass(self):
        policy = StrategyPolicy()
        ok, reason = check_policy_bounds(policy, policy)
        assert (ok, reason) == (True, "no-change")

    def test_small_step_passes(self):
        current = StrategyPolicy()
        candidate = StrategyPolicy(
            momentum_lookback=8,
            momentum_threshold_bps="400",
            mean_reversion_lookback=12,
            mean_reversion_z="-2.0",
            max_notional_fraction="0.2",
        )
        assert check_policy_bounds(current, candidate) == (True, "ok")

    def test_momentum_snapshot_infeasible_upper_bound_is_rejected(self):
        current = StrategyPolicy()
        candidate = StrategyPolicy(
            momentum_lookback=24,
            momentum_threshold_bps="300",
            mean_reversion_lookback=10,
            mean_reversion_z="-1.5",
            max_notional_fraction="0.15",
        )
        assert check_policy_bounds(current, candidate) == (False, "range-exceeded:momentum_lookback")

    def test_mean_reversion_snapshot_length_24_remains_allowed(self):
        current = StrategyPolicy(mean_reversion_lookback=12)
        candidate = StrategyPolicy(mean_reversion_lookback=24)
        assert check_policy_bounds(current, candidate) == (True, "ok")

    def test_extreme_lookback_rejected_by_range(self):
        current = StrategyPolicy()
        candidate = StrategyPolicy(
            momentum_lookback=200,
            momentum_threshold_bps="300",
            mean_reversion_lookback=10,
            mean_reversion_z="-1.5",
            max_notional_fraction="0.15",
        )
        assert check_policy_bounds(current, candidate) == (False, "range-exceeded:momentum_lookback")

    def test_all_in_notional_rejected_by_range(self):
        current = StrategyPolicy()
        candidate = StrategyPolicy(
            momentum_lookback=6,
            momentum_threshold_bps="300",
            mean_reversion_lookback=10,
            mean_reversion_z="-1.5",
            max_notional_fraction="1.0",
        )
        assert check_policy_bounds(current, candidate) == (False, "range-exceeded:max_notional_fraction")

    def test_in_range_but_oversized_step_rejected_by_delta(self):
        current = StrategyPolicy()
        candidate = StrategyPolicy(
            momentum_lookback=23,  # in [2, 23] but beyond 6*2
            momentum_threshold_bps="300",
            mean_reversion_lookback=10,
            mean_reversion_z="-1.5",
            max_notional_fraction="0.15",
        )
        assert check_policy_bounds(current, candidate) == (False, "delta-exceeded:momentum_lookback")

    def test_threshold_step_rejected_by_delta(self):
        current = StrategyPolicy()
        candidate = StrategyPolicy(
            momentum_lookback=6,
            momentum_threshold_bps="2000",  # in range but beyond 300*2
            mean_reversion_lookback=10,
            mean_reversion_z="-1.5",
            max_notional_fraction="0.15",
        )
        assert check_policy_bounds(current, candidate) == (False, "delta-exceeded:momentum_threshold_bps")


class TestShadow:
    def test_dead_candidate_detected_on_same_snapshot(self):
        closes = {"BTC": _rising(24), "ETH": _rising(24, start=50.0)}
        current = StrategyPolicy()
        dead = StrategyPolicy(
            momentum_lookback=24,  # needs 25 bars; cache holds 24
            momentum_threshold_bps="300",
            mean_reversion_lookback=24,  # rising series never fires negative-z
            mean_reversion_z="-1.5",
            max_notional_fraction="0.15",
        )
        verdict = paper_improve.shadow_compare(closes, current, dead)
        assert verdict["verdict"] == "dead"
        assert verdict["current"]["total"] > 0
        assert verdict["candidate"]["total"] == 0

    def test_empty_snapshot_is_no_data(self):
        verdict = paper_improve.shadow_compare({}, StrategyPolicy(), StrategyPolicy())
        assert verdict["verdict"] == "no-data"


class TestAdoption:
    def test_malformed_candidate_fails_without_reject(self, tmp_path, live_facts):
        trading = _trading(tmp_path)
        _seed_cache(trading, {"BTC": _rising(24)})
        result = paper_improve.run_paper_improve(
            _g(tmp_path), trading_dir=trading, agents="x", now=1000.0,
            llm=_llm_with("not json at all {{{"),
        )
        assert result["status"] == "failed"
        assert "decision" not in result

    def test_extreme_candidate_rejected_and_corner_survives(self, tmp_path, live_facts):
        trading = _trading(tmp_path)
        _seed_cache(trading, {"BTC": _rising(24)})
        result = paper_improve.run_paper_improve(
            _g(tmp_path), trading_dir=trading, agents="x", now=1000.0,
            llm=_llm_with(_policy_json(momentum_lookback=200)),
        )
        assert result["status"] == "rejected"
        assert result["decision"] == "rejected"
        assert result["reason_code"] == "range-exceeded:momentum_lookback"
        assert result["changed"] is False
        # Policy file untouched (defaults: no file was ever written).
        assert load_strategy_policy(trading) == StrategyPolicy()
        status = json.loads((trading / paper_improve.STATUS_FILENAME).read_text())
        assert status["decision"] == "rejected"
        assert status["reason_code"] == "range-exceeded:momentum_lookback"

    def test_snapshot_infeasible_momentum_candidate_rejected(self, tmp_path, live_facts):
        trading = _trading(tmp_path)
        _seed_cache(trading, {"BTC": _rising(24)})
        result = paper_improve.run_paper_improve(
            _g(tmp_path), trading_dir=trading, agents="x", now=1000.0,
            llm=_llm_with(_policy_json(momentum_lookback=24)),
        )
        assert result["status"] == "rejected"
        assert result["reason_code"] == "range-exceeded:momentum_lookback"
        assert result["changed"] is False

    def test_signal_dead_candidate_rejected(self, tmp_path, live_facts):
        trading = _trading(tmp_path)
        _seed_cache(trading, {"BTC": _rising(23)})
        current = StrategyPolicy(momentum_lookback=12, mean_reversion_lookback=12)
        save_strategy_policy(trading, current)
        result = paper_improve.run_paper_improve(
            _g(tmp_path), trading_dir=trading, agents="x", now=1000.0,
            llm=_llm_with(_policy_json(momentum_lookback=23, mean_reversion_lookback=24)),
        )
        assert result["status"] == "rejected"
        assert result["reason_code"] == "signal-dead"
        assert load_strategy_policy(trading) == current

    def test_no_change_reports_unchanged(self, tmp_path, live_facts):
        trading = _trading(tmp_path)
        _seed_cache(trading, {"BTC": _rising(24)})
        result = paper_improve.run_paper_improve(
            _g(tmp_path), trading_dir=trading, agents="x", now=1000.0,
            llm=_llm_with(_policy_json()),
        )
        assert result["status"] == "improved"
        assert result["decision"] == "unchanged"
        assert result["reason_code"] == "no-change"
        assert result["changed"] is False

    def test_good_candidate_accepted_and_archived(self, tmp_path, live_facts):
        trading = _trading(tmp_path)
        _seed_cache(trading, {"BTC": _rising(24)})
        result = paper_improve.run_paper_improve(
            _g(tmp_path), trading_dir=trading, agents="x", now=1000.0,
            llm=_llm_with(_policy_json(momentum_lookback=8, max_notional_fraction="0.2")),
        )
        assert result["status"] == "improved"
        assert result["decision"] == "accepted"
        assert result["changed"] is True
        assert load_strategy_policy(trading).momentum_lookback == 8
        revisions = list_policy_revisions(trading)
        assert len(revisions) == 1
        assert revisions[0]["policy"] == StrategyPolicy()


class TestRollback:
    def test_dead_current_restores_viable_previous(self, tmp_path, live_facts):
        trading = _trading(tmp_path)
        _seed_cache(trading, {"BTC": _rising(24)})
        viable = StrategyPolicy()
        dead = StrategyPolicy(
            momentum_lookback=24,
            mean_reversion_lookback=24,
        )
        save_strategy_policy(trading, dead)
        archive_strategy_policy(trading, viable, at=900.0)

        def exploding_llm(prompt_text):
            raise AssertionError("rollback must not consult the LLM")

        result = paper_improve.run_paper_improve(
            _g(tmp_path), trading_dir=trading, agents="x", now=1000.0, llm=exploding_llm
        )
        assert result["status"] == "rolled_back"
        assert result["decision"] == "rolled_back"
        assert result["reason_code"] == "signal-dead-rollback"
        assert load_strategy_policy(trading) == viable
        status = json.loads((trading / paper_improve.STATUS_FILENAME).read_text())
        assert status["decision"] == "rolled_back"

    def test_no_revision_means_no_rollback(self, tmp_path, live_facts):
        trading = _trading(tmp_path)
        _seed_cache(trading, {"BTC": _rising(24)})
        dead = StrategyPolicy(momentum_lookback=24, mean_reversion_lookback=24)
        save_strategy_policy(trading, dead)
        result = paper_improve.run_paper_improve(
            _g(tmp_path), trading_dir=trading, agents="x", now=1000.0,
            llm=_llm_with(_policy_json()),
        )
        # 戻す revision がないため復帰は起きない。既定値への一気戻しは
        # bounded delta に反するため reject され、現行が維持される
        # (中間値の候補経由で段階的に回復する設計)。
        assert result["status"] == "rejected"
        assert result["reason_code"] == "delta-exceeded:momentum_lookback"
        assert load_strategy_policy(trading) == dead

    def test_revisions_pruned_to_bound(self, tmp_path):
        trading = _trading(tmp_path)
        for step in range(POLICY_MAX_REVISIONS + 3):
            policy = StrategyPolicy(momentum_lookback=2 + (step % 20))
            adopt_strategy_policy(trading, policy)
        revisions = list_policy_revisions(trading)
        assert len(revisions) == POLICY_MAX_REVISIONS
