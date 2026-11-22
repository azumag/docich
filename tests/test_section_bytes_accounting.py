"""Regression tests for VM diagnostics Phase 0 section byte accounting.

Extract only the relevant AST nodes so these tests do not require production VM
dependencies or network access.
"""
import ast
import io
import json
from pathlib import Path
import sys
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "ops/vm_actions/collect_diagnostics.py"


def _nodes():
    module = ast.parse(SOURCE.read_text(encoding="utf-8"))
    helper = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == "_compute_section_bytes")
    main = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    injection = next(
        n for n in ast.walk(main)
        if isinstance(n, ast.If)
        and isinstance(n.test, ast.Call)
        and isinstance(n.test.func, ast.Name)
        and n.test.func.id == "isinstance"
        and any(isinstance(x, ast.Constant) and x.value == "section_bytes" for x in ast.walk(n))
    )
    return helper, injection


def _namespace(hard_limit=65536):
    helper, _ = _nodes()
    ns = {"json": json, "_SECTION_BYTES_TOP_N": 5, "HARD_JSON_BYTES": hard_limit}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[helper], type_ignores=[])), str(SOURCE), "exec"), ns)
    return ns


def _inject(payload, hard_limit=65536):
    _, injection = _nodes()
    ns = _namespace(hard_limit)
    ns["payload"] = payload
    ns["text"] = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[injection], type_ignores=[])), str(SOURCE), "exec"), ns)
    return ns["text"]


class SectionBytesAccountingTests(unittest.TestCase):
    def test_bounded_shape_and_serialized_size(self):
        data = {f"section_{i}": "あ" * (i + 1) for i in range(20)}
        result = _namespace()["_compute_section_bytes"](data)
        self.assertEqual(set(result), {"top", "others", "total"})
        self.assertLessEqual(len(result["top"]), 5)
        self.assertEqual(result["others"] + sum(result["top"].values()), result["total"])
        self.assertLessEqual(len(json.dumps(result).encode("utf-8")), 400)

    def test_unserializable_section_is_zero_not_exception(self):
        result = _namespace()["_compute_section_bytes"]({"broken": object()})
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["others"] + sum(result["top"].values()), 0)

    def test_stdout_equivalent_contains_accounting_and_stays_bounded(self):
        payload = {"meta": {"source": "test"}, "status": "ok", "sample": "日本語"}
        text = _inject(payload)
        output = json.loads(text)
        self.assertIn("section_bytes", output["meta"])
        self.assertEqual(output["meta"]["section_bytes"]["total"],
                         _namespace()["_compute_section_bytes"](
                             {**output, "meta": {"source": "test"}})["total"])
        self.assertLessEqual(len(text.encode("utf-8")), 65536)

    def test_non_dict_meta_preserves_original_output(self):
        # Optional accounting must not crash if the meta contract changes.
        payload = {"meta": None, "status": "ok"}
        text = _inject(payload)
        self.assertEqual(json.loads(text), payload)
        self.assertLessEqual(len(text.encode("utf-8")), 65536)

    def test_near_hard_cap_drops_accounting_without_exceeding_limit(self):
        payload = {"meta": {}, "sample": "x" * 200}
        original = len(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                  separators=(",", ":")).encode("utf-8"))
        text = _inject(payload, hard_limit=original + 1)
        self.assertNotIn("section_bytes", json.loads(text)["meta"])
        self.assertLessEqual(len(text.encode("utf-8")), original + 1)


    def test_real_main_writes_accounting_to_stdout(self):
        """Execute the actual main AST with isolated collector dependencies."""
        from contextlib import redirect_stdout
        from unittest.mock import MagicMock

        source_tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        main_node = next(n for n in source_tree.body
                         if isinstance(n, ast.FunctionDef) and n.name == "main")
        ns = _namespace()
        ns.update({"sys": sys, "Path": Path, "time": __import__("time"),
                   "PROD_ROOT": Path("/unused"),
                   "json": json})
        # Every collector is isolated; no production files, processes or network.
        collector_names = {
            n.id for n in ast.walk(main_node)
            if isinstance(n, ast.Name) and n.id.startswith("_")
        }
        for name in collector_names:
            if name not in ns:
                ns[name] = MagicMock(return_value={})
        ns["_collect_soren91_manual_evidence"].return_value = {"active": False}
        ns["_collect_meta"].return_value = {"source": "test"}
        ns["_collect_ai"].return_value = {
            key: 0 for key in ("queue_giveups", "attempts", "failures",
                              "rate_limits", "fallbacks", "all_failed",
                              "budget_exhausted")
        }
        ns["_collect_opencode_retention"].return_value = {
            "attempt": {"present": False}, "timer": {"active": True}
        }
        # main() reads status/triggers from _severity_report (#3658), not _severity.
        ns["_severity_report"].return_value = {"status": "ok", "triggers": []}
        ns["_diagnostics_budget"] = lambda payload: json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        exec(compile(ast.fix_missing_locations(
            ast.Module(body=[main_node], type_ignores=[])), str(SOURCE), "exec"), ns)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(ns["main"](["collect_diagnostics.py", "/unused"]), 0)
        raw = output.getvalue()
        parsed = json.loads(raw)
        self.assertIn("section_bytes", parsed["meta"])
        self.assertLessEqual(len(raw.encode("utf-8")), 65536)
        accounting = parsed["meta"]["section_bytes"]
        self.assertEqual(accounting["others"] + sum(accounting["top"].values()),
                         accounting["total"])


if __name__ == "__main__":
    unittest.main()
