"""Hard-ceiling guarantee for the diagnostics envelope (#2533).

The collector's last-resort reductions used to be best-effort: a payload that
was still over ``HARD_JSON_BYTES`` after all of them was returned unchanged, and
the gateway then rejected the whole diagnostics operation closed. These checks
run under ``unittest discover -s ops/vm_actions/tests`` (the VM operations CI
step), so the guarantee is exercised on every PR that touches ``ops/**``.
"""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"


def load_collector():
    spec = importlib.util.spec_from_file_location("collect_diagnostics", str(COLLECTOR))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def production_scale_payload(**overrides):
    """An envelope whose corner projections alone exceed the hard ceiling."""
    payload = {
        "ai": {"recent_events": [], "recent_events_omitted": True,
               "anomalous_components": {}},
        "workers": {"details": {}},
        "nethack_history": {"daily": {"records": [], "omitted_records": 0,
                                       "output_omitted": False},
                            "completed_runs": {"records": [], "omitted_records": 0,
                                               "output_omitted": False}},
        "soren91_drop_profile": {"profileStatus": "unavailable", "groups": [],
                                 "games": [], "slowest": []},
        "corners": {
            "retro_corner": {"scene_narration": {"status": "ok"}},
            "rotation_evidence": {"corners": [
                {"corner": f"corner-{index}", "detail": "x" * 64}
                for index in range(700)
            ]},
        },
    }
    payload.update(overrides)
    return payload


class HardCeilingTests(unittest.TestCase):

    def setUp(self):
        self.module = load_collector()

    def _assert_bounded(self, payload):
        before = len(json.dumps(payload, sort_keys=True,
                                ensure_ascii=False).encode("utf-8"))
        self.assertGreater(before, self.module.HARD_JSON_BYTES)
        text = self.module._diagnostics_budget(payload)
        self.assertLessEqual(len(text.encode("utf-8")), self.module.HARD_JSON_BYTES)
        return payload

    def test_corner_projections_are_dropped_instead_of_failing_the_gateway(self):
        payload = self._assert_bounded(production_scale_payload())
        self.assertEqual(payload["corners"]["status"], "output_omitted")
        self.assertEqual(payload["corners"]["reason"], "hard_ceiling")

    def test_largest_remaining_section_is_the_fallback(self):
        # The ordered drop names the sections that normally carry the weight. A
        # payload whose heavy block is not in that order must still be bounded,
        # so the fallback drops the largest remaining top-level section and
        # keeps ``meta`` (the deployed SHA and collection status).
        payload = self._assert_bounded(production_scale_payload(
            corners=None,
            storage_artifacts={"artifacts": [
                {"name": f"artifact-{index}", "detail": "x" * 64}
                for index in range(700)
            ]},
        ))
        self.assertEqual(payload["storage_artifacts"]["status"], "output_omitted")

    def test_a_normal_envelope_is_left_alone(self):
        # The guarantee must not fire on the production envelope: measured
        # 50,280 B, it sits under the 56 KiB ceiling and every corner
        # projection has to survive.
        payload = production_scale_payload(
            corners={"retro_corner": {"scene_narration": {"status": "ok"}},
                     "rotation_evidence": {"corners": []}},
        )
        text = self.module._diagnostics_budget(payload)
        self.assertLessEqual(len(text.encode("utf-8")), self.module.HARD_JSON_BYTES)
        self.assertNotIn("status", payload["corners"])
        self.assertEqual(payload["corners"]["rotation_evidence"], {"corners": []})


if __name__ == "__main__":
    unittest.main()
