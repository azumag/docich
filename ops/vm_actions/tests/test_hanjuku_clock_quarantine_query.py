import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
PATH = ROOT / "authorize_hanjuku_clock_quarantine_query.py"
SPEC = importlib.util.spec_from_file_location("authorize_hanjuku_clock_quarantine_query", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class HanjukuClockQuarantineQueryAuthTest(unittest.TestCase):
    def env(self):
        return {
            "GITHUB_REPOSITORY": "azumag/docich",
            "GITHUB_REPOSITORY_ID": "1327276249",
            "GITHUB_REPOSITORY_OWNER": "azumag",
            "GITHUB_REPOSITORY_OWNER_ID": "9018513",
            "GITHUB_ACTOR": "azumag",
            "GITHUB_ACTOR_ID": "9018513",
            "GITHUB_TRIGGERING_ACTOR": "azumag",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_REF_PROTECTED": "true",
            "GITHUB_DEFAULT_BRANCH": "main",
            "GITHUB_WORKFLOW_REF": "azumag/docich/.github/workflows/hanjuku-clock-quarantine-query.yml@refs/heads/main",
            "GITHUB_EVENT_NAME": "issue_comment",
            "GITHUB_EVENT_ACTION": "created",
            "GITHUB_SHA": "a" * 40,
            "ISSUE_NUMBER": "1657",
            "COMMENT_BODY": "/hanjuku-clear-clock-quarantine production",
            "COMMENT_AUTHOR": "azumag",
            "COMMENT_AUTHOR_ID": "9018513",
        }

    def test_exact_owner_command_is_authorized(self):
        self.assertEqual(
            MODULE.authorize(self.env()),
            {"operation": "clear-hanjuku-clock-quarantine", "target": "production", "ref": "main"},
        )

    def test_security_boundaries_are_fail_closed(self):
        mutations = {
            "GITHUB_REPOSITORY": "other/docich",
            "GITHUB_REPOSITORY_ID": "1",
            "GITHUB_REPOSITORY_OWNER": "other",
            "GITHUB_REPOSITORY_OWNER_ID": "1",
            "GITHUB_ACTOR": "other",
            "GITHUB_ACTOR_ID": "1",
            "GITHUB_TRIGGERING_ACTOR": "other",
            "GITHUB_REF": "refs/heads/dev",
            "GITHUB_REF_PROTECTED": "false",
            "GITHUB_DEFAULT_BRANCH": "dev",
            "GITHUB_WORKFLOW_REF": "other",
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_EVENT_ACTION": "edited",
            "GITHUB_SHA": "not-a-sha",
            "ISSUE_NUMBER": "1",
            "COMMENT_AUTHOR": "other",
            "COMMENT_AUTHOR_ID": "1",
        }
        for key, value in mutations.items():
            with self.subTest(key=key):
                env = self.env()
                env[key] = value
                with self.assertRaises(ValueError):
                    MODULE.authorize(env)

    def test_command_must_be_complete_exact_match(self):
        for command in (
            "/hanjuku-clear-clock-quarantine production ",
            " /hanjuku-clear-clock-quarantine production",
            "/hanjuku-clear-clock-quarantine production\n",
            "/hanjuku-clear-clock-quarantine staging",
            "/hanjuku-clear-clock-quarantine production; id",
        ):
            with self.subTest(command=command):
                env = self.env()
                env["COMMENT_BODY"] = command
                with self.assertRaises(ValueError):
                    MODULE.authorize(env)


if __name__ == "__main__":
    unittest.main()
