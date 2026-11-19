"""The paired bitbank spot session (Issue #3623 review candidate), synthetic keys only.

No test opens a socket: the reader uses the L3 fake transport, the sender is either replaced by
a capturing class or never makes a request, and the LIVE ``block_network`` fixture is a second
guard.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from bitbank_live_support import (SENTINEL_KEY, SENTINEL_SECRET, SYMBOLS, FakeTransport, default_script)
from docich.trading.exchanges import bitbank_spot_session as session
from docich.trading.exchanges.bitbank_live import credentials as cr
from docich.trading.exchanges.bitbank_live.credentials import Credentials
from docich.trading.exchanges.bitbank_live.reader import BitbankReader, ReaderUnavailable
from docich.trading.exchanges.bitbank_spot_sender import BitbankSpotSender
from docich.trading.live.paths import live_layout

from test_trading_live_support import block_network

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


@pytest.fixture
def state():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def _install(state, *, body=None, mode=0o600):
    layout = live_layout(state)
    layout.root.mkdir(mode=0o700)
    layout.owner_dir.mkdir(mode=0o700)
    os.chmod(layout.root, 0o700)
    os.chmod(layout.owner_dir, 0o700)
    path = layout.owner_dir / cr.SPOT_ORDER_KEY_FILENAME
    if body is not None:
        path.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
        os.chmod(path, mode)
    return path


def _good(**kw):
    data = {"schema_version": 1, "kind": cr.SPOT_ORDER_KEY_KIND, "api_key": SENTINEL_KEY,
            "api_secret": SENTINEL_SECRET}
    data.update(kw)
    return data


def _assert_safe(err, state):
    text = str(err.value) + repr(err.value) + repr(err.value.__cause__) + repr(err.value.__context__)
    assert SENTINEL_KEY not in text and SENTINEL_SECRET not in text and str(state) not in text


def test_one_key_object_builds_both_halves_with_the_paired_clock_and_no_ahead_override(state, monkeypatch):
    _install(state, body=_good())
    seen = {}

    def fake_reader(credentials, **kwargs):
        seen["credentials"] = credentials
        seen["reader_kwargs"] = kwargs
        return object()

    class FakeSender:
        def __init__(self, key, secret, symbols, **kwargs):
            seen["sender"] = (key, secret, tuple(symbols))
            seen["sender_kwargs"] = kwargs

    monkeypatch.setattr(session, "open_bitbank_reader_from_credentials", fake_reader)
    monkeypatch.setattr(session, "BitbankSpotSender", FakeSender)
    clock = lambda: 1.0

    reader, sender = session.open_bitbank_spot_session(state, symbols=SYMBOLS, clock=clock)

    assert reader is not None and isinstance(sender, FakeSender)
    assert isinstance(seen["credentials"], Credentials)
    assert seen["credentials"].reveal() == (SENTINEL_KEY, SENTINEL_SECRET)
    # The sender got the same account pair, never a second file or environment.
    assert seen["sender"] == (SENTINEL_KEY, SENTINEL_SECRET, SYMBOLS)
    assert seen["reader_kwargs"]["max_behind_s"] == session.SPOT_SESSION_MAX_BEHIND_S == 3.0
    assert session.SIGNING_WINDOW_S == 5.0 and session.TRANSIT_BUDGET_S == 2.0
    assert "max_ahead_s" not in seen["reader_kwargs"]      # the existing reader default stays
    assert seen["reader_kwargs"]["clock"] is clock
    assert seen["sender_kwargs"]["wall_clock"] is clock


def test_the_pair_builds_the_real_get_only_reader_and_spot_sender(state):
    _install(state, body=_good())
    transport = FakeTransport(default_script(), skew_s=0.0)

    reader, sender = session.open_bitbank_spot_session(
        state, symbols=SYMBOLS, clock=lambda: 1.0,
        transport_factory=lambda _credentials: transport)

    assert isinstance(reader, BitbankReader)
    assert isinstance(sender, BitbankSpotSender)
    assert reader.market_rules("OAS/JPY").min_amount == 1
    assert sender.kind == "bitbank_spot"
    assert SENTINEL_KEY not in repr(sender) and SENTINEL_SECRET not in str(sender)


def test_the_paired_clock_refuses_too_far_behind_but_keeps_the_reader_ahead_default(state):
    _install(state, body=_good())
    # 3 s behind is the paired bound: 5 s signing window minus a 2 s transit budget.
    with pytest.raises(ReaderUnavailable) as err:
        session.open_bitbank_spot_session(
            state, symbols=SYMBOLS, clock=lambda: 1.0,
            transport_factory=lambda _c: FakeTransport(default_script(), skew_s=-3.1))
    assert err.value.kind == "clock"
    assert "clock" in str(err.value)

    # Only the behind bound was tightened: the reader's existing ahead bound still applies.
    with pytest.raises(ReaderUnavailable) as ahead:
        session.open_bitbank_spot_session(
            state, symbols=SYMBOLS, clock=lambda: 1.0,
            transport_factory=lambda _c: FakeTransport(default_script(), skew_s=0.9))
    assert ahead.value.kind == "clock"

    reader, _ = session.open_bitbank_spot_session(
        state, symbols=SYMBOLS, clock=lambda: 1.0,
        transport_factory=lambda _c: FakeTransport(default_script(), skew_s=-2.9))
    assert reader.market_rules("OAS/JPY").min_amount == 1


@pytest.mark.parametrize("body,reason", [
    (None, "file_missing"),
    ("{not json", "corrupt_json"),
    (json.dumps(_good(kind="docich-bitbank-readonly-key")), "schema_invalid"),
    (json.dumps(_good(schema_version=2)), "schema_invalid"),
    (json.dumps(_good(extra="x")), "unknown_keys"),
])
def test_a_missing_or_bad_spot_key_is_a_config_failure_before_the_transport(state, body, reason):
    _install(state, body=body)
    built = []

    with pytest.raises(ReaderUnavailable) as err:
        session.open_bitbank_spot_session(
            state, symbols=SYMBOLS, clock=lambda: 1.0,
            transport_factory=lambda credentials: built.append(credentials))

    assert err.value.kind == "config"
    assert reason in str(err.value)
    assert built == []
    _assert_safe(err, state)


def test_a_symlinked_spot_key_is_refused_before_the_transport(state):
    path = _install(state)
    target = state / "elsewhere.json"
    target.write_text(json.dumps(_good()), encoding="utf-8")
    os.chmod(target, 0o600)
    path.symlink_to(target)
    built = []

    with pytest.raises(ReaderUnavailable) as err:
        session.open_bitbank_spot_session(
            state, symbols=SYMBOLS, clock=lambda: 1.0,
            transport_factory=lambda credentials: built.append(credentials))

    assert err.value.kind == "config" and "not_regular" in str(err.value)
    assert built == []
    _assert_safe(err, state)


def test_a_bad_local_sender_configuration_never_builds_a_reader(state, monkeypatch):
    _install(state, body=_good())
    readers = []
    monkeypatch.setattr(session, "open_bitbank_reader_from_credentials",
                        lambda *a, **k: readers.append(1))

    with pytest.raises(ReaderUnavailable) as err:
        session.open_bitbank_spot_session(state, symbols=("not-a-pair",), clock=lambda: 1.0)

    assert err.value.kind == "config"
    assert readers == []


def test_a_raw_sender_error_with_synthetic_values_is_hidden(state, monkeypatch):
    _install(state, body=_good())

    class RawSenderError(RuntimeError):
        pass

    def broken_sender(*_args, **_kwargs):
        raise RawSenderError(f"{SENTINEL_KEY} {SENTINEL_SECRET} {state}")

    monkeypatch.setattr(session, "BitbankSpotSender", broken_sender)
    with pytest.raises(ReaderUnavailable) as err:
        session.open_bitbank_spot_session(state, symbols=SYMBOLS, clock=lambda: 1.0)

    assert err.value.kind == "config" and str(err.value) == "sender: RawSenderError"
    assert err.value.__cause__ is None and err.value.__context__ is None
    _assert_safe(err, state)


def test_a_raw_reader_error_with_synthetic_values_is_hidden(state, monkeypatch):
    _install(state, body=_good())
    monkeypatch.setattr(session, "BitbankSpotSender", lambda *_a, **_k: object())

    class RawReaderError(RuntimeError):
        pass

    def broken_reader(*_args, **_kwargs):
        raise RawReaderError(f"{SENTINEL_KEY} {SENTINEL_SECRET} {state}")

    monkeypatch.setattr(session, "open_bitbank_reader_from_credentials", broken_reader)
    with pytest.raises(ReaderUnavailable) as err:
        session.open_bitbank_spot_session(state, symbols=SYMBOLS, clock=lambda: 1.0)

    assert err.value.kind == "config" and str(err.value) == "reader: RawReaderError"
    assert err.value.__cause__ is None and err.value.__context__ is None
    _assert_safe(err, state)


def test_importing_the_session_module_loads_no_network_module(tmp_path):
    code = textwrap.dedent(f"""
        import sys
        import docich.trading.exchanges.bitbank_spot_session
        bad = [m for m in ("ccxt", "requests", "ssl", "socket", "http.client", "urllib.request")
               if m in sys.modules]
        assert not bad, bad
        print("clean")
    """)
    out = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True,
                         env=dict(os.environ, PYTHONPATH=str(SRC)), cwd=str(tmp_path))
    assert out.stdout.strip() == "clean", out.stderr
