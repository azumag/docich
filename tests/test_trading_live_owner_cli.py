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
from docich.trading.live import lease as live_lease
from docich.trading.live import policy as live_policy
from docich.trading.live import safety as sf
from docich.trading.live.executor import SubmitOutcome as S
from docich.trading.live.ledger import LiveLedger
from docich.trading.live.paths import live_layout
from docich.trading.live_owner import cli, revision as live_revision
from test_trading_live_support import (Harness, Workspace, block_network, market_rules, policy_set,
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
def _run(state_dir, *argv, is_tty=False, now=NOW, revision=None):
    out = io.StringIO()
    extra = {} if revision is None else {"revision": revision}
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


# ---- no exchange, no network, no process (except revision.py) --------------------------------
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


def test_the_owner_package_imports_no_exchange_network_or_process_module():
    files = sorted(PKG.glob("*.py"))
    assert [path.name for path in files] == ["__init__.py", "__main__.py", "cli.py", "revision.py"]
    for path in files:
        imported = _imported_modules(path)
        if path.name != "__init__.py":             # the docstring-only package marker imports nothing
            assert imported, path.name
        for name in imported:
            assert not any(name == bad or name.startswith(f"{bad}.") for bad in FORBIDDEN_MODULES), (path.name, name)
            if name.split(".")[0] == "subprocess":
                assert path.name == "revision.py", path.name       # the only process call in the package


def test_the_revision_module_has_no_other_dependency():
    modules = _imported_modules(PKG / "revision.py")
    assert modules, "the import scan found nothing"
    assert {name.split(".")[0] for name in modules} <= {"__future__", "re", "os", "pathlib", "subprocess"}


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


def test_later_human_only_apis_are_not_mentioned_yet():
    files = sorted(PKG.glob("*.py"))
    assert [path.name for path in files] == ["__init__.py", "__main__.py", "cli.py", "revision.py"]
    for path in files:
        text = path.read_text(encoding="utf-8")
        for word in ("clear_safety_trip", "clear_safety"):
            assert word not in text, (path.name, word)
        if path.name != "cli.py":
            assert "record_owner_activation" not in text, path.name
    assert "record_owner_activation" in (PKG / "cli.py").read_text(encoding="utf-8")


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


def _activate_run(state_dir, snapshot, digest, *, confirm=None, revision=None, **kw):
    argv = _activate_args(snapshot, digest, **kw)
    if confirm is not None:
        argv += ["--confirm", confirm]
    return _run(state_dir, *argv, revision=revision or (lambda: "a" * 40))


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


def _refused(state_dir, *argv, revision=None):
    before = _listing(state_dir)
    code, text = _run(state_dir, *argv, revision=revision or (lambda: "a" * 40))
    assert code == 1, text
    assert text.startswith("refused:") and len(text.splitlines()) == 1, text
    assert _listing(state_dir) == before and not (Path(state_dir) / "trading-live" / "live.sqlite3").exists()
    return text


def test_activate_preview_creates_no_ledger(workspace, tmp_path):
    ps = policy_set(live_policy.INITIAL_SYMBOLS)
    workspace.write_enable(ps, revision="a" * 40)
    snapshot, digest = _snapshot(tmp_path)
    before = _listing(workspace.state)
    code, text = _activate_run(workspace.state, snapshot, digest)
    assert code == 0
    assert "ledger: will be created" in text
    assert f"policy_set: {ps.digest[:12]}" in text
    assert f"experiment: {digest[:12]}" in text
    assert f"code_revision: {'a' * 12}" in text
    assert "mode: trade" in text and "operator: owner" in text
    assert re.fullmatch(r"[0-9a-f]{10}", _token(text))
    assert _listing(workspace.state) == before and not workspace.layout.ledger_path.exists()


def test_activate_confirm_records_the_activation_and_the_ledger_accepts_a_repeat(workspace, tmp_path):
    ps = policy_set(live_policy.INITIAL_SYMBOLS)
    workspace.write_enable(ps, revision="a" * 40)
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


def test_activate_is_allowed_in_shadow_mode(workspace, tmp_path):
    ps = policy_set(live_policy.INITIAL_SYMBOLS)
    workspace.write_enable(ps, mode="shadow", revision="a" * 40)
    snapshot, digest = _snapshot(tmp_path)
    code, preview = _activate_run(workspace.state, snapshot, digest)
    assert code == 0 and "mode: shadow" in preview
    code, text = _activate_run(workspace.state, snapshot, digest, confirm=_token(preview))
    assert code == 0 and text.startswith("activated: ") and _active_digest(workspace) == ps.digest


def test_activate_refuses_an_enable_file_that_does_not_match(workspace, tmp_path):
    snapshot, digest = _snapshot(tmp_path)
    ps = policy_set(live_policy.INITIAL_SYMBOLS)
    assert "file_missing" in _refused(workspace.state, *_activate_args(snapshot, digest))
    workspace.write_enable(policy_set(), revision="a" * 40)                  # another policy set
    assert "policy_set_digest_mismatch" in _refused(workspace.state, *_activate_args(snapshot, digest))
    workspace.write_enable(ps, revision="a" * 40)                            # the right set, another commit
    assert "code_revision_mismatch" in _refused(workspace.state, *_activate_args(snapshot, digest),
                                                revision=lambda: "b" * 40)


def test_activate_refuses_a_bad_snapshot_a_bad_revision_a_wrong_token_or_a_bad_operator(workspace, tmp_path):
    ps = policy_set(live_policy.INITIAL_SYMBOLS)
    workspace.write_enable(ps, revision="a" * 40)
    snapshot, digest = _snapshot(tmp_path)
    assert "confirmation token does not match" in _refused(
        workspace.state, *_activate_args(snapshot, digest), "--confirm", "0" * 10)
    assert "experiment digest mismatch" in _refused(workspace.state, *_activate_args(snapshot, "0" * 64))
    assert "LivePolicyError" in _refused(workspace.state, *_activate_args("no-such/snapshot.json", digest))
    assert "LivePolicyError" in _refused(workspace.state, *_activate_args(tmp_path / "missing.json", digest))

    def dirty():
        raise live_revision.RevisionError("the tracked tree is dirty: commit or revert before activating")

    assert "the tracked tree is dirty" in _refused(workspace.state, *_activate_args(snapshot, digest), revision=dirty)
    assert "operator must not be empty" in _refused(workspace.state, *_activate_args(snapshot, digest, operator=" "))


def test_activate_with_the_worker_holding_the_lease_returns_five(workspace, tmp_path):
    ps = policy_set(live_policy.INITIAL_SYMBOLS)
    workspace.write_enable(ps, revision="a" * 40)
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


# ---- revision.py: the commit the enable file is bound to -------------------------------------
def _git(repo, *args):
    """Run git like revision.py does: no inherited GIT_* variable, a fixed configuration."""
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "LC_ALL": "C"})
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, check=True, env=env)


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-m", "one")
    return repo


def test_current_revision_returns_head_and_refuses_a_dirty_or_missing_repo(tmp_path):
    repo = _git_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{40}", head)
    assert live_revision.current_revision(repo) == head
    (repo / "untracked.txt").write_text("x\n", encoding="utf-8")
    assert live_revision.current_revision(repo) == head          # untracked files are not the revision
    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    with pytest.raises(live_revision.RevisionError, match="dirty"):
        live_revision.current_revision(repo)
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(live_revision.RevisionError):
        live_revision.current_revision(plain)


def test_current_revision_ignores_inherited_git_dir_and_work_tree(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init")
    (other / "other.txt").write_text("other\n", encoding="utf-8")
    _git(other, "add", "other.txt")
    _git(other, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-m", "other")
    repo_head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    other_head = _git(other, "rev-parse", "HEAD").stdout.strip()
    assert repo_head != other_head
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    assert live_revision.current_revision(repo) == repo_head      # not the other repository's HEAD


def test_current_revision_requires_the_top_level_of_its_own_checkout(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "sub").mkdir()
    with pytest.raises(live_revision.RevisionError, match="top of its own git checkout"):
        live_revision.current_revision(repo / "sub")             # a subdirectory is not the checkout
    nested = repo / "sub" / "deeper"
    nested.mkdir()
    with pytest.raises(live_revision.RevisionError, match="top of its own git checkout"):
        live_revision.current_revision(nested)                   # nor a plain directory inside the repo
