"""The real ccxt 4.5.78 under CcxtReadTransport, with only the HTTP layer replaced (Issue #3623, L3b).

Needs ``pip install -r requirements-trading.txt``; skipped otherwise.  No network is used: ``session.request`` is
a fake that returns canned bodies.  This is the check that the config keys, the implicit method names, the signing
headers, the float-free parser and the error classes really behave the way the transport assumes.
"""
import json
import logging

import pytest

ccxt = pytest.importorskip("ccxt")

from bitbank_live_support import SENTINEL_KEY, SENTINEL_SECRET  # noqa: E402
from docich.trading.exchanges.bitbank_live import transport as tr  # noqa: E402
from docich.trading.exchanges.bitbank_live.credentials import Credentials  # noqa: E402
from docich.trading.live.exchange import ExchangeAmbiguous, OrderNotFound  # noqa: E402
from test_trading_live_support import block_network  # noqa: E402


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


class FakeResponse:
    def __init__(self, text, status=200, reason="OK"):
        self.text, self.status_code, self.reason, self.headers = text, status, reason, {"content-type": "application/json"}
        self.encoding = "utf-8"

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.exceptions.HTTPError(f"{self.status_code}", response=self)


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = outcomes if isinstance(outcomes, list) else [outcomes]
        self.requests = []
        self.headers = {}
        self.cookies = type("C", (), {"clear": lambda self: None})()

    def request(self, method, url, data=None, headers=None, timeout=None, proxies=None, verify=None, files=None):
        self.requests.append({"method": method, "url": url, "headers": dict(headers or {}), "body": data})
        item = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(item, BaseException):
            raise item
        return item


def make(outcomes, **kw):
    sleeps = []
    transport = tr.CcxtReadTransport(Credentials(SENTINEL_KEY, SENTINEL_SECRET), sleep=sleeps.append,
                                     min_interval_s=0, **kw)
    session = FakeSession(outcomes)
    transport._exchange.session = session
    return transport, session, sleeps


def body(data):
    return FakeResponse(json.dumps({"success": 1, "data": data}))


def test_the_config_keys_really_take_effect_on_the_real_object():
    transport, _, _ = make(body({}), timeout_ms=7000, time_window_ms=9000)
    ex = transport._exchange
    assert type(ex).__name__ == "bitbank" and ex.enableRateLimit is False and ex.timeout == 7000
    assert ex.verbose is False and ex.options["authMethod"] == "timeWindow" and ex.options["timeWindow"] == "9000"
    assert ex.apiKey == SENTINEL_KEY and ex.logger.name == "docich.bitbank_live.ccxt"


def test_every_table_endpoint_is_a_get_to_an_allowed_url_and_private_ones_are_signed():
    names = dict(tr.ENDPOINTS) | dict(tr.CHECK_ONLY_ENDPOINTS)
    for logical, method_name in names.items():
        params = {"pair": "oas_jpy"} if logical in ("depth", "order", "active_orders", "trade_history") else {}
        if logical == "order":
            params["order_id"] = "1"
        transport = tr.CcxtReadTransport(Credentials(SENTINEL_KEY, SENTINEL_SECRET), min_interval_s=0,
                                         endpoints=names)
        session = FakeSession(body({"x": "1"}))
        transport._exchange.session = session
        assert transport.get(logical, params) == {"x": "1"}
        sent = session.requests[0]
        assert sent["method"] == "GET" and not sent["body"] and tr.url_allowed(sent["url"]), (logical, sent["url"])
        if logical not in ("pairs", "depth"):
            headers = sent["headers"]
            assert headers["ACCESS-KEY"] == SENTINEL_KEY and len(headers["ACCESS-SIGNATURE"]) == 64
            assert headers["ACCESS-TIME-WINDOW"] == "10000" and headers["ACCESS-REQUEST-TIME"].isdigit()


def test_query_parameters_are_sent_as_a_query_string_on_a_get():
    transport, session, _ = make(body({"orders": []}))
    transport.get("active_orders", {"pair": "oas_jpy", "count": 100})
    url = session.requests[0]["url"]
    assert url.startswith("https://api.bitbank.cc/v1/user/spot/active_orders?") and "pair=oas_jpy" in url \
        and "count=100" in url


def test_numbers_in_the_response_stay_strings_even_when_the_exchange_sends_json_numbers():
    transport, _, _ = make(FakeResponse('{"success":1,"data":{"amount":0.00100000,"n":3,"big":1234567.891}}'))
    data = transport.get("assets", {})
    assert data == {"amount": "0.00100000", "n": 3, "big": "1234567.891"}


def test_error_bodies_become_the_right_l2_exceptions():
    cases = [
        (FakeResponse('{"success":0,"data":{"code":20001}}'), "auth"),
        (FakeResponse('{"success":0,"data":{"code":20033}}'), "clock"),
        (FakeResponse('{"success":0,"data":{"code":10009}}', status=429, reason="Too Many Requests"), "rate"),
        (FakeResponse("<html>bad gateway</html>", status=502, reason="Bad Gateway"), "network"),
    ]
    for response, kind in cases:
        transport, _, _ = make(response)
        with pytest.raises(ExchangeAmbiguous) as err:
            transport.get("assets", {})
        assert err.value.health_kind == kind, (kind, str(err.value))
        assert SENTINEL_KEY not in str(err.value) and SENTINEL_SECRET not in str(err.value)
        assert err.value.__cause__ is None


def test_a_missing_order_is_order_not_found_and_only_for_the_order_endpoint():
    transport, _, _ = make(FakeResponse('{"success":0,"data":{"code":50009}}'))
    with pytest.raises(OrderNotFound):
        transport.get("order", {"pair": "oas_jpy", "order_id": "1"})
    transport, _, _ = make(FakeResponse('{"success":0,"data":{"code":50009}}'))
    with pytest.raises(ExchangeAmbiguous):
        transport.get("assets", {})


def test_a_timeout_from_the_http_layer_is_ambiguous_network():
    import requests
    transport, _, _ = make(requests.exceptions.Timeout("slow"))
    with pytest.raises(ExchangeAmbiguous) as err:
        transport.get("assets", {})
    assert err.value.health_kind == "network"


def test_a_rate_limit_is_retried_once_and_then_succeeds():
    transport, session, sleeps = make([FakeResponse('{"success":0,"data":{"code":10009}}', status=429),
                                       body({"assets": []})], retry_pause_s=0.5)
    assert transport.get("assets", {}) == {"assets": []} and sleeps == [0.5] and len(session.requests) == 2


def test_the_wrapped_fetch_blocks_a_post_on_the_real_object_before_the_http_layer():
    transport, session, _ = make(body({}))
    with pytest.raises(tr.ReadOnlyViolation):
        transport._exchange.privatePostUserSpotOrder({"pair": "oas_jpy", "amount": "1", "side": "buy",
                                                      "type": "market"})
    assert session.requests == []
    for name in ("privatePostUserSpotCancelOrder", "privatePostUserRequestWithdrawal"):
        with pytest.raises(tr.ReadOnlyViolation):
            getattr(transport._exchange, name)({"pair": "oas_jpy"})
    assert session.requests == []


def test_debug_logging_never_carries_the_key_or_the_signature(caplog):
    caplog.set_level(logging.DEBUG)
    for name in ("ccxt", "docich.bitbank_live.ccxt", ""):
        logging.getLogger(name).setLevel(logging.DEBUG)
    transport, session, _ = make(body({"assets": []}))
    transport._exchange.logger.setLevel(logging.WARNING)     # the transport's own setting must hold
    transport.get("assets", {})
    signature = session.requests[0]["headers"]["ACCESS-SIGNATURE"]
    assert SENTINEL_KEY not in caplog.text and SENTINEL_SECRET not in caplog.text and signature not in caplog.text


def test_traces_on_the_real_object_are_cleared_after_a_call():
    transport, _, _ = make(body({}))
    transport.get("assets", {})
    ex = transport._exchange
    assert ex.last_http_response is None and getattr(ex, "last_request_headers", None) is None
