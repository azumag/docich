"""Step 3 contract: validation, auth, atomic publish and no-order static guard."""
from __future__ import annotations

import ast
import base64
import sys
import threading
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "infra" / "market-bridge"))

from mbridge.common import auth  # noqa: E402
from mbridge.common.contract import (  # noqa: E402
    Candidate, Envelope, Quote, Snapshot, decode_envelope, decode_snapshot,
    encode_envelope, encode_snapshot, is_tradeable, validate_envelope,
    validate_snapshot)
from mbridge.common.publish import load_published, publish_atomic  # noqa: E402

NOW = 1_800_000_000.0
JST = ZoneInfo("Asia/Tokyo")
SYNTHETIC_SECRET = b"s" * 32  # test-only value, not a credential


def snap(**changes):
    base = dict(
        market="stocks", source="kabu-station", source_epoch="kabu-win-01",
        generation=1, sequence=1, upstream_sequence=1, status="ready",
        reason="ok", market_state="open",
        source_ts=NOW - 1, received_ts=NOW,
        quotes=(Quote("JP.7203", 100.0, 100.1, 10, 10, NOW - 1, NOW, 1),),
        candidates=(Candidate("JP.7203", 1, 1.0, NOW - 1),))
    base.update(changes)
    return Snapshot(**base)


def envelope(**changes):
    base = dict(schema_version=1, snapshot=snap(), published_at=NOW - 0.1,
                lease_deadline=NOW + 4.9, host_epoch="host-1", publisher="stocks-1",
                generation=1)
    base.update(changes)
    return Envelope(**base)


class SnapshotValidationTests(unittest.TestCase):
    def kwargs(self, **overrides):
        params = dict(expected_market="stocks", expected_source="kabu-station",
                      source_epoch="kabu-win-01", generation=1, now=NOW,
                      max_source_age_s=15.0, clock_skew_s=2.0)
        params.update(overrides)
        return params

    def test_valid_snapshot(self):
        self.assertEqual(validate_snapshot(snap(), **self.kwargs()), (True, "ok"))

    def test_roundtrip_preserves_validation(self):
        restored = decode_snapshot(encode_snapshot(snap()))
        self.assertEqual(restored, snap())
        self.assertEqual(validate_snapshot(restored, **self.kwargs()), (True, "ok"))
        enc = decode_envelope(encode_envelope(envelope()))
        self.assertEqual(enc, envelope())
        self.assertEqual(validate_envelope(enc, **self.kwargs(host_epoch="host-1")), (True, "ok"))

    def test_wrong_source_and_market(self):
        self.assertEqual(validate_snapshot(snap(source="oanda-mt5-demo"), **self.kwargs()),
                         (False, "wrong_source"))
        self.assertEqual(validate_snapshot(snap(market="fx"), **self.kwargs()),
                         (False, "wrong_market"))

    def test_generation_mixing(self):
        self.assertEqual(validate_snapshot(snap(generation=2), **self.kwargs()),
                         (False, "generation_conflict"))
        self.assertEqual(validate_snapshot(snap(source_epoch="kabu-other"), **self.kwargs()),
                         (False, "generation_conflict"))

    def test_upstream_sequence_roundtrip_and_validation(self):
        restored = decode_snapshot(encode_snapshot(snap(upstream_sequence=42)))
        self.assertEqual(restored.upstream_sequence, 42)
        self.assertEqual(validate_snapshot(restored, **self.kwargs()), (True, "ok"))
        self.assertEqual(validate_snapshot(snap(upstream_sequence=-1), **self.kwargs()),
                         (False, "invalid_sequence"))

    def test_stale_and_future_source_time(self):
        self.assertEqual(validate_snapshot(snap(source_ts=NOW - 60), **self.kwargs()),
                         (False, "stale_data"))
        future = snap(source_ts=NOW + 30,
                      quotes=(Quote("JP.7203", 100.0, 101.0, 1, 1, NOW + 30, NOW + 30, 1),))
        self.assertEqual(validate_snapshot(future, **self.kwargs()), (False, "future_timestamp"))

    def test_publish_time_cannot_refresh_old_source(self):
        # published_at is fresh; the original source_ts is still stale.
        enc = envelope(published_at=NOW, lease_deadline=NOW + 5,
                       snapshot=snap(source_ts=NOW - 60))
        ok, reason = is_tradeable(enc, **self.kwargs(host_epoch="host-1"))
        self.assertFalse(ok)
        self.assertEqual(reason, "stale_data")

    def test_invalid_quote_shapes(self):
        cases = [
            snap(quotes=(Quote("JP.7203", 101.0, 100.0, 1, 1, NOW - 1, NOW, 1),)),
            snap(quotes=(Quote("JP.BAD", 100.0, 101.0, 1, 1, NOW - 1, NOW, 1),)),
            snap(quotes=(replace(snap().quotes[0], received_ts=NOW - 20),)),
        ]
        for case in cases:
            with self.subTest(case=case):
                self.assertFalse(validate_snapshot(case, **self.kwargs())[0])

    def test_warming_up_is_not_tradeable_but_not_connection_error(self):
        warming = snap(status="warming_up", reason="preopen_empty_ranking",
                       market_state="preopen", source_ts=0.0, quotes=(), candidates=())
        self.assertEqual(validate_snapshot(warming, **self.kwargs()), (True, "ok"))
        ok, reason = is_tradeable(envelope(snapshot=warming), **self.kwargs(host_epoch="host-1"))
        self.assertFalse(ok)
        self.assertEqual(reason, "preopen_empty_ranking")

    def test_unavailable_is_fail_closed(self):
        down = snap(status="unavailable", reason="token_expired", quotes=(), candidates=())
        self.assertEqual(validate_snapshot(down, **self.kwargs()), (False, "token_expired"))


class EnvelopeValidationTests(unittest.TestCase):
    def kwargs(self, **overrides):
        params = dict(expected_market="stocks", expected_source="kabu-station",
                      source_epoch="kabu-win-01", generation=1, host_epoch="host-1",
                      now=NOW, max_source_age_s=15.0, max_lease_age_s=5.0)
        params.update(overrides)
        return params

    def test_schema_host_and_lease(self):
        self.assertEqual(validate_envelope(envelope(), **self.kwargs()), (True, "ok"))
        self.assertEqual(validate_envelope(envelope(schema_version=99), **self.kwargs()),
                         (False, "invalid_schema"))
        self.assertEqual(validate_envelope(envelope(host_epoch="other-host"), **self.kwargs()),
                         (False, "stale_host_epoch"))
        self.assertEqual(validate_envelope(
            envelope(published_at=NOW - 2, lease_deadline=NOW - 1), **self.kwargs()),
            (False, "lease_expired"))
        self.assertEqual(validate_envelope(envelope(generation=2), **self.kwargs()),
                         (False, "generation_conflict"))

    def test_lease_deadline_after_now_from_envelope_only(self):
        # A disk copy left by a previous boot must not be accepted as ready.
        self.assertEqual(validate_envelope(envelope(), **self.kwargs(now=NOW + 10)),
                         (False, "lease_expired"))


class AuthTests(unittest.TestCase):
    def setUp(self):
        auth.reset_replay_cache()
        self.secret = auth.parse_key(base64.urlsafe_b64encode(SYNTHETIC_SECRET).decode())
        self.headers = auth.build_headers(self.secret, ts=int(NOW), nonce="a1b2c3d4")

    def test_valid_headers(self):
        self.assertEqual(auth.verify_headers(self.headers, secret=self.secret, now=NOW),
                         (True, "ok"))

    def test_missing_wrong_key_and_expired(self):
        self.assertEqual(auth.verify_headers({}, secret=self.secret, now=NOW)[1], "auth_missing")
        self.assertEqual(auth.verify_headers(
            dict(self.headers, **{"X-Docich-Key-Id": "mb-wrong"}),
            secret=self.secret, now=NOW), (False, "auth_failed"))
        self.assertEqual(auth.verify_headers(self.headers, secret=self.secret, now=NOW + 120),
                         (False, "auth_failed"))

    def test_tampered_signature(self):
        value = self.headers["X-Docich-Bridge"].rsplit(".", 1)[0] + ".0" * 32
        tampered = dict(self.headers, **{"X-Docich-Bridge": value})
        self.assertEqual(auth.verify_headers(tampered, secret=self.secret, now=NOW),
                         (False, "auth_failed"))

    def test_replay_within_skew_is_rejected(self):
        # Same nonce re-sent within the skew window is a replay.
        self.assertEqual(auth.verify_headers(self.headers, secret=self.secret, now=NOW),
                         (True, "ok"))
        self.assertEqual(auth.verify_headers(self.headers, secret=self.secret, now=NOW + 30),
                         (False, "replay_detected"))

    def test_replay_allowed_after_skew_expiry(self):
        # Same nonce with a fresh timestamp is accepted once the skew window
        # has elapsed (the cache entry is evicted after skew).
        self.assertEqual(auth.verify_headers(self.headers, secret=self.secret, now=NOW),
                         (True, "ok"))
        rotated = auth.build_headers(self.secret, ts=int(NOW + 120), nonce="a1b2c3d4")
        self.assertEqual(auth.verify_headers(rotated, secret=self.secret, now=NOW + 120),
                         (True, "ok"))

    def test_future_timestamp_replay_is_rejected_until_signature_expires(self):
        # A timestamp at the future edge remains valid for another full skew
        # window; its nonce must not expire at request_time + max_skew_s.
        future_ts = int(NOW + 60)
        future = auth.build_headers(self.secret, ts=future_ts, nonce="a1b2c3d5")
        self.assertEqual(auth.verify_headers(future, secret=self.secret, now=NOW),
                         (True, "ok"))
        self.assertEqual(auth.verify_headers(future, secret=self.secret, now=NOW + 60),
                         (False, "replay_detected"))
        # The skew check is inclusive, so the exact last valid instant is
        # still protected. Once it has passed, a freshly signed request may
        # reuse the nonce.
        self.assertEqual(auth.verify_headers(future, secret=self.secret, now=NOW + 120),
                         (False, "replay_detected"))
        fresh = auth.build_headers(self.secret, ts=int(NOW + 121), nonce="a1b2c3d5")
        self.assertEqual(auth.verify_headers(fresh, secret=self.secret, now=NOW + 121),
                         (True, "ok"))

    def test_replay_cache_is_bounded_and_fails_closed_until_expiry(self):
        original_limit = auth.REPLAY_CACHE_LIMIT
        auth.REPLAY_CACHE_LIMIT = 2
        auth.reset_replay_cache()
        try:
            first = auth.build_headers(self.secret, ts=int(NOW), nonce="11111111")
            second = auth.build_headers(self.secret, ts=int(NOW), nonce="22222222")
            overflow = auth.build_headers(self.secret, ts=int(NOW), nonce="33333333")
            self.assertEqual(auth.verify_headers(first, secret=self.secret, now=NOW), (True, "ok"))
            self.assertEqual(auth.verify_headers(second, secret=self.secret, now=NOW), (True, "ok"))
            self.assertEqual(auth.verify_headers(overflow, secret=self.secret, now=NOW),
                             (False, "replay_cache_full"))
            self.assertEqual(len(auth._REPLAY_CACHE), 2)
            later = NOW + 61
            after_expiry = auth.build_headers(self.secret, ts=int(later), nonce="33333333")
            self.assertEqual(auth.verify_headers(after_expiry, secret=self.secret, now=later),
                             (True, "ok"))
            self.assertEqual(len(auth._REPLAY_CACHE), 1)
        finally:
            auth.REPLAY_CACHE_LIMIT = original_limit
            auth.reset_replay_cache()


    def test_concurrent_replay_is_accepted_once_within_process(self):
        barrier = threading.Barrier(8)
        result_lock = threading.Lock()
        results = []

        def verify():
            barrier.wait(timeout=2)
            result = auth.verify_headers(self.headers, secret=self.secret, now=NOW)
            with result_lock:
                results.append(result)

        workers = [threading.Thread(target=verify) for _ in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=3)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(results.count((True, "ok")), 1)
        self.assertEqual(results.count((False, "replay_detected")), 7)

    def test_invalid_bridge_key_is_rejected(self):
        with self.assertRaises(ValueError):
            auth.parse_key("not-a-key")


class AtomicPublishTests(unittest.TestCase):
    def test_roundtrip_and_no_temp_leftover(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = publish_atomic(root, "market-snapshot.json", envelope())
            self.assertEqual(target.name, "market-snapshot.json")
            self.assertEqual(load_published(target), envelope())
            self.assertEqual(list(p.name for p in root.iterdir()), ["market-snapshot.json"])

    def test_malformed_file_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "market-snapshot.json"
            path.write_text("{broken", encoding="utf-8")
            self.assertIsNone(load_published(path))


class NoOrderPathStaticTests(unittest.TestCase):
    FORBIDDEN = {"sendorder", "cancelorder", "order_send", "send_order",
                 "cancel_order", "place_order", "order_send_async"}

    def test_no_order_calls_or_routes_in_bridge_package(self):
        package = ROOT / "infra" / "market-bridge" / "mbridge"
        seen = 0
        for path in package.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)):
                    name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                    self.assertNotIn(name.lower(), self.FORBIDDEN, str(path))
                    seen += 1
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    lowered = node.value.lower()
                    for word in self.FORBIDDEN:
                        self.assertNotIn(word, lowered, str(path))
        self.assertGreater(seen, 0)

    def test_only_five_routes_are_declared(self):
        from mbridge.windows.bridge import ROUTES
        self.assertEqual(set(ROUTES), {
            "/v1/health", "/v1/readiness", "/v1/snapshot",
            "/v1/scanner", "/v1/subscriptions"})


if __name__ == "__main__":
    unittest.main()
