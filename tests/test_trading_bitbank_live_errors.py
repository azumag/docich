"""Error classification: only Ambiguous / OrderNotFound come out of a failed read (Issue #3623, L3)."""
import json

import pytest

from bitbank_live_support import SENTINEL_KEY
from docich.trading.exchanges.bitbank_live import errors as er
from docich.trading.live.exchange import ExchangeAmbiguous, ExchangeRejected, OrderNotFound


def body(code):
    return json.dumps({"success": 0, "data": {"code": code}})


@pytest.mark.parametrize("code,kind", [
    (20001, "auth"), (20002, "auth"), (20003, "auth"), (20005, "auth"), (20011, "auth"), (20026, "auth"),
    (20004, "clock"), (20033, "clock"), (20039, "clock"),
    (10009, "rate"),
    (10001, "network"), (10008, "network"), (70001, "network"), (70012, "network"),
    (50003, "account"), (50008, "account"),
    (10000, "request"), (30001, "request"), (40013, "request"), (40001, "request"), (50011, "request"),
    (60001, "request"), (60002, "request"), (99999, "request")])
def test_bitbank_codes_map_to_ambiguous_with_a_kind(code, kind):
    error = er.classify(endpoint="assets", body=body(code))
    assert type(error) is ExchangeAmbiguous and error.health_kind == kind


def test_sixty_thousand_two_is_never_treated_as_a_funds_hold():
    assert er.kind_for_code(60002, "order") == "request" and er.kind_for_code(60001, "order") == "request"


def test_only_50009_on_an_order_lookup_is_order_not_found():
    assert type(er.classify(endpoint="order", body=body(50009))) is OrderNotFound
    for endpoint in ("assets", "active_orders", "trade_history", "pairs", "depth"):
        assert type(er.classify(endpoint=endpoint, body=body(50009))) is ExchangeAmbiguous
    assert type(er.classify(endpoint="order", body=body(40013))) is ExchangeAmbiguous   # not "not found"


@pytest.mark.parametrize("status,kind", [(429, "rate"), (401, "auth"), (403, "auth"), (500, "network"),
                                         (502, "network"), (503, "network"), (504, "network"), (408, "network")])
def test_http_status_without_a_bitbank_code(status, kind):
    error = er.classify(endpoint="assets", http_status=status, body="<html>bad gateway</html>")
    assert type(error) is ExchangeAmbiguous and error.health_kind == kind


@pytest.mark.parametrize("names,kind", [
    (("RateLimitExceeded", "NetworkError"), "rate"), (("InvalidNonce", "NetworkError"), "clock"),
    (("AuthenticationError", "ExchangeError"), "auth"), (("PermissionDenied", "AuthenticationError"), "auth"),
    (("RequestTimeout", "NetworkError"), "network"), (("ExchangeNotAvailable",), "network"),
    (("BadResponse",), "network"), (("SomethingNobodyKnows",), "network"), ((), "network")])
def test_exception_class_names_when_there_is_nothing_else(names, kind):
    error = er.classify(endpoint="assets", exc_names=names)
    assert type(error) is ExchangeAmbiguous and error.health_kind == kind


def test_a_bitbank_code_wins_over_the_http_status_and_the_class_name():
    error = er.classify(endpoint="assets", http_status=500, body=body(20001), exc_names=("RequestTimeout",))
    assert error.health_kind == "auth"


def test_success_given_as_a_string_is_understood():
    assert er.parse_error_body(json.dumps({"success": "0", "data": {"code": 20001}})) == 20001
    assert er.parse_error_body(json.dumps({"success": "1", "data": {"code": 20001}})) is None


def test_a_success_body_or_garbage_is_not_mistaken_for_an_error_code():
    assert er.parse_error_body(json.dumps({"success": 1, "data": {"code": 20001}})) is None
    assert er.parse_error_body("not json") is None and er.parse_error_body(None) is None
    assert er.parse_error_body(json.dumps({"success": 0, "data": {"code": True}})) is None
    assert er.parse_error_body(json.dumps({"success": 0, "data": "x"})) is None


def test_never_a_rejection_and_never_a_body_in_the_message():
    leaking = json.dumps({"success": 0, "data": {"code": 20001, "secret": SENTINEL_KEY}})
    for endpoint in ("order", "assets"):
        for code in (20001, 50009, 60001, 60002, 99999):
            error = er.classify(endpoint=endpoint, body=leaking.replace("20001", str(code)), http_status=400,
                                exc_names=("AuthenticationError",))
            assert not isinstance(error, ExchangeRejected)
            assert SENTINEL_KEY not in str(error) and "secret" not in str(error)
