"""Pin the #882 direct-route golden against docich's own classifier.

The golden was first fixed against soviet_now's ``lib/comment_classifier_jev.py``
(PR-0). Classification is now owned by docich (``docich.comment_classifier`` +
``docich.semantic_decision``) and soviet_now keeps no copy, so the same
contract is asserted on the docich modules. Mock only: no network, no
secrets, no VM, and no game checkout is needed.
"""

import inspect
import json
from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from docich.comment_classifier import heuristic, jev  # noqa: E402
from docich.semantic_decision import transport  # noqa: E402
from docich.semantic_decision.routes import resolve_route  # noqa: E402

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "jev_direct_golden.json"


class TestJevDirectGolden(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.route = resolve_route("direct")

    def test_endpoint_model_and_credential_are_fixed(self):
        direct = self.golden["route_direct"]
        self.assertEqual(self.route.endpoint, direct["endpoint"])
        self.assertEqual(self.route.requested_model, direct["requested_model"])
        self.assertEqual(self.route.credential_env, direct["credential_env"])
        self.assertEqual(jev.Config().model, direct["requested_model"])
        self.assertEqual(jev.RUBRIC_VERSION, direct["rubric_version"])

    def test_transport_contract(self):
        contract, bounds = self.golden["transport"], self.golden["bounds"]
        config = jev.Config()
        self.assertEqual(config.timeout_ms, contract["timeout_ms"])
        self.assertEqual(config.min_confidence, bounds["min_confidence"])
        self.assertEqual(jev.MAX_COMMENTS, bounds["max_comments_per_request"])
        self.assertEqual(jev.MAX_COMMENT_BYTES, bounds["max_comment_bytes"])
        self.assertEqual(transport.MAX_REQUEST_BYTES, bounds["max_request_bytes"])
        self.assertEqual(transport.MAX_RESPONSE_BYTES, bounds["max_response_bytes"])
        source = inspect.getsource(transport)
        self.assertIn("ProxyHandler({})", source)
        self.assertIn("def redirect_request", source)
        # retry 0: one transport call per gated request, no retry loop.
        for module in (transport, jev):
            text = inspect.getsource(module)
            self.assertNotIn("for attempt in", text)
            self.assertNotIn("for retry in", text)

    def test_categories_notifications_and_system_users_match_golden(self):
        bounds = self.golden["bounds"]
        self.assertEqual(list(jev.CRITERIA), bounds["categories"])
        self.assertEqual(jev.NOTIFICATIONS, set(bounds["notifications_not_sent"]))
        self.assertEqual(heuristic.SYSTEM_USERS, set(bounds["system_users"]))

    def test_projection_sends_only_comment_bodies(self):
        # Category names are rubric choices, so the baseline label is checked
        # through the request shape; the other context uses unique markers.
        rows = [{"index": 1, "user": "viewer_zq", "comment": "hello",
                 "category": "chitchat", "is_english": True,
                 "persona": "persona_zq", "game": "game_zq", "history": ["history_zq"]}]
        request = jev.build_request(rows, jev.Config().model)
        self.assertEqual(request["state"], {"comments": [{"index": 1, "text": "hello"}]})
        self.assertEqual(set(request), {"model", "state", "questions"})
        serialized = json.dumps(request)
        for leaked in ("viewer_zq", "persona_zq", "game_zq", "history_zq", "is_english"):
            self.assertNotIn(leaked, serialized)


if __name__ == "__main__":
    unittest.main()
