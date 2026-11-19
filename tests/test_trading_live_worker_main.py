"""The LIVE worker process entry point (L4b-6b): args, signals and the real wiring.

The in-process tests pass a fake ``wiring`` (mock exchange + synthetic frames) to
``main``; the signal tests run ``tests/helpers/live_worker_sigterm_helper.py`` as a real
subprocess in its own process group; the real-wiring test runs the entry point against an
empty state dir (no key file) and expects the credentials failure to be a config exit.  No
test uses the network and none can place an order.
"""
from __future__ import annotations

import ast
import json
import os
import signal
import subprocess
import sys
import time
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest

from docich.trading.live import control as live_control
from docich.trading.live.lease import acquire_writer_lease, release_writer_lease
from docich.trading.live.ledger import LiveLedger
from docich.trading.live.paths import live_layout
from docich.trading.live_worker import __main__ as main_module
from docich.trading.live_worker import runner as runner_module
from docich.trading.live_worker.status import STATUS_FILENAME
from docich.trading.market_data import MarketFrame

from test_trading_live_support import Clock, block_network, make_mock, policy_set

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
HELPER = ROOT / "tests" / "helpers" / "live_worker_sigterm_helper.py"
SYMBOLS = ("OAS/JPY",)

# ``-I`` ignores PYTHONPATH and ``-P`` drops the cwd; this repository is not installed as a
# package, so a literal ``python -I -m docich...`` cannot find ``src``.  The bootstrap adds
# the repository's ``src`` and runs the very same module as ``__main__`` (runpy), with the
# isolation of ``-I`` still in force.
BOOTSTRAP = (
    "import runpy, sys\n"
    "src = sys.argv[1]\n"
    "sys.path.insert(0, src)\n"
    "sys.argv = ['docich.trading.live_worker', *sys.argv[2:]]\n"
    "runpy.run_module('docich.trading.live_worker', run_name='__main__')\n"
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


# ---- helpers -----------------------------------------------------------------------------
def prepare_state(state_dir: Path):
    """A ledger + active policy set + on/trade control file (the runner's inputs)."""
    now = time.time()
    clock = Clock(start_ms=int(now * 1000))
    mock, _ = make_mock(clock, symbols=SYMBOLS, jpy="10000")
    ps = policy_set(SYMBOLS)
    layout = live_layout(state_dir)
    paper = state_dir / "trading"
    paper.mkdir(exist_ok=True)
    ledger = LiveLedger.open(layout, paper_dir=paper, now=now)
    try:
        lease = acquire_writer_lease(ledger, layout, now=now)
        try:
            ledger.store_policy_set(lease, ps, now=now)
            live_control.write_control(state_dir, state="on", mode="trade",
                                       updated_by="owner", now=now)
            ledger.record_owner_activation(lease, ps, operator="owner", note="main test", now=now)
        finally:
            release_writer_lease(ledger, lease, now=now)
    finally:
        ledger.close()
    return mock


def status_of(state_dir: Path) -> dict:
    try:
        return json.loads((live_layout(state_dir).root / STATUS_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class FakeReader:
    """The mock exchange plus the runner's health hook; ``kind="auth"`` ends the first cycle."""

    def __init__(self, inner, kind=None):
        self._inner = inner
        self._kind = kind

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def health(self):
        return SimpleNamespace(last_error_kind=self._kind, consecutive_failures=0)

    def fetch_balance(self):
        return self._inner.fetch_balance()


class FakeMarketData:
    def fetch_frame(self, symbol: str, now: float) -> MarketFrame:
        return MarketFrame(symbol=symbol, timeframe_seconds=300, timestamps=(float(now),),
                           closes=(D("100"),), volumes=(D("1"),))


def refuse_wiring(_state_dir):
    raise AssertionError("wiring must not be touched by argument validation")


def _child_env() -> dict:
    return dict(os.environ, PYTHONPATH=os.pathsep.join([str(SRC), str(ROOT / "tests")]))


def _wait_for_running(proc, state_dir: Path, deadline_s: float = 60.0) -> None:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if status_of(state_dir).get("phase") == "running":
            return
        if proc.poll() is not None:
            pytest.fail(f"the worker exited before it reached running (code {proc.returncode})")
        time.sleep(0.05)
    pytest.fail(f"the worker never reached running within {deadline_s:g} s")


# ---- arguments ---------------------------------------------------------------------------
def test_a_relative_state_dir_is_rejected_before_anything_is_created(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code = main_module.main(["run", "--state-dir", "relative/state"], wiring=refuse_wiring)
    assert code == main_module.EXIT_CONFIG == 2
    assert list(tmp_path.iterdir()) == []


def test_a_state_dir_whose_parent_does_not_exist_is_rejected(tmp_path):
    missing_parent = tmp_path / "missing" / "state"
    code = main_module.main(["run", "--state-dir", str(missing_parent)], wiring=refuse_wiring)
    assert code == 2
    assert not missing_parent.exists() and not missing_parent.parent.exists()


@pytest.mark.parametrize("interval", ["29", "3601"])
def test_an_out_of_range_interval_is_rejected(tmp_path, interval):
    calls = []

    def wiring(_state_dir):
        calls.append(_state_dir)
        raise AssertionError("wiring must not be touched by argument validation")

    code = main_module.main(
        ["run", "--state-dir", str(tmp_path), "--interval", interval], wiring=wiring)
    assert code == main_module.EXIT_CONFIG == 2
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_the_module_entry_point_answers_help():
    proc = subprocess.run(
        [sys.executable, "-m", "docich.trading.live_worker", "run", "--help"],
        env=_child_env(), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "run" in proc.stdout and "--state-dir" in proc.stdout
    assert "30..3600" in proc.stdout


# ---- the fake wiring reaches the runner ---------------------------------------------------
def test_a_fake_wiring_reaches_the_runner_and_returns_its_exit_code(tmp_path):
    mock = prepare_state(tmp_path)
    reader = FakeReader(mock, kind="auth")
    market_data = FakeMarketData()
    wired = []

    def wiring(state_dir):
        wired.append(state_dir)
        return {"open_reader": lambda _given: reader,
                "make_market_data": lambda _reader: market_data}

    code = main_module.main(["run", "--state-dir", str(tmp_path), "--interval", "60"],
                            wiring=wiring)

    assert code == runner_module.EXIT_AUTH == 3
    assert wired == [str(tmp_path)]
    status = status_of(tmp_path)
    assert status["phase"] == "exited"
    assert status["exit_code"] == 3
    assert status["reason"] == "auth"
    assert status["cycle"] == 1
    assert mock.submissions == []


# ---- failures while wiring ---------------------------------------------------------------
def test_an_import_error_while_wiring_is_a_config_exit(tmp_path, capsys):
    def wiring(_state_dir):
        raise ImportError("No module named 'ccxt'")

    code = main_module.main(["run", "--state-dir", str(tmp_path)], wiring=wiring)

    assert code == main_module.EXIT_CONFIG == 2
    assert capsys.readouterr().err.strip() == "ImportError"
    assert not live_layout(tmp_path).root.exists()


def test_any_other_wiring_exception_is_retryable_and_prints_only_the_class_name(tmp_path, capsys):
    def wiring(_state_dir):
        raise RuntimeError("api_key=SENTINEL must never be printed")

    code = main_module.main(["run", "--state-dir", str(tmp_path)], wiring=wiring)

    assert code == main_module.EXIT_RETRYABLE == 8
    err = capsys.readouterr().err
    assert err.strip() == "RuntimeError"
    assert "SENTINEL" not in err and "Traceback" not in err
    assert not live_layout(tmp_path).root.exists()


# ---- real signals in a real process ------------------------------------------------------
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_a_signal_stops_the_real_process_cleanly(tmp_path, signum):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    proc = subprocess.Popen(
        [sys.executable, str(HELPER), str(state_dir), "60"],
        env=_child_env(), start_new_session=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _wait_for_running(proc, state_dir)
        os.killpg(proc.pid, signum)                        # pgid == pid (start_new_session)
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            pytest.fail(f"the worker did not exit within 30 s after {signum.name}")
        assert proc.returncode == 0, (out, err)
        status = status_of(state_dir)
        assert status["phase"] == "exited"
        assert status["exit_code"] == 0
        assert status["reason"] == "stop_requested"
        assert status["outcome"] == "stop_requested"
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


# ---- the real wiring without a key -------------------------------------------------------
def test_the_real_wiring_without_a_key_exits_config_without_the_network(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    proc = subprocess.Popen(
        [sys.executable, "-I", "-c", BOOTSTRAP, str(SRC),
         "run", "--state-dir", str(state_dir)],
        env=_child_env(), start_new_session=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        try:
            out, err = proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            pytest.fail("the real wiring did not fail closed within 60 s")
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()

    assert proc.returncode == 2, (out, err)
    status = status_of(state_dir)
    assert status["phase"] == "exited"
    assert status["exit_code"] == 2
    assert status["reason"] == "config"
    assert "Traceback" not in err
    # No socket can have been opened: open_bitbank_reader loads the key file first, and a
    # missing file raises ReaderUnavailable(kind="config") before a transport is built.


# ---- the default entry point has no order path; the paired role is explicit --------------
FORBIDDEN_REFERENCES = frozenset({
    "SENDER_KINDS_ALLOWED", "BitbankSpotSender", "bitbank_spot_sender",
    "place_order", "create_order", "cancel_order", "submit_order",
})


def test_the_entry_point_never_references_an_order_placing_primitive():
    tree = ast.parse(Path(main_module.__file__).read_text(encoding="utf-8"))
    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            seen.add(node.id)
        elif isinstance(node, ast.Attribute):
            seen.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            seen.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seen.add(node.name)
        elif isinstance(node, ast.arg):
            seen.add(node.arg)
    assert seen & FORBIDDEN_REFERENCES == set()


def test_the_default_cli_role_is_read_only_and_the_spot_pair_is_explicit(tmp_path):
    default = main_module.real_wiring(tmp_path)
    opted = main_module.real_wiring(tmp_path, spot_order_sender=True)

    assert set(default) == {"open_reader", "make_market_data"}
    assert "open_pair" not in default
    assert set(opted) == {"open_reader", "open_pair", "make_market_data"}

    args = main_module._parser().parse_args(["run", "--state-dir", str(tmp_path)])
    assert args.spot_order_sender is False
    args = main_module._parser().parse_args(
        ["run", "--state-dir", str(tmp_path), "--spot-order-sender"])
    assert args.spot_order_sender is True


def test_the_default_run_does_not_ask_for_the_paired_role(tmp_path):
    seen = []

    def wiring(_state_dir, *, spot_order_sender=False):
        seen.append(spot_order_sender)
        return {"open_reader": lambda _: None,
                "make_market_data": lambda _: None}

    code = main_module.main(["run", "--state-dir", str(tmp_path)], wiring=wiring)

    assert code == runner_module.EXIT_OK == 0
    assert seen == [False]
    assert status_of(tmp_path)["phase"] == "inert"


def test_the_opt_in_flag_opens_the_pair_and_never_uses_the_reader_fallback(tmp_path):
    seen = []
    reader_calls = []

    def wiring(_state_dir, *, spot_order_sender=False):
        seen.append(spot_order_sender)

        def open_reader(_given):
            reader_calls.append("reader")
            return None

        def open_pair(_given):
            return object(), SimpleNamespace(kind="bitbank_spot")

        return {"open_reader": open_reader,
                "open_pair": open_pair,
                "make_market_data": lambda _: None}

    code = main_module.main(
        ["run", "--state-dir", str(tmp_path), "--spot-order-sender"], wiring=wiring)

    assert code == runner_module.EXIT_OK == 0
    assert seen == [True]
    assert reader_calls == []
    assert status_of(tmp_path)["phase"] == "inert"
