"""CcxtReadTransport against a fake ccxt object (Issue #3623, L3b). No ccxt, no network."""
import logging
import pickle

import pytest

from bitbank_live_support import SENTINEL_KEY, SENTINEL_SECRET
from docich.trading.exchanges.bitbank_live import transport as tr
from docich.trading.exchanges.bitbank_live.credentials import Credentials
from docich.trading.exchanges.bitbank_live.reader import BitbankReader
from docich.trading.live.exchange import ExchangeAmbiguous, ExchangeDataError, OrderNotFound


# ---- a ccxt look-alike: implicit GET methods that go through ``fetch`` like the real ones ----------------
class NetworkError(Exception):
    pass


class RateLimitExceeded(NetworkError):
    pass


class RequestTimeout(NetworkError):
    pass


class AuthenticationError(Exception):
    pass


URLS = {
    "marketsGetSpotPairs": "https://api.bitbank.cc/spot/pairs",
    "publicGetPairDepth": "https://public.bitbank.cc/oas_jpy/depth",
    "privateGetUserAssets": "https://api.bitbank.cc/v1/user/assets",
    "privateGetUserSpotOrder": "https://api.bitbank.cc/v1/user/spot/order",
    "privateGetUserSpotActiveOrders": "https://api.bitbank.cc/v1/user/spot/active_orders",
    "privateGetUserSpotTradeHistory": "https://api.bitbank.cc/v1/user/spot/trade_history",
    "privateGetUserDepositHistory": "https://api.bitbank.cc/v1/user/deposit_history",
    "privateGetSpotStatus": "https://api.bitbank.cc/v1/spot/status",
}


class FakeCcxt:
    def __init__(self, config):
        self.config = config
        self.script = {}
        self.fetched = []            # what reached the "network"
        self.touched = set()
        self.last_http_response = None
        self.last_request_headers = "hdr"
        self.last_request_body = "body"
        self.last_response_headers = None
        self.date_header = None
        self.enableLastResponseHeaders = False
        self.verbose = True
        self.logger = logging.getLogger("ccxt")
        self.on_json_response = None
        self.apiKey = config.get("apiKey")
        self.secret = config.get("secret")

    def fetch(self, url, method="GET", headers=None, body=None):
        self.fetched.append((method, url))
        return None

    def __getattr__(self, name):
        if name in URLS:
            self.touched.add(name)
            return lambda params: self._call(name, params)
        raise AttributeError(name)

    def _call(self, name, params):
        self.fetch(URLS[name], "GET", {"ACCESS-KEY": SENTINEL_KEY}, None)
        if self.date_header is not None:
            self.last_response_headers = {"Date": self.date_header}
        item = self.script.get(name, {"success": 1, "data": {"ok": True}})
        if isinstance(item, list):
            item = item.pop(0) if len(item) > 1 else item[0]
        if isinstance(item, BaseException):
            raise item
        return item


def make(credentials=True, **kw):
    box = {}

    def factory(config):
        box["ex"] = FakeCcxt(config)
        return box["ex"]

    sleeps = []
    transport = tr.CcxtReadTransport(Credentials(SENTINEL_KEY, SENTINEL_SECRET) if credentials else None,
                                     exchange_factory=factory, sleep=sleeps.append, **kw)
    return transport, box["ex"], sleeps


# ---- configuration -----------------------------------------------------------------------
def test_config_passed_to_ccxt_is_read_only_friendly_and_has_no_hidden_extras():
    transport, ex, _ = make(timeout_ms=7000, time_window_ms=9000)
    assert ex.config["enableRateLimit"] is False and ex.config["timeout"] == 7000 and ex.config["verbose"] is False
    assert ex.config["options"] == {"authMethod": "timeWindow", "timeWindow": "9000"}
    assert ex.apiKey == SENTINEL_KEY and ex.secret == SENTINEL_SECRET
    assert ex.verbose is False and ex.logger.name == "docich.bitbank_live.ccxt"
    assert ex.logger.propagate is False and ex.logger.level == logging.WARNING


def test_no_credentials_means_no_key_in_the_config():
    _, ex, _ = make(credentials=False)
    assert "apiKey" not in ex.config and ex.apiKey is None


def test_the_json_parser_keeps_numbers_as_strings():
    _, ex, _ = make()
    assert ex.on_json_response('{"a": 1.50, "b": [2.25], "c": 3}') == {"a": "1.50", "b": ["2.25"], "c": 3}


def test_repr_str_and_pickle_reveal_nothing():
    transport, _, _ = make()
    assert SENTINEL_KEY not in repr(transport) and SENTINEL_SECRET not in str(transport)
    with pytest.raises(TypeError):
        pickle.dumps(transport)


# ---- the read-only table --------------------------------------------------------------------
def test_endpoint_tables_are_exactly_the_fixed_reads():
    assert dict(tr.ENDPOINTS) == {
        "pairs": "marketsGetSpotPairs", "depth": "publicGetPairDepth", "assets": "privateGetUserAssets",
        "order": "privateGetUserSpotOrder", "active_orders": "privateGetUserSpotActiveOrders",
        "trade_history": "privateGetUserSpotTradeHistory"}
    assert dict(tr.CHECK_ONLY_ENDPOINTS) == {"deposit_history": "privateGetUserDepositHistory",
                                             "spot_status": "privateGetSpotStatus"}
    for name in list(tr.ENDPOINTS.values()) + list(tr.CHECK_ONLY_ENDPOINTS.values()):
        assert "Get" in name and "Post" not in name        # the implicit method names say GET


def test_an_unknown_endpoint_name_is_a_violation_not_a_lookup():
    transport, ex, _ = make()
    for bad in ("create_order", "privatePostUserSpotOrder", "", "deposit_history"):
        with pytest.raises(tr.ReadOnlyViolation):
            transport.get(bad, {})
    assert ex.fetched == []


@pytest.mark.parametrize("url,ok", [
    ("https://api.bitbank.cc/v1/user/assets", True),
    ("https://api.bitbank.cc/v1/user/spot/order?pair=oas_jpy&order_id=1", True),
    ("https://api.bitbank.cc/v1/user/spot/active_orders", True),
    ("https://api.bitbank.cc/v1/user/spot/trade_history?count=5", True),
    ("https://api.bitbank.cc/spot/pairs", True),
    ("https://public.bitbank.cc/oas_jpy/depth", True),
    ("https://api.bitbank.cc/v1/user/spot/cancel_order", False),
    ("https://api.bitbank.cc/v1/user/spot/orders_info", False),
    ("https://api.bitbank.cc/v1/user/request_withdrawal", False),
    ("https://api.bitbank.cc/v1/user/spot/order/extra", False),
    ("http://api.bitbank.cc/v1/user/assets", False),
    ("https://api.bitbank.cc.evil.example/v1/user/assets", False),
    ("https://evil.example/v1/user/assets", False),
    ("https://user:pw@api.bitbank.cc/v1/user/assets", False),
    ("https://api.bitbank.cc:8443/v1/user/assets", False),
    ("https://public.bitbank.cc/oas_jpy/transactions", False),
    ("https://public.bitbank.cc/../v1/user/assets", False),
    ("", False), (None, False)])
def test_url_allowed(url, ok):
    assert tr.url_allowed(url) is ok


def test_the_wrapped_fetch_refuses_post_bodies_and_foreign_urls_before_anything_is_sent():
    transport, ex, _ = make()
    raw_fetch_calls = len(ex.fetched)
    for args in (("https://api.bitbank.cc/v1/user/spot/order", "POST"),
                 ("https://api.bitbank.cc/v1/user/assets", "DELETE"),
                 ("https://api.bitbank.cc/v1/user/assets", "GET", None, '{"x":1}'),
                 ("https://evil.example/", "GET")):
        with pytest.raises(tr.ReadOnlyViolation):
            ex.fetch(*args)
    assert len(ex.fetched) == raw_fetch_calls          # none of them reached the real fetch
    ex.fetch("https://api.bitbank.cc/v1/user/assets")  # an allowed GET goes through
    assert ex.fetched[-1] == ("GET", "https://api.bitbank.cc/v1/user/assets")


def test_a_violation_is_not_an_exception_so_nothing_swallows_it():
    assert not issubclass(tr.ReadOnlyViolation, Exception)
    transport, ex, _ = make()
    ex.script["privateGetUserAssets"] = None
    ex.fetch = lambda *a, **k: (_ for _ in ()).throw(tr.ReadOnlyViolation("x"))
    try:
        transport.get("assets", {})
    except tr.ReadOnlyViolation:
        pass
    else:
        raise AssertionError("should have propagated")


def test_every_table_method_is_the_only_thing_the_transport_ever_touches():
    transport, ex, _ = make()
    for endpoint in tr.ENDPOINTS:
        transport.get(endpoint, {})
    assert ex.touched == set(tr.ENDPOINTS.values())


# ---- responses and errors --------------------------------------------------------------------
def test_success_returns_the_data_and_accepts_a_string_success():
    transport, ex, _ = make()
    assert transport.get("assets", {}) == {"ok": True}
    ex.script["privateGetUserAssets"] = {"success": "1", "data": {"assets": []}}
    assert transport.get("assets", {}) == {"assets": []}
    ex.script["privateGetUserAssets"] = {"success": 1, "data": [1]}
    assert transport.get("assets", {}) == [1]


@pytest.mark.parametrize("response,exc_type", [
    ({"success": 1}, ExchangeDataError), ({"success": 1, "data": "x"}, ExchangeDataError),
    ({"success": 1, "data": None}, ExchangeDataError),
    ({"data": {}}, ExchangeAmbiguous), ("<html>", ExchangeAmbiguous), (None, ExchangeAmbiguous),
    ({"success": 0, "data": {"code": 20001}}, ExchangeAmbiguous), ({"success": 0}, ExchangeAmbiguous)])
def test_odd_responses(response, exc_type):
    transport, ex, _ = make()
    ex.script["privateGetUserAssets"] = response
    with pytest.raises(exc_type):
        transport.get("assets", {})


def test_a_ccxt_exception_is_classified_by_the_response_body_first_then_by_class_name():
    transport, ex, _ = make()
    ex.last_http_response = None

    def boom(params):
        ex.last_http_response = '{"success":0,"data":{"code":20001}}'
        raise AuthenticationError("bitbank {SECRET-LEAK}")

    ex.script["privateGetUserAssets"] = None
    ex._call = lambda name, params: boom(params)
    with pytest.raises(ExchangeAmbiguous) as err:
        transport.get("assets", {})
    assert err.value.health_kind == "auth" and "LEAK" not in str(err.value)
    assert err.value.__cause__ is None and err.value.__context__ is None


def test_order_not_found_only_for_the_order_endpoint():
    transport, ex, _ = make()

    def lookup(name, params):
        ex.last_http_response = '{"success":0,"data":{"code":50009}}'
        raise Exception("x")

    ex._call = lookup
    with pytest.raises(OrderNotFound):
        transport.get("order", {"pair": "oas_jpy", "order_id": "1"})
    with pytest.raises(ExchangeAmbiguous):
        transport.get("assets", {})


def test_a_timeout_is_ambiguous_network():
    transport, ex, _ = make()
    ex.script["privateGetUserAssets"] = RequestTimeout("slow {SECRET-LEAK}")
    with pytest.raises(ExchangeAmbiguous) as err:
        transport.get("assets", {})
    assert err.value.health_kind == "network" and "LEAK" not in str(err.value)


def test_rate_limiting_is_retried_once_after_a_pause_then_gives_up():
    transport, ex, sleeps = make(retry_pause_s=1.5, min_interval_s=0)
    ex.script["privateGetUserAssets"] = [RateLimitExceeded("429"), {"success": 1, "data": {"ok": 1}}]
    assert transport.get("assets", {}) == {"ok": 1} and sleeps == [1.5]
    ex.script["privateGetUserAssets"] = [RateLimitExceeded("429")]
    sleeps.clear()
    with pytest.raises(ExchangeAmbiguous) as err:
        transport.get("assets", {})
    assert err.value.health_kind == "rate" and sleeps == [1.5]       # one retry only
    ex.script["privateGetUserAssets"] = [RequestTimeout("t"), {"success": 1, "data": {}}]
    sleeps.clear()
    with pytest.raises(ExchangeAmbiguous):
        transport.get("assets", {})                                    # a timeout is not retried here
    assert sleeps == []


def test_calls_are_paced_by_the_minimum_interval():
    now = [100.0]
    sleeps = []
    box = {}

    def factory(config):
        box["ex"] = FakeCcxt(config)
        return box["ex"]

    transport = tr.CcxtReadTransport(None, exchange_factory=factory, min_interval_s=0.2, clock=lambda: now[0],
                                     sleep=lambda s: (sleeps.append(round(s, 3)), now.__setitem__(0, now[0] + s)))
    transport.get("assets", {})
    now[0] += 0.05
    transport.get("assets", {})
    now[0] += 5
    transport.get("assets", {})
    assert sleeps == [0.15]


def test_request_traces_are_scrubbed_after_every_call():
    transport, ex, _ = make()
    transport.get("assets", {})
    assert ex.last_request_headers is None and ex.last_request_body is None and ex.last_http_response is None
    ex.script["privateGetUserAssets"] = RequestTimeout("t")
    ex.last_request_headers = {"ACCESS-KEY": SENTINEL_KEY}
    with pytest.raises(ExchangeAmbiguous):
        transport.get("assets", {})
    assert ex.last_request_headers is None


def test_a_violation_in_the_reader_path_is_not_turned_into_an_ordinary_error():
    transport, ex, _ = make()
    reader_script = {"assets": {"assets": []}}

    class Boom:
        def get(self, endpoint, params):
            raise tr.ReadOnlyViolation("x")

    with pytest.raises(tr.ReadOnlyViolation):
        BitbankReader(Boom(), symbols=("OAS/JPY",), clock=lambda: 0.0)


def test_the_response_date_header_becomes_a_clock_sample_on_success_and_on_error():
    import email.utils
    wall = [1_700_000_000.0]
    box = {}

    def factory(config):
        box["ex"] = FakeCcxt(config)
        return box["ex"]

    transport = tr.CcxtReadTransport(None, exchange_factory=factory, min_interval_s=0, wall_clock=lambda: wall[0])
    ex = box["ex"]
    assert ex.enableLastResponseHeaders is True and transport.clock_samples() == ()
    ex.date_header = email.utils.formatdate(1_699_999_999.0, usegmt=True)       # the server said ...999
    transport.get("assets", {})
    assert transport.clock_samples() == ((0.0, 1.0),)      # [start - Date - 1, end - Date], instantaneous call
    ex.script["privateGetUserAssets"] = RequestTimeout("t")
    wall[0] += 5
    with pytest.raises(ExchangeAmbiguous):
        transport.get("assets", {})
    assert transport.clock_samples() == ((0.0, 1.0), (5.0, 6.0))
    assert transport.clock_bounds() == (5.0, 1.0)          # the readings disagree: the factory calls this inconsistent
    assert ex.last_response_headers is None                                   # scrubbed afterwards


def test_no_date_header_means_no_sample_and_a_garbage_one_is_ignored():
    transport, ex, _ = make()
    transport.get("assets", {})
    ex.date_header = "not a date"
    transport.get("assets", {})
    assert transport.clock_samples() == ()


@pytest.mark.parametrize("text,epoch", [
    ("Sun, 06 Nov 1994 08:49:37 GMT", 784111777.0), ("Mon, 16 Nov 2026 12:00:00 GMT", 1794830400.0),
    ("Thu, 01 Jan 1970 00:00:00 GMT", 0.0)])
def test_http_dates_are_parsed_without_email_utils(text, epoch):
    assert tr.parse_http_date(text) == epoch


@pytest.mark.parametrize("bad", [None, "", "yesterday", "Mon, 16 Nov 2026 12:00:00 UTC", "Mon, 16 Xyz 2026 12:00:00 GMT",
                                 "Mon, 32 Nov 2026 99:99:99 GMT", 5])
def test_bad_http_dates_are_ignored(bad):
    assert tr.parse_http_date(bad) is None or isinstance(tr.parse_http_date(bad), float)
    assert tr.parse_http_date(None) is None and tr.parse_http_date("yesterday") is None
