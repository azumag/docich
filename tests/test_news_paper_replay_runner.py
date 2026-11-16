"""N04b replay-runner tests: real N05/N06 execution, digests, A/B conditions."""
import ast
import datetime as dt
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

from docich.trading.news_paper import replay_runner
from docich.trading.news_paper.execution import (
    InstrumentConstraints,
    MarketObservation,
)
from docich.trading.news_paper.manager import (
    DecisionRequest,
    FIXTURE_MANAGER_POLICY_V1,
    ManagerPolicy,
)
from docich.trading.news_paper.replay import ReplayError, load_manifest
from docich.trading.news_paper.replay_runner import (
    ReplayArm,
    ReplayConditions,
    ReplayScenario,
    ReplayStep,
    ab_common_conditions,
    run_n04b,
    run_replay,
)
from docich.trading.news_paper.risk import FIXTURE_RISK_POLICY_V1

UTC = dt.timezone.utc
BASE = dt.datetime(2026, 9, 18, 0, 10, 4, tzinfo=UTC)
ASSET = "stocks:XTKS:DEMO"
RUN_ID = "run_demo_001"
SCENARIO_ID = "price_plus_news"
ACCOUNT_ID = "paper_news_replay"


def _manifest(arm: str = "A"):
    raw = json.dumps([{
        "db": "d", "observation_id": 1, "ingest_sequence": 1,
    }]).encode()
    versions = {
        "source": "v1", "mapping": "v1", "instrument": "v1",
        "calendar": "v1", "cost": "v1", "risk": "v1",
        "policy": "v1", "model": "v1", "execution": "v1",
    }
    data = {"header": {
        "schema_version": "1", "run_id": RUN_ID, "scenario_id": SCENARIO_ID,
        "versions": versions, "clock": {"fence_at": int(BASE.timestamp())},
        "arm": arm,
        "chunks": [{
            "seq": 1, "filename": "c1.json",
            "sha256": hashlib.sha256(raw).hexdigest(), "record_count": 1,
        }],
    }}
    return load_manifest(data, lambda filename: raw)


def _constraints() -> InstrumentConstraints:
    return InstrumentConstraints(
        version="fixture-constraints-v1", market="stocks", quote_currency="JPY",
        lot=D("100"), min_quantity=D("100"), min_notional=D("100000"),
        price_tick=D("1"), fee_bps=D("1"), fee_min=D("1"), slippage_bps=D("2"),
        spread_limit_bps=D("100"), participation_rate=D("1"),
        max_quote_age_seconds=D("60"),
    )


def _request(receipt_id: str, event_group: str, at: dt.datetime) -> DecisionRequest:
    return DecisionRequest(
        receipt_id=receipt_id, run_id=RUN_ID, scenario_id=SCENARIO_ID,
        account_id=ACCOUNT_ID,
        decision_at=at, manager_seen_at=at - dt.timedelta(seconds=2),
        input_cutoff_sequence=120,
        signal_refs=("signal_001",), market_refs=("quote_120",),
        decision_quote=("src", ASSET, BASE - dt.timedelta(seconds=10), "a" * 64),
        asset=ASSET, side="buy", reference_price=D("1000"),
        expires_at=BASE + dt.timedelta(hours=1),
        constraints=_constraints(), event_group_id=event_group,
        quantity_cap=D("300"),
    )


def _observation(seq: int, at: dt.datetime, quote_id: str) -> MarketObservation:
    event_at = at - dt.timedelta(seconds=2)
    return MarketObservation(
        observation_id=quote_id, source="src", asset=ASSET,
        ingest_sequence=seq, market_event_at=event_at,
        market_available_at=event_at, available_at=at,
        bid=D("999"), ask=D("1000"), bid_size=D("5000"), ask_size=D("5000"),
        size_semantics="executable", tradeable=True, realtime=True,
        session_id="s1", calendar_version="c1", quote_digest="c" * 64,
        provenance_class="test_fixture",
        instrument_constraints_version="fixture-constraints-v1",
        volatility_bps=D("50"),
    )


class N04bRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = _manifest("A")
        self.constraints = _constraints()
        self.risk = FIXTURE_RISK_POLICY_V1
        self.manager_policy = ManagerPolicy(execution_delay_ms=D("250"))
        common = dict(
            initial_capital=D("1000000"),
            universe=(ASSET,),
            warmup_seconds=0,
            window_start=BASE,
            window_end=BASE + dt.timedelta(hours=1),
            risk_policy=self.risk,
            constraints=((ASSET, self.constraints),),
            manager_policy=self.manager_policy,
            account_id=ACCOUNT_ID,
            quote_currency="JPY",
        )
        self.arm_a = ReplayArm("A", ReplayConditions(**common))
        self.arm_b = ReplayArm("B", ReplayConditions(
            **common, news_filter=True, classification_delay_ms=5000,
        ))
        self.scenario = ReplayScenario((
            ReplayStep(
                at=BASE + dt.timedelta(seconds=10),
                decision=_request("receipt_001", "event_001",
                                  BASE + dt.timedelta(seconds=10)),
                news_eligible=True,
            ),
            ReplayStep(
                at=BASE + dt.timedelta(seconds=20), receipt_id="receipt_001",
                observations=(_observation(121, BASE + dt.timedelta(seconds=20),
                                           "quote_121"),),
            ),
            ReplayStep(
                at=BASE + dt.timedelta(seconds=30),
                decision=_request("receipt_002", "event_002",
                                  BASE + dt.timedelta(seconds=30)),
                news_eligible=False,
            ),
            ReplayStep(
                at=BASE + dt.timedelta(seconds=40), receipt_id="receipt_002",
                observations=(_observation(122, BASE + dt.timedelta(seconds=40),
                                           "quote_122"),),
            ),
        ))

    def test_ab_runs_are_reproducible_and_use_the_same_model(self):
        result = run_n04b(self.manifest, self.arm_a, self.arm_b, self.scenario)

        self.assertTrue(result.passed)
        for repro in (result.arm_a, result.arm_b):
            self.assertTrue(repro.matches)
            self.assertEqual(repro.mismatches, ())
            self.assertEqual(repro.first.decision_digest, repro.second.decision_digest)
            self.assertEqual(repro.first.fill_digest, repro.second.fill_digest)
            self.assertEqual(repro.first.account_digest, repro.second.account_digest)

        # Both arms go through the real manager/execution/ledger path.
        self.assertEqual(result.arm_a.first.fill_count, 2)
        self.assertEqual(result.arm_b.first.fill_count, 1)
        self.assertEqual(result.arm_a.first.decision_count, 2)
        self.assertEqual(result.arm_b.first.decision_count, 1)
        # The shared cost model produces the same price/fee on the first
        # (common) fill for both arms.
        self.assertEqual(result.arm_a.first.fills[0]["price"],
                         result.arm_b.first.fills[0]["price"])
        self.assertEqual(result.arm_a.first.fills[0]["fee_jpy"],
                         result.arm_b.first.fills[0]["fee_jpy"])
        self.assertEqual(result.arm_b.first.fills[0]["quantity"], "300")
        self.assertTrue(any(
            action["kind"] == "decision_skipped"
            and action["reason_code"] == "NEWS_FILTERED"
            for action in result.arm_b.first.actions
        ))
        # B's classification delay is preserved, not erased for comparison.
        self.assertEqual(
            result.arm_b.first.actions[0]["at"] - result.arm_a.first.actions[0]["at"],
            dt.timedelta(seconds=5),
        )
        for fill in result.arm_a.first.fills:
            self.assertIn(fill["receipt_id"], {"receipt_001", "receipt_002"})
            self.assertTrue(fill["quote_ref"])
            self.assertEqual(fill["quantity"], "300")

        # Common model evidence: every non-news condition is shared.
        self.assertEqual(self.arm_a.conditions.model_conditions(),
                         self.arm_b.conditions.model_conditions())
        self.assertEqual(result.common.differences["news_filter"], [False, True])
        self.assertEqual(result.common.differences["classification_delay_ms"], [0, 5000])
        self.assertEqual(result.common.conditions["news_filter"], False)
        self.assertEqual(result.common.conditions["classification_delay_ms"], 0)
        self.assertEqual(result.common.conditions["initial_capital"], "1000000")
        self.assertEqual(result.common.conditions["universe"], [ASSET])
        self.assertTrue(result.common.conditions["constraints"])
        self.assertTrue(result.common.conditions["risk_policy"])
        self.assertTrue(result.common.conditions["manager_policy"])
        self.assertEqual(result.common.manifest_versions["execution"], "v1")
        self.assertTrue(result.common.scenario_digest)
        evidence = result.to_dict()
        json.dumps(evidence, sort_keys=True)
        self.assertTrue(evidence["passed"])
        self.assertEqual(evidence["common"]["differences"]["news_filter"], [False, True])

    def test_ab_rejects_non_news_condition_difference(self):
        bad_risk = replace(self.risk, max_positions=4)
        bad_b = ReplayArm("B", replace(
            self.arm_b.conditions, risk_policy=bad_risk,
        ))
        with self.assertRaises(ReplayError):
            ab_common_conditions(self.manifest, self.arm_a, bad_b)

    def test_manifest_arm_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="n04b-manifest-") as root:
            with self.assertRaises(ReplayError):
                run_replay(self.manifest, self.arm_b, self.scenario,
                           state_dir=Path(root) / "run")

    def test_runner_has_no_wall_clock_or_network_imports(self):
        source = Path(replay_runner.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        forbidden = {
            "socket", "ssl", "http", "urllib", "requests", "httpx", "aiohttp",
            "openai", "anthropic", "litellm", "time", "random", "secrets",
        }
        self.assertFalse(imported & forbidden)
        wall_clock_calls = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"now", "utcnow", "today", "time"}
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in {"datetime", "date", "time"}):
                wall_clock_calls.add(node.func.attr)
        self.assertFalse(wall_clock_calls)


if __name__ == "__main__":
    unittest.main()
