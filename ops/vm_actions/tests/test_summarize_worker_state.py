import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "summarize_worker_state.py"


def load_module():
    spec = importlib.util.spec_from_file_location("summarize_worker_state", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WorkerStateSummaryTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def test_registered_workers_are_collapsed_to_fixed_categories(self):
        data = {
            "workers": {
                "details": {
                    "radio_worker": {"paused": True, "stale_pid_file": False},
                    "audio_worker": {"paused": False, "stale_pid_file": True},
                    "youtube_worker": {"paused": True, "stale_pid_file": True},
                },
                "unregistered": [],
            }
        }
        summary = self.mod.summarize_worker_state(data)
        self.assertIn("paused_radio=1", summary)
        self.assertIn("paused_chat=1", summary)
        self.assertIn("stale_pid_audio=1", summary)
        self.assertIn("stale_pid_chat=1", summary)
        self.assertNotIn("radio_worker", summary)
        self.assertNotIn("youtube_worker", summary)

    def test_unregistered_workers_emit_only_fixed_state_counts(self):
        data = {
            "workers": {
                "unregistered": ["SECRET_DYNAMIC_ONE", "SECRET_DYNAMIC_TWO"],
                "details": {
                    "SECRET_DYNAMIC_ONE": {
                        "alive": True,
                        "stale_pid_file": False,
                        "paused": False,
                    },
                    "SECRET_DYNAMIC_TWO": {
                        "alive": False,
                        "stale_pid_file": True,
                        "paused": True,
                    },
                },
            }
        }
        summary = self.mod.summarize_worker_state(data)
        self.assertIn("unregistered_alive=1", summary)
        self.assertIn("unregistered_stale=1", summary)
        self.assertIn("unregistered_paused=1", summary)
        self.assertIn("unregistered_alive_category_other=1", summary)
        self.assertIn("unregistered_stale_category_other=1", summary)
        self.assertIn("unregistered_paused_category_other=1", summary)
        self.assertNotIn("SECRET_DYNAMIC_ONE", summary)
        self.assertNotIn("SECRET_DYNAMIC_TWO", summary)

    def test_known_auxiliaries_emit_fixed_categories_without_names(self):
        data = {
            "workers": {
                "unregistered": ["soren_loop.manual", "explore", "explore_bridge"],
                "details": {
                    "soren_loop.manual": {
                        "alive": False,
                        "stale_pid_file": True,
                        "paused": False,
                    },
                    "explore": {
                        "alive": True,
                        "stale_pid_file": False,
                        "paused": False,
                    },
                    "explore_bridge": {
                        "alive": True,
                        "stale_pid_file": False,
                        "paused": False,
                    },
                },
            }
        }
        summary = self.mod.summarize_worker_state(data)
        self.assertIn("unregistered_stale_category_manual_loop=1", summary)
        self.assertIn("unregistered_alive_category_exploration=1", summary)
        self.assertIn("unregistered_alive_category_exploration_bridge=1", summary)
        self.assertIn("unregistered_alive_category_other=0", summary)
        self.assertNotIn("soren_loop.manual", summary)
        self.assertNotIn("explore_bridge", summary)

    def test_malformed_dynamic_values_fail_closed_to_zero(self):
        data = {
            "workers": {
                "unregistered": "SECRET_DYNAMIC",
                "details": {
                    "radio_worker": {"paused": "true", "stale_pid_file": 1},
                },
            }
        }
        summary = self.mod.summarize_worker_state(data)
        self.assertIn("paused_radio=0", summary)
        self.assertIn("stale_pid_radio=0", summary)
        self.assertIn("unregistered_alive=0", summary)
        self.assertIn("unregistered_stale=0", summary)
        self.assertIn("unregistered_paused=0", summary)
        self.assertIn("unregistered_alive_category_other=0", summary)
        self.assertNotIn("SECRET_DYNAMIC", summary)


if __name__ == "__main__":
    unittest.main()