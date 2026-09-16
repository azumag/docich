import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT / "ops" / "vm_actions" / "run_soren91_daily_improve.sh"
CLASSIFIER = ROOT / "ops" / "vm_actions" / "classify_soren91_daily_gateway.py"
NONZERO_HELPER = ROOT / "ops" / "vm_actions" / "classify_soren91_opencode_nonzero.py"


def load_nonzero_helper():
    spec = importlib.util.spec_from_file_location("soren91_opencode_nonzero", NONZERO_HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Soren91OpenCodeNonzeroJsonTests(unittest.TestCase):
    def test_runner_fits_gateway_production_exec_budget(self):
        self.assertLessEqual(len(RUNNER.read_bytes()), 16384)
        text = RUNNER.read_text(encoding="utf-8")
        self.assertIn("classify_soren91_opencode_nonzero.py", text)
        self.assertIn("SOREN91_OPENCODE_NONZERO_CLASSIFIER", text)

    def test_shim_keeps_child_output_private_and_adds_only_fixed_marker(self):
        text = RUNNER.read_text(encoding="utf-8")
        self.assertIn('child_out="$(mktemp /home/ubuntu/.soren91-opencode-child-out.XXXXXX)"', text)
        self.assertIn('child_err="$(mktemp /home/ubuntu/.soren91-opencode-child-err.XXXXXX)"', text)
        self.assertIn('/snap/bin/opencode run --format json --agent soren-daily-improve --model "$5" >"$child_out" 2>"$child_err"', text)
        self.assertIn('printf \'soren91_opencode_nonzero_json=%s\\n\' "$category" >&2', text)
        self.assertIn('rm -f "$child_out" "$child_err"', text)
        self.assertNotIn('echo "$category"', text)

    def test_nonzero_stdout_json_categories_are_closed_vocabulary(self):
        text = RUNNER.read_text(encoding="utf-8")
        for category in (
            "error_event",
            "error_part",
            "tool_event",
            "unexpected_event",
            "invalid_json",
            "structured_nonzero",
            "no_json",
        ):
            self.assertIn(category, text)
        self.assertIn('case "$category" in', text)
        self.assertIn('*) category=structured_nonzero ;;', text)

    def test_private_helper_classifies_without_exposing_payload(self):
        helper = load_nonzero_helper()
        cases = [
            (b"", "no_json"),
            (b"not json\n", "invalid_json"),
            (b'{"error":{"message":"private"}}\n', "error_event"),
            (b'{"type":"text","part":{"error":{"message":"private"}}}\n', "error_part"),
            (b'{"type":"step_finish","part":{"reason":"tool-calls"}}\n', "tool_event"),
            (b'{"type":"mystery","part":{}}\n', "unexpected_event"),
            (b'{"type":"text","part":{"type":"text","text":"private"}}\n', "structured_nonzero"),
        ]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "events.jsonl"
            for payload, expected in cases:
                path.write_bytes(payload)
                self.assertEqual(helper.classify(path), expected)

    def test_specific_nonzero_json_categories_precede_generic_cli_failure(self):
        text = RUNNER.read_text(encoding="utf-8")
        generic = text.index("failure_rc=102")
        for code in range(123, 130):
            marker = f"failure_rc={code}"
            self.assertIn(marker, text)
            self.assertLess(text.index(marker), generic)

    def test_gateway_exposes_only_fixed_nonzero_json_names(self):
        text = CLASSIFIER.read_text(encoding="utf-8")
        expected = {
            123: "opencode_nonzero_error_event",
            124: "opencode_nonzero_error_part",
            125: "opencode_nonzero_tool_event",
            126: "opencode_nonzero_unexpected_event",
            127: "opencode_nonzero_invalid_json",
            128: "opencode_nonzero_structured",
            129: "opencode_nonzero_no_json",
        }
        for code, name in expected.items():
            self.assertIn(f'{code}: "{name}"', text)


if __name__ == "__main__":
    unittest.main()
