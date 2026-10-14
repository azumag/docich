import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "summarize_comment_winners.py"
SPEC = importlib.util.spec_from_file_location("summarize_comment_winners", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class CommentWinnerSummaryTests(unittest.TestCase):
    def test_counts_only_comment_winners_by_fixed_backend(self):
        data = {
            "ai": {
                "recent_events": [
                    {"event": "winner", "component": "COMMENT", "provider": "opencode", "model": "secret-free-model"},
                    {"event": "winner", "component": "COMMENT:translation", "provider": "amd", "model": "private-model"},
                    {"event": "winner", "component": "RADIO:news", "provider": "vercel", "model": "ignored"},
                    {"event": "fail", "component": "COMMENT", "provider": "vercel", "model": "ignored"},
                    {"event": "winner", "component": "COMMENT", "provider": "opencode-go", "model": "private-paid"},
                ]
            }
        }
        summary = mod.summarize(data)
        self.assertIn("comment_winner_sampled=3", summary)
        self.assertIn("comment_winner_backend_opencode=1", summary)
        self.assertIn("comment_winner_backend_opencode_go=1", summary)
        self.assertIn("comment_winner_backend_amd=1", summary)
        self.assertIn("comment_winner_backend_vercel=0", summary)
        self.assertNotIn("secret-free-model", summary)
        self.assertNotIn("private-model", summary)
        self.assertNotIn("private-paid", summary)

    def test_unknown_provider_is_fixed_other_bucket(self):
        data = {"ai": {"recent_events": [
            {"event": "winner", "component": "comment", "provider": "new-provider", "model": "do-not-leak"}
        ]}}
        summary = mod.summarize(data)
        self.assertIn("comment_winner_sampled=1", summary)
        self.assertIn("comment_winner_backend_other=1", summary)
        self.assertNotIn("new-provider", summary)
        self.assertNotIn("do-not-leak", summary)

    def test_missing_recent_events_is_zero_not_failure(self):
        summary = mod.summarize({"ai": {}})
        self.assertIn("comment_winner_sampled=0", summary)
        for family in mod.BACKEND_FAMILIES:
            self.assertIn(f"comment_winner_backend_{family}=0", summary)

    def test_rejects_non_object_root(self):
        with self.assertRaises(ValueError):
            mod.summarize([])


if __name__ == "__main__":
    unittest.main()
