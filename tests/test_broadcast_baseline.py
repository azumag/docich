"""PR-0/PR-1 baseline: pin the broadcast migration boundary for #829.

Mock only. No shell execution, no network, no secrets, no VM.
Static reads assert that chat/radio remain compatibility wrappers while the
AI entry point uses the native docich dispatcher. Any silent drift of the
boundary fails before later native pipeline work.
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
            stages["classifier_py"],
            stages["classifier_sh"],
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

    def test_classifier_gate_documented(self):
        stages = self.golden["soviet_now_stages"]
        path = REPO_ROOT / stages["classifier_sh"]
        if not path.is_file():
            raise unittest.SkipTest("classifier shim absent (no submodule)")
        content = path.read_text(encoding="utf-8")
        self.assertIn(stages["classifier_gate_env"], content)
        self.assertIn(stages["classifier_gate_value"], content)


if __name__ == "__main__":
    unittest.main()
