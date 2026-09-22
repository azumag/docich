"""Pin the broadcast ownership/delegation boundary for #829/#882.

Mock/static only. No network, secrets, or VM. Chat/radio compatibility wrappers
remain in docich, while comment classification is owned by docich and Soren only
delegates to the fixed docich entry point.
"""

import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "broadcast_baseline.json"


def _load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class TestBroadcastBaseline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = _load_fixture()
        cls.chat_py = (
            REPO_ROOT / cls.golden["docich_wrappers"]["chat_source"]
        ).read_text(encoding="utf-8")
        cls.ai_py = (REPO_ROOT / "src" / "docich" / "ai_generate.py").read_text(
            encoding="utf-8"
        )
        cls.dispatch_py = (REPO_ROOT / "src" / "docich" / "llm" / "dispatch.py").read_text(
            encoding="utf-8"
        )

    def test_fixture_exists(self):
        self.assertTrue(FIXTURE.is_file())

    def test_comment_wrapper_references_legacy_entry(self):
        w = self.golden["docich_wrappers"]
        self.assertIn(w["comment_function"], self.chat_py)
        for source in w["comment_sources"]:
            self.assertIn(source, self.chat_py)

    def test_radio_wrapper_references_legacy_entry(self):
        w = self.golden["docich_wrappers"]
        self.assertIn(w["radio_function"], self.chat_py)

    def test_ai_entry_uses_native_dispatch(self):
        w = self.golden["docich_wrappers"]
        self.assertIn(w["native_ai_entry"], self.dispatch_py)
        self.assertIn("Dispatcher", self.ai_py)
        self.assertNotIn("game_submodule", self.ai_py)

    def test_rc_contract_documented(self):
        self.assertIn("rc 0", self.chat_py)
        rc = self.golden["docich_wrappers"]["rc_contract"]
        self.assertEqual(set(rc.keys()), {"0", "1", "2"})

    def test_real_run_gates_present(self):
        gates = self.golden["docich_wrappers"]["real_run_gates"]
        self.assertIn(gates["comment"], self.chat_py)
        self.assertIn(gates["radio"], self.chat_py)
        self.assertIn(gates["ai"], self.ai_py)

    def test_outbound_redirect_present(self):
        # Compat wrappers must not publish to the real chat queue.
        self.assertIn("OUTBOUND_CHAT_QUEUE_DIR", self.chat_py)

    def test_soviet_stage_files_present_when_submodule_available(self):
        stages = self.golden["soviet_now_stages"]
        paths = [
            stages["comment"],
            stages["radio_engine"],
            stages["ai_generate"],
            stages["classifier_owner_entry"],
            stages["news_spam"],
            stages["prepass_budget"],
        ]
        missing = [p for p in paths if not (REPO_ROOT / p).is_file()]
        submodule = REPO_ROOT / "games" / "soviet_now"
        if missing and not (submodule / ".git").exists():
            raise unittest.SkipTest("games/soviet_now submodule not present")
        self.assertEqual(missing, [], f"missing stage files: {missing}")

    def test_soviet_entry_points_present_when_submodule_available(self):
        stages = self.golden["soviet_now_stages"]
        checks = [
            (stages["comment"], stages["comment_entry"]),
            (stages["radio_engine"], stages["radio_entry"]),
            (stages["ai_generate"], stages["ai_entry"]),
        ]
        for rel, token in checks:
            path = REPO_ROOT / rel
            if not path.is_file():
                raise unittest.SkipTest(f"submodule file absent: {rel}")
            with self.subTest(file=rel):
                self.assertIn(token, path.read_text(encoding="utf-8"))

    def test_classifier_is_docich_owned_and_soren_only_delegates(self):
        stages = self.golden["soviet_now_stages"]
        comment = REPO_ROOT / stages["comment"]
        if not comment.is_file():
            raise unittest.SkipTest("games/soviet_now submodule not present")
        content = comment.read_text(encoding="utf-8")
        self.assertIn(stages["classifier_delegate_env"], content)
        self.assertIn(stages["classifier_delegate_default"], content)
        self.assertTrue((REPO_ROOT / stages["classifier_owner_entry"]).is_file())
        for rel in stages["classifier_removed"]:
            with self.subTest(removed=rel):
                self.assertFalse((REPO_ROOT / rel).exists(), rel)


if __name__ == "__main__":
    unittest.main()
