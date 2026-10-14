import base64
import io
import json
import unittest
from unittest import mock

from ops.vm_actions import collect_diagnostics, gateway, runtime_registry


class Soren91EvidenceDiagnosticsBudgetTests(unittest.TestCase):
    def test_full_manual_chunk_fits_collector_with_gateway_headroom(self):
        chunk_bytes = collect_diagnostics.MANUAL_EVIDENCE_CHUNK_BYTES
        encoded = base64.b64encode(b"x" * chunk_bytes).decode("ascii")
        parts = [
            encoded[index:index + collect_diagnostics.MANUAL_EVIDENCE_PART_CHARS]
            for index in range(0, len(encoded), collect_diagnostics.MANUAL_EVIDENCE_PART_CHARS)
        ]
        payload = {
            "status": "ok",
            "soren91_manual_evidence": {
                "active": True,
                "version": 1,
                "createdAtMs": 9_999_999_999_999,
                "expiresAtMs": 10_000_000_599_999,
                "bundleBytes": collect_diagnostics.MANUAL_EVIDENCE_BUNDLE_MAX,
                "bundleSha256": "f" * 64,
                "chunkBytes": chunk_bytes,
                "chunkCount": collect_diagnostics.MANUAL_EVIDENCE_MAX_CHUNKS,
                "chunkIndex": collect_diagnostics.MANUAL_EVIDENCE_MAX_CHUNKS - 1,
                "games": [999_999_999_999_997, 999_999_999_999_998, 999_999_999_999_999],
                "parts": parts,
            },
        }
        rendered_bytes = len(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8"))

        # The original 32 KiB collector cap rejected every full 24 KiB chunk
        # after base64 + JSON expansion, before the gateway could return it.
        self.assertGreater(rendered_bytes, 32 * 1024)
        self.assertLessEqual(rendered_bytes, runtime_registry.MAX_JSON_BYTES)
        # Preserve room for gateway-added bundle/projection metadata rather
        # than simply widening the public diagnostics boundary to match it.
        self.assertLess(runtime_registry.MAX_JSON_BYTES, gateway.DIAGNOSTICS_JSON_MAX)

    def test_active_manual_evidence_stdout_is_one_valid_json_document(self):
        # The gateway parses the collector's entire stdout with json.loads();
        # a literal backslash+n trailer is therefore a protocol failure.
        evidence = {
            "active": True,
            "version": 1,
            "createdAtMs": 1,
            "expiresAtMs": 2,
            "bundleBytes": 1,
            "bundleSha256": "f" * 64,
            "chunkBytes": collect_diagnostics.MANUAL_EVIDENCE_CHUNK_BYTES,
            "chunkCount": 1,
            "chunkIndex": 0,
            "games": [1],
            "parts": ["eA=="],
        }
        out = io.StringIO()
        with mock.patch.object(
            collect_diagnostics,
            "_collect_soren91_manual_evidence",
            return_value=evidence,
        ), mock.patch("sys.stdout", out):
            rc = collect_diagnostics.main(["collect_diagnostics.py", "/tmp/soren"])

        self.assertEqual(rc, 0)
        raw = out.getvalue()
        self.assertTrue(raw.endswith("\n"))
        self.assertFalse(raw.endswith("\\n"))
        decoded = json.loads(raw)
        self.assertEqual(decoded["status"], "ok")
        self.assertEqual(decoded["soren91_manual_evidence"], evidence)


if __name__ == "__main__":
    unittest.main()
