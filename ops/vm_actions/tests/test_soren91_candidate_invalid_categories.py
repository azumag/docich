import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "run_soren91_daily_improve.sh"
CLASSIFIER = ROOT / "ops" / "vm_actions" / "classify_soren91_daily_gateway.py"


def load_classifier():
    spec = importlib.util.spec_from_file_location("soren91_daily_gateway_candidate", CLASSIFIER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gateway_result(exit_code, sha="a" * 40):
    return json.dumps({
        "status": "executed",
        "sha": sha,
        "exit_code": exit_code,
        "output": "withheld",
        "operation_id": "b" * 32,
    }, separators=(",", ":"))


class Soren91CandidateInvalidCategoriesTests(unittest.TestCase):
    def test_candidate_validation_subtypes_are_fixed_and_public_safe(self):
        classifier = load_classifier()
        sha = "a" * 40
        expected = {
            130: "candidate_missing_decide",
            131: "candidate_decide_not_function",
            132: "candidate_return_invalid",
            133: "candidate_x_out_of_range",
            134: "candidate_behavior_contract",
            135: "candidate_undefined_variable",
            136: "candidate_code_error",
        }
        for exit_code, category in expected.items():
            self.assertEqual(
                classifier.classify_gateway_result(gateway_result(exit_code), sha, exit_code),
                category,
            )

    def test_specific_candidate_markers_precede_generic_candidate_invalid(self):
        text = SCRIPT.read_text(encoding="utf-8")
        generic = text.index("if grep -Fq 'candidate_invalid:'")
        markers = {
            "candidate_invalid:no decide() function found": 130,
            "candidate_invalid:decide is not exported as a function": 131,
            "candidate_invalid:decide() returned invalid format": 132,
            "candidate_invalid:decide() returned x=": 133,
            "candidate_invalid:Strategy contract:": 134,
            "candidate_invalid:Undefined variable detected:": 135,
            "candidate_invalid:Code error:": 136,
        }
        for marker, exit_code in markers.items():
            pos = text.index(marker)
            self.assertLess(pos, generic)
            self.assertIn(f"failure_rc={exit_code}", text[pos:generic])

        # Raw model/candidate output remains private; only the fixed exit category
        # is allowed to leave the owner-only gateway.
        self.assertNotIn('cat "$out"', text)
        self.assertNotIn('echo "$out"', text)


if __name__ == "__main__":
    unittest.main()
