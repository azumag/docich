"""N02 news observation tests (#778).

Fixtures only: fake transport and resolver, fixed clock, temporary state dir.
No socket, DNS lookup, HTTP client, credential or live source is used.
Expected values come from the #778 acceptance list and the #775 frozen design.
"""
import gzip
import inspect
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from docich.trading.news_paper import collectors as collectors_pkg
from docich.trading.news_paper import dedupe, market_observation_adapter, normalization
from docich.trading.news_paper import observation_store as store_module
from docich.trading.news_paper import provenance as provenance_module
from docich.trading.news_paper.collectors import fixture_feed
from docich.trading.news_paper.collectors.fixture_feed import (
    FixtureCollector,
    Response,
    TransportTimeout,
    is_public_ip,
    run_cycle,
)
from docich.trading.news_paper.market_observation_adapter import AdapterRejection, adapt_quote
from docich.trading.news_paper.observation_store import ObservationStore, StoreError
from docich.trading.news_paper.provenance import ProvenanceError, SourceRegistry, SourceSpec

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
SOURCE = "fixture_wire"
FEED_URL = "https://news.example.test/rss"
PUBLIC_IP = "93.184.216.34"
BANNED_IMPORTS = ("socket", "urllib.request", "http.client", "http.server", "requests",
                  "ssl", "ftplib", "subprocess", "httpx", "asyncio")


def fixture_spec(**overrides):
    base = dict(
        source_id=SOURCE, origin_kind="primary", delivered_by=None,
        allowed_hosts=("news.example.test",), allowed_path_prefixes=("/rss",),
        allowed_query_keys=(), time_field="pubDate", time_precision="second",
        reliability_label="fixture", asset_mapping_version="fixture-asset-map-v1",
        usage_terms_ref="fixture-terms-v1",
    )
    base.update(overrides)
    return SourceSpec(**base)


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


class FakeResolver:
    def __init__(self, addresses=(PUBLIC_IP,)):
        self.addresses = tuple(addresses)
        self.calls = []

    def resolve(self, host):
        self.calls.append(host)
        return self.addresses


class FakeTransport:
    """Scripted responses. Records every call; never performs I/O."""

    def __init__(self, responses=(), raises=None):
        self.responses = list(responses)
        self.raises = raises
        self.calls = []

    def get(self, url, *, pinned_ip, headers, connect_timeout, read_timeout, total_timeout, max_bytes):
        self.calls.append({"url": url, "pinned_ip": pinned_ip, "max_bytes": max_bytes,
                           "timeouts": (connect_timeout, read_timeout, total_timeout)})
        if self.raises is not None:
            raise self.raises
        return self.responses.pop(0)


def rss(*items):
    body = "".join(
        f"<item><guid>{guid}</guid><link>{link}</link><title>{title}</title>"
        f"<description>{desc}</description><pubDate>{pub}</pubDate></item>"
        for guid, link, title, desc, pub in items
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>{body}</channel></rss>'.encode("utf-8")


GOOD_ITEM = ("g-1", "https://news.example.test/a/1", "Fixture rate decision",
             "Central bank holds rates.", "Fri, 30 Oct 2026 10:00:00 +0000")


def ok(body, headers=None):
    return Response(status=200, headers=headers or {}, body=body)


def collector_with(responses=(), *, raises=None, addresses=(PUBLIC_IP,), clock=None):
    registry = SourceRegistry([fixture_spec()])
    transport = FakeTransport(responses, raises=raises)
    resolver = FakeResolver(addresses)
    return FixtureCollector(registry, transport, resolver, clock or Clock()), transport, resolver


class StructuralNoNetworkTests(unittest.TestCase):
    def test_modules_import_no_network_library(self):
        modules = [
            collectors_pkg, fixture_feed, normalization, store_module,
            dedupe, market_observation_adapter, provenance_module,
        ]
        for module in modules:
            source = inspect.getsource(module)
            for banned in BANNED_IMPORTS:
                for marker in (f"import {banned}", f"from {banned}"):
                    self.assertNotIn(marker, source, f"{module.__name__} contains {marker!r}")

    def test_collector_has_no_default_transport(self):
        sig = inspect.signature(FixtureCollector.__init__)
        self.assertNotIn("default", " ".join(sig.parameters))
        self.assertEqual(list(sig.parameters)[1:], ["registry", "transport", "resolver", "clock"])


class SourceRegistryTests(unittest.TestCase):
    def test_registry_has_no_live_source(self):
        registry = SourceRegistry([fixture_spec()])
        self.assertEqual(registry.live_source_count, 0)

    def test_live_status_is_rejected(self):
        with self.assertRaises(ProvenanceError):
            fixture_spec(status="live")

    def test_aggregator_needs_delivered_by_and_origin_stays_separate(self):
        with self.assertRaises(ProvenanceError):
            fixture_spec(origin_kind="aggregator", delivered_by=None)
        spec = fixture_spec(origin_kind="aggregator", delivered_by="wire_agg")
        self.assertEqual(spec.delivered_by, "wire_agg")

    def test_duplicate_and_unknown_sources_fail_closed(self):
        with self.assertRaises(ProvenanceError):
            SourceRegistry([fixture_spec(), fixture_spec()])
        registry = SourceRegistry([fixture_spec()])
        with self.assertRaises(ProvenanceError):
            registry.get("not_registered")

    def test_provenance_order_is_enforced(self):
        base = dict(source_id=SOURCE, revision_kind="original", supersedes=None)
        with self.assertRaises(ProvenanceError):
            provenance_module.Provenance(
                origin_published_at=NOW, fetched_at=NOW - timedelta(minutes=1),
                available_at=NOW, first_seen_at=NOW, **base)
        with self.assertRaises(ProvenanceError):
            provenance_module.Provenance(
                origin_published_at=NOW.replace(tzinfo=None), fetched_at=NOW,
                available_at=NOW, first_seen_at=NOW, **base)


class UrlAndAddressTests(unittest.TestCase):
    def test_canonical_url_accepts_https_and_sorts_allowed_query(self):
        url = normalization.canonical_url(
            "HTTPS://News.Example.Test/rss/a?b=2&a=1", allowed_query_keys=("a", "b"))
        self.assertEqual(url, "https://news.example.test/rss/a?a=1&b=2")

    def test_canonical_url_refuses_unsafe_forms(self):
        cases = {
            "http://news.example.test/rss": normalization.CODE_URL_SCHEME,
            "https://user:pw@news.example.test/rss": normalization.CODE_URL_USERINFO,
            "https://news.example.test:8443/rss": normalization.CODE_URL_PORT,
            "https://news.example.test/rss/../etc": normalization.CODE_URL_PATH,
            "https://news.example.test/rss?evil=1": normalization.CODE_URL_QUERY_KEY,
        }
        for raw, code in cases.items():
            with self.subTest(raw=raw):
                with self.assertRaises(normalization.NormalizationError) as ctx:
                    normalization.canonical_url(raw)
                self.assertEqual(str(ctx.exception), code)

    def test_non_public_addresses_are_refused(self):
        refused = [
            "10.0.0.1", "172.16.5.4", "192.168.1.1", "127.0.0.1", "169.254.169.254",
            "100.100.1.1",  # shared / tailnet range
            "0.0.0.0", "::1", "fe80::1", "fd00::1", "::ffff:10.0.0.1", "::ffff:127.0.0.1",
            "2002::1",  # 6to4 tunnel form
            "198.18.0.5",
        ]
        for address in refused:
            with self.subTest(address=address):
                self.assertFalse(is_public_ip(address))
        self.assertTrue(is_public_ip(PUBLIC_IP))


class CollectorTests(unittest.TestCase):
    def test_accepts_item_and_stamps_receipt_time(self):
        collector, transport, _ = collector_with([ok(rss(GOOD_ITEM))])
        outcome = collector.fetch(SOURCE, FEED_URL)
        self.assertIsNone(outcome.code)
        self.assertEqual(len(outcome.items), 1)
        item = outcome.items[0]
        self.assertEqual(item.source_item_id, "g-1")
        self.assertEqual(item.fetched_at, NOW)
        self.assertLessEqual(item.origin_published_at, item.fetched_at)
        self.assertEqual(transport.calls[0]["pinned_ip"], PUBLIC_IP)
        self.assertEqual(transport.calls[0]["timeouts"],
                         (fixture_feed.FIXTURE_CONNECT_TIMEOUT_S, fixture_feed.FIXTURE_READ_TIMEOUT_S,
                          fixture_feed.FIXTURE_TOTAL_TIMEOUT_S))

    def test_non_public_dns_never_reaches_transport(self):
        collector, transport, _ = collector_with([ok(rss(GOOD_ITEM))], addresses=("10.1.2.3",))
        outcome = collector.fetch(SOURCE, FEED_URL)
        self.assertEqual(outcome.code, fixture_feed.CODE_DNS_NON_PUBLIC)
        self.assertEqual(transport.calls, [])

    def test_mixed_dns_answer_is_refused(self):
        collector, transport, _ = collector_with([ok(rss(GOOD_ITEM))], addresses=(PUBLIC_IP, "127.0.0.1"))
        self.assertEqual(collector.fetch(SOURCE, FEED_URL).code, fixture_feed.CODE_DNS_NON_PUBLIC)
        self.assertEqual(transport.calls, [])

    def test_disallowed_host_or_path_never_reaches_transport(self):
        collector, transport, _ = collector_with([ok(rss(GOOD_ITEM))])
        self.assertEqual(collector.fetch(SOURCE, "https://evil.example.test/rss").code,
                         fixture_feed.CODE_URL_NOT_ALLOWED)
        self.assertEqual(collector.fetch(SOURCE, "https://news.example.test/admin").code,
                         fixture_feed.CODE_URL_NOT_ALLOWED)
        self.assertEqual(transport.calls, [])

    def test_unknown_source_fails_closed(self):
        collector, transport, _ = collector_with([])
        self.assertEqual(collector.fetch("nope", FEED_URL).code, fixture_feed.CODE_SOURCE_UNKNOWN)
        self.assertEqual(transport.calls, [])

    def test_redirect_is_refused_not_followed(self):
        collector, transport, _ = collector_with([Response(302, {"Location": "https://10.0.0.1/"}, b"")])
        self.assertEqual(collector.fetch(SOURCE, FEED_URL).code, fixture_feed.CODE_REDIRECT_REFUSED)
        self.assertEqual(len(transport.calls), 1)

    def test_retry_after_is_capped(self):
        collector, _, _ = collector_with([Response(429, {"Retry-After": "99999"}, b"")])
        outcome = collector.fetch(SOURCE, FEED_URL)
        self.assertEqual(outcome.code, fixture_feed.CODE_HTTP_RETRY)
        self.assertEqual(outcome.backoff_seconds, fixture_feed.FIXTURE_RETRY_AFTER_CAP_S)

    def test_server_error_and_unexpected_status(self):
        collector, _, _ = collector_with([Response(503, {}, b"")])
        self.assertEqual(collector.fetch(SOURCE, FEED_URL).code, fixture_feed.CODE_HTTP_RETRY)
        collector, _, _ = collector_with([Response(404, {}, b"")])
        self.assertEqual(collector.fetch(SOURCE, FEED_URL).code, fixture_feed.CODE_HTTP_STATUS_UNEXPECTED)

    def test_timeout_and_transport_failure_are_fixed_codes(self):
        collector, _, _ = collector_with(raises=TransportTimeout("connect timed out 10.9.9.9"))
        outcome = collector.fetch(SOURCE, FEED_URL)
        self.assertEqual(outcome.code, fixture_feed.CODE_TIMEOUT)
        self.assertNotIn("10.9.9.9", repr(outcome))
        collector, _, _ = collector_with(raises=OSError("secret-host-detail"))
        self.assertEqual(collector.fetch(SOURCE, FEED_URL).code, fixture_feed.CODE_TRANSPORT_FAILED)

    def test_raw_body_limit(self):
        collector, _, _ = collector_with([ok(b"x" * (fixture_feed.FIXTURE_MAX_BODY_BYTES + 1))])
        self.assertEqual(collector.fetch(SOURCE, FEED_URL).code, fixture_feed.CODE_PAYLOAD_LIMIT)

    def test_gzip_bomb_is_bounded_after_expansion(self):
        bomb = gzip.compress(b"\x00" * (fixture_feed.FIXTURE_MAX_EXPANDED_BYTES * 4))
        self.assertLess(len(bomb), fixture_feed.FIXTURE_MAX_BODY_BYTES)
        collector, _, _ = collector_with([ok(bomb, {"Content-Encoding": "gzip"})])
        self.assertEqual(collector.fetch(SOURCE, FEED_URL).code, fixture_feed.CODE_PAYLOAD_LIMIT)

    def test_gzip_within_limits_is_decoded(self):
        packed = gzip.compress(rss(GOOD_ITEM))
        collector, _, _ = collector_with([ok(packed, {"Content-Encoding": "gzip"})])
        self.assertEqual(len(collector.fetch(SOURCE, FEED_URL).items), 1)

    def test_dtd_and_entities_are_refused(self):
        for marker in (b'<!DOCTYPE rss [<!ENTITY x "y">]>', b"<!ENTITY a 'b'>"):
            with self.subTest(marker=marker):
                collector, _, _ = collector_with([ok(marker + rss(GOOD_ITEM))])
                self.assertEqual(collector.fetch(SOURCE, FEED_URL).code, fixture_feed.CODE_XML_DTD_REFUSED)

    def test_invalid_xml(self):
        collector, _, _ = collector_with([ok(b"<rss><item>")])
        self.assertEqual(collector.fetch(SOURCE, FEED_URL).code, fixture_feed.CODE_XML_INVALID)

    def test_time_and_secret_problems_are_quarantined_not_ready(self):
        items = [
            ("g-2", "https://news.example.test/a/2", "No time", "x", ""),
            ("g-3", "https://news.example.test/a/3", "Naive time", "x", "30 Oct 2026 10:00:00"),
            ("g-4", "https://news.example.test/a/4", "Future", "x", "Fri, 30 Oct 2026 13:00:00 +0000"),
            ("g-5", "https://news.example.test/a/5", "Stale", "x", "Mon, 01 Jun 2026 10:00:00 +0000"),
            ("g-6", "https://news.example.test/a/6", "api_key=abcdef123456", "x", "Fri, 30 Oct 2026 10:00:00 +0000"),
        ]
        collector, _, _ = collector_with([ok(rss(*items))])
        outcome = collector.fetch(SOURCE, FEED_URL)
        codes = sorted(q.code for q in outcome.quarantined)
        self.assertEqual(codes, sorted([
            fixture_feed.CODE_TIME_MISSING, "TIMEZONE_MISSING", "TIME_FUTURE",
            normalization.CODE_TIME_STALE, fixture_feed.CODE_SECRET_SUSPECTED,
        ]))
        self.assertEqual(outcome.items, [])

    def test_item_limit_quarantines_overflow(self):
        many = [("g-%d" % i, "https://news.example.test/a/%d" % i, "t%d" % i, "d",
                 "Fri, 30 Oct 2026 10:00:00 +0000") for i in range(fixture_feed.FIXTURE_MAX_ITEMS + 1)]
        collector, _, _ = collector_with([ok(rss(*many))])
        outcome = collector.fetch(SOURCE, FEED_URL)
        self.assertEqual(len(outcome.items), fixture_feed.FIXTURE_MAX_ITEMS)
        self.assertIn(fixture_feed.CODE_ITEM_LIMIT, [q.code for q in outcome.quarantined])

    def test_cycle_budget_and_isolated_failures(self):
        collector, transport, _ = collector_with([ok(rss(GOOD_ITEM)), Response(500, {}, b"")])
        jobs = [(SOURCE, FEED_URL), (SOURCE, FEED_URL), (SOURCE, FEED_URL)]
        results = run_cycle(collector, jobs, budget=2)
        self.assertIsNone(results[0].code)
        self.assertEqual(results[1].code, fixture_feed.CODE_HTTP_RETRY)
        self.assertEqual(results[2].code, fixture_feed.CODE_BUDGET_EXHAUSTED)
        self.assertEqual(len(transport.calls), 2)


class ObservationStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = Path(self._tmp.name)
        self.clock = Clock(NOW + timedelta(seconds=5))
        self.collector, self.transport, _ = collector_with([ok(rss(GOOD_ITEM))], clock=Clock(NOW))
        self.item = self.collector.fetch(SOURCE, FEED_URL).items[0]

    def tearDown(self):
        self._tmp.cleanup()

    def store(self):
        return ObservationStore(self.state, self.clock)

    def test_original_is_paper_only_and_available_after_receipt(self):
        result = self.store().persist(self.item)
        self.assertEqual(result.status, "original")
        record = result.record
        self.assertEqual(record.mode, "paper")
        self.assertFalse(record.live_eligible)
        self.assertGreaterEqual(record.provenance.available_at, record.provenance.fetched_at)
        self.assertEqual(record.provenance.first_seen_at, record.provenance.available_at)

    def test_redelivery_keeps_first_seen_and_available_and_logs_delivery(self):
        store = self.store()
        first = store.persist(self.item).record
        self.clock.now = NOW + timedelta(hours=2)
        again = store.persist(self.item)
        self.assertEqual(again.status, "redelivery")
        self.assertEqual(again.record.provenance.first_seen_at, first.provenance.first_seen_at)
        self.assertEqual(again.record.provenance.available_at, first.provenance.available_at)
        deliveries = list(Path(self.state, "news-paper", "observations", first.identity,
                               "deliveries.jsonl").read_text(encoding="utf-8").splitlines())
        self.assertEqual(len(deliveries), 1)

    def test_restart_rereads_the_same_first_seen_time(self):
        first = self.store().persist(self.item).record
        self.clock.now = NOW + timedelta(days=3)
        restarted = self.store().persist(self.item)
        self.assertEqual(restarted.status, "redelivery")
        self.assertEqual(restarted.record.provenance.first_seen_at, first.provenance.first_seen_at)

    def test_changed_content_is_a_revision_and_original_is_kept(self):
        store = self.store()
        original = store.persist(self.item).record
        changed = self.item.__class__(**{**self.item.__dict__, "content_digest": "f" * 64, "title": "Revised"})
        self.clock.now = NOW + timedelta(minutes=10)
        revised = store.persist(changed)
        self.assertEqual(revised.status, "revision")
        self.assertEqual(revised.record.provenance.supersedes, original.version_id)
        self.assertEqual(len(store.versions(original.identity)), 2)

    def test_correction_is_invisible_before_its_available_time(self):
        store = self.store()
        original = store.persist(self.item).record
        corrected = self.item.__class__(**{**self.item.__dict__, "content_digest": "e" * 64, "title": "Corrected"})
        self.clock.now = NOW + timedelta(hours=1)
        result = store.persist(corrected, correction_of=original.version_id)
        self.assertEqual(result.status, "correction")
        before = store.effective_version(original.identity, NOW + timedelta(minutes=30))
        self.assertEqual(before.version_id, original.version_id)
        after = store.effective_version(original.identity, NOW + timedelta(hours=2))
        self.assertEqual(after.title, "Corrected")

    def test_correction_of_unknown_version_is_refused(self):
        with self.assertRaises(StoreError):
            self.store().persist(self.item, correction_of="missing:version")

    def test_ready_excludes_future_available_and_stale_origin(self):
        store = self.store()
        store.persist(self.item)
        self.assertEqual(len(store.ready(NOW + timedelta(seconds=6))), 1)
        self.assertEqual(store.ready(NOW), [])  # not yet available at receipt time
        self.assertEqual(store.ready(NOW + timedelta(days=3)), [])  # origin is now stale

    def test_quarantine_stores_code_and_source_only(self):
        store = self.store()
        path = store.quarantine(fixture_feed.Quarantine(SOURCE, "TIME_MISSING", 0))
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text)
        self.assertEqual(payload["code"], "TIME_MISSING")
        self.assertEqual(payload["mode"], "paper")
        self.assertFalse(payload["live_eligible"])
        self.assertNotIn("title", payload)
        self.assertEqual(store.quarantine_codes(), ["TIME_MISSING"])

    def test_writes_stay_under_observations_directory(self):
        self.store().persist(self.item)
        written = [p for p in self.state.rglob("*") if p.is_file()]
        self.assertTrue(written)
        for path in written:
            relative = path.relative_to(self.state).as_posix()
            self.assertTrue(relative.startswith("news-paper/observations/"), relative)

    def test_stored_record_has_no_full_body(self):
        self.store().persist(self.item)
        stored = [p for p in self.state.rglob("*.json")]
        for path in stored:
            self.assertNotIn("description", json.loads(path.read_text(encoding="utf-8")))


class DedupeTests(unittest.TestCase):
    def _record(self, source, item_id, url, digest, title, when=NOW):
        item = collectors_item(source, item_id, url, digest, title, when)
        with tempfile.TemporaryDirectory() as tmp:
            store = ObservationStore(Path(tmp), Clock(when + timedelta(seconds=1)))
            return store.persist(item).record

    def test_relations_and_held_similarity(self):
        base = self._record(SOURCE, "a", "https://news.example.test/x/1", "1" * 64, "Central bank holds rates today")
        dup = self._record(SOURCE, "a", "https://news.example.test/x/1", "2" * 64, "Central bank holds rates today")
        reprint = self._record("other_src", "b", "https://other.example.test/y", "1" * 64, "Central bank holds")
        similar = self._record(SOURCE, "c", "https://news.example.test/x/2", "3" * 64,
                               "Central bank holds rates today!")
        unrelated = self._record(SOURCE, "d", "https://news.example.test/x/4", "4" * 64, "Oil inventories fall")
        kinds = {(r.kind, r.left, r.right) for r in dedupe.relate([base, dup, reprint, similar, unrelated])}
        self.assertIn(dedupe.KIND_DUPLICATE, {k for k, _, _ in kinds})
        self.assertIn(dedupe.KIND_REPRINT, {k for k, _, _ in kinds})
        held = dedupe.held_ids(dedupe.relate([base, similar, unrelated]))
        self.assertEqual(held, {base.version_id, similar.version_id})
        self.assertNotIn(unrelated.version_id, held)

    def test_reprint_and_duplicate_count_once_as_evidence(self):
        a = self._record(SOURCE, "a", "https://news.example.test/x/1", "1" * 64, "Headline one")
        b = self._record("other_src", "b", "https://other.example.test/y", "1" * 64, "Headline one")
        c = self._record(SOURCE, "c", "https://news.example.test/x/9", "9" * 64, "Different story")
        self.assertEqual(dedupe.independent_evidence_count([a, b, c]), 2)


def collectors_item(source, item_id, url, digest, title, when):
    return fixture_feed.CollectedItem(
        source_id=source, source_item_id=item_id, canonical_url=url, title=title,
        excerpt="", content_digest=digest,
        origin_published_at=when - timedelta(minutes=5), fetched_at=when)


class MarketAdapterTests(unittest.TestCase):
    def test_existing_quote_shape_is_refused_with_field_names_only(self):
        legacy = {"symbol": "USD_JPY", "ts": 1798708800.0, "bid": "150.1", "ask": "150.2",
                  "bid_size": "1", "ask_size": "1", "tradeable": True, "source": "legacy_feed"}
        result = adapt_quote(legacy)
        self.assertIsInstance(result, AdapterRejection)
        self.assertEqual(result.code, market_observation_adapter.CODE_PROVENANCE_INCOMPLETE)
        self.assertIn("observation_id", result.missing)
        self.assertIn("size_semantics", result.missing)
        self.assertNotIn("150.1", repr(result))

    def _complete(self, **overrides):
        base = {
            "observation_id": "obs-1", "source": "fixture_quotes", "asset": "USD_JPY",
            "ingest_sequence": 7, "market_event_at": NOW, "market_available_at": NOW + timedelta(seconds=1),
            "available_at": NOW + timedelta(seconds=2), "bid": "150.10", "ask": "150.20",
            "bid_size": "1000", "ask_size": "900", "size_semantics": "executable",
            "tradeable": True, "realtime": True, "session_id": "fx-session-1",
            "calendar_version": "fixture-calendar-v1", "provenance_class": "test_fixture",
            "instrument_constraints_version": "fixture-instr-v1",
        }
        base.update(overrides)
        return base

    def test_complete_fixture_quote_is_adapted_with_stable_identity(self):
        first = adapt_quote(self._complete())
        again = adapt_quote(self._complete())
        self.assertEqual(first.quote_digest, again.quote_digest)
        changed = adapt_quote(self._complete(bid="150.11"))
        self.assertNotEqual(first.quote_digest, changed.quote_digest)

    def test_missing_or_invalid_provenance_is_not_inferred(self):
        naive = adapt_quote(self._complete(available_at=(NOW + timedelta(seconds=2)).replace(tzinfo=None)))
        self.assertIsInstance(naive, AdapterRejection)
        self.assertEqual(naive.code, market_observation_adapter.CODE_FIELD_INVALID)
        bad_semantics = adapt_quote(self._complete(size_semantics="guess"))
        self.assertIsInstance(bad_semantics, AdapterRejection)


if __name__ == "__main__":
    unittest.main()
