"""PR-0 baseline: pin #882 direct golden against the soviet_now source.

Mock only. No network, no secrets, no VM. Reads the移行元 file and the
fixed fixture, and asserts the security/behavior contract has not drifted
before the docich native port (PR-2). Skips when the submodule is absent.
"""

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "jev_direct_golden.json"
SOURCE = REPO_ROOT / "games" / "soviet_now" / "lib" / "comment_classifier_jev.py"


def _load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class TestJevDirectGolden(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SOURCE.is_file():
            raise unittest.SkipTest("games/soviet_now submodule not present")
        cls.src = SOURCE.read_text(encoding="utf-8")
        cls.golden = _load_fixture()

    def test_fixture_files_exist(self):
        self.assertTrue(FIXTURE.is_file())

    def test_endpoint_is_fixed_direct(self):
        self.assertIn(
            "ENDPOINT = 'https://api.typesafe.ai/v1/systemone'", self.src
        )
        self.assertEqual(
            self.golden["route_direct"]["endpoint"],
            "https://api.typesafe.ai/v1/systemone",
        )

    def test_requested_model_default(self):
        self.assertIn("model: str = 'jev-1.13.0'", self.src)
        self.assertEqual(
            self.golden["route_direct"]["requested_model"], "jev-1.13.0"
        )

    def test_credential_env_is_typesafe_key(self):
        self.assertIn("TYPESAFE_API_KEY", self.src)
        self.assertEqual(
            self.golden["route_direct"]["credential_env"], "TYPESAFE_API_KEY"
        )
        # The Vercel credential must not be introduced into the移行元.
        self.assertNotIn("DOCICH_JEV_VERCEL_API_KEY", self.src)

    def test_transport_contract(self):
        for token in (
            "ProxyHandler({})",
            "MAX_COMMENTS = 8",
            "timeout_ms: int = 1500",
            "min_confidence: float = 0.70",
            "MAX_COMMENT_BYTES = 4096",
            "MAX_REQUEST_BYTES = 32768",
            "MAX_RESPONSE_BYTES = 131072",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.src)
        # retry 0: single transport call inside a single gated request
        # (no retry loop around request_once/gated_request).
        self.assertNotIn("for attempt in", self.src)
        self.assertNotIn("for retry in", self.src)

    def test_categories_match_golden(self):
        m = re.search(r"CRITERIA = \{(.*?)\n\}", self.src, re.DOTALL)
        self.assertIsNotNone(m)
        keys = re.findall(r"^    '([a-z_]+)':", m.group(1), re.MULTILINE)
        self.assertEqual(keys, self.golden["bounds"]["categories"])

    def test_projection_does_not_send_context(self):
        # Docstring-level contract: never serialize event/context.
        self.assertIn("Never serialize a received event/context", self.src)
        self.assertIn("Never pass baseline labels to Jev", self.src)

    def test_metrics_redaction_contract(self):
        self.assertIn("no raw data", self.src)


if __name__ == "__main__":
    unittest.main()
