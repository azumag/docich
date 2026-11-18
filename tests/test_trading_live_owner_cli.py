"""The owner-only LIVE CLI (L4a): read-only status/list, two-step cashflow add and activate."""
from __future__ import annotations

import ast
import io
import json
import os
import re
import subprocess
import sys
import textwrap
from decimal import Decimal
from pathlib import Path

import pytest

from docich.trading.live import balance as live_balance
from docich.trading.live import control as live_control
from docich.trading.live import executor as live_executor
from docich.trading.live import lease as live_lease
from docich.trading.live import policy as live_policy
from docich.trading.live import safety as sf
from docich.trading.live import reconcile as live_reconcile
from docich.trading.live import views as live_views
from docich.trading.live.exchange import Capabilities, ExchangeAmbiguous, OrderRequest, OrderSnapshot
from docich.trading.live.exchange import FillPage, OrderPage
from docich.trading.live.ingest import observation_from
from docich.trading.live.executor import SubmitOutcome as S
from docich.trading.live.ledger import LiveLedger
from docich.trading.live.lease import acquire_writer_lease
from docich.trading.live.paths import live_layout
from docich.trading.live_owner import cli
from test_trading_live_support import (Harness, SimulatedCrash, Workspace, block_network, market_rules, policy_set,
                                       synthetic_mapping, synthetic_spec)

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
def _run(state_dir, *argv, is_tty=False, now=NOW, reader_factory=None):
    out = io.StringIO()
    extra = {}
    if reader_factory is not None:
        extra["reader_factory"] = reader_factory
    code = cli.main(["--state-dir", str(state_dir), *argv], out=out, clock=lambda: now, is_tty=is_tty, **extra)
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


# ---- no exchange, no network, no process ------------------------------------------------------
FORBIDDEN_MODULES = ("docich.trading.exchanges", "ccxt", "socket", "urllib", "http", "requests")


def _imported_modules(path: Path) -> list[str]:
    """Every module this file imports, as an absolute dotted name (relative imports resolved)."""
    rel = path.relative_to(PKG.parents[2]).with_suffix("")
    base = list(rel.parts[:-1])
    imported: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                keep = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                module = ".".join(keep + ([module] if module else []))
            imported.append(module)
    return imported


PROCESS_MODULES = ("subprocess", "multiprocessing", "pty")


def test_the_owner_package_imports_no_exchange_network_or_process_module():
    files = sorted(PKG.glob("*.py"))
    assert [path.name for path in files] == ["__init__.py", "__main__.py", "cli.py", "exchange_access.py"]
    for path in files:
        imported = _imported_modules(path)
        if path.name != "__init__.py":             # the docstring-only package marker imports nothing
            assert imported, path.name
        for name in imported:
            if name == "docich.trading.exchanges" or name.startswith("docich.trading.exchanges."):
                assert path.name == "exchange_access.py", (path.name, name)
                continue                           # the one module allowed to reach the exchange layer
            assert not any(name == bad or name.startswith(f"{bad}.") for bad in FORBIDDEN_MODULES), (path.name, name)
            assert name.split(".")[0] not in PROCESS_MODULES, (path.name, name)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id in ("os", "subprocess")
                    and node.attr in ("system", "popen")):
                raise AssertionError((path.name, f"{node.value.id}.{node.attr}"))


def test_exchange_access_imports_the_exchange_layer_only_inside_a_function():
    tree = ast.parse((PKG / "exchange_access.py").read_text(encoding="utf-8"))
    top = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
           and not (isinstance(node, ast.ImportFrom) and node.module == "__future__")]
    assert top == [], "importing exchange_access must not import the exchange layer"
    inside = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for child in ast.walk(node):
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    names = ([alias.name for alias in child.names] if isinstance(child, ast.Import)
                             else [child.module or ""])
                    if any(name == "ccxt" or name.startswith("docich.trading.exchanges") for name in names):
                        inside.append(node.name)
    assert inside == ["open_reader"]


def test_only_the_package_entry_point_imports_the_owner_package():
    package = "docich.trading.live_owner"
    offenders = []
    for path in PKG.parents[1].rglob("*.py"):        # src/docich
        if PKG in path.parents:
            continue                                  # inside the package: relative imports stay local
        for name in _imported_modules(path):
            if name == package or name.startswith(f"{package}."):
                offenders.append((str(path.relative_to(PKG.parents[1])), name))
    assert offenders == []
    assert f"{package}.cli" in _imported_modules(PKG / "__main__.py")


def test_the_cli_never_imports_the_exchange_layer(tmp_path):
    code = textwrap.dedent(f"""
        import io, sys
        from pathlib import Path
        from docich.trading.live_owner import cli
        state = Path({str(tmp_path)!r})
        clock = lambda: 1_800_000_000.0
        assert cli.main(["--state-dir", str(state), "status"], out=io.StringIO(), clock=clock) == 0
        assert cli.main(["--state-dir", str(state), "cashflow", "list"], out=io.StringIO(), clock=clock) == 0
        assert cli.main(["--state-dir", str(state), "cashflow", "add", "--kind", "deposit", "--currency", "JPY",
                         "--amount", "1", "--occurred-at", "2026-01-01T00:00:00+00:00", "--operator", "owner",
                         "--note", "n"], out=io.StringIO(), clock=clock) == 0
        leaked = sorted(m for m in sys.modules if m == "docich.trading.live_owner.exchange_access"
                        or m.startswith("docich.trading.exchanges"))
        print("clean" if not leaked else "leaked:" + ",".join(leaked))
    """)
    result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True,
                            env=dict(os.environ, PYTHONPATH=str(PKG.parents[2])))
    assert result.stdout.strip() == "clean", result.stderr


def test_the_live_package_never_pulls_in_the_owner_cli():
    code = textwrap.dedent("""
        import sys
        import docich.trading.live.executor, docich.trading.live.reconcile, docich.trading.live.ledger
        leaked = sorted(m for m in sys.modules
                        if m == "docich.trading.live_owner" or m.startswith("docich.trading.live_owner."))
        print("clean" if not leaked else "leaked:" + ",".join(leaked))
    """)
    result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True,
                            env=dict(os.environ, PYTHONPATH=str(PKG.parents[2])))
    assert result.stdout.strip() == "clean", result.stderr


def test_human_only_clears_are_called_only_from_the_owner_cli():
    files = sorted(PKG.glob("*.py"))
    assert [path.name for path in files] == ["__init__.py", "__main__.py", "cli.py", "exchange_access.py"]
    for path in files:
        text = path.read_text(encoding="utf-8")
        if path.name == "cli.py":
            continue
        for word in ("clear_safety_trip", "clear_safety"):
            assert word not in text, (path.name, word)
        assert "record_owner_activation" not in text, path.name
    cli_text = (PKG / "cli.py").read_text(encoding="utf-8")
    assert cli_text.count("safety.clear_safety_trip(") == 1
    assert ".clear_safety(" not in cli_text
    assert "record_owner_activation" in cli_text


# ---- activate: the owner's policy set activation --------------------------------------------
def _snapshot(tmp_path):
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(synthetic_mapping()), encoding="utf-8")
    return path, live_policy.experiment_digest(synthetic_spec())


def _activate_args(snapshot, digest, **kw):
    values = {"snapshot": str(snapshot), "experiment_digest": digest, "operator": "owner",
              "note": "owner activation"}
    values.update(kw)
    argv = []
    for name, value in values.items():
        argv += [f"--{name.replace('_', '-')}", value]
    return ["activate", *argv]


def _activate_run(state_dir, snapshot, digest, *, confirm=None, **kw):
    argv = _activate_args(snapshot, digest, **kw)
    if confirm is not None:
        argv += ["--confirm", confirm]
    return _run(state_dir, *argv)


def _activation_rows(workspace):
    ledger = LiveLedger.open_readonly(workspace.layout)
    try:
        with ledger.read_cursor() as cur:
            return [row[0] for row in cur.execute("SELECT reason FROM policy_activations ORDER BY activation_id")]
    finally:
        ledger.close()


def _active_digest(workspace):
    ledger = LiveLedger.open_readonly(workspace.layout)
    try:
        return ledger.active_policy_set_digest()
    finally:
        ledger.close()


def _refused(state_dir, *argv):
    before = _listing(state_dir)
    code, text = _run(state_dir, *argv)
    assert code == 1, text
    assert text.startswith("refused:") and len(text.splitlines()) == 1, text
    assert _listing(state_dir) == before and not (Path(state_dir) / "trading-live" / "live.sqlite3").exists()
    return text


def test_activate_preview_creates_no_ledger(workspace, tmp_path):
    ps = policy_set(live_policy.INITIAL_SYMBOLS)
    snapshot, digest = _snapshot(tmp_path)
    before = _listing(workspace.state)
    code, text = _activate_run(workspace.state, snapshot, digest)
    assert code == 0
    assert "ledger: will be created" in text
    assert f"policy_set: {ps.digest[:12]}" in text
    assert f"experiment: {digest[:12]}" in text
    assert "mode: none" in text and "operator: owner" in text
    assert "code_revision" not in text and "enable_file" not in text
    assert re.fullmatch(r"[0-9a-f]{10}", _token(text))
    assert _listing(workspace.state) == before and not workspace.layout.ledger_path.exists()


def test_ledger_id_keeps_following_a_symlinked_ledger(workspace):
    ledger = LiveLedger.open(workspace.layout, paper_dir=workspace.paper, now=NOW)
    ledger.close()
    real = workspace.state / "live-real.sqlite3"
    workspace.layout.ledger_path.rename(real)
    workspace.layout.ledger_path.symlink_to(real)
    assert cli._ledger_id(live_layout(workspace.state)) != "no-ledger"


def test_activate_confirm_records_the_activation_and_the_ledger_accepts_a_repeat(workspace, tmp_path):
    ps = policy_set(live_policy.INITIAL_SYMBOLS)
    snapshot, digest = _snapshot(tmp_path)
    code, preview = _activate_run(workspace.state, snapshot, digest)
    assert code == 0
    code, text = _activate_run(workspace.state, snapshot, digest, confirm=_token(preview))
    assert code == 0 and text == f"activated: {ps.digest[:12]}\n"
    assert _activation_rows(workspace) == ["owner_initial"] and _active_digest(workspace) == ps.digest
    # the ledger accepts the same activation again: the second row is an owner_manual one
    code, preview = _activate_run(workspace.state, snapshot, digest)
    assert code == 0 and "ledger: exists" in preview
    code, text = _activate_run(workspace.state, snapshot, digest, confirm=_token(preview))
    assert code == 0 and text.startswith("activated: ")
    assert _activation_rows(workspace) == ["owner_initial", "owner_manual"]
    assert _active_digest(workspace) == ps.digest


def test_activate_needs_no_control_file_but_shows_its_mode_when_present(workspace, tmp_path):
    snapshot, digest = _snapshot(tmp_path)
    code, preview = _activate_run(workspace.state, snapshot, digest)
    assert code == 0 and "mode: none" in preview                  # no control file at all
    workspace.write_control(mode="shadow")
    code, preview = _activate_run(workspace.state, snapshot, digest)
    assert code == 0 and "mode: shadow" in preview
    code, text = _activate_run(workspace.state, snapshot, digest, confirm=_token(preview))
    assert code == 0 and text.startswith("activated: ")
    assert _active_digest(workspace) == policy_set(live_policy.INITIAL_SYMBOLS).digest


def test_activate_refuses_a_bad_snapshot_a_wrong_token_or_a_bad_operator(workspace, tmp_path):
    snapshot, digest = _snapshot(tmp_path)
    assert "confirmation token does not match" in _refused(
        workspace.state, *_activate_args(snapshot, digest), "--confirm", "0" * 10)
    assert "experiment digest mismatch" in _refused(workspace.state, *_activate_args(snapshot, "0" * 64))
    assert "LivePolicyError" in _refused(workspace.state, *_activate_args("no-such/snapshot.json", digest))
    assert "LivePolicyError" in _refused(workspace.state, *_activate_args(tmp_path / "missing.json", digest))
    assert "operator must not be empty" in _refused(workspace.state, *_activate_args(snapshot, digest, operator=" "))


def test_activate_with_the_worker_holding_the_lease_returns_five(workspace, tmp_path):
    snapshot, digest = _snapshot(tmp_path)
    ledger = LiveLedger.open(workspace.layout, paper_dir=workspace.paper, now=NOW)
    try:
        code, preview = _activate_run(workspace.state, snapshot, digest)
        assert code == 0 and "ledger: exists" in preview
        held = live_lease.acquire_writer_lease(ledger, workspace.layout, now=NOW)
        try:
            code, text = _activate_run(workspace.state, snapshot, digest, confirm=_token(preview))
            assert code == 5 and text == "refused: the live worker holds the writer lease; stop it first\n"
            with ledger.read_cursor() as cur:
                assert cur.execute("SELECT COUNT(*) FROM policy_activations").fetchone()[0] == 0
        finally:
            live_lease.release_writer_lease(ledger, held, now=NOW)
    finally:
        ledger.close()


# ---- unknown: list / show / resolve ---------------------------------------------------------
def _unknown_harness(workspace, **kw):
    h = Harness(workspace, symbols=live_policy.INITIAL_SYMBOLS, **kw)
    h.mock.set_book("OAS/JPY", asks=[(Decimal("100"), Decimal("1000"))], bids=[(Decimal("99"), Decimal("1000"))])
    return h


def _lost_buy(h, key="b1", amount="10", price="90", fault="LOST_RESPONSE"):
    h.mock.faults.push("submit", fault)
    intent, outcome = h.send(side="buy", key=key, amount=amount, price=price)
    assert outcome is S.UNKNOWN, outcome
    return intent


def _released(h):
    """Hand the writer lease back so the CLI can take it (the Harness keeps it for its lifetime)."""
    live_lease.release_writer_lease(h.ledger, h.lease, now=h.clock.seconds())


def _orders(ledger):
    with ledger.read_cursor() as cur:
        return cur.execute("SELECT exchange_order_id, intent_id FROM orders ORDER BY exchange_order_id").fetchall()


def _events(ledger):
    """Every event except the writer-lease ones (taking the lease is itself recorded)."""
    return [(event.kind, event.entity_id) for event in ledger.events(limit=1000)
            if event.kind not in ("lease_acquired", "lease_released")]


def _evidence(ledger, intent_id):
    with ledger.read_cursor() as cur:
        return cur.execute("SELECT evidence FROM intents WHERE intent_id=?", (intent_id,)).fetchone()[0]


def _token_for(state_dir, intent_id, mode, evidence, operator="owner", override=False):
    content = {"intent_id": intent_id, "mode": mode, "evidence": evidence, "operator": operator,
               "override_incomplete_proof": "true" if override else "false"}
    return cli._token(content, cli._ledger_id(live_layout(state_dir)))


def test_unknown_list_without_a_ledger_creates_nothing(tmp_path):
    before = _listing(tmp_path)
    code, text = _run(tmp_path, "unknown", "list")
    assert code == 0 and text == "ledger: not created\n"
    assert _listing(tmp_path) == before and not (tmp_path / "trading-live").exists()


def test_unknown_list_shows_the_lost_intents_and_hides_amounts(workspace):
    h = _unknown_harness(workspace)
    try:
        intent = _lost_buy(h)
        code, text = _run(workspace.state, "unknown", "list", now=h.clock.seconds())
        assert code == 0 and text.count("\n") == 1
        assert intent.intent_id[:12] in text and "OAS/JPY buy limit" in text and "amount: hidden" in text
        _, tty = _run(workspace.state, "unknown", "list", now=h.clock.seconds(), is_tty=True)
        assert "amount: 10" in tty and "price: 90" in tty
    finally:
        h.close()


def test_unknown_show_reports_the_checks_and_changes_nothing(workspace):
    h = _unknown_harness(workspace)
    try:
        intent = _lost_buy(h)
        before = (_events(h.ledger), _orders(h.ledger), h.ledger.get_intent(intent.intent_id).state)
        code, text = _run(workspace.state, "unknown", "show", "--intent", intent.intent_id,
                          now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert code == 0
        assert f"intent: {intent.intent_id[:12]}" in text and "window_ms: " in text
        assert "open_list_complete: true" in text and "candidates: 1" in text and "matching: 1" in text
        assert "order: id: hidden" in text and "settle_elapsed: false" in text
        _, tty = _run(workspace.state, "unknown", "show", "--intent", intent.intent_id, is_tty=True,
                      now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert f"order: {h.mock.all_orders()[0].exchange_order_id}" in tty
        assert (_events(h.ledger), _orders(h.ledger), h.ledger.get_intent(intent.intent_id).state) == before
    finally:
        h.close()


def test_unknown_resolve_found_order_records_the_owner_evidence(workspace):
    h = _unknown_harness(workspace)
    try:
        intent = _lost_buy(h)
        eid = h.mock.all_orders()[0].exchange_order_id
        argv = ["unknown", "resolve", "--intent", intent.intent_id, "--found-order", eid,
                "--evidence", "checked the exchange history by hand", "--operator", "owner"]
        code, preview = _run(workspace.state, *argv, now=h.clock.seconds(),
                             reader_factory=lambda state_dir: h.mock)
        # the harness still holds the lease: a preview that took it would have returned 5
        assert code == 0 and "order_matches: true" in preview and "token: " in preview
        assert h.ledger.get_intent(intent.intent_id).state == "UNKNOWN_SUBMISSION" and _orders(h.ledger) == []
        _released(h)
        code, text = _run(workspace.state, *argv, "--confirm", _token(preview), now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 0 and text == "resolved: found\n"
        after = h.ledger.get_intent(intent.intent_id)
        assert after.state in ("ACKED", "PARTIALLY_FILLED", "FILLED") and after.reason_code == "found_by_owner"
        proof = json.loads(_evidence(h.ledger, intent.intent_id))
        assert proof["method"] == "owner_manual" and proof["mode"] == "found_order"
        assert proof["found_order_id"] == eid and proof["evidence"] == "checked the exchange history by hand"
    finally:
        h.close()


def test_unknown_resolve_refuses_an_order_that_does_not_match_and_records_nothing(workspace):
    h = _unknown_harness(workspace)
    try:
        intent = _lost_buy(h)
        h.mock.place_external_order(OrderRequest("OAS/JPY", "buy", "limit", Decimal("3"), Decimal("90"), None))
        other = [snap for snap in h.mock.all_orders() if snap.amount == Decimal("3")][0].exchange_order_id
        before = (_events(h.ledger), _orders(h.ledger), h.trips())
        argv = ["unknown", "resolve", "--intent", intent.intent_id, "--found-order", other,
                "--evidence", "checked the exchange history by hand", "--operator", "owner"]
        code, text = _run(workspace.state, *argv, now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 1 and text == "refused: the exchange order does not match this intent\n"
        _released(h)
        # the same command with a token of its own must hit the check again under the lease
        token = _token_for(workspace.state, intent.intent_id, f"found:{other}",
                           "checked the exchange history by hand")
        code, text = _run(workspace.state, *argv, "--confirm", token, now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 1 and text == "refused: the exchange order does not match this intent\n"
        assert (_events(h.ledger), _orders(h.ledger), h.trips()) == before
        assert h.ledger.get_intent(intent.intent_id).state == "UNKNOWN_SUBMISSION"
    finally:
        h.close()


def test_unknown_resolve_refuses_an_order_already_linked_to_another_intent(workspace):
    h = _unknown_harness(workspace)
    try:
        first, _ = h.send(side="buy", key="a1", amount="10", price="90")     # resting, then filled
        h.mock.trade_against("OAS/JPY", taker_side="sell", price="90", amount="10")
        h.ex.step()
        assert h.state(first.intent_id) == "FILLED"
        second = _lost_buy(h, key="b1", amount="10", price="90")            # same shape, still unknown
        first_eid = h.ledger.get_intent(first.intent_id).exchange_order_id
        before = (_events(h.ledger), _orders(h.ledger), h.trips())
        code, text = _run(workspace.state, "unknown", "resolve", "--intent", second.intent_id,
                          "--found-order", first_eid, "--evidence", "picked the wrong order id",
                          "--operator", "owner", now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert code == 1 and text == "refused: that exchange order id belongs to another intent\n"
        assert (_events(h.ledger), _orders(h.ledger), h.trips()) == before
        assert h.ledger.get_intent(second.intent_id).state == "UNKNOWN_SUBMISSION"
    finally:
        h.close()


def test_unknown_resolve_not_created_lets_the_machine_prove_it_first(workspace):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, outcome = h.send(side="buy", key="b1", amount="10", price="90")
        assert outcome is S.UNKNOWN
        argv = ["unknown", "resolve", "--intent", intent.intent_id, "--not-created",
                "--evidence", "no such order in the exchange history", "--operator", "owner"]
        code, text = _run(workspace.state, *argv, now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 1 and text == "refused: settle time has not passed\n"
        h.advance(200)
        code, preview = _run(workspace.state, *argv, now=h.clock.seconds(),
                             reader_factory=lambda state_dir: h.mock)
        assert code == 0 and "settle_elapsed: true" in preview and "matching: 0" in preview
        _released(h)
        code, text = _run(workspace.state, *argv, "--confirm", _token(preview), now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 0 and text == "resolved: by the machine (not_created)\n"
        assert h.ledger.get_intent(intent.intent_id).state == "NOT_CREATED"
        # the machine's own proof is what was recorded, not the owner's text
        assert '"method":"list_match"' in _evidence(h.ledger, intent.intent_id)
    finally:
        h.close()


def test_unknown_resolve_not_created_accepts_an_override_for_an_unprovable_sell(workspace):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        buy, _ = h.send(side="buy", key="b0", amount="10", price="100")
        assert h.state(buy.intent_id) == "FILLED"
        h.mock.faults.push("submit", "LOST_REQUEST")
        sell, outcome = h.send(side="sell", key="s1", amount="10", price="99")
        assert outcome is S.UNKNOWN
        h.mock.adjust_balance("OAS", "-2")                 # the base balance can no longer be proven
        h.advance(500)
        argv = ["unknown", "resolve", "--intent", sell.intent_id, "--not-created",
                "--evidence", "checked the exchange history by hand", "--operator", "owner"]
        _released(h)
        code, preview = _run(workspace.state, *argv, now=h.clock.seconds(),
                             reader_factory=lambda state_dir: h.mock)
        assert code == 0 and "override_incomplete_proof: false" in preview
        code, text = _run(workspace.state, *argv, "--confirm", _token(preview), now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 1 and "the machine cannot prove this" in text
        assert h.ledger.get_intent(sell.intent_id).state == "UNKNOWN_SUBMISSION"
        code, preview = _run(workspace.state, *argv, "--override-incomplete-proof", now=h.clock.seconds(),
                             reader_factory=lambda state_dir: h.mock)
        assert code == 0 and "override_incomplete_proof: true" in preview
        code, text = _run(workspace.state, *argv, "--override-incomplete-proof", "--confirm", _token(preview),
                          now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert code == 0 and text == "resolved: not created (owner evidence)\n"
        after = h.ledger.get_intent(sell.intent_id)
        assert after.state == "NOT_CREATED" and after.reason_code == "owner_not_created"
        proof = json.loads(_evidence(h.ledger, sell.intent_id))
        assert proof["method"] == "owner_manual" and proof["mode"] == "not_created"
        assert proof["machine"]["override_incomplete_proof"] == "true" and proof["machine"]["result"] == "waiting"
        assert proof["machine"]["fills_complete"] == "true"
        # the exit is no longer pinned by the unknown submission
        ledger = LiveLedger.open(h.ws.layout, paper_dir=h.ws.paper, now=h.clock.seconds())
        lease = acquire_writer_lease(ledger, h.ws.layout, now=h.clock.seconds())
        try:
            ex = live_executor.OrderExecutor(ledger, lease, reader=h.mock, sender=h.mock, enable_check=h._gate,
                                             clock=h.clock.seconds, symbols=h.symbols, config=h.config)
            again = ex.plan(h.req(side="sell", key="s2", amount="8", price="99"))
            assert again.state == "PLANNED"
        finally:
            live_lease.release_writer_lease(ledger, lease, now=h.clock.seconds())
            ledger.close()
    finally:
        h.close()


def test_unknown_resolve_refuses_a_matching_open_order_for_not_created(workspace):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, _ = h.send(side="buy", key="b1", amount="10", price="90")
        h.mock.place_external_order(OrderRequest("OAS/JPY", "buy", "limit", Decimal("10"), Decimal("90"), None))
        before = (_events(h.ledger), _orders(h.ledger), h.trips())
        code, text = _run(workspace.state, "unknown", "resolve", "--intent", intent.intent_id, "--not-created",
                          "--evidence", "no such order in the exchange history", "--operator", "owner",
                          now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert code == 1 and text == "refused: a matching order exists on the exchange; use --found-order\n"
        assert (_events(h.ledger), _orders(h.ledger), h.trips()) == before
    finally:
        h.close()


class _LateIncompletePage:
    """The response changes between reads: no match first, then a cut-off page that does contain one.

    The first open-orders read (the CLI's own candidate check) sees nothing.  Every later read returns
    an incomplete page with the intent's order: try_resolve must answer "waiting", not a proof.
    """

    def __init__(self, mock, matching, *, fail_on: int | None = None):
        self.mock, self.matching, self.fail_on = mock, matching, fail_on
        self.open_reads = 0

    @property
    def capabilities(self):
        return self.mock.capabilities

    def market_rules(self, symbol):
        return self.mock.market_rules(symbol)

    def fetch_balance(self):
        return self.mock.fetch_balance()

    def fetch_open_orders(self, symbol):
        self.open_reads += 1
        if self.open_reads == 1:
            return OrderPage((), complete=True)
        if self.fail_on is not None and self.open_reads == self.fail_on:
            raise ExchangeAmbiguous("boom")
        return OrderPage((self.matching,), complete=False)

    def fetch_fills(self, symbol, *, since_ms, exchange_order_id=None):
        return FillPage((), complete=True)

    def fetch_order(self, symbol, exchange_order_id):
        return self.mock.fetch_order(symbol, exchange_order_id)


def test_not_created_refuses_an_order_that_appears_after_the_first_read(workspace):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, outcome = h.send(side="buy", key="b1", amount="10", price="90")
        assert outcome is S.UNKNOWN
        h.advance(500)
        eid = h.mock.place_external_order(
            OrderRequest("OAS/JPY", "buy", "limit", Decimal("10"), Decimal("90"), None))
        matching = h.mock.fetch_order("OAS/JPY", eid)
        _released(h)
        before_trips = h.trips()
        argv = ["unknown", "resolve", "--intent", intent.intent_id, "--not-created",
                "--override-incomplete-proof", "--evidence", "checked the exchange history by hand",
                "--operator", "owner"]
        code, preview = _run(workspace.state, *argv, now=h.clock.seconds(),
                             reader_factory=lambda state_dir: _LateIncompletePage(h.mock, matching))
        assert code == 0 and "matching: 0" in preview, preview
        code, text = _run(workspace.state, *argv, "--confirm", _token(preview), now=h.clock.seconds(),
                          reader_factory=lambda state_dir: _LateIncompletePage(h.mock, matching))
        after = h.ledger.get_intent(intent.intent_id)
        result = (code, text, after.state, len(h.ledger.list_intents(states=("NOT_CREATED",))), h.trips())
        assert result == (1, "refused: a matching order exists on the exchange; use --found-order\n",
                          "UNKNOWN_SUBMISSION", 0, before_trips)
    finally:
        h.close()


def test_not_created_refuses_a_late_matching_order_when_the_machine_waits(workspace, monkeypatch):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, outcome = h.send(side="buy", key="b1", amount="10", price="90")
        assert outcome is S.UNKNOWN
        h.advance(500)
        eid = h.mock.place_external_order(
            OrderRequest("OAS/JPY", "buy", "limit", Decimal("10"), Decimal("90"), None))
        matching = h.mock.fetch_order("OAS/JPY", eid)

        def waiting(ex, pending):
            return "waiting"                                   # this test is about the fresh read only

        monkeypatch.setattr(live_reconcile, "try_resolve", waiting)
        _released(h)
        before_trips = h.trips()
        argv = ["unknown", "resolve", "--intent", intent.intent_id, "--not-created",
                "--override-incomplete-proof", "--evidence", "checked the exchange history by hand",
                "--operator", "owner"]
        code, preview = _run(workspace.state, *argv, now=h.clock.seconds(),
                             reader_factory=lambda state_dir: _LateIncompletePage(h.mock, matching))
        assert code == 0 and "matching: 0" in preview, preview
        code, text = _run(workspace.state, *argv, "--confirm", _token(preview), now=h.clock.seconds(),
                          reader_factory=lambda state_dir: _LateIncompletePage(h.mock, matching))
        after = h.ledger.get_intent(intent.intent_id)
        result = (code, text, after.state, len(h.ledger.list_intents(states=("NOT_CREATED",))), h.trips())
        assert result == (1, "refused: a matching order exists on the exchange; use --found-order\n",
                          "UNKNOWN_SUBMISSION", 0, before_trips)
    finally:
        h.close()


def test_not_created_refuses_when_the_fresh_read_is_exchange_unavailable(workspace, monkeypatch):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, outcome = h.send(side="buy", key="b1", amount="10", price="90")
        assert outcome is S.UNKNOWN
        h.advance(500)
        eid = h.mock.place_external_order(
            OrderRequest("OAS/JPY", "buy", "limit", Decimal("10"), Decimal("90"), None))
        matching = h.mock.fetch_order("OAS/JPY", eid)

        def waiting(ex, pending):
            return "waiting"                                   # only the fresh read touches the exchange

        monkeypatch.setattr(live_reconcile, "try_resolve", waiting)
        _released(h)
        argv = ["unknown", "resolve", "--intent", intent.intent_id, "--not-created",
                "--override-incomplete-proof", "--evidence", "checked the exchange history by hand",
                "--operator", "owner"]
        code, preview = _run(workspace.state, *argv, now=h.clock.seconds(),
                             reader_factory=lambda state_dir: _LateIncompletePage(h.mock, matching))
        assert code == 0, preview                              # the preview's single read still succeeds
        code, text = _run(workspace.state, *argv, "--confirm", _token(preview), now=h.clock.seconds(),
                          reader_factory=lambda state_dir: _LateIncompletePage(h.mock, matching, fail_on=2))
        assert code == 1 and text.startswith("refused: exchange unavailable")
        assert h.ledger.get_intent(intent.intent_id).state == "UNKNOWN_SUBMISSION"
        assert h.ledger.list_intents(states=("NOT_CREATED",)) == []
    finally:
        h.close()


def test_found_order_links_the_order_with_an_active_blocking_stop(workspace):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        intent = _lost_buy(h, fault="LOST_RESPONSE")
        eid = h.mock.all_orders()[0].exchange_order_id
        h.ledger.trip_safety(sf.SafetyReason.UNKNOWN_ORDER, detail="test stop", now=h.clock.seconds())
        assert h.trips() == ["unknown_order"]
        _released(h)
        argv = ["unknown", "resolve", "--intent", intent.intent_id, "--found-order", eid,
                "--evidence", "found it on the exchange by hand", "--operator", "owner"]
        code, preview = _run(workspace.state, *argv, now=h.clock.seconds(),
                             reader_factory=lambda state_dir: h.mock)
        assert code == 0 and "order_matches: true" in preview, preview
        code, text = _run(workspace.state, *argv, "--confirm", _token(preview), now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        after = h.ledger.get_intent(intent.intent_id)
        assert (code, text, after.state, after.reason_code) == (0, "resolved: found\n", "ACKED", "found_by_owner")
        assert h.trips() == ["unknown_order"]                  # the stop was not the thing resolved
    finally:
        h.close()


@pytest.mark.parametrize("argv, expected", [
    (["--not-created", "--found-order", "1", "--evidence", "checked the exchange history by hand"],
     "refused: give exactly one of --found-order and --not-created\n"),
    (["--evidence", "checked the exchange history by hand"],
     "refused: give exactly one of --found-order and --not-created\n"),
    (["--not-created", "--evidence", "too short"], "refused: evidence must be 20..400 characters\n"),
    (["--not-created", "--evidence", "x" * 401], "refused: evidence must be 20..400 characters\n"),
    (["--found-order", "no-such-id", "--evidence", "x" * 20],
     "refused: the exchange does not know that order id\n"),
])
def test_unknown_resolve_refuses_bad_arguments(workspace, argv, expected):
    h = _unknown_harness(workspace)
    try:
        intent = _lost_buy(h)
        before = (_events(h.ledger), _orders(h.ledger), h.trips())
        code, text = _run(workspace.state, "unknown", "resolve", "--intent", intent.intent_id, *argv,
                          "--operator", "owner", now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert code == 1 and text == expected
        assert (_events(h.ledger), _orders(h.ledger), h.trips()) == before
        assert h.ledger.get_intent(intent.intent_id).state == "UNKNOWN_SUBMISSION"
    finally:
        h.close()


def test_unknown_resolve_refuses_an_unknown_intent_a_known_state_or_a_wrong_token(workspace):
    h = _unknown_harness(workspace)
    try:
        intent = _lost_buy(h)
        planned = h.ex.plan(h.req(symbol="XYM/JPY", key="p1"))
        code, text = _run(workspace.state, "unknown", "resolve", "--intent", "0" * 32, "--not-created",
                          "--evidence", "checked the exchange history by hand", "--operator", "owner",
                          now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert code == 1 and text == "refused: unknown intent\n"
        code, text = _run(workspace.state, "unknown", "resolve", "--intent", planned.intent_id, "--not-created",
                          "--evidence", "checked the exchange history by hand", "--operator", "owner",
                          now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert code == 1 and text == "refused: not an unknown submission\n"
        code, text = _run(workspace.state, "unknown", "resolve", "--intent", intent.intent_id, "--not-created",
                          "--evidence", "checked the exchange history by hand", "--operator", "owner",
                          "--confirm", "0" * 10, now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 1 and text == "refused: confirmation token does not match\n"
        assert h.ledger.get_intent(intent.intent_id).state == "UNKNOWN_SUBMISSION"
        assert h.ledger.get_intent(planned.intent_id).state == "PLANNED"
    finally:
        h.close()


def test_unknown_resolve_with_the_worker_holding_the_lease_returns_five(workspace):
    h = _unknown_harness(workspace)
    try:
        intent = _lost_buy(h)
        eid = h.mock.all_orders()[0].exchange_order_id
        argv = ["unknown", "resolve", "--intent", intent.intent_id, "--found-order", eid,
                "--evidence", "checked the exchange history by hand", "--operator", "owner"]
        code, preview = _run(workspace.state, *argv, now=h.clock.seconds(),
                             reader_factory=lambda state_dir: h.mock)
        assert code == 0
        code, text = _run(workspace.state, *argv, "--confirm", _token(preview), now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 5 and text == "refused: the live worker holds the writer lease; stop it first\n"
        assert h.ledger.get_intent(intent.intent_id).state == "UNKNOWN_SUBMISSION" and _orders(h.ledger) == []
    finally:
        h.close()


def test_unknown_commands_refuse_a_broken_exchange_and_change_nothing(workspace):
    class Broken:
        def fetch_order(self, symbol, exchange_order_id):
            raise ExchangeAmbiguous("boom")

        def fetch_open_orders(self, symbol):
            raise ExchangeAmbiguous("boom")

    h = _unknown_harness(workspace)
    try:
        intent = _lost_buy(h)
        eid = h.mock.all_orders()[0].exchange_order_id
        before = (_events(h.ledger), _orders(h.ledger), h.trips())
        code, text = _run(workspace.state, "unknown", "show", "--intent", intent.intent_id,
                          now=h.clock.seconds(), reader_factory=lambda state_dir: Broken())
        assert code == 1 and text == "refused: exchange unavailable: ExchangeAmbiguous\n"
        for argv in (["--found-order", eid], ["--not-created"]):
            code, text = _run(workspace.state, "unknown", "resolve", "--intent", intent.intent_id, *argv,
                              "--evidence", "checked the exchange history by hand", "--operator", "owner",
                              now=h.clock.seconds(), reader_factory=lambda state_dir: Broken())
            assert code == 1 and text == "refused: exchange unavailable: ExchangeAmbiguous\n"
        assert (_events(h.ledger), _orders(h.ledger), h.trips()) == before
        assert h.ledger.get_intent(intent.intent_id).state == "UNKNOWN_SUBMISSION"
    finally:
        h.close()


# ---- unknown: orders that are only visible in the fill history -------------------------------
def _filled_lost_buy(h, key="b1", amount="10", price="100"):
    """A lost submit that really filled at once: absent from the open list, present in the fills."""
    h.mock.faults.push("submit", "LOST_RESPONSE")
    intent, outcome = h.send(side="buy", key=key, amount=amount, price=price)
    assert outcome is S.UNKNOWN, outcome
    assert h.mock.fetch_open_orders("OAS/JPY").orders == ()
    return intent, h.mock.all_orders()[0].exchange_order_id


def test_not_created_refuses_a_lost_order_that_filled_at_once(workspace):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        intent, eid = _filled_lost_buy(h)
        h.advance(500)
        _released(h)
        code, show = _run(workspace.state, "unknown", "show", "--intent", intent.intent_id, now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 0 and "candidates: 1" in show and "matching: 1" in show
        argv = ["unknown", "resolve", "--intent", intent.intent_id, "--not-created",
                "--evidence", "checked the exchange history by hand", "--operator", "owner"]
        for extra in ([], ["--override-incomplete-proof"]):
            code, text = _run(workspace.state, *argv, *extra, now=h.clock.seconds(),
                              reader_factory=lambda state_dir: h.mock)
            assert code == 1, text
            assert text == "refused: a matching order exists on the exchange; use --found-order\n"
        assert h.ledger.get_intent(intent.intent_id).state == "UNKNOWN_SUBMISSION"
        assert _orders(h.ledger) == [] and h.trips() == []
        # the found path resolves the very same order
        code, preview = _run(workspace.state, "unknown", "resolve", "--intent", intent.intent_id,
                             "--found-order", eid, "--evidence", "found it in the fill history",
                             "--operator", "owner", now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert code == 0, preview
        code, text = _run(workspace.state, "unknown", "resolve", "--intent", intent.intent_id,
                          "--found-order", eid, "--evidence", "found it in the fill history",
                          "--operator", "owner", "--confirm", _token(preview), now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 0 and text == "resolved: found\n"
        assert h.ledger.get_intent(intent.intent_id).reason_code == "found_by_owner"
    finally:
        h.close()


def test_a_fill_of_another_order_is_a_candidate_but_does_not_match(workspace):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, outcome = h.send(side="buy", key="b1", amount="10", price="90")
        assert outcome is S.UNKNOWN
        h.mock.place_external_order(OrderRequest("OAS/JPY", "buy", "limit", Decimal("3"), Decimal("100"), None))
        h.advance(500)
        _released(h)
        code, show = _run(workspace.state, "unknown", "show", "--intent", intent.intent_id, now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 0 and "candidates: 1" in show and "matching: 0" in show
        argv = ["unknown", "resolve", "--intent", intent.intent_id, "--not-created",
                "--evidence", "checked the exchange history by hand", "--operator", "owner"]
        code, preview = _run(workspace.state, *argv, now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert code == 0 and "matching: 0" in preview                  # the candidate stage does not refuse
        code, text = _run(workspace.state, *argv, "--confirm", _token(preview), now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        # the unaccounted external fill leaves the balance unproven: only the owner can conclude
        assert code == 1 and "the machine cannot prove this" in text
        assert h.ledger.get_intent(intent.intent_id).state == "UNKNOWN_SUBMISSION"
    finally:
        h.close()


def test_not_created_refuses_two_matching_orders(workspace):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, _ = h.send(side="buy", key="b1", amount="10", price="90")
        for _ in range(2):
            h.mock.place_external_order(OrderRequest("OAS/JPY", "buy", "limit", Decimal("10"), Decimal("90"), None))
        _released(h)
        code, text = _run(workspace.state, "unknown", "resolve", "--intent", intent.intent_id, "--not-created",
                          "--evidence", "checked the exchange history by hand", "--operator", "owner",
                          "--override-incomplete-proof", now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 1 and text == "refused: a matching order exists on the exchange; use --found-order\n"
        assert h.ledger.get_intent(intent.intent_id).state == "UNKNOWN_SUBMISSION"
    finally:
        h.close()


def test_an_unlinked_order_row_is_a_candidate_and_can_be_found(workspace):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, _ = h.send(side="buy", key="b1", amount="10", price="90")
        h.mock.place_external_order(OrderRequest("OAS/JPY", "buy", "limit", Decimal("10"), Decimal("90"), None))
        eid = h.mock.all_orders()[0].exchange_order_id
        # the sweep's own write: the ledger knows the order but not which intent it belongs to
        h.ledger.record_order_observation(
            h.lease, observation_from(h.mock.fetch_order("OAS/JPY", eid), None), now=h.clock.seconds())
        assert live_views.order_row(h.ledger, eid).intent_id is None
        _released(h)
        code, text = _run(workspace.state, "unknown", "resolve", "--intent", intent.intent_id, "--not-created",
                          "--evidence", "checked the exchange history by hand", "--operator", "owner",
                          "--override-incomplete-proof", now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 1 and text == "refused: a matching order exists on the exchange; use --found-order\n"
        code, preview = _run(workspace.state, "unknown", "resolve", "--intent", intent.intent_id,
                             "--found-order", eid, "--evidence", "that unlinked order is mine",
                             "--operator", "owner", now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert code == 0, preview
        code, text = _run(workspace.state, "unknown", "resolve", "--intent", intent.intent_id,
                          "--found-order", eid, "--evidence", "that unlinked order is mine",
                          "--operator", "owner", "--confirm", _token(preview), now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 0 and text == "resolved: found\n"
        assert h.ledger.get_intent(intent.intent_id).state in ("ACKED", "PARTIALLY_FILLED", "FILLED")
    finally:
        h.close()


def test_an_anomaly_is_never_overridable(workspace, monkeypatch):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, _ = h.send(side="buy", key="b1", amount="10", price="90")
        h.advance(500)
        _released(h)
        argv = ["unknown", "resolve", "--intent", intent.intent_id, "--not-created", "--override-incomplete-proof",
                "--evidence", "checked the exchange history by hand", "--operator", "owner"]

        def anomaly_result(ex, pending):
            return "anomaly"

        code, preview = _run(workspace.state, *argv, now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert code == 0, preview                                     # the preview never calls try_resolve
        monkeypatch.setattr(live_reconcile, "try_resolve", anomaly_result)
        code, text = _run(workspace.state, *argv, "--confirm", _token(preview), now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 1 and text == "refused: the machine found an anomaly; the override does not apply\n"
        assert h.ledger.get_intent(intent.intent_id).state == "UNKNOWN_SUBMISSION"

        def anomaly_side_effect(ex, pending):
            ex.ingest.anomaly("a side effect the result does not show")
            return "waiting"

        monkeypatch.setattr(live_reconcile, "try_resolve", anomaly_side_effect)
        code, text = _run(workspace.state, *argv, "--confirm", _token(preview), now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 1 and text == "refused: the machine found an anomaly; the override does not apply\n"
        assert h.ledger.get_intent(intent.intent_id).state == "UNKNOWN_SUBMISSION"
    finally:
        h.close()


def test_the_anomaly_counter_still_decides_when_a_trip_is_already_active(workspace, monkeypatch):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, _ = h.send(side="buy", key="b1", amount="10", price="90")
        h.advance(500)
        _released(h)

        def anomaly_side_effect(ex, pending):
            ex.ingest.anomaly("same reason, already tripped")      # the trip set does not change
            return "waiting"

        argv = ["unknown", "resolve", "--intent", intent.intent_id, "--not-created",
                "--override-incomplete-proof", "--evidence", "checked the exchange history by hand",
                "--operator", "owner"]
        code, preview = _run(workspace.state, *argv, now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert code == 0, preview
        # the trip is already active when the machine trips the same reason (the row set does not change)
        sf.trip(h.ledger, sf.SafetyReason.DATA_ANOMALY, detail="already active", now=h.clock.seconds())
        monkeypatch.setattr(live_reconcile, "try_resolve", anomaly_side_effect)
        code, text = _run(workspace.state, *argv, "--confirm", _token(preview), now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 1 and text == "refused: the machine found an anomaly; the override does not apply\n"
        assert [trip.reason for trip in sf.read_safety_state(h.ledger).trips] == ["data_anomaly"]
        assert h.ledger.get_intent(intent.intent_id).state == "UNKNOWN_SUBMISSION"
    finally:
        h.close()


def test_a_stop_recorded_outside_the_anomaly_counter_refuses_the_override(workspace, monkeypatch):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, outcome = h.send(side="buy", key="b1", amount="10", price="90")
        assert outcome is S.UNKNOWN
        h.advance(500)
        _released(h)
        argv = ["unknown", "resolve", "--intent", intent.intent_id, "--not-created",
                "--override-incomplete-proof", "--evidence", "checked the exchange history by hand",
                "--operator", "owner"]
        code, preview = _run(workspace.state, *argv, now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert code == 0, preview

        def fee_missing(ex, pending):
            assert ex.ledger.trip_safety(sf.SafetyReason.FEE_MISSING, detail="test", now=ex.clock())
            return "waiting"                                   # no ingest.anomaly: the counter cannot see the trip

        monkeypatch.setattr(live_reconcile, "try_resolve", fee_missing)
        code, text = _run(workspace.state, *argv, "--confirm", _token(preview), now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 1 and text.startswith("refused: the machine recorded a safety stop during this run")
        assert h.ledger.get_intent(intent.intent_id).state == "UNKNOWN_SUBMISSION"
        assert live_views.trip_count(h.ledger) == 1             # exactly the row the test's own path inserted
        assert h.trips() == ["fee_missing"]
    finally:
        h.close()


def test_an_active_stop_refuses_the_override_until_the_owner_clears_it(workspace, monkeypatch):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, outcome = h.send(side="buy", key="b1", amount="10", price="90")
        assert outcome is S.UNKNOWN
        h.advance(500)
        _released(h)

        def waiting(ex, pending):
            return "waiting"                                   # no trip comes from this path

        monkeypatch.setattr(live_reconcile, "try_resolve", waiting)
        argv = ["unknown", "resolve", "--intent", intent.intent_id, "--not-created",
                "--override-incomplete-proof", "--evidence", "checked the exchange history by hand",
                "--operator", "owner"]
        code, preview = _run(workspace.state, *argv, now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert code == 0, preview
        token = _token(preview)
        h.ledger.trip_safety(sf.SafetyReason.UNKNOWN_ORDER, detail="test", now=h.clock.seconds())
        code, text = _run(workspace.state, *argv, "--confirm", token, now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 1 and text.startswith("refused: an active safety stop must be cleared first")
        assert h.ledger.get_intent(intent.intent_id).state == "UNKNOWN_SUBMISSION"
        h.ledger.clear_safety(sf.SafetyReason.UNKNOWN_ORDER, operator="owner", note="test", evidence="test",
                              now=h.clock.seconds())
        code, text = _run(workspace.state, *argv, "--confirm", token, now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 0 and text == "resolved: not created (owner evidence)\n"
        assert h.ledger.get_intent(intent.intent_id).state == "NOT_CREATED"
    finally:
        h.close()


def test_the_preview_reports_active_stops_and_refuses_them_only_with_the_override(workspace):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, outcome = h.send(side="buy", key="b1", amount="10", price="90")
        assert outcome is S.UNKNOWN
        h.advance(500)
        _released(h)
        argv = ["unknown", "resolve", "--intent", intent.intent_id, "--not-created",
                "--evidence", "checked the exchange history by hand", "--operator", "owner"]
        code, clean = _run(workspace.state, *argv, now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert code == 0 and "safety_stops_active" not in clean and "token: " in clean
        h.ledger.trip_safety(sf.SafetyReason.UNKNOWN_ORDER, detail="test", now=h.clock.seconds())
        code, refused = _run(workspace.state, *argv, "--override-incomplete-proof", now=h.clock.seconds(),
                             reader_factory=lambda state_dir: h.mock)
        assert code == 1 and refused.startswith("refused: an active safety stop must be cleared first")
        assert "token: " not in refused
        code, stopped = _run(workspace.state, *argv, now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert code == 0 and "safety_stops_active: unknown_order" in stopped and "token: " in stopped
        assert h.ledger.get_intent(intent.intent_id).state == "UNKNOWN_SUBMISSION"
    finally:
        h.close()


def test_an_unreadable_safety_state_refuses_the_preview_with_or_without_the_override(workspace, monkeypatch):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, outcome = h.send(side="buy", key="b1", amount="10", price="90")
        assert outcome is S.UNKNOWN
        h.advance(500)
        _released(h)
        monkeypatch.setattr(sf, "read_safety_state", lambda ledger: sf.SafetyState((), 0, False))
        argv = ["unknown", "resolve", "--intent", intent.intent_id, "--not-created",
                "--evidence", "checked the exchange history by hand", "--operator", "owner"]
        for extra in ([], ["--override-incomplete-proof"]):
            code, text = _run(workspace.state, *argv, *extra, now=h.clock.seconds(),
                              reader_factory=lambda state_dir: h.mock)
            assert code == 1, text
            assert text.startswith("refused: the safety state cannot be read")
            assert "token: " not in text
        assert h.ledger.get_intent(intent.intent_id).state == "UNKNOWN_SUBMISSION"
    finally:
        h.close()


def test_an_unreadable_safety_state_refuses_the_confirm_after_a_valid_preview(workspace, monkeypatch):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, outcome = h.send(side="buy", key="b1", amount="10", price="90")
        assert outcome is S.UNKNOWN
        h.advance(500)
        _released(h)
        argv = ["unknown", "resolve", "--intent", intent.intent_id, "--not-created",
                "--evidence", "checked the exchange history by hand", "--operator", "owner"]
        code, preview = _run(workspace.state, *argv, now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert code == 0, preview                                    # the real state is readable
        token = _token(preview)

        def waiting(ex, pending):
            return "waiting"

        monkeypatch.setattr(live_reconcile, "try_resolve", waiting)
        monkeypatch.setattr(sf, "read_safety_state", lambda ledger: sf.SafetyState((), 0, False))
        code, text = _run(workspace.state, *argv, "--confirm", token, now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 1 and text == "refused: the safety state cannot be read\n"
        assert h.ledger.get_intent(intent.intent_id).state == "UNKNOWN_SUBMISSION"
        assert h.ledger.list_intents(states=("NOT_CREATED",)) == []
    finally:
        h.close()


def test_found_order_refuses_an_inconsistent_snapshot_without_a_trip(workspace):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        h.mock.faults.push("submit", "LOST_RESPONSE")
        intent, _ = h.send(side="buy", key="b1", amount="10", price="90")
        eid = h.mock.all_orders()[0].exchange_order_id
        real = h.mock.fetch_order

        def inconsistent(symbol, exchange_order_id):
            snap = real(symbol, exchange_order_id)
            return OrderSnapshot(**{**snap.__dict__, "remaining_amount": snap.remaining_amount + 1})

        h.mock.fetch_order = inconsistent
        before = (_events(h.ledger), _orders(h.ledger), h.trips())
        argv = ["unknown", "resolve", "--intent", intent.intent_id, "--found-order", eid,
                "--evidence", "checked the exchange history by hand", "--operator", "owner"]
        code, text = _run(workspace.state, *argv, now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert code == 1 and text == "refused: the exchange order is not consistent\n"
        _released(h)
        token = _token_for(workspace.state, intent.intent_id, f"found:{eid}", "checked the exchange history by hand")
        code, text = _run(workspace.state, *argv, "--confirm", token, now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 1 and text == "refused: the exchange order is not consistent\n"
        assert (_events(h.ledger), _orders(h.ledger), h.trips()) == before
        assert h.ledger.get_intent(intent.intent_id).state == "UNKNOWN_SUBMISSION"
    finally:
        h.close()


def test_found_only_candidates_that_cannot_be_read_are_exchange_unavailable(workspace):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, _ = h.send(side="buy", key="b1", amount="10", price="90")
        h.mock.place_external_order(OrderRequest("OAS/JPY", "buy", "limit", Decimal("3"), Decimal("100"), None))
        h.advance(500)
        _released(h)
        before = (_events(h.ledger), _orders(h.ledger), h.trips())
        h.mock.faults.push("fetch_order", "READ_TIMEOUT")
        code, text = _run(workspace.state, "unknown", "show", "--intent", intent.intent_id, now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 1 and text == "refused: exchange unavailable: ExchangeAmbiguous\n"
        h.mock.faults.push("fetch_order", "READ_TIMEOUT")
        code, text = _run(workspace.state, "unknown", "resolve", "--intent", intent.intent_id, "--not-created",
                          "--evidence", "checked the exchange history by hand", "--operator", "owner",
                          now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert code == 1 and text == "refused: exchange unavailable: ExchangeAmbiguous\n"
        assert (_events(h.ledger), _orders(h.ledger), h.trips()) == before
        assert h.ledger.get_intent(intent.intent_id).state == "UNKNOWN_SUBMISSION"
    finally:
        h.close()


def test_an_intent_that_already_has_an_order_is_refused(workspace):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))

    def hook(point):
        if point == "after_order_recorded":
            raise SimulatedCrash(point)

    try:
        live_executor.FAULT_HOOK = hook
        with pytest.raises(SimulatedCrash):
            h.send(side="buy", key="b1", amount="10", price="90")
        live_executor.FAULT_HOOK = None
        h.crash()
        h.mock.faults.push("fetch_order", "READ_TIMEOUT")      # the restart cannot see the order yet
        live_reconcile.on_startup(h.ex)
        intent = h.ledger.list_intents(states=("UNKNOWN_SUBMISSION",))[0]
        recorded = live_views.order_for_intent(h.ledger, intent.intent_id)
        assert recorded is not None and recorded.exchange_order_id == h.mock.all_orders()[0].exchange_order_id
        _released(h)
        code, text = _run(workspace.state, "unknown", "resolve", "--intent", intent.intent_id, "--not-created",
                          "--override-incomplete-proof", "--evidence", "checked the exchange history by hand",
                          "--operator", "owner", now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert code == 1 and text == "refused: the ledger already has an order for this intent; use --found-order\n"
        # a different order id would make sync_order trip "second order for one intent": refuse with no trip
        h.mock.place_external_order(OrderRequest("OAS/JPY", "buy", "limit", Decimal("4"), Decimal("90"), None))
        other = [snap.exchange_order_id for snap in h.mock.all_orders() if snap.amount == Decimal("4")][0]
        before = (_events(h.ledger), _orders(h.ledger), h.trips())
        code, text = _run(workspace.state, "unknown", "resolve", "--intent", intent.intent_id,
                          "--found-order", other, "--evidence", "checked the exchange history by hand",
                          "--operator", "owner", now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert code == 1 and text == "refused: the ledger already has a different order for this intent\n"
        assert (_events(h.ledger), _orders(h.ledger), h.trips()) == before
        assert h.ledger.get_intent(intent.intent_id).state == "UNKNOWN_SUBMISSION"
    finally:
        live_executor.FAULT_HOOK = None
        h.close()


def test_the_id_printed_by_unknown_list_round_trips(workspace):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, _ = h.send(side="buy", key="b1", amount="10", price="90")
        h.advance(500)
        code, line = _run(workspace.state, "unknown", "list", now=h.clock.seconds())
        assert code == 0 and line.split()[0] == intent.intent_id          # the full id, not a prefix
        code, show = _run(workspace.state, "unknown", "show", "--intent", intent.intent_id, now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 0 and f"intent: {intent.intent_id}" in show
        code, preview = _run(workspace.state, "unknown", "resolve", "--intent", intent.intent_id, "--not-created",
                             "--evidence", "checked the exchange history by hand", "--operator", "owner",
                             now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert code == 0 and f"intent: {intent.intent_id}" in preview
    finally:
        h.close()


def test_unknown_works_in_the_default_client_id_mode(workspace):
    h = _unknown_harness(workspace)                        # the harness default: client order ids
    try:
        intent = _lost_buy(h)
        code, text = _run(workspace.state, "unknown", "list", now=h.clock.seconds())
        assert code == 0 and intent.intent_id in text
        code, show = _run(workspace.state, "unknown", "show", "--intent", intent.intent_id, now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 0 and "matching: 1" in show and "ledger_order: no" in show
    finally:
        h.close()


def test_unknown_show_without_a_key_file_reports_the_reader_failure(workspace):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        intent = _lost_buy(h)
        _released(h)
        code, text = _run(workspace.state, "unknown", "show", "--intent", intent.intent_id, now=h.clock.seconds())
        assert code == 1 and text == "refused: exchange unavailable: ReaderUnavailableError\n"
        code, text = _run(workspace.state, "unknown", "resolve", "--intent", intent.intent_id, "--not-created",
                          "--evidence", "checked the exchange history by hand", "--operator", "owner",
                          now=h.clock.seconds())
        assert code == 1 and text == "refused: exchange unavailable: ReaderUnavailableError\n"
        assert h.ledger.get_intent(intent.intent_id).state == "UNKNOWN_SUBMISSION" and _orders(h.ledger) == []
    finally:
        h.close()


# ---- trip: list the safety stops and clear one (human only) ----------------------------------
def _trip(state_dir, reason, *, detail="test stop", now=NOW):
    ledger = LiveLedger.open(live_layout(state_dir), paper_dir=Path(state_dir) / "trading", now=now)
    try:
        assert ledger.trip_safety(sf.SafetyReason(reason), detail=detail, now=now)
    finally:
        ledger.close()


def _trip_clear_args(reason, **kw):
    values = {"reason": reason, "operator": "owner", "note": "owner cleared the stop",
              "evidence": "checked the exchange and the ledger by hand"}
    values.update(kw)
    argv = []
    for name, value in values.items():
        argv += [f"--{name.replace('_', '-')}", value]
    return ["trip", "clear", *argv]


def _active_trips(state_dir):
    ledger = LiveLedger.open_readonly(live_layout(state_dir))
    try:
        with ledger.read_cursor() as cur:
            return sf.active_trips(cur)
    finally:
        ledger.close()


def _trip_rows(state_dir):
    ledger = LiveLedger.open_readonly(live_layout(state_dir))
    try:
        with ledger.read_cursor() as cur:
            return cur.execute("SELECT trip_id, reason, cleared_at, cleared_by FROM safety_trips "
                               "ORDER BY trip_id").fetchall()
    finally:
        ledger.close()


def _safety_cleared(state_dir):
    ledger = LiveLedger.open_readonly(live_layout(state_dir))
    try:
        return [event for event in ledger.events(limit=1000) if event.kind == "safety_cleared"]
    finally:
        ledger.close()


def test_trip_list_without_a_ledger_creates_nothing(tmp_path):
    before = _listing(tmp_path)
    code, text = _run(tmp_path, "trip", "list")
    assert code == 0 and text == "ledger: not created\n"
    assert _listing(tmp_path) == before and not (tmp_path / "trading-live").exists()


def test_trip_list_shows_each_stop_and_hides_the_detail_unless_a_tty(tmp_path):
    _trip(tmp_path, "data_anomaly", detail="the balance could not be proven")
    trip_id = _active_trips(tmp_path)[0].trip_id
    code, text = _run(tmp_path, "trip", "list", is_tty=False)
    assert code == 0
    assert f"trip_id={trip_id} reason=data_anomaly tripped_at=2026-01-01T00:00:00+00:00" in text
    assert "detail=hidden" in text and "the balance could not be proven" not in text
    code, tty = _run(tmp_path, "trip", "list", is_tty=True)
    assert code == 0 and "detail=the balance could not be proven" in tty and "detail=hidden" not in tty


def test_trip_list_refuses_an_unreadable_state(tmp_path, monkeypatch):
    _trip(tmp_path, "data_anomaly")
    monkeypatch.setattr(sf, "read_safety_state", lambda ledger: sf.SafetyState((), 0, False))
    code, text = _run(tmp_path, "trip", "list")
    assert code == 1 and text == "refused: the safety state cannot be read\n"


def test_trip_clear_preview_writes_nothing(tmp_path):
    _trip(tmp_path, "data_anomaly")
    before = _trip_rows(tmp_path)
    code, text = _run(tmp_path, *_trip_clear_args("data_anomaly"))
    assert code == 0 and "token: " in text
    assert [trip.reason for trip in _active_trips(tmp_path)] == ["data_anomaly"]
    assert _trip_rows(tmp_path) == before and _safety_cleared(tmp_path) == []


def test_trip_clear_with_a_wrong_token_changes_nothing(tmp_path):
    _trip(tmp_path, "data_anomaly")
    code, text = _run(tmp_path, *_trip_clear_args("data_anomaly"), "--confirm", "0" * 10)
    assert code == 1 and text == "refused: confirmation token does not match\n"
    assert [trip.reason for trip in _active_trips(tmp_path)] == ["data_anomaly"]
    assert _safety_cleared(tmp_path) == []


def test_trip_clear_refuses_when_that_reason_has_no_active_stop(tmp_path):
    _trip(tmp_path, "data_anomaly")
    code, text = _run(tmp_path, *_trip_clear_args("fee_missing"))
    assert code == 1 and text == "refused: no active trip for that reason\n"
    assert [trip.reason for trip in _active_trips(tmp_path)] == ["data_anomaly"]


def test_trip_clear_preview_then_confirm_clears_the_row(tmp_path):
    _trip(tmp_path, "data_anomaly")
    trip_id = _active_trips(tmp_path)[0].trip_id
    code, preview = _run(tmp_path, *_trip_clear_args("data_anomaly"))
    assert code == 0
    code, text = _run(tmp_path, *_trip_clear_args("data_anomaly"), "--confirm", _token(preview))
    assert code == 0 and text == f"cleared: data_anomaly (trip_id={trip_id})\n"
    assert _active_trips(tmp_path) == ()
    trip_row_id, _, cleared_at, cleared_by = _trip_rows(tmp_path)[0]
    assert trip_row_id == trip_id and cleared_at is not None and cleared_by == "owner"
    assert [event.entity_id for event in _safety_cleared(tmp_path)] == ["data_anomaly"]


def test_a_retripped_stop_invalidates_the_previous_token(tmp_path):
    _trip(tmp_path, "data_anomaly", detail="the first stop")
    first = _active_trips(tmp_path)[0].trip_id
    code, preview = _run(tmp_path, *_trip_clear_args("data_anomaly"))
    assert code == 0
    code, text = _run(tmp_path, *_trip_clear_args("data_anomaly"), "--confirm", _token(preview))
    assert code == 0 and text == f"cleared: data_anomaly (trip_id={first})\n"
    _trip(tmp_path, "data_anomaly", detail="the second stop")
    second = _active_trips(tmp_path)[0].trip_id
    assert second != first
    code, text = _run(tmp_path, *_trip_clear_args("data_anomaly"), "--confirm", _token(preview))
    assert code == 1 and text == "refused: confirmation token does not match\n"   # the token names the old trip
    assert [trip.trip_id for trip in _active_trips(tmp_path)] == [second]
    code, preview = _run(tmp_path, *_trip_clear_args("data_anomaly"))
    assert code == 0
    code, text = _run(tmp_path, *_trip_clear_args("data_anomaly"), "--confirm", _token(preview))
    assert code == 0 and text == f"cleared: data_anomaly (trip_id={second})\n"
    assert _active_trips(tmp_path) == ()


def test_a_stop_that_changes_under_the_lease_refuses_the_clear(tmp_path, monkeypatch):
    _trip(tmp_path, "data_anomaly", detail="the previewed stop")
    previewed = _active_trips(tmp_path)[0].trip_id
    code, preview = _run(tmp_path, *_trip_clear_args("data_anomaly"))
    assert code == 0
    token = _token(preview)
    real = live_lease.acquire_writer_lease

    def changed(ledger, layout, **kw):
        sf.clear_safety_trip(ledger, sf.SafetyReason.DATA_ANOMALY, operator="owner", note="test",
                             evidence="test", now=kw["now"])
        ledger.trip_safety(sf.SafetyReason.DATA_ANOMALY, detail="the new stop", now=kw["now"])
        return real(ledger, layout, **kw)

    monkeypatch.setattr(live_lease, "acquire_writer_lease", changed)
    code, text = _run(tmp_path, *_trip_clear_args("data_anomaly"), "--confirm", token)
    assert code == 1 and text == "refused: the safety stop changed since the preview; run the preview again\n"
    active = _active_trips(tmp_path)
    assert [trip.reason for trip in active] == ["data_anomaly"] and active[0].trip_id != previewed


def test_trip_clear_with_the_worker_holding_the_lease_returns_five(tmp_path):
    layout = live_layout(tmp_path)
    ledger = LiveLedger.open(layout, paper_dir=tmp_path / "trading", now=NOW)
    try:
        ledger.trip_safety(sf.SafetyReason.DATA_ANOMALY, detail="test", now=NOW)
        held = live_lease.acquire_writer_lease(ledger, layout, now=NOW)
        try:
            code, preview = _run(tmp_path, *_trip_clear_args("data_anomaly"))
            assert code == 0 and "token: " in preview
            code, text = _run(tmp_path, *_trip_clear_args("data_anomaly"), "--confirm", _token(preview))
            assert code == 5 and text == "refused: the live worker holds the writer lease; stop it first\n"
        finally:
            live_lease.release_writer_lease(ledger, held, now=NOW)
    finally:
        ledger.close()
    assert [trip.reason for trip in _active_trips(tmp_path)] == ["data_anomaly"]


def test_legacy_enable_missing_is_cleared_like_any_other_trip(tmp_path):
    """ENABLE_MISSING is a legacy row: no control file, no active policy set, human-only clear."""
    _trip(tmp_path, "enable_missing", detail="a legacy stop")
    argv = _trip_clear_args("enable_missing")
    code, preview = _run(tmp_path, *argv)
    assert code == 0 and "token: " in preview
    code, text = _run(tmp_path, *argv, "--confirm", _token(preview))
    assert code == 0 and text.startswith("cleared: enable_missing")
    assert _active_trips(tmp_path) == ()


def test_the_owner_clears_an_unknown_order_stop_and_then_resolves_the_intent(workspace, monkeypatch):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, outcome = h.send(side="buy", key="b1", amount="10", price="90")
        assert outcome is S.UNKNOWN
        h.advance(500)
        h.ledger.trip_safety(sf.SafetyReason.UNKNOWN_ORDER, detail="an order the ledger does not know",
                             now=h.clock.seconds())
        _released(h)

        def waiting(ex, pending):
            return "waiting"                                   # no trip comes from this path

        monkeypatch.setattr(live_reconcile, "try_resolve", waiting)
        resolve_argv = ["unknown", "resolve", "--intent", intent.intent_id, "--not-created",
                        "--override-incomplete-proof", "--evidence", "checked the exchange history by hand",
                        "--operator", "owner"]
        code, text = _run(workspace.state, *resolve_argv, now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 1
        assert text == (f"refused: an active safety stop must be cleared first "
                        f"({sf.SafetyReason.UNKNOWN_ORDER.value})\n")
        clear_argv = _trip_clear_args("unknown_order")
        code, preview = _run(workspace.state, *clear_argv)
        assert code == 0 and "token: " in preview
        code, text = _run(workspace.state, *clear_argv, "--confirm", _token(preview))
        assert code == 0 and text.startswith("cleared: unknown_order")
        code, preview = _run(workspace.state, *resolve_argv, now=h.clock.seconds(),
                             reader_factory=lambda state_dir: h.mock)
        assert code == 0, preview
        code, text = _run(workspace.state, *resolve_argv, "--confirm", _token(preview), now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 0 and text == "resolved: not created (owner evidence)\n"
        assert h.ledger.get_intent(intent.intent_id).state == "NOT_CREATED"
    finally:
        h.close()


def test_a_non_blocking_stop_does_not_block_the_not_created_override(workspace, monkeypatch):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, outcome = h.send(side="buy", key="b1", amount="10", price="90")
        assert outcome is S.UNKNOWN
        h.advance(500)
        h.ledger.trip_safety(sf.SafetyReason.ENABLE_MISSING, detail="the gate closed", now=h.clock.seconds())
        _released(h)

        def waiting(ex, pending):
            return "waiting"

        monkeypatch.setattr(live_reconcile, "try_resolve", waiting)
        argv = ["unknown", "resolve", "--intent", intent.intent_id, "--not-created",
                "--override-incomplete-proof", "--evidence", "checked the exchange history by hand",
                "--operator", "owner"]
        code, preview = _run(workspace.state, *argv, now=h.clock.seconds(), reader_factory=lambda state_dir: h.mock)
        assert code == 0, preview                                 # ENABLE_MISSING is not one of the blocking four
        assert "token: " in preview
        code, text = _run(workspace.state, *argv, "--confirm", _token(preview), now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 0 and text == "resolved: not created (owner evidence)\n"
        assert h.ledger.get_intent(intent.intent_id).state == "NOT_CREATED"
    finally:
        h.close()


# ---- pins: a clear with no ledger, and a stop the machine records during a resolution ----------
def _state_contents(state_dir) -> list[tuple[str, int]]:
    """Every entry below the state dir (relative name, size): a created ledger or lease shows up."""
    root = Path(state_dir)
    return sorted((str(entry.relative_to(root)), entry.stat().st_size) for entry in root.rglob("*"))


@pytest.mark.parametrize("reason", ["unknown_order", "enable_missing"])
@pytest.mark.parametrize("confirm", [False, True], ids=["preview", "confirm"])
def test_trip_clear_without_a_ledger_refuses_and_creates_nothing(tmp_path, reason, confirm):
    argv = _trip_clear_args(reason)
    if confirm:
        argv += ["--confirm", "0" * 10]              # any 10-hex token: the missing ledger is refused first
    before = _state_contents(tmp_path)
    code, text = _run(tmp_path, *argv)
    assert code == 1 and text == "refused: no active trip for that reason\n"
    assert _state_contents(tmp_path) == before
    assert before == [] and not (tmp_path / "trading-live").exists()


def _record_machine_stop(ex, reason: str) -> None:
    """The write the worker itself makes for this reason (never ingest.anomaly)."""
    ex.ledger.trip_safety(sf.SafetyReason.LEASE_LOST, detail="the writer lease was lost",
                          now=ex.clock())


@pytest.mark.parametrize("reason", ["lease_lost"])
def test_a_stop_recorded_by_the_machine_during_the_resolution_refuses_the_owner_override(
        workspace, monkeypatch, reason):
    h = _unknown_harness(workspace, caps=Capabilities(False, False, True))
    try:
        h.mock.faults.push("submit", "LOST_REQUEST")
        intent, outcome = h.send(side="buy", key="b1", amount="10", price="90")
        assert outcome is S.UNKNOWN
        h.advance(500)
        _released(h)                                     # the harness lease goes back once; each CLI run takes its own
        argv = ["unknown", "resolve", "--intent", intent.intent_id, "--not-created",
                "--override-incomplete-proof", "--evidence", "checked the exchange history by hand",
                "--operator", "owner"]
        code, preview = _run(workspace.state, *argv, now=h.clock.seconds(),
                             reader_factory=lambda state_dir: h.mock)
        assert code == 0, preview
        token = _token(preview)

        def recorded_stop(ex, pending):
            _record_machine_stop(ex, reason)
            return "waiting"

        monkeypatch.setattr(live_reconcile, "try_resolve", recorded_stop)
        code, text = _run(workspace.state, *argv, "--confirm", token, now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 1
        assert text == (f"refused: the machine recorded a safety stop during this run ({reason}); "
                        f"the override does not apply\n")
        assert h.ledger.get_intent(intent.intent_id).state == "UNKNOWN_SUBMISSION"
        assert h.ledger.list_intents(states=("NOT_CREATED",)) == []

        def waiting(ex, pending):
            return "waiting"                             # no stop comes from this path

        monkeypatch.setattr(live_reconcile, "try_resolve", waiting)
        code, preview = _run(workspace.state, *argv, now=h.clock.seconds(),
                             reader_factory=lambda state_dir: h.mock)
        assert code == 0, preview
        code, text = _run(workspace.state, *argv, "--confirm", _token(preview), now=h.clock.seconds(),
                          reader_factory=lambda state_dir: h.mock)
        assert code == 0 and text == "resolved: not created (owner evidence)\n"
        assert h.ledger.get_intent(intent.intent_id).state == "NOT_CREATED"
    finally:
        h.close()


# ---- control: the owner's plain on/off switch -----------------------------------------------
def test_control_status_without_a_file_is_unknown_and_creates_nothing(tmp_path):
    before = _listing(tmp_path)
    code, text = _run(tmp_path, "control", "status")
    assert code == 0
    assert "state: unknown" in text and "mode: none" in text
    assert "updated_at: none" in text and "updated_by: none" in text
    assert "reasons: file_missing" in text
    assert _listing(tmp_path) == before and not (tmp_path / "trading-live").exists()


def test_control_on_off_writes_the_file_and_on_keeps_the_existing_mode(tmp_path):
    code, text = _run(tmp_path, "control", "on", "--mode", "trade", "--yes-trade",
                      "--operator", "owner")
    assert code == 0
    assert "state: on" in text and "mode: trade" in text and "updated_by: owner" in text
    path = live_layout(tmp_path).control_path
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    result = live_control.read_control(tmp_path, now=NOW)
    assert result.enabled and result.allows_orders

    code, text = _run(tmp_path, "control", "off", "--operator", "owner")
    assert code == 0 and "state: off" in text and "mode: trade" in text
    result = live_control.read_control(tmp_path, now=NOW)
    assert not result.enabled and result.reasons == (live_control.EnableReason.SWITCHED_OFF,)
    assert result.mode == "trade"

    code, text = _run(tmp_path, "control", "on", "--operator", "owner")   # no --mode: keep trade
    assert code == 0 and "state: on" in text and "mode: trade" in text
    assert live_control.read_control(tmp_path, now=NOW).allows_orders


def test_control_on_defaults_to_shadow_and_trade_needs_yes_trade(tmp_path):
    code, text = _run(tmp_path, "control", "on", "--mode", "trade", "--operator", "owner")
    assert code == 1 and text == "refused: --mode trade requires --yes-trade\n"
    assert not live_layout(tmp_path).control_path.exists()

    code, text = _run(tmp_path, "control", "on", "--operator", "owner")
    assert code == 0 and "state: on" in text and "mode: shadow" in text
    result = live_control.read_control(tmp_path, now=NOW)
    assert result.enabled and not result.allows_orders

    # an existing trade mode is kept when --mode is omitted, even through off
    assert _run(tmp_path, "control", "on", "--mode", "trade", "--yes-trade",
                "--operator", "owner")[0] == 0
    assert _run(tmp_path, "control", "off", "--operator", "owner")[0] == 0
    code, text = _run(tmp_path, "control", "on", "--operator", "owner")
    assert code == 0 and "mode: trade" in text


def test_control_set_refuses_a_bad_operator_or_a_directory_it_must_not_touch(tmp_path):
    code, text = _run(tmp_path, "control", "on", "--operator", " ")
    assert code == 1 and text.startswith("refused: operator must not be empty")
    assert not live_layout(tmp_path).control_path.exists()

    root = live_layout(tmp_path).root
    root.mkdir(mode=0o755)
    os.chmod(root, 0o755)
    code, text = _run(tmp_path, "control", "on", "--operator", "owner")
    assert code == 1 and text == "refused: trading-live must be mode 0700\n"
    assert not live_layout(tmp_path).control_path.exists()
