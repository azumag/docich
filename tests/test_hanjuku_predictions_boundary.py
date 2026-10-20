"""Boundary regression for the #2216 health record (#2575 post-merge review)."""
from pathlib import Path

import json
import math

from docich import hanjuku_predictions as p
from docich.game_switch import atomic_write_json
from docich.retroarch_boundary import read_record


def test_bounded_clock_rejects_huge_and_bad_values():
    assert p._bounded_clock(10 ** 400) is None
    assert p._bounded_clock(-1) is None
    assert p._bounded_clock(True) is None
    assert p._bounded_clock(float("inf")) is None
    assert p._bounded_clock(float("nan")) is None
    assert p._bounded_clock("100") is None
    assert p._bounded_clock([100]) is None
    assert p._bounded_clock(100) == 100
    assert p._bounded_clock(0) == 0
    assert p._bounded_clock(100.5) == 100.5


def test_update_health_does_not_crash_on_untrusted_outcome(tmp_path):
    state_dir = tmp_path
    # outcome.error is an unhashable/non-string value: must not raise, and must
    # be normalised to 'unexpected' (or None when not failing).
    p._update_health(state_dir, {"mode": "error", "error": ["invalid_state"]}, 100)
    health = read_record(state_dir / p.HEALTH_FILE)
    assert health["last_error"] == "unexpected"
    assert health["consecutive_errors"] == 1
    assert health["last_error_at"] == 100

    # Non-error outcome with a bogus error must not record an error.
    p._update_health(state_dir, {"mode": "idle", "error": {"x": 1}}, 200)
    health = read_record(state_dir / p.HEALTH_FILE)
    assert health["last_error"] is None
    assert health["consecutive_errors"] == 0
    assert health["last_ok_at"] == 200

    # Huge clock values must be projected to None instead of raising.
    p._update_health(state_dir, {"mode": "error", "error": "invalid_state"}, 10 ** 400)
    health = read_record(state_dir / p.HEALTH_FILE)
    assert health["last_error_at"] is None


def test_update_health_survives_broken_prior_health(tmp_path):
    state_dir = tmp_path
    atomic_write_json(state_dir / p.HEALTH_FILE, {
        "schema": p.HEALTH_SCHEMA,
        "consecutive_errors": "not-an-int",
        "last_error": ["boom"],
        "last_error_at": 10 ** 400,
        "last_ok_at": "x",
    })
    # Must not raise even though the prior record is malformed.
    p._update_health(state_dir, {"mode": "error", "error": "invalid_state"}, 50)
    health = read_record(state_dir / p.HEALTH_FILE)
    assert health["consecutive_errors"] == 1
    assert health["last_error"] == "invalid_state"
    assert health["last_error_at"] == 50
    assert health["last_ok_at"] is None
