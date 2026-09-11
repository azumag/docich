from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "ops/vm_actions/reconcile_presynced_submodule.py"
ATTESTATIONS = ROOT / "ops/vm_actions/reviewed_lineage_attestations.json"
EXEC_STDIN_LIMIT = 16384


class ReconcileExecPayloadBudgetTests(unittest.TestCase):
    def test_current_attested_reconcile_command_fits_gateway_exec_limit(self):
        data = json.loads(ATTESTATIONS.read_text(encoding="utf-8"))
        entries = [
            item
            for item in data.get("attestations", [])
            if item.get("projection") == "games/soviet_now"
        ]
        self.assertTrue(entries)

        first = entries[0]
        old_sub = first["old_sub"]
        new_sub = first["new_sub"]
        matching = [
            item
            for item in entries
            if item.get("old_sub") == old_sub and item.get("new_sub") == new_sub
        ]
        tokens: list[str] = []
        for item in matching:
            tokens.extend([item["path"], item["sha256"], item["mode"]])

        argv = [
            "/home/ubuntu/docich",
            "0" * 40,
            old_sub,
            new_sub,
            "games/soviet_now",
            "lineage",
            *tokens,
        ]
        prefix = "python3 - " + " ".join(f"'{token}'" for token in argv) + " <<'PY'\n"
        payload = prefix.encode("utf-8") + HELPER.read_bytes() + b"\nPY\n"

        self.assertLessEqual(
            len(payload),
            EXEC_STDIN_LIMIT,
            f"production exec payload is {len(payload)} bytes, above {EXEC_STDIN_LIMIT}",
        )


if __name__ == "__main__":
    unittest.main()
