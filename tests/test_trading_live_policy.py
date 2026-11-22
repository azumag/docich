"""LIVE L1: policy snapshots and digests (Issue #3623 §2.4/§2.6)."""
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from docich.trading.live import policy as pol
from docich.trading.live.canonical import CanonicalError, canonical_json, decimal_text

from test_trading_live_support import block_network, policy_set, synthetic_mapping, synthetic_spec


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


def _write(tmp_path, data, name="snap.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_experiment_digest_excludes_activated_at():
    a = synthetic_spec()
    mapping = synthetic_mapping()
    mapping["activated_at"] = 999.0
    from docich.trading.strategy_lab import experiment_from_mapping
    assert pol.experiment_digest(a) == pol.experiment_digest(experiment_from_mapping(mapping))


def test_experiment_digest_golden_fixture():
    # Pins experiment_to_payload's output shape: if PAPER changes it, LIVE digests would drift.
    assert pol.experiment_digest(synthetic_spec()) == (
        "052eeb0ac0819d8f7a1806a0270952038cb824bd41d89075adff0c56510b7636")


def test_canonical_json_rejects_float_nan_and_nonstr_keys():
    for bad in ({"a": 1.5}, {"a": [float("nan")]}, {1: "x"}):
        with pytest.raises(CanonicalError):
            canonical_json(bad)
    assert canonical_json({"b": 1, "a": "あ"}) == '{"a":"あ","b":1}'.encode("utf-8")


def test_decimal_text_rejects_nan_inf_negative_and_float():
    for bad in ("NaN", "Infinity", "-1", 1.5, True, "x"):
        with pytest.raises(CanonicalError):
            decimal_text(bad, name="v")
    assert decimal_text("1.50", name="v") == "1.50"
    assert decimal_text("0", name="v", allow_zero=True) == "0"


def test_load_snapshot_accepts_matching_digest(tmp_path):
    path = _write(tmp_path, synthetic_mapping())
    spec = pol.load_experiment_snapshot(path, expected_digest=pol.experiment_digest(synthetic_spec()))
    assert spec.experiment_id == "synthetic_v1"


def test_load_snapshot_rejects_tampered_threshold(tmp_path):
    good = pol.experiment_digest(synthetic_spec())
    data = synthetic_mapping()
    data["entry_rules"][0]["conditions"][0]["threshold"] = "-100"
    with pytest.raises(pol.LivePolicyError):
        pol.load_experiment_snapshot(_write(tmp_path, data), expected_digest=good)


def test_load_snapshot_rejects_lossy_roundtrip(tmp_path):
    good = pol.experiment_digest(synthetic_spec())
    for mutate in (lambda d: d.update(extra_key=1),
                   lambda d: d["entry_rules"][0].pop("combine"),
                   lambda d: d["entry_rules"][0]["conditions"][0].update(threshold=-120.0),
                   lambda d: d["entry_rules"][0].update(rule_id="Entry 1!")):
        data = synthetic_mapping()
        mutate(data)
        with pytest.raises(pol.LivePolicyError):
            pol.load_experiment_snapshot(_write(tmp_path, data), expected_digest=good)


def test_load_snapshot_rejects_symlink_oversize_nonutf8_and_bad_digest(tmp_path):
    good = pol.experiment_digest(synthetic_spec())
    real = _write(tmp_path, synthetic_mapping())
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(pol.LivePolicyError):
        pol.load_experiment_snapshot(link, expected_digest=good)
    big = tmp_path / "big.json"
    big.write_text(" " * (pol.MAX_SNAPSHOT_BYTES + 1), encoding="utf-8")
    with pytest.raises(pol.LivePolicyError):
        pol.load_experiment_snapshot(big, expected_digest=good)
    junk = tmp_path / "junk.json"
    junk.write_bytes(b"\xff\xfe{}")
    with pytest.raises(pol.LivePolicyError):
        pol.load_experiment_snapshot(junk, expected_digest=good)
    for bad in ("", "ABC", good.upper(), good[:-1]):
        with pytest.raises(pol.LivePolicyError):
            pol.load_experiment_snapshot(real, expected_digest=bad)


def test_policy_fraction_is_the_max_entry_fraction_and_capped():
    policy = pol.build_policy(synthetic_spec(), "OAS/JPY")
    assert policy.max_opportunity_fraction == Decimal("0.07")
    assert policy.max_opportunity_fraction <= Decimal("0.30")


def test_policy_rejects_non_jpy_pair():
    with pytest.raises(pol.LivePolicyError):
        pol.build_policy(synthetic_spec(), "ETH/BTC")


def test_initial_policy_set_has_eleven_unique_jpy_symbols():
    ps = pol.build_policy_set(synthetic_spec())
    assert len(ps.policies) == 11 and set(ps.policies) == set(pol.INITIAL_SYMBOLS)
    assert all(symbol.endswith("/JPY") for symbol in ps.policies)
    with pytest.raises(pol.LivePolicyError):
        pol.build_policy_set(synthetic_spec(), ("OAS/JPY", "OAS/JPY"))


def test_policy_set_digest_is_order_independent_and_sensitive():
    a = pol.build_policy_set(synthetic_spec(), ("OAS/JPY", "XYM/JPY"))
    b = pol.build_policy_set(synthetic_spec(), ("XYM/JPY", "OAS/JPY"))
    c = pol.build_policy_set(synthetic_spec(), ("OAS/JPY", "OMG/JPY"))
    assert a.digest == b.digest and a.digest != c.digest


def test_policy_payload_roundtrip_reverifies_digest():
    policy = pol.build_policy(synthetic_spec(), "OAS/JPY")
    assert pol.policy_from_payload(policy.payload()).digest == policy.digest
    tampered = policy.payload()
    tampered["experiment"]["exit_rules"][0]["conditions"][0]["threshold"] = "1"
    with pytest.raises(pol.LivePolicyError):
        pol.policy_from_payload(tampered)
    forged = policy.payload()
    forged["digest"] = "0" * 64
    with pytest.raises(pol.LivePolicyError):
        pol.policy_from_payload(forged)


def test_policy_cli_prints_the_digest_only(tmp_path):
    path = _write(tmp_path, synthetic_mapping())
    env = dict(os.environ, PYTHONPATH=str(Path(pol.__file__).parents[3]))
    out = subprocess.run([sys.executable, "-B", "-m", "docich.trading.live.policy", "digest", str(path)],
                         capture_output=True, text=True, env=env, check=True)
    assert out.stdout.strip() == pol.experiment_digest(synthetic_spec())
    assert list(tmp_path.iterdir()) == [path]
