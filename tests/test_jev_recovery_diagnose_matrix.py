import tempfile
import time
import unittest
from pathlib import Path

from docich.jev_corner import _stale_precommit_recovery_category


class _StatusAdapter:
    def __init__(self, root: Path, payload: dict[str, object]):
        self.root = root
        self.payload = payload

    def _status(self, deadline, cancel):
        return self.payload

    @staticmethod
    def _ack(payload):
        return payload.get("ack", {})


class _Manager:
    def __init__(self, adapter):
        self.adapter = adapter


class JevRecoveryDiagnoseMatrixTests(unittest.TestCase):
    def test_reversible_pre_stop_statuses_split_live_and_expired(self):
        cases = ("accepted", "waiting", "boundary", "stop_requested")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for status in cases:
                with self.subTest(status=status, expiry="live"):
                    payload = {
                        "request": {
                            "game": "sorengame",
                            "request_id": "req-1",
                            "deadline_epoch": time.time() + 60,
                        },
                        "ack": {
                            "game": "sorengame",
                            "request_id": "req-1",
                            "status": status,
                        },
                        "resource": {"game": "sorengame", "request_id": "req-1"},
                    }
                    category = _stale_precommit_recovery_category(
                        _Manager(_StatusAdapter(root, payload))
                    )
                    self.assertEqual(category, f"corner_stale_precommit_{status}_live")

                with self.subTest(status=status, expiry="expired"):
                    payload["request"]["deadline_epoch"] = 1
                    category = _stale_precommit_recovery_category(
                        _Manager(_StatusAdapter(root, payload))
                    )
                    self.assertEqual(category, f"corner_stale_precommit_{status}_expired")


if __name__ == "__main__":
    unittest.main()
