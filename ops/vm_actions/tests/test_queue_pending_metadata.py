import importlib.util
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"


def load_collector():
    spec = importlib.util.spec_from_file_location("collect_diagnostics_pending_test", str(COLLECTOR))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PriorityQueueMetadataTests(unittest.TestCase):
    def test_pending_request_container_is_not_reported_as_stale_lane(self):
        module = load_collector()
        with tempfile.TemporaryDirectory(prefix="vmops-pending-") as tmp:
            soren = Path(tmp) / "soren"
            request = soren / "tmp" / "state" / ".ai_generation_locks" / ".pending" / "123.1.radio"
            request.mkdir(parents=True)
            (request / "owner").write_text("pid=99999999\nlane=radio\nlabel=RADIO:test\n", encoding="utf-8")

            queues = module._collect_queues(soren, int(time.time()))

            self.assertNotIn(".pending", queues["lanes"])
            self.assertEqual(queues["stale_locks"], 0)
            self.assertIn("radio", queues["lanes"])
            self.assertFalse(queues["lanes"]["radio"]["locked"])


if __name__ == "__main__":
    unittest.main()
