import json
import unittest

from ops.vm_actions import collect_diagnostics as collector


def _recent_event():
    return {
        "ts": 1,
        "component": "comment",
        "provider": "other",
        "model": "other",
        "event": "all_failed",
        "rc": "",
    }


class DiagnosticsPayloadBudgetTests(unittest.TestCase):
    def test_worker_detail_pressure_does_not_drop_recent_ai_failure(self):
        event = _recent_event()
        payload = {
            "workers": {"details": {"worker": "x" * collector.MAX_JSON_BYTES}},
            "ai": {"recent_events": [event], "anomalous_components": {}},
            "soren91_drop_profile": {"profileStatus": "empty"},
        }

        rendered = collector._render_bounded_payload(payload)
        decoded = json.loads(rendered)

        self.assertLessEqual(len(rendered.encode("utf-8")), collector.MAX_JSON_BYTES)
        self.assertEqual(decoded["workers"]["details"], {})
        self.assertEqual(decoded["ai"]["recent_events"], [event])

    def test_soren_profile_pressure_is_trimmed_before_recent_ai_failure(self):
        event = _recent_event()
        payload = {
            "workers": {"details": {}},
            "ai": {"recent_events": [event], "anomalous_components": {}},
            "soren91_drop_profile": {
                "profileStatus": "ok",
                "omittedComparisonGroups": 0,
                "omittedGameGroups": 0,
                "groups": {"oversized": "x" * collector.MAX_JSON_BYTES},
                "games": [{"game": 1}],
                "slowest": [{"phase": "capture"}],
            },
        }

        rendered = collector._render_bounded_payload(payload)
        decoded = json.loads(rendered)
        profile = decoded["soren91_drop_profile"]

        self.assertLessEqual(len(rendered.encode("utf-8")), collector.MAX_JSON_BYTES)
        self.assertEqual(decoded["ai"]["recent_events"], [event])
        self.assertEqual(profile["groups"], {})
        self.assertEqual(profile["games"], [])
        self.assertEqual(profile["slowest"], [])
        self.assertTrue(profile["representativeOmitted"])
        self.assertEqual(profile["omittedComparisonGroups"], 1)
        self.assertEqual(profile["omittedGameGroups"], 1)


if __name__ == "__main__":
    unittest.main()
