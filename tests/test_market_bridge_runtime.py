"""Step 3 runtime: fault injection, deadlines, auth, bridge routes and provider."""
from __future__ import annotations

import base64
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "infra" / "market-bridge"))

import secrets
from mbridge.common.auth import build_headers, reset_replay_cache  # noqa: E402
from mbridge.common.contract import (Candidate, Quote, Snapshot,  # noqa: E402
                                     validate_envelope)
from mbridge.common.timeutil import Caller, DeadlineExceeded  # noqa: E402
from mbridge.linux.client import BridgeClient, BridgeError  # noqa: E402
from mbridge.linux.provider import MarketBridgeProvider  # noqa: E402
from mbridge.windows.adapters import (KabuAdapter, Mt5Adapter,  # noqa: E402
                                      plan_registrations)
from mbridge.windows.bridge import BridgeServer  # noqa: E402
from mbridge.windows.mt5_child import run as run_mt5_child  # noqa: E402
from mbridge.common.auth import parse_key  # noqa: E402

NOW = 1_800_000_000.0
JST = ZoneInfo("Asia/Tokyo")
SECRET = parse_key(base64.urlsafe_b64encode(b"t" * 32).decode())  # synthetic


def jst(hour: int, minute: int = 0) -> float:
    return datetime(2026, 9, 16, hour, minute, tzinfo=JST).timestamp()


class FakeKabuClient:
    def __init__(self, *, valid=True, rows=None, hang=False):
        self.valid = valid
        self.rows = rows if rows is not None else [
            {"symbol": "JP.7203", "rank": 1, "score": 1.0, "source_ts": NOW - 1}]
        self.hang = hang
        self.calls = []
        self.registered = ()

    def token_valid(self, now):
        self.calls.append("token_valid")
        return self.valid

    def ranking(self, now):
        self.calls.append("ranking")
        return list(self.rows)

    def quote(self, symbol, now):
        self.calls.append("quote")
        return {"bid": 100.0, "ask": 100.1, "bid_size": 10.0,
                "ask_size": 10.0, "source_ts": now - 1}

    def register(self, symbols):
        self.calls.append("register")
        self.registered = tuple(symbols)


class FakeReadingAdapter:
    """Bridge adapter with a fixed reading; no vendor SDK, no order methods."""

    source = "kabu-station"
    source_epoch = "kabu-win-01"
    generation = 1

    def __init__(self, reading):
        self.reading = reading
        self.registered = ()
        self.calls = []

    def read(self, now):
        self.calls.append("read")
        return self.reading

    def set_subscriptions(self, symbols):
        self.calls.append("subscriptions")
        self.registered = tuple(symbols)


class HangingAdapter(FakeReadingAdapter):
    def __init__(self):
        super().__init__(None)
        self.release = threading.Event()

    def read(self, now):
        self.release.wait(2)
        return self.reading


def ready_reading(source_ts=None, sequence=1):
    from mbridge.windows.adapters import AdapterReading
    ts = NOW - 1 if source_ts is None else source_ts
    return AdapterReading(
        status="ready", reason="ok", market_state="open", source_ts=ts,
        received_ts=NOW, sequence=sequence,
        quotes=(Quote("JP.7203", 100.0, 100.1, 10, 10, ts, NOW, sequence),),
        candidates=(Candidate("JP.7203", 1, 1.0, ts),))


class ScriptedClient:
    """Injected client: returns a payload or raises a fixed BridgeError."""

    def __init__(self, payloads):
        self.payloads = dict(payloads)
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        value = self.payloads.get(path)
        if isinstance(value, Exception):
            raise value
        return value

    def put(self, path, body):
        self.calls.append(path)
        return {"status": "ok"}


def snapshot_payload(**changes):
    base = dict(market="stocks", source="kabu-station", source_epoch="kabu-win-01",
                generation=1, sequence=1, upstream_sequence=1, status="ready",
                reason="ok",
                market_state="open", source_ts=NOW - 1, received_ts=NOW,
                quotes=[{"symbol": "JP.7203", "bid": 100.0, "ask": 100.1,
                         "bid_size": 10.0, "ask_size": 10.0,
                         "source_ts": NOW - 1, "received_ts": NOW, "sequence": 1}],
                candidates=[{"symbol": "JP.7203", "rank": 1, "score": 1.0,
                             "source_ts": NOW - 1}])
    base.update(changes)
    return base


def make_provider(client, root, **changes):
    params = dict(market="stocks", client=client, source_epoch="kabu-win-01",
                  state_dir=Path(root), publisher="stocks-1", host_epoch="host-1",
                  lease_ttl_s=5.0, max_source_age_s=15.0,
                  clock=lambda: NOW, monotonic=lambda: 100.0)
    params.update(changes)
    return MarketBridgeProvider(**params)


class KabuAdapterTests(unittest.TestCase):
    def test_registration_budget_holdings_first(self):
        held = tuple(f"JP.{i:04d}" for i in range(60))
        plan = plan_registrations(held, ("JP.9001",), ("JP.9002",))
        self.assertEqual(len(plan), 50)
        self.assertEqual(plan[0], "JP.0000")
        self.assertNotIn("JP.9001", plan)

    def test_preopen_empty_ranking_is_warming_up_not_failure(self):
        client = FakeKabuClient(rows=[])
        adapter = KabuAdapter(client, held_symbols=("JP.7203",))
        reading = adapter.read(jst(8, 55))
        self.assertEqual((reading.status, reading.reason),
                         ("warming_up", "preopen_empty_ranking"))

    def test_token_expired_is_fail_closed(self):
        client = FakeKabuClient(valid=False)
        adapter = KabuAdapter(client)
        reading = adapter.read(jst(9, 10))
        self.assertEqual((reading.status, reading.reason), ("unavailable", "token_expired"))
        self.assertEqual(reading.quotes, ())

    def test_subscription_registration_is_bounded_and_additive(self):
        client = FakeKabuClient()
        adapter = KabuAdapter(client, max_registrations=50)
        symbols = tuple(f"JP.{i:04d}" for i in range(60))
        adapter.set_subscriptions(symbols)
        self.assertEqual(len(client.registered), 50)
        self.assertFalse(hasattr(client, "unregister_all"))
        self.assertIn("register", client.calls)

    def test_ready_reading_after_open(self):
        adapter = KabuAdapter(FakeKabuClient(), held_symbols=("JP.7203",))
        reading = adapter.read(jst(9, 10))
        self.assertEqual(reading.status, "ready")
        self.assertEqual(reading.quotes[0].symbol, "JP.7203")


class Mt5AdapterTests(unittest.TestCase):
    def result(self, **changes):
        base = dict(status="ok", reason="ok", initialized=True, server_verified=True,
                    account_verified=True, symbols_ready=True, trade_allowed=False,
                    source_ts=NOW - 1,
                    quotes=[{"symbol": "EURUSD", "bid": 1.1, "ask": 1.1002,
                             "bid_size": 1.0, "ask_size": 1.0,
                             "source_ts": NOW - 1, "received_ts": NOW, "sequence": 1}])
        base.update(changes)
        return base

    def adapter(self, launcher, **changes):
        params = dict(symbols=("EURUSD",), expected_server="Demo", expected_login=123,
                      launcher=launcher, deadline_s=0.2, clock=lambda: NOW)
        params.update(changes)
        return Mt5Adapter(**params)

    def test_trade_allowed_is_not_required(self):
        reading = self.adapter(lambda: self.result(trade_allowed=False)).read()
        self.assertEqual(reading.status, "ready")
        self.assertEqual(reading.reason, "ok")

    def test_initialize_isolated_and_bounded(self):
        release = threading.Event()

        def hang():
            release.wait(2)
            return self.result()

        adapter = self.adapter(hang)
        try:
            start = time.monotonic()
            reading = adapter.read()
            self.assertLess(time.monotonic() - start, 1.0)
            self.assertEqual((reading.status, reading.reason), ("unavailable", "adapter_hang"))
        finally:
            release.set()
            adapter.close()

    def test_wrong_terminal_and_sdk_missing_are_fail_closed(self):
        cases = [
            (dict(server_verified=False), "wrong_terminal"),
            (dict(initialized=False, status="terminal_unavailable",
                  reason="terminal_disconnected", symbols_ready=False), "terminal_disconnected"),
            (dict(status="sdk_unavailable", reason="sdk_unavailable",
                  symbols_ready=False, initialized=False), "upstream_unavailable"),
        ]
        for changes, expected in cases:
            with self.subTest(expected=expected):
                adapter = self.adapter(lambda: self.result(**changes))
                self.assertEqual(adapter.read().reason, expected)
                adapter.close()


class Mt5ChildTests(unittest.TestCase):
    def test_unknown_tick_time_is_not_published_as_a_fresh_quote(self):
        class FakeMt5:
            def __init__(self, tick):
                self.tick = tick
                self.shutdown_called = False

            def initialize(self, **kwargs):
                return True

            def account_info(self):
                return SimpleNamespace(server="Demo", login=123, trade_allowed=False)

            def symbol_select(self, symbol, enabled):
                return True

            def symbol_info_tick(self, symbol):
                return self.tick

            def shutdown(self):
                self.shutdown_called = True

        ticks = (
            SimpleNamespace(bid=1.1, ask=1.1002, time=0),
            SimpleNamespace(bid=1.1, ask=1.1002, time=None),
            SimpleNamespace(bid=1.1, ask=1.1002),
        )
        payload = {
            "symbols": ["EURUSD"],
            "expected_server": "Demo",
            "expected_login": 123,
        }
        for tick in ticks:
            with self.subTest(tick_time=getattr(tick, "time", "missing")):
                fake = FakeMt5(tick)
                result = run_mt5_child(payload, fake, clock=lambda: NOW)
                self.assertEqual(result["quotes"], [])
                self.assertFalse(result["symbols_ready"])
                self.assertEqual(result["source_ts"], 0.0)
                self.assertEqual(result["status"], "data_unavailable")
                self.assertEqual(result["reason"], "upstream_unavailable")
                self.assertTrue(fake.shutdown_called)


class BridgeServerTests(unittest.TestCase):
    def setUp(self):
        self.adapter = FakeReadingAdapter(ready_reading())
        self.server = BridgeServer(market="stocks", adapter=self.adapter, secret=SECRET,
                                   clock=lambda: NOW, request_deadline_s=0.2)
        self.server.start()
        self.base = "http://127.0.0.1:%d" % self.server.address[1]
        self.opener = urllib.request.build_opener()

    def tearDown(self):
        self.server.stop()

    def _request(self, method, path, *, body=None, auth=True):
        reset_replay_cache()
        headers = (build_headers(SECRET, ts=int(NOW), nonce=secrets.token_hex(8))
                   if auth else {})
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data,
                                         headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=2) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_auth_required_and_five_routes(self):
        self.assertEqual(self._request("GET", "/v1/health", auth=False)[0], 401)
        code, payload = self._request("GET", "/v1/health")
        self.assertEqual(code, 200)
        self.assertEqual(payload["status"], "alive")
        self.assertEqual(self._request("GET", "/v1/snapshot")[0], 200)
        self.assertEqual(self._request("GET", "/v1/readiness")[0], 200)
        self.assertEqual(self._request("GET", "/v1/scanner")[0], 200)
        self.assertEqual(self._request("PUT", "/v1/subscriptions",
                                       body={"symbols": ["JP.7203"]})[0], 200)

    def test_order_routes_and_wrong_method_are_unreachable(self):
        for path in ("/v1/sendorder", "/v1/cancelorder", "/v1/order_send",
                     "/v1/orders", "/v1/account", "/v1/proxy"):
            for method in ("GET", "POST"):
                with self.subTest(method=method, path=path):
                    self.assertEqual(self._request(method, path)[0], 404)
        self.assertEqual(self._request("POST", "/v1/snapshot")[0], 405)

    def test_subscription_limits(self):
        too_many = [f"JP.{i:04d}" for i in range(51)]
        self.assertEqual(self._request("PUT", "/v1/subscriptions",
                                       body={"symbols": too_many})[0], 400)
        self.assertEqual(self._request("PUT", "/v1/subscriptions",
                                       body={"symbols": ["BAD"]})[0], 400)

    def test_hang_becomes_deadline_reason(self):
        hanging = HangingAdapter()
        server = BridgeServer(market="stocks", adapter=hanging, secret=SECRET,
                              clock=lambda: NOW, request_deadline_s=0.1)
        server.start()
        self.addCleanup(lambda: (hanging.release.set(), server.stop()))
        base = "http://127.0.0.1:%d" % server.address[1]
        headers = build_headers(SECRET, ts=int(NOW), nonce="a1b2c3d4")
        request = urllib.request.Request(base + "/v1/snapshot", headers=headers, method="GET")
        try:
            start = time.monotonic()
            try:
                with urllib.request.build_opener().open(request, timeout=2) as response:
                    code, payload = response.status, json.loads(response.read().decode())
            except urllib.error.HTTPError as exc:
                code, payload = exc.code, json.loads(exc.read().decode())
            self.assertLess(time.monotonic() - start, 1.0)
            self.assertEqual(code, 503)
            self.assertEqual(payload["reason"], "adapter_timeout")
        finally:
            hanging.release.set()


class ProviderFaultInjectionTests(unittest.TestCase):
    def test_initial_state_alive_but_unready(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = make_provider(ScriptedClient({}), tmp)
            self.assertTrue(provider.alive)
            self.assertEqual(provider.readiness(), (False, "provider_unavailable"))

    def test_success_publishes_valid_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = make_provider(ScriptedClient({"/v1/snapshot": snapshot_payload()}), tmp)
            env = provider.poll()
            self.assertEqual(env.snapshot.status, "ready")
            self.assertEqual(provider.readiness(), (True, "ok"))
            published = provider.read_published()
            self.assertEqual(published, env)
            ok, reason = validate_envelope(
                published, expected_market="stocks", expected_source="kabu-station",
                source_epoch="kabu-win-01", generation=1, host_epoch="host-1", now=NOW)
            self.assertEqual((ok, reason), (True, "ok"))

    def test_faults_are_fail_closed(self):
        cases = [
            ("stale", snapshot_payload(source_ts=NOW - 60,
                                       quotes=[{"symbol": "JP.7203", "bid": 100.0,
                                                "ask": 100.1, "source_ts": NOW - 60,
                                                "received_ts": NOW, "sequence": 1}],
                                       candidates=[]), "stale_data"),
            ("future", snapshot_payload(source_ts=NOW + 30,
                                        quotes=[{"symbol": "JP.7203", "bid": 100.0,
                                                 "ask": 100.1, "source_ts": NOW + 30,
                                                 "received_ts": NOW + 30, "sequence": 1}],
                                        candidates=[]), "future_timestamp"),
            ("wrong_source", snapshot_payload(source="oanda-mt5-demo"), "wrong_source"),
            ("generation_conflict", snapshot_payload(generation=0), "generation_conflict"),
            ("wrong_epoch", snapshot_payload(source_epoch="other-epoch"), "generation_conflict"),
            ("broken_schema", {"market": "stocks"}, "invalid_schema"),
            ("hang", BridgeError("adapter_timeout"), "adapter_timeout"),
            ("auth", BridgeError("auth_failed"), "auth_failed"),
            ("warming_up", snapshot_payload(status="warming_up",
                                            reason="preopen_empty_ranking", source_ts=0.0,
                                            quotes=[], candidates=[]), "preopen_empty_ranking"),
        ]
        for name, payload, expected in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                provider = make_provider(ScriptedClient({"/v1/snapshot": payload}), tmp)
                env = provider.poll()
                if name == "warming_up":
                    # Warm-up is a distinct, non-error state and never tradeable.
                    self.assertEqual(env.snapshot.status, "warming_up")
                    self.assertEqual(provider.readiness()[1], expected)
                else:
                    self.assertEqual(env.snapshot.status, "unavailable")
                    self.assertEqual(provider.readiness()[1], expected)
                # A rejected/unavailable snapshot never republishes stale quotes.
                self.assertEqual(env.snapshot.quotes, ())

    def test_scanner_generation_mixing_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = ScriptedClient({
                "/v1/snapshot": snapshot_payload(),
                "/v1/scanner": {"generation": 2, "candidates": []},
            })
            provider = make_provider(client, tmp, fetch_scanner=True)
            self.assertEqual(provider.poll().snapshot.reason, "generation_conflict")
            self.assertEqual(provider.readiness()[1], "generation_conflict")

    def test_lease_expiry_after_monotonic_advance(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = [100.0]

            def monotonic():
                return now[0]

            provider = make_provider(ScriptedClient({"/v1/snapshot": snapshot_payload()}),
                                     tmp, monotonic=monotonic)
            provider.poll()
            self.assertEqual(provider.readiness(), (True, "ok"))
            now[0] += 6.0
            self.assertEqual(provider.readiness(), (False, "lease_expired"))

    def test_recovery_and_sequence_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = make_provider(ScriptedClient({"/v1/snapshot": snapshot_payload()}), tmp)
            provider.poll()
            self.assertEqual(provider.readiness()[0], True)
            # Same sequence replayed on reconnect must not be accepted.
            provider.poll()
            self.assertEqual(provider.readiness()[1], "sequence_regressed")
            client = ScriptedClient({"/v1/snapshot": snapshot_payload(sequence=2, upstream_sequence=2)})
            provider.client = client
            provider.poll()
            self.assertEqual(provider.readiness()[0], True)

    def test_published_sequence_is_provider_issued_upstream_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = ScriptedClient({"/v1/snapshot": snapshot_payload(sequence=9, upstream_sequence=9)})
            provider = make_provider(client, tmp)
            provider.poll()
            self.assertEqual(provider.readiness()[0], True)
            published = provider.read_published()
            # published envelope sequence is the provider counter (1), not the
            # upstream bridge sequence (9); the upstream value is preserved
            self.assertEqual(published.snapshot.sequence, 1)
            self.assertEqual(published.snapshot.upstream_sequence, 9)
            # re-publishing the same upstream sequence is rejected as regressed
            provider.poll()
            self.assertEqual(provider.readiness()[1], "sequence_regressed")

    def test_published_state_rejected_after_host_reboot(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = make_provider(ScriptedClient({"/v1/snapshot": snapshot_payload()}), tmp)
            env = provider.poll()
            after_reboot = make_provider(ScriptedClient({}), tmp, host_epoch="other-host")
            ok, reason = validate_envelope(
                env, expected_market="stocks", expected_source="kabu-station",
                source_epoch="kabu-win-01", generation=1, host_epoch="other-host", now=NOW)
            self.assertEqual((ok, reason), (False, "stale_host_epoch"))


class EndToEndTests(unittest.TestCase):
    def test_bridge_client_provider_over_loopback(self):
        adapter = FakeReadingAdapter(ready_reading())
        server = BridgeServer(market="stocks", adapter=adapter, secret=SECRET,
                              clock=lambda: NOW, request_deadline_s=1.0)
        server.start()
        self.addCleanup(server.stop)
        client = BridgeClient(url="http://127.0.0.1:%d" % server.address[1],
                              secret=SECRET, clock=lambda: NOW, deadline_s=1.0,
                              allow_insecure_http=True)
        self.addCleanup(client.close)
        with tempfile.TemporaryDirectory() as tmp:
            provider = make_provider(client, tmp)
            env = provider.poll()
            self.assertEqual(env.snapshot.status, "ready")
            self.assertEqual(provider.readiness(), (True, "ok"))
            self.assertIn("/v1/snapshot", client.calls if hasattr(client, "calls") else ["/v1/snapshot"])

    def test_no_order_methods_on_adapters(self):
        client = FakeKabuClient()
        adapter = KabuAdapter(client)
        for name in ("sendorder", "cancelorder", "order_send", "place_order"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(adapter, name))
                self.assertFalse(hasattr(client, name))


class CallerHangTests(unittest.TestCase):
    def test_capacity_is_reserved_before_submit_can_race(self):
        caller = Caller(workers=1, name="test-submit-race")
        self.addCleanup(caller.close)
        work_release = threading.Event()
        submit_entered = threading.Event()
        submit_release = threading.Event()
        original_submit = caller._pool.submit

        def blocked_submit(fn, *args, **kwargs):
            submit_entered.set()
            if not submit_release.wait(timeout=3.0):
                raise AssertionError("test did not release the first submit")
            return original_submit(fn, *args, **kwargs)

        caller._pool.submit = blocked_submit
        first_result = {}

        def first_call():
            first_result['value'] = caller.call(lambda: work_release.wait(timeout=3.0),
                                                timeout_s=3.0)

        first = threading.Thread(target=first_call)
        first.start()
        self.assertTrue(submit_entered.wait(timeout=1.0))
        second_result = {}

        def second_call():
            started = time.monotonic()
            try:
                caller.call(lambda: "unexpected", timeout_s=3.0)
            except DeadlineExceeded as exc:
                second_result['error'] = exc
            second_result['elapsed'] = time.monotonic() - started

        second = threading.Thread(target=second_call)
        second.start()
        second.join(timeout=0.5)
        self.assertFalse(second.is_alive(), "a reserved slot must reject before submit")
        self.assertIsInstance(second_result.get('error'), DeadlineExceeded)
        self.assertLess(second_result.get('elapsed', 99.0), 0.5)
        submit_release.set()
        work_release.set()
        first.join(timeout=2.0)
        self.assertFalse(first.is_alive())
        self.assertTrue(first_result.get('value'))


    def test_full_pool_fails_closed_immediately(self):
        caller = Caller(workers=2, name="test-hang")
        self.addCleanup(caller.close)
        release = threading.Event()

        def hang():
            release.wait()  # never released -> worker stuck for the test

        # Occupy every worker through call() so _running reflects real in-flight
        # calls backed by un-stoppable futures.
        t1 = threading.Thread(target=lambda: caller.call(hang, timeout_s=30.0))
        t2 = threading.Thread(target=lambda: caller.call(hang, timeout_s=30.0))
        t1.start()
        t2.start()
        deadline = time.monotonic() + 5.0
        while True:
            with caller._lock:
                running = caller._running
            if running >= 2:
                break
            if time.monotonic() > deadline:
                self.fail("caller workers did not become occupied")
            time.sleep(0.01)

        # A new call must fail closed immediately instead of waiting up to
        # timeout_s for a worker that will never free.
        start = time.monotonic()
        with self.assertRaises(DeadlineExceeded):
            caller.call(lambda: "ok", timeout_s=30.0)
        self.assertLess(time.monotonic() - start, 2.0,
                        "must fail fast when the pool is full")
        release.set()
        t1.join(timeout=2)
        t2.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
