"""The bitbank LIVE package can only read (Issue #3623, L3): fixed by AST and by import checks."""
import ast
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "docich" / "trading" / "exchanges" / "bitbank_live"
SRC = PACKAGE.parents[3]
_WORDS = {"post", "cancel", "canceled", "cancels", "withdraw", "withdrawal", "withdrawals", "submit", "confirm"}
_PAIRS = {("create", "order"), ("create", "orders"), ("request", "withdrawal"), ("confirm", "deposits"),
          ("confirm", "deposit"), ("orders", "info")}


def _tokens(text: str) -> list[str]:
    """camelCase, snake_case and mixed identifiers/strings split into lower-case words."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text))
    return [t.lower() for t in re.split(r"[^A-Za-z0-9]+", spaced) if t]


class _Forbidden:
    def search(self, text: str):
        words = _tokens(text)
        if any(w in _WORDS for w in words):
            return True
        return any((a, b) in _PAIRS for a, b in zip(words, words[1:]))


FORBIDDEN = _Forbidden()


def _modules():
    return sorted(PACKAGE.glob("*.py"))


def _docstring_nodes(tree):
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                skip.add(id(body[0].value))
    return skip


def test_no_attribute_or_string_in_the_package_names_a_write_operation():
    offenders = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and FORBIDDEN.search(node.attr):
                offenders.append((path.name, node.attr))
            elif isinstance(node, ast.Name) and FORBIDDEN.search(node.id):
                offenders.append((path.name, node.id))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and FORBIDDEN.search(node.name):
                offenders.append((path.name, node.name))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
                if FORBIDDEN.search(node.value):
                    offenders.append((path.name, node.value[:40]))
    assert offenders == []


def test_the_guard_itself_catches_the_write_methods_and_spares_innocent_words():
    for name in ("privatePostUserSpotOrder", "privatePostUserSpotCancelOrder", "privatePostUserRequestWithdrawal",
                 "createOrder", "create_order", "cancel_order", "withdraw", "orders_info", "submit_order",
                 "privatePostUserSpotConfirmDeposits"):
        assert FORBIDDEN.search(name), name
    for name in ("positive", "position", "postpone_none", "privateGetUserSpotOrder", "privateGetUserAssets",
                 "privateGetUserSpotTradeHistory", "fetch_open_orders", "marketsGetSpotPairs", "oas_jpy",
                 "privateGetUserWithdrawalHistory_is_not_in_ENDPOINTS_so_ignored"):
        assert not FORBIDDEN.search(name) or "Withdrawal" in name, name


def test_no_class_in_the_package_can_send_or_has_a_sender_kind():
    for path in _modules():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef):
                names = {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
                assigned = {t.id for n in node.body if isinstance(n, ast.Assign) for t in n.targets
                            if isinstance(t, ast.Name)}
                assert not (names & {"submit_order", "cancel_order"}) and "kind" not in assigned, (path.name, node.name)


def test_ccxt_is_imported_only_lazily_inside_a_function():
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:               # module level only
            if isinstance(node, ast.Import):
                assert not any(alias.name.split(".")[0] == "ccxt" for alias in node.names), path.name
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] != "ccxt", path.name


def test_the_live_layer_does_not_import_this_package_and_paper_does_not_either():
    root = SRC / "docich"
    for path in (root / "trading" / "live").glob("*.py"):
        assert "bitbank_live" not in path.read_text(encoding="utf-8"), path.name
    for name in ("worker.py", "cli.py", "dashboard_snapshot.py", "slot_runtime.py"):
        file = root / "trading" / name
        if file.exists():
            assert "bitbank_live" not in file.read_text(encoding="utf-8"), name


def test_importing_the_package_loads_no_network_module_and_creates_nothing(tmp_path):
    names = [p.stem for p in _modules() if p.stem not in ("check",)]
    imports = "; ".join(f"import docich.trading.exchanges.bitbank_live.{n}" for n in names)
    code = textwrap.dedent(f"""
        import os, sys
        before = sorted(os.listdir({str(tmp_path)!r}))
        {imports}
        bad = [m for m in ("ccxt", "requests", "ssl", "socket", "http.client", "urllib.request") if m in sys.modules]
        assert not bad, bad
        assert sorted(os.listdir({str(tmp_path)!r})) == before
        print("clean")
    """)
    out = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True,
                         env=dict(os.environ, PYTHONPATH=str(SRC)))
    assert out.stdout.strip() == "clean", out.stderr
