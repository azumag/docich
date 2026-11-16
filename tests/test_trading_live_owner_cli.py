"""The owner-only LIVE CLI (L4a): read-only status/list and the two-step ``cashflow add``."""
from __future__ import annotations

import ast
import io
import os
import re
from decimal import Decimal
from pathlib import Path

import pytest

from docich.trading.live import balance as live_balance
from docich.trading.live import lease as live_lease
from docich.trading.live import safety as sf
from docich.trading.live.executor import SubmitOutcome as S
from docich.trading.live.ledger import LiveLedger
from docich.trading.live.paths import live_layout
from docich.trading.live_owner import cli
from test_trading_live_support import Harness, Workspace, block_network, market_rules

NOW = 1_767_225_600.0                     # 2026-01-01T00:00:00+00:00
WHEN = "2026-01-01T00:00:00+00:00"
PKG = Path(__file__).resolve().parents[1] / "src" / "docich" / "trading" / "live_owner"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


@pytest.fixture
def workspace():
    ws = Workspace()
    yield ws
    ws.cleanup()


# ---- helpers -------------------------------------------------------------------------------
def _run(state_dir, *argv, is_tty=False, now=NOW):
    out = io.StringIO()
    code = cli.main(["--state-dir", str(state_dir), *argv], out=out, clock=lambda: now, is_tty=is_tty)
    return code, out.getvalue()


def _add_args(**kw):
    values = {"kind": "deposit", "currency": "JPY", "amount": "1000", "occurred_at": WHEN,
              "operator": "owner", "note": "opening balance"}
    values.update(kw)
    argv = []
    for name, value in values.items():
        argv += [f"--{name.replace('_', '-')}", value]
    return ["cashflow", "add", *argv]


def _token(text: str) -> str:
    tokens = [line.split(": ", 1)[1] for line in text.splitlines() if line.startswith("token: ")]
    assert len(tokens) == 1, text
    return tokens[0]


def _confirmed(state_dir, *argv, is_tty=False):
    """Preview, then repeat with the printed token (the owner's two-step confirmation)."""
    code, text = _run(state_dir, *argv, is_tty=is_tty)
    assert code == 0, text
    return _run(state_dir, *argv, "--confirm", _token(text), is_tty=is_tty)


def _listing(path) -> list[str]:
    return sorted(os.listdir(path))


def _cashflows(state_dir) -> list[tuple]:
    ledger = LiveLedger.open_readonly(live_layout(state_dir))
    try:
        with ledger.read_cursor() as cur:
            return cur.execute("SELECT cashflow_id, kind, currency, amount, source, note FROM cashflows").fetchall()
    finally:
        ledger.close()


# ---- read-only commands --------------------------------------------------------------------
def test_status_without_a_ledger_creates_nothing(tmp_path):
    before = _listing(tmp_path)
    code, text = _run(tmp_path, "status")
    assert code == 0 and text == "ledger: not created\n"
    assert _listing(tmp_path) == before and not (tmp_path / "trading-live").exists()


def test_a_state_dir_that_does_not_exist_is_refused(tmp_path):
    missing = tmp_path / "not-created"
    for argv in (("status",), ("cashflow", "list"), (*_add_args(), "--confirm", "0" * 10)):
        code, text = _run(missing, *argv)
        assert code == 2 and text == "refused: --state-dir must be an existing directory\n", argv
    assert not missing.exists() and _listing(tmp_path) == []


def test_cashflow_list_without_a_ledger_creates_nothing(tmp_path):
    before = _listing(tmp_path)
    code, text = _run(tmp_path, "cashflow", "list")
    assert code == 0 and text == "ledger: not created\n"
    assert _listing(tmp_path) == before and not (tmp_path / "trading-live").exists()


def test_status_on_a_fresh_ledger_shows_no_amounts_and_no_policy(tmp_path):
    ledger = LiveLedger.open(live_layout(tmp_path), paper_dir=tmp_path / "trading", now=NOW)
    ledger.close()
    code, text = _run(tmp_path, "status")
    assert code == 0
    assert "policy: none" in text and "intents: none" in text and "safety: none" in text
    assert "positions.open: 0" in text and "positions.residual_inventory: 0" in text
    assert "unknown_submissions: 0" in text and "cashflows: 0" in text and "events: 1" in text
    for line in text.splitlines():
        assert "amount" not in line.lower(), line
        assert not re.search(r"\d+\.\d+", line), line          # no decimal amounts
        assert not re.search(r"[0-9a-f]{16,}", line), line     # no ids or digests


def test_status_on_a_populated_ledger(workspace):
    h = Harness(workspace)
    h.mock.set_book("OAS/JPY", asks=[(Decimal("100"), Decimal("1000"))], bids=[(Decimal("99"), Decimal("1000"))])
    try:
        filled, outcome = h.send(side="buy", key="b1", amount="10", price="100")     # a real fill
        assert outcome is S.ACKED and h.state(filled.intent_id) == "FILLED"
        h.mock.faults.push("submit", "LOST_RESPONSE")
        lost, outcome = h.send(side="buy", key="b2", amount="5", price="90")         # resting, response lost
        assert outcome is S.UNKNOWN and h.state(lost.intent_id) == "UNKNOWN_SUBMISSION"
        sf.trip(h.ledger, sf.SafetyReason.DATA_ANOMALY, detail="status test", now=h.clock.seconds())
        code, text = _run(workspace.state, "status")
    finally:
        h.close()
    assert code == 0
    assert re.search(r"^policy: (none|[0-9a-f]{12})$", text, re.M)
    assert "safety: data_anomaly" in text
    assert "unknown_submissions: 1" in text
    assert "positions.open: 1" in text and "positions.residual_inventory: 0" in text
    assert "FILLED=1" in text and "UNKNOWN_SUBMISSION=1" in text
    assert "cashflows: 1" in text                    # the harness records the opening deposit
    assert "amount" not in text.lower()
    for line in text.splitlines():
        assert not re.search(r"\d+\.\d+", line), line          # no decimal amounts
        assert not re.search(r"[0-9a-f]{16,}", line), line     # no ids or digests


# ---- cashflow add: preview ------------------------------------------------------------------
def test_cashflow_add_preview_creates_nothing_and_the_token_is_stable(tmp_path):
    before = _listing(tmp_path)
    code, text = _run(tmp_path, *_add_args())
    assert code == 0
    assert "ledger: will be created" in text
    assert "kind: deposit" in text and "currency: JPY" in text and "operator: owner" in text
    assert "amount: hidden" in text and f"occurred_at: {WHEN}" in text
    assert re.fullmatch(r"[0-9a-f]{10}", _token(text))
    assert _listing(tmp_path) == before and not (tmp_path / "trading-live").exists()
    assert _token(_run(tmp_path, *_add_args())[1]) == _token(text)          # the same content, the same token
    assert _token(_run(tmp_path, *_add_args(note="another"))[1]) != _token(text)
    _, tty_text = _run(tmp_path, *_add_args(), is_tty=True)
    assert "amount: 1000" in tty_text and "amount: hidden" not in tty_text
    # the token is over the UTC time, so the same instant in another offset matches
    _, jst = _run(tmp_path, *_add_args(occurred_at="2026-01-01T09:00:00+09:00"))
    assert _token(jst) == _token(text) and f"occurred_at: {WHEN}" in jst
    # after the first record the same preview says the ledger is there, and the token moves (ledger id)
    assert _confirmed(tmp_path, *_add_args())[0] == 0
    _, after = _run(tmp_path, *_add_args())
    assert "ledger: exists" in after and "will be created" not in after
    assert _token(after) != _token(text)


# ---- cashflow add: recording and duplicate guards ------------------------------------------
def test_cashflow_add_records_exactly_one_row_then_refuses_duplicates(tmp_path):
    code, text = _confirmed(tmp_path, *_add_args())
    assert code == 0 and text.startswith("recorded: cf-")
    rows = _cashflows(tmp_path)
    assert len(rows) == 1
    cashflow_id, kind, currency, amount, source, note = rows[0]
    assert cashflow_id == text.split(": ", 1)[1].strip()
    assert (kind, currency, Decimal(amount), source) == ("deposit", "JPY", Decimal("1000"), "owner_manual")
    assert note == "owner: opening balance"

    code, again = _confirmed(tmp_path, *_add_args())                       # the same command again
    assert code == 1 and again == "refused: this cashflow is already recorded\n"
    assert len(_cashflows(tmp_path)) == 1

    code, other = _confirmed(tmp_path, *_add_args(note="second deposit"))  # the same money, another note
    assert code == 1 and "refused: an identical cashflow exists" in other
    assert len(_cashflows(tmp_path)) == 1

    code, allowed = _confirmed(tmp_path, *_add_args(note="second deposit"), "--allow-repeat")
    assert code == 0 and allowed.startswith("recorded: cf-")
    assert len(_cashflows(tmp_path)) == 2


def test_cashflow_money_direction_reaches_the_expected_balance(tmp_path):
    assert _confirmed(tmp_path, *_add_args(kind="deposit", amount="1000"))[0] == 0
    assert _confirmed(tmp_path, *_add_args(kind="withdrawal", amount="100", note="withdraw"))[0] == 0
    code, text = _confirmed(tmp_path, *_add_args(kind="adjustment", amount="-50", note="correction"))
    assert code == 0 and text.startswith("recorded: cf-")           # a negative adjustment is allowed
    code, refused = _run(tmp_path, *_add_args(kind="withdrawal", amount="-100", note="reverse"))
    assert code == 1 and refused.startswith("refused:") and "must be positive" in refused
    assert len(_cashflows(tmp_path)) == 3
    ledger = LiveLedger.open_readonly(live_layout(tmp_path))
    try:
        expected = live_balance.expected_balances(ledger, {"OAS/JPY": market_rules("OAS/JPY")})
    finally:
        ledger.close()
    assert expected["JPY"].total == Decimal("850")                  # 1000 - 100 + (-50)


@pytest.mark.parametrize("changed", [
    dict(occurred_at="2026-01-01T00:00:00"),              # naive
    dict(occurred_at="2026-01-02T00:00:00+00:00"),        # in the future
    dict(currency="XYZ"),
    dict(amount="1e1"),
    dict(amount="NaN"),
    dict(amount="0"),
    dict(amount="-5"),                                    # a deposit cannot be negative
    dict(amount="1.000000001"),                           # 9 decimal places
    dict(operator="   "),
    dict(note=""),
])
def test_bad_input_is_refused_and_creates_nothing(tmp_path, changed):
    before = _listing(tmp_path)
    code, text = _run(tmp_path, *_add_args(**changed))
    assert code == 1
    assert text.startswith("refused:") and len(text.splitlines()) == 1, text
    assert _listing(tmp_path) == before and not (tmp_path / "trading-live").exists()


def test_a_wrong_token_is_refused_and_creates_nothing(tmp_path):
    before = _listing(tmp_path)
    code, text = _run(tmp_path, *_add_args(), "--confirm", "0" * 10)
    assert code == 1 and text == "refused: confirmation token does not match\n"
    assert _listing(tmp_path) == before and not (tmp_path / "trading-live").exists()


def test_relative_state_dir_and_missing_arguments_are_usage_errors(tmp_path):
    out = io.StringIO()
    assert cli.main(["--state-dir", "relative", "status"], out=out, clock=lambda: NOW) == 2
    assert out.getvalue() == "refused: --state-dir must be an absolute path\n"
    for argv in ([], ["--state-dir", str(tmp_path)],
                 ["--state-dir", str(tmp_path), "cashflow", "add"], ["--state-dir", str(tmp_path), "bogus"]):
        assert cli.main(argv, out=io.StringIO(), clock=lambda: NOW) == 2
    assert _listing(tmp_path) == [] and not (tmp_path / "trading-live").exists()


def test_the_live_worker_holding_the_lease_refuses_the_write(tmp_path):
    layout = live_layout(tmp_path)
    ledger = LiveLedger.open(layout, paper_dir=tmp_path / "trading", now=NOW)
    held = live_lease.acquire_writer_lease(ledger, layout, now=NOW)
    try:
        code, text = _confirmed(tmp_path, *_add_args())
        assert code == 5 and text == "refused: the live worker holds the writer lease; stop it first\n"
        with ledger.read_cursor() as cur:
            assert cur.execute("SELECT COUNT(*) FROM cashflows").fetchone()[0] == 0
    finally:
        live_lease.release_writer_lease(ledger, held, now=NOW)
        ledger.close()


# ---- cashflow list: amounts only on a TTY ---------------------------------------------------
def test_cashflow_list_hides_amounts_unless_the_output_is_a_tty(tmp_path):
    assert _confirmed(tmp_path, *_add_args())[0] == 0
    code, text = _run(tmp_path, "cashflow", "list", is_tty=False)
    assert code == 0 and "amount: hidden" in text and "amount: 1000" not in text
    assert "deposit" in text and "JPY" in text and WHEN in text and "owner_manual" in text
    code, tty_text = _run(tmp_path, "cashflow", "list", is_tty=True)
    assert code == 0 and "amount: 1000" in tty_text and "amount: hidden" not in tty_text
    out = io.StringIO()                          # is_tty=None: the stream decides (StringIO is not a TTY)
    assert cli.main(["--state-dir", str(tmp_path), "cashflow", "list"], out=out, clock=lambda: NOW) == 0
    assert "amount: hidden" in out.getvalue()


# ---- no exchange, no later human-only APIs --------------------------------------------------
def test_cli_imports_no_exchange_or_network_module():
    tree = ast.parse((PKG / "cli.py").read_text(encoding="utf-8"))
    forbidden = ("docich.trading.exchanges", "ccxt", "socket", "subprocess", "urllib", "http", "requests")
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                parts = "docich.trading.live_owner.cli".split(".")
                module = ".".join(parts[: len(parts) - node.level] + ([module] if module else []))
            imported.append(module)
    assert imported, "the import scan found nothing"
    for name in imported:
        assert not any(name == bad or name.startswith(f"{bad}.") for bad in forbidden), name


def test_later_human_only_apis_are_not_mentioned_yet():
    files = sorted(PKG.glob("*.py"))
    assert [path.name for path in files] == ["__init__.py", "__main__.py", "cli.py"]
    for path in files:
        text = path.read_text(encoding="utf-8")
        for word in ("clear_safety_trip", "record_owner_activation", "clear_safety"):
            assert word not in text, (path.name, word)
