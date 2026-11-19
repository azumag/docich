"""The isolated pre-E bitbank spot sender (Issue #3623), against synthetic HTTPS only.

No test in this file opens a socket: the module connection factory is replaced, and the LIVE
no-network helper forbids a real ``socket.connect``/``create_connection`` as a second guard.
"""
from __future__ import annotations

import ast
import hashlib
import hmac
import json
import pickle
import re
from decimal import Decimal as D
from pathlib import Path

import pytest

from docich.trading.exchanges import bitbank_spot_sender as bitbank
from docich.trading.exchanges.bitbank_spot_sender import (API_HOST, CANCEL_PATH, MAX_RESPONSE_BYTES,
                                                          MIN_INTERVAL_S, ORDER_PATH, REJECT_CODES,
                                                          TIME_WINDOW_MS, BitbankSpotSender)
from docich.trading.live import executor as live_executor
from docich.trading.live.exchange import (CancelKind, ExchangeAmbiguous, ExchangeRejected, OrderRequest,
                                          OrderStatus)
from docich.trading.live_worker import runner as runner_module
from test_trading_live_support import Harness, Workspace, block_network

KEY = "SYNTHETIC-KEY-0000"
SECRET = "SYNTHETIC-SECRET-0000"
SYMBOLS = ("OAS/JPY", "XYM/JPY")
WALL_MS = 1_700_000_000_000
WALL_S = WALL_MS / 1000.0
T0 = 1_700_000_000_000
ORDER_BODY = b'{"amount":"10","pair":"oas_jpy","price":"100","side":"buy","type":"limit"}'
CANCEL_BODY = b'{"order_id":1000001,"pair":"oas_jpy"}'


class _Reply:
    def __init__(self, status: int = 200, body: bytes = b"", *, request_error=None, response_error=None,
                 read_error=None, headers=None, length=None) -> None:
        self.status, self.body = status, body
        self.request_error, self.response_error, self.read_error = request_error, response_error, read_error
        self.headers = dict(headers or {})
        self.length = length


class _Response:
    def __init__(self, reply: _Reply) -> None:
        self.status, self._reply = reply.status, reply
        self.length = reply.length
        self.read_limit: int | None = None
        self.closed = False

    def read(self, limit: int = -1) -> bytes:
        self.read_limit = limit
        if self._reply.read_error is not None:
            raise self._reply.read_error
        return self._reply.body if limit is None or limit < 0 else self._reply.body[:limit]

    def close(self) -> None:
        self.closed = True


class _Connection:
    def __init__(self, recorder: "_Recorder", index: int) -> None:
        self.recorder, self.reply = recorder, recorder.replies[index]
        self.response: _Response | None = None
        self.closed = False

    def request(self, method: str, path: str, body: bytes, headers: dict) -> None:
        self.recorder.requests.append({"method": method, "path": path, "body": body,
                                       "headers": dict(headers)})
        if self.reply.request_error is not None:
            raise self.reply.request_error

    def getresponse(self) -> _Response:
        if self.reply.response_error is not None:
            raise self.reply.response_error
        self.response = _Response(self.reply)
        self.recorder.responses.append(self.response)
        return self.response

    def close(self) -> None:
        self.closed = True
        self.recorder.connections_closed.append(self)


class _Recorder:
    def __init__(self) -> None:
        self.replies: list[_Reply] = []
        self.connections: list[tuple[str, float]] = []
        self.requests: list[dict] = []
        self.responses: list[_Response] = []
        self.connections_closed: list[_Connection] = []

    def open(self, host: str, timeout_s: float) -> _Connection:
        self.connections.append((host, timeout_s))
        return _Connection(self, len(self.connections) - 1)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(bitbank, "_open_connection", rec.open)
    return rec


@pytest.fixture
def ws():
    workspace = Workspace()
    yield workspace
    workspace.cleanup()


def _body(data, success=1):
    return json.dumps({"success": success, "data": data}, separators=(",", ":")).encode("utf-8")


def _ok(data, success=1):
    return _Reply(200, _body(data, success))


def _failure(code, *, success=0, status=200):
    return _Reply(status, _body({"code": code}, success))


def _raw(text, *, status=200):
    return _Reply(status, text.encode("utf-8"))


def _order(**over):
    row = {"order_id": 1000001, "pair": "oas_jpy", "side": "buy", "type": "limit",
           "start_amount": "10", "remaining_amount": "10", "executed_amount": "0",
           "price": "100", "average_price": "0", "ordered_at": T0, "status": "UNFILLED"}
    row.update(over)
    return row


def _req(**over):
    values = dict(symbol="OAS/JPY", side="buy", order_type="limit", amount=D("10"), price=D("100"),
                  client_order_id=None)
    values.update(over)
    return OrderRequest(**values)


def _sender(*, symbols=("OAS/JPY",), wall_clock=lambda: WALL_S, monotonic=lambda: 0.0,
            sleep=lambda _seconds: None):
    return BitbankSpotSender(KEY, SECRET, symbols, timeout_s=10.0, monotonic=monotonic,
                             sleep=sleep, wall_clock=wall_clock)


def _assert_signed(request, *, wall_ms=WALL_MS):
    headers = request["headers"]
    expected = hmac.new(SECRET.encode("utf-8"),
                        f"{wall_ms}{TIME_WINDOW_MS}".encode("ascii") + request["body"],
                        hashlib.sha256).hexdigest()
    assert headers["ACCESS-KEY"] == KEY
    assert headers["ACCESS-REQUEST-TIME"] == str(wall_ms)
    assert headers["ACCESS-TIME-WINDOW"] == str(TIME_WINDOW_MS)
    assert headers["ACCESS-SIGNATURE"] == expected
    assert headers["ACCESS-SIGNATURE"] == headers["ACCESS-SIGNATURE"].lower()


def _assert_one_post(recorder):
    assert len(recorder.connections) == 1
    assert len(recorder.requests) == 1
    assert len(recorder.connections_closed) == 1
    assert recorder.connections_closed[0].closed
    assert len(recorder.responses) == 1
    assert recorder.responses[0].closed


# ---- construction and local validation ---------------------------------------------------
def test_construction_is_local_and_redacts_the_key_pair(recorder):
    sender = _sender()
    assert recorder.connections == []
    assert KEY not in repr(sender) and SECRET not in repr(sender)
    assert KEY not in str(sender) and SECRET not in str(sender)
    with pytest.raises(TypeError):
        pickle.dumps(sender)


@pytest.mark.parametrize("config", [
    pytest.param({"key": "", "secret": SECRET, "symbols": ("OAS/JPY",)}, id="empty-key"),
    pytest.param({"key": KEY, "secret": "", "symbols": ("OAS/JPY",)}, id="empty-secret"),
    pytest.param({"key": "KEY\n", "secret": SECRET, "symbols": ("OAS/JPY",)}, id="control-key"),
    pytest.param({"key": KEY, "secret": "SECRET\n", "symbols": ("OAS/JPY",)}, id="control-secret"),
    pytest.param({"key": None, "secret": SECRET, "symbols": ("OAS/JPY",)}, id="non-text-key"),
    pytest.param({"key": KEY, "secret": SECRET, "symbols": ()}, id="no-symbols"),
    pytest.param({"key": KEY, "secret": SECRET, "symbols": "OAS/JPY"}, id="string-symbols"),
    pytest.param({"key": KEY, "secret": SECRET, "symbols": ("oas/jpy",)}, id="lower-symbol"),
    pytest.param({"key": KEY, "secret": SECRET, "symbols": ("OASJPY",)}, id="no-slash"),
    pytest.param({"key": KEY, "secret": SECRET, "symbols": ("OAS/JPY\n",)}, id="symbol-newline"),
    pytest.param({"key": KEY, "secret": SECRET, "symbols": ("OAS/JPY", "OAS/JPY")}, id="duplicate-symbol"),
    pytest.param({"key": KEY, "secret": SECRET, "symbols": ("OAS/JPY",), "timeout_s": 0}, id="zero-timeout"),
    pytest.param({"key": KEY, "secret": SECRET, "symbols": ("OAS/JPY",), "timeout_s": -1}, id="negative-timeout"),
    pytest.param({"key": KEY, "secret": SECRET, "symbols": ("OAS/JPY",), "timeout_s": 61}, id="too-long-timeout"),
    pytest.param({"key": KEY, "secret": SECRET, "symbols": ("OAS/JPY",), "timeout_s": float("inf")},
                 id="infinite-timeout"),
    pytest.param({"key": KEY, "secret": SECRET, "symbols": ("OAS/JPY",), "timeout_s": float("nan")},
                 id="nan-timeout"),
    pytest.param({"key": KEY, "secret": SECRET, "symbols": ("OAS/JPY",), "timeout_s": "10"}, id="text-timeout"),
    pytest.param({"key": KEY, "secret": SECRET, "symbols": ("OAS/JPY",), "monotonic": None},
                 id="no-monotonic"),
    pytest.param({"key": KEY, "secret": SECRET, "symbols": ("OAS/JPY",), "sleep": None}, id="no-sleep"),
    pytest.param({"key": KEY, "secret": SECRET, "symbols": ("OAS/JPY",), "wall_clock": None},
                 id="no-wall-clock"),
])
def test_constructor_rejects_invalid_configuration(recorder, config):
    args = dict(config)
    key, secret = args.pop("key"), args.pop("secret")
    with pytest.raises(ValueError):
        BitbankSpotSender(key, secret, **args)
    assert recorder.connections == []


INVALID_SUBMITS = [
    pytest.param({"symbol": "BTC/JPY"}, id="unconfigured-symbol"),
    pytest.param({"symbol": "oas/jpy"}, id="lower-symbol"),
    pytest.param({"side": "long"}, id="bad-side"),
    pytest.param({"order_type": "market"}, id="market-order"),
    pytest.param({"order_type": "LIMIT"}, id="upper-type"),
    pytest.param({"amount": D("0")}, id="zero-amount"),
    pytest.param({"amount": D("-1")}, id="negative-amount"),
    pytest.param({"amount": D("NaN")}, id="nan-amount"),
    pytest.param({"amount": D("Infinity")}, id="infinite-amount"),
    pytest.param({"amount": 10.0}, id="float-amount"),
    pytest.param({"amount": True}, id="bool-amount"),
    pytest.param({"amount": None}, id="missing-amount"),
    pytest.param({"price": None}, id="missing-price"),
    pytest.param({"price": D("0")}, id="zero-price"),
    pytest.param({"price": D("-1")}, id="negative-price"),
    pytest.param({"price": D("NaN")}, id="nan-price"),
    pytest.param({"price": 100.0}, id="float-price"),
    pytest.param({"client_order_id": ""}, id="empty-client-id"),
    pytest.param({"client_order_id": "synthetic"}, id="client-id"),
]


@pytest.mark.parametrize("over", INVALID_SUBMITS)
def test_invalid_submits_are_rejected_before_any_request(recorder, over):
    with pytest.raises(ExchangeRejected) as caught:
        _sender().submit_order(_req(**over))
    assert caught.value.code == "local_invalid"
    assert caught.value.detail == "local validation"
    assert KEY not in str(caught.value) and SECRET not in str(caught.value)
    assert recorder.connections == [] and recorder.requests == []


@pytest.mark.parametrize("order_id", [
    pytest.param("", id="empty"), pytest.param("0", id="zero"), pytest.param("00", id="leading-zero"),
    pytest.param("-1", id="negative"), pytest.param("+1", id="plus"), pytest.param("1.0", id="decimal"),
    pytest.param(" 1", id="leading-space"), pytest.param("1 ", id="trailing-space"),
    pytest.param("1\n", id="newline"), pytest.param("abc", id="text"), pytest.param("١", id="non-ascii"),
    pytest.param(1, id="int"), pytest.param(None, id="none"), pytest.param(True, id="bool"),
])
def test_invalid_cancels_are_rejected_before_any_request(recorder, order_id):
    with pytest.raises(ValueError):
        _sender().cancel_order("OAS/JPY", order_id)
    assert recorder.connections == [] and recorder.requests == []


def test_an_unconfigured_cancel_pair_is_rejected_before_any_request(recorder):
    with pytest.raises(ValueError):
        _sender().cancel_order("BTC/JPY", "1000001")
    assert recorder.connections == [] and recorder.requests == []


# ---- exact create request ----------------------------------------------------------------
def test_the_create_post_has_the_exact_host_path_body_and_signature(recorder):
    recorder.replies.append(_ok(_order()))
    sender = _sender()
    snapshot = sender.submit_order(_req(amount=D("10.000"), price=D("100.00")))
    assert recorder.connections == [(API_HOST, 10.0)]
    request = recorder.requests[0]
    assert request["method"] == "POST"
    assert request["path"] == ORDER_PATH
    assert request["body"] == ORDER_BODY
    assert request["headers"]["Content-Type"] == "application/json"
    _assert_signed(request)
    assert "ACCESS-NONCE" not in request["headers"]
    assert "nonce" not in json.loads(request["body"])
    # The signature covers the time, the window and the exact body -- not the path.
    with_path = hmac.new(SECRET.encode("utf-8"),
                         f"{WALL_MS}{TIME_WINDOW_MS}".encode("ascii") + ORDER_PATH.encode("ascii")
                         + request["body"], hashlib.sha256).hexdigest()
    assert request["headers"]["ACCESS-SIGNATURE"] != with_path
    assert snapshot.exchange_order_id == "1000001"
    assert snapshot.symbol == "OAS/JPY" and snapshot.side == "buy" and snapshot.order_type == "limit"
    assert snapshot.amount == D("10") and snapshot.price == D("100")
    assert snapshot.status is OrderStatus.UNFILLED
    assert snapshot.executed_amount == D("0") and snapshot.remaining_amount == D("10")
    _assert_one_post(recorder)


@pytest.mark.parametrize("success", [1, "1"])
def test_exact_int_or_string_success_envelopes_are_accepted(recorder, success):
    recorder.replies.append(_ok(_order(), success=success))
    assert _sender().submit_order(_req()).exchange_order_id == "1000001"
    _assert_one_post(recorder)


@pytest.mark.parametrize("position_side", [None, ""])
def test_an_empty_position_side_is_still_a_spot_order(recorder, position_side):
    recorder.replies.append(_ok(_order(position_side=position_side)))
    assert _sender().submit_order(_req()).exchange_order_id == "1000001"
    _assert_one_post(recorder)


@pytest.mark.parametrize("amount,price,amount_text,price_text", [
    (D("10.000"), D("100.00"), "10", "100"),
    (D("1.2300"), D("1E+2"), "1.23", "100"),
    (D("0.0001"), D("123.4500"), "0.0001", "123.45"),
])
def test_decimal_inputs_become_canonical_strings_without_floats(recorder, amount, price, amount_text, price_text):
    recorder.replies.append(_ok(_order(start_amount=format(amount, "f"), remaining_amount=format(amount, "f"),
                                       price=format(price, "f"))))
    snapshot = _sender().submit_order(_req(amount=amount, price=price))
    body = json.loads(recorder.requests[0]["body"])
    assert body["amount"] == amount_text and body["price"] == price_text
    assert type(body["amount"]) is str and type(body["price"]) is str
    assert not any(isinstance(value, float) for value in body.values())
    assert snapshot.amount == amount and snapshot.price == price
    _assert_one_post(recorder)


@pytest.mark.parametrize("status,executed,remaining,average", [
    ("PARTIALLY_FILLED", "4", "6", "100"),
    ("FULLY_FILLED", "10", "0", "100"),
    ("CANCELED_UNFILLED", "0", "10", "0"),
    ("CANCELED_PARTIALLY_FILLED", "4", "6", "100"),
])
def test_consistent_terminal_or_partial_acks_are_accepted(recorder, status, executed, remaining, average):
    recorder.replies.append(_ok(_order(status=status, executed_amount=executed, remaining_amount=remaining,
                                       average_price=average)))
    snapshot = _sender().submit_order(_req())
    assert snapshot.status is OrderStatus(status)
    assert snapshot.executed_amount + snapshot.remaining_amount == snapshot.amount
    _assert_one_post(recorder)


# ---- explicit rejects vs ambiguous responses ---------------------------------------------
@pytest.mark.parametrize("status", [200, 400])
@pytest.mark.parametrize("zero", [0, "0"])
@pytest.mark.parametrize("code", sorted(REJECT_CODES))
def test_documented_order_validation_rejects_are_rejected_without_a_retry(recorder, code, zero, status):
    recorder.replies.append(_failure(code, success=zero, status=status))
    with pytest.raises(ExchangeRejected) as caught:
        _sender().submit_order(_req())
    assert caught.value.code == str(code)
    assert caught.value.detail == "order validation"
    assert KEY not in str(caught.value) and SECRET not in str(caught.value)
    assert len(recorder.connections) == 1 and len(recorder.requests) == 1
    assert recorder.connections_closed[0].closed


AMBIGUOUS_SUBMITS = [
    pytest.param(_Reply(500, _body({"code": 60001}, 0)), id="5xx-even-allowlisted-code"),
    pytest.param(_Reply(429, _body({"code": 10009}, 0)), id="rate-limit"),
    pytest.param(_Reply(400, _body({"code": 50009}, 0)), id="old-order-not-found"),
    pytest.param(_Reply(200, _body({"code": 70019}, 0)), id="uncertain-70019"),
    pytest.param(_Reply(400, _body({"code": 60002}, 0)), id="code-outside-allowlist"),
    pytest.param(_Reply(200, _body({"code": 60001}, False)), id="bool-zero"),
    pytest.param(_Reply(200, _body({"code": 60001}, 0.0)), id="float-zero"),
    pytest.param(_Reply(200, _body({"code": True}, 0)), id="bool-code"),
    pytest.param(_Reply(200, _body({"code": 60001.0}, 0)), id="float-code"),
    pytest.param(_Reply(200, _body({"code": "60001"}, 0)), id="text-code"),
    pytest.param(_Reply(200, b'{"success":1.0,"data":{}}'), id="float-success"),
    pytest.param(_Reply(200, b'{"success":true,"data":{}}'), id="bool-success"),
    pytest.param(_Reply(200, b'{"success":"1","data":[]}'), id="list-data"),
    pytest.param(_Reply(200, b'{"success":1}'), id="missing-data"),
    pytest.param(_Reply(200, b"not-json"), id="malformed-json"),
    pytest.param(_Reply(200, b"[]"), id="not-an-object"),
    pytest.param(_Reply(200, _body({"code": 60001}, "1")), id="success-with-failure-body"),
    pytest.param(_Reply(200, _body(_order(side="sell"))), id="side-mismatch"),
    pytest.param(_Reply(200, _body(_order(price="101"))), id="price-mismatch"),
    pytest.param(_Reply(200, _body(_order(start_amount="11"))), id="amount-mismatch"),
    pytest.param(_Reply(200, _body(_order(type="market"))), id="type-mismatch"),
    pytest.param(_Reply(200, _body(_order(order_id=0))), id="nonpositive-id"),
    pytest.param(_Reply(200, _body(_order(status="REJECTED"))), id="rejected-status"),
    pytest.param(_Reply(200, _body(_order(status="INACTIVE"))), id="inactive-status"),
    pytest.param(_Reply(200, _body(_order(position_side="long"))), id="non-spot"),
    pytest.param(_Reply(200, _body(_order(executed_amount="1"))), id="quantity-sum-mismatch"),
    pytest.param(_Reply(200, _body(_order(status="PARTIALLY_FILLED", executed_amount="0",
                                          remaining_amount="10"))), id="impossible-partial"),
    pytest.param(_Reply(200, _body(_order(status="FULLY_FILLED", executed_amount="10",
                                          remaining_amount="1"))), id="impossible-full"),
    pytest.param(_Reply(200, _body(_order(ordered_at="bad"))), id="bad-timestamp"),
    pytest.param(_Reply(200, _body(_order(price=None))), id="missing-limit-price"),
    pytest.param(_Reply(200, _body(_order(pair="zzz_jpy"))), id="unknown-pair"),
    pytest.param(_Reply(200, _body(_order(start_amount="0"))), id="nonpositive-start"),
    pytest.param(_Reply(200, _body(_order(remaining_amount=None))), id="missing-remaining"),
    pytest.param(_Reply(200, _body(_order(status="BOGUS"))), id="unknown-status"),
]


@pytest.mark.parametrize("reply", AMBIGUOUS_SUBMITS)
def test_ambiguous_or_mismatched_create_responses_are_ambiguous_once(recorder, reply):
    recorder.replies.append(reply)
    with pytest.raises(ExchangeAmbiguous):
        _sender().submit_order(_req())
    assert len(recorder.connections) == 1 and len(recorder.requests) == 1
    assert recorder.connections_closed[0].closed


def test_a_response_for_another_configured_pair_is_ambiguous(recorder):
    recorder.replies.append(_ok(_order(pair="xym_jpy")))
    with pytest.raises(ExchangeAmbiguous):
        _sender(symbols=SYMBOLS).submit_order(_req())
    assert len(recorder.requests) == 1


def test_malformed_responses_do_not_echo_the_body_or_key_material(recorder):
    recorder.replies.append(_raw("not-json-" + SECRET))
    with pytest.raises(ExchangeAmbiguous) as caught:
        _sender().submit_order(_req())
    assert SECRET not in str(caught.value) and KEY not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert len(recorder.requests) == 1


def test_the_response_read_is_bounded(recorder):
    recorder.replies.append(_Reply(200, b"x" * (MAX_RESPONSE_BYTES + 1)))
    with pytest.raises(ExchangeAmbiguous):
        _sender().submit_order(_req())
    assert recorder.responses[0].read_limit == MAX_RESPONSE_BYTES + 1
    assert recorder.responses[0].closed and recorder.connections_closed[0].closed
    assert len(recorder.requests) == 1


def test_an_otherwise_valid_body_with_declared_bytes_left_is_ambiguous(recorder):
    body = _body(_order())
    recorder.replies.append(_Reply(200, body, headers={"Content-Length": str(len(body) + 1)}, length=1))
    with pytest.raises(ExchangeAmbiguous):
        _sender().submit_order(_req())
    assert recorder.responses[0].read_limit == MAX_RESPONSE_BYTES + 1
    assert recorder.responses[0].closed and recorder.connections_closed[0].closed
    assert len(recorder.requests) == 1


def test_any_other_private_path_is_rejected_before_connecting(recorder):
    with pytest.raises(ValueError):
        _sender()._post("/v1/user/spot/orders", {})
    assert recorder.connections == [] and recorder.requests == []


@pytest.mark.parametrize("reply", [
    pytest.param(_Reply(request_error=OSError("synthetic lost request")), id="lost-request"),
    pytest.param(_Reply(response_error=TimeoutError("synthetic lost response")), id="lost-response"),
    pytest.param(_Reply(read_error=OSError("synthetic broken read")), id="broken-read"),
])
def test_lost_requests_or_responses_are_ambiguous_and_never_replayed(recorder, reply):
    recorder.replies.append(reply)
    with pytest.raises(ExchangeAmbiguous):
        _sender().submit_order(_req())
    assert len(recorder.connections) == 1 and len(recorder.requests) == 1
    assert len(recorder.connections_closed) == 1 and recorder.connections_closed[0].closed


def test_transport_failures_do_not_expose_secrets_or_exception_context(recorder):
    synthetic = "SYNTHETIC-TRANSPORT-SECRET-9999"
    recorder.replies.append(_Reply(request_error=OSError(f"lost {KEY} {SECRET} {synthetic}")))
    with pytest.raises(ExchangeAmbiguous) as caught:
        _sender().submit_order(_req())
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert all(value not in str(caught.value) for value in (KEY, SECRET, synthetic))
    assert all(value not in repr(caught.value) for value in (KEY, SECRET, synthetic))
    assert len(recorder.connections) == 1 and len(recorder.requests) == 1
    assert len(recorder.connections_closed) == 1 and recorder.connections_closed[0].closed


def test_a_redirect_is_ambiguous_and_not_followed(recorder):
    recorder.replies.append(_Reply(302, _body(_order()), headers={"Location": "https://synthetic.invalid/next"}))
    with pytest.raises(ExchangeAmbiguous):
        _sender().submit_order(_req())
    assert len(recorder.connections) == 1 and len(recorder.requests) == 1
    assert recorder.requests[0]["path"] == ORDER_PATH


# ---- cancel -------------------------------------------------------------------------------
def test_the_cancel_post_has_the_exact_host_path_body_and_signature(recorder):
    recorder.replies.append(_ok(_order(status="CANCELED_UNFILLED")))
    outcome = _sender().cancel_order("OAS/JPY", "1000001")
    assert recorder.connections == [(API_HOST, 10.0)]
    request = recorder.requests[0]
    assert request["method"] == "POST" and request["path"] == CANCEL_PATH
    assert request["body"] == CANCEL_BODY
    assert request["headers"]["Content-Type"] == "application/json"
    assert type(json.loads(request["body"])["order_id"]) is int
    assert json.loads(request["body"])["order_id"] == 1000001
    _assert_signed(request)
    assert outcome.kind is CancelKind.CANCELED
    assert outcome.snapshot.exchange_order_id == "1000001" and outcome.snapshot.symbol == "OAS/JPY"
    assert outcome.snapshot.status is OrderStatus.CANCELED_UNFILLED
    _assert_one_post(recorder)


@pytest.mark.parametrize("row,kind", [
    pytest.param(_order(status="CANCELED_PARTIALLY_FILLED", executed_amount="4", remaining_amount="6",
                        average_price="100"), CancelKind.CANCELED, id="partial-fill-canceled"),
    pytest.param(_order(status="CANCELED_UNFILLED"), CancelKind.CANCELED, id="unfilled-canceled"),
    pytest.param(_order(status="FULLY_FILLED", executed_amount="10", remaining_amount="0",
                        average_price="100"), CancelKind.ALREADY_CLOSED, id="full-fill-race"),
])
def test_terminal_cancel_snapshots_map_to_the_cancel_kind(recorder, row, kind):
    recorder.replies.append(_ok(row))
    outcome = _sender().cancel_order("OAS/JPY", "1000001")
    assert outcome.kind is kind
    assert outcome.snapshot.executed_amount + outcome.snapshot.remaining_amount == outcome.snapshot.amount


AMBIGUOUS_CANCELS = [
    pytest.param(_Reply(200, _body(_order(status="UNFILLED"))), id="still-open"),
    pytest.param(_Reply(200, _body(_order(status="REJECTED"))), id="rejected-status"),
    pytest.param(_Reply(200, _body(_order(status="INACTIVE"))), id="inactive-status"),
    pytest.param(_Reply(200, _body(_order(status="CANCELED_PARTIALLY_FILLED", executed_amount="0",
                                          remaining_amount="10"))), id="impossible-partial-cancel"),
    pytest.param(_Reply(200, _body(_order(status="FULLY_FILLED", executed_amount="10",
                                          remaining_amount="1"))), id="impossible-full-race"),
    pytest.param(_Reply(200, _body(_order(order_id=999999, status="CANCELED_UNFILLED"))), id="id-mismatch"),
    pytest.param(_Reply(200, _body(_order(position_side="long", status="CANCELED_UNFILLED"))), id="non-spot"),
    pytest.param(_Reply(200, b'{"success":1,"data":{}}'), id="malformed-order"),
    pytest.param(_Reply(200, b"not-json"), id="malformed-json"),
    pytest.param(_Reply(302, _body(_order(status="CANCELED_UNFILLED"))), id="redirect"),
]


@pytest.mark.parametrize("reply", AMBIGUOUS_CANCELS)
def test_ambiguous_cancel_responses_are_ambiguous_once(recorder, reply):
    recorder.replies.append(reply)
    with pytest.raises(ExchangeAmbiguous):
        _sender().cancel_order("OAS/JPY", "1000001")
    assert len(recorder.connections) == 1 and len(recorder.requests) == 1
    assert recorder.connections_closed[0].closed


def test_a_cancel_response_for_another_configured_pair_is_ambiguous(recorder):
    recorder.replies.append(_ok(_order(pair="xym_jpy", status="CANCELED_UNFILLED")))
    with pytest.raises(ExchangeAmbiguous):
        _sender(symbols=SYMBOLS).cancel_order("OAS/JPY", "1000001")
    assert len(recorder.requests) == 1


@pytest.mark.parametrize("code,status", [
    (50009, 200), (50009, 400), (70019, 200), (70019, 400), (10009, 429), (60001, 200),
    (60001, 400), (60001, 500),
])
def test_uncertain_cancel_errors_are_never_treated_as_canceled_or_rejected(recorder, code, status):
    recorder.replies.append(_failure(code, status=status))
    with pytest.raises(ExchangeAmbiguous):
        _sender().cancel_order("OAS/JPY", "1000001")
    assert len(recorder.connections) == 1 and len(recorder.requests) == 1


def test_a_lost_cancel_response_is_ambiguous_and_never_replayed(recorder):
    recorder.replies.append(_Reply(response_error=TimeoutError("synthetic lost cancel response")))
    with pytest.raises(ExchangeAmbiguous):
        _sender().cancel_order("OAS/JPY", "1000001")
    assert len(recorder.connections) == 1 and len(recorder.requests) == 1


# ---- pacing -------------------------------------------------------------------------------
def test_signing_happens_after_at_least_200ms_of_pacing(recorder):
    class Pacer:
        def __init__(self):
            self.now, self.sleeps = 0.0, []

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.sleeps.append(seconds)
            self.now += seconds

    pacer, wall_calls = Pacer(), []

    def wall_clock():
        wall_calls.append(len(pacer.sleeps))
        return WALL_S + len(wall_calls) - 1

    recorder.replies.extend([_ok(_order()), _ok(_order())])
    sender = _sender(monotonic=pacer.monotonic, sleep=pacer.sleep, wall_clock=wall_clock)
    sender.submit_order(_req())
    sender.submit_order(_req())
    assert len(pacer.sleeps) == 1 and pacer.sleeps[0] == pytest.approx(MIN_INTERVAL_S)
    assert wall_calls == [0, 1]          # the second signature was made after the sleep
    assert recorder.requests[1]["headers"]["ACCESS-REQUEST-TIME"] == str(WALL_MS + 1000)
    assert len(recorder.connections) == 2


# ---- the deliberate absence of production wiring -----------------------------------------
def test_the_executor_still_accepts_only_the_mock():
    assert BitbankSpotSender.kind == "bitbank_spot"
    assert live_executor.SENDER_KINDS_ALLOWED == frozenset({"mock"})
    assert BitbankSpotSender.kind not in live_executor.SENDER_KINDS_ALLOWED


def test_the_executor_refuses_this_real_sender(ws):
    harness = Harness(ws)
    try:
        with pytest.raises(ValueError, match="not allowed"):
            live_executor.OrderExecutor(harness.ledger, harness.lease, reader=harness.mock,
                                        sender=_sender(), enable_check=harness._gate,
                                        clock=harness.clock.seconds, symbols=harness.symbols,
                                        config=harness.config)
    finally:
        harness.close()


def test_the_production_runner_still_wires_sender_none():
    tree = ast.parse(Path(runner_module.__file__).read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "OrderExecutor"]
    assert len(calls) == 1
    sender = next((keyword.value for keyword in calls[0].keywords if keyword.arg == "sender"), None)
    assert isinstance(sender, ast.Constant) and sender.value is None


def test_only_the_two_fixed_spot_paths_exist():
    assert API_HOST == "api.bitbank.cc"
    assert ORDER_PATH == "/v1/user/spot/order"
    assert CANCEL_PATH == "/v1/user/spot/cancel_order"
    source = Path(bitbank.__file__).read_text(encoding="utf-8")
    assert sorted(re.findall(r'"/v1/[^"]*"', source)) == [f'"{CANCEL_PATH}"', f'"{ORDER_PATH}"']
