import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

from ops.vm_actions import resolve_canonical_diagnostics_run as resolver


SHA = "a" * 40
DIAGNOSTICS_JSON = '{"status":"diagnosed","age_sec":***,"sha":"%s"}' % SHA
AUTHORIZATION_LOG = (
    "operate\tAuthorize owner operation\t2026-10-15T02:24:40Z   INPUT_OPERATION: diagnostics\n"
    "operate\tAuthorize owner operation\t2026-10-15T02:24:40Z   INPUT_TARGET: production\n"
    "operate\tAuthorize owner operation\t2026-10-15T02:24:40Z   INPUT_REF: main\n"
    "operate\tAuthorize owner operation\t2026-10-15T02:24:40Z   INPUT_CONFIRM:\n"
)
DIAGNOSTICS_LOG = (
    "operate\tQuery read-only runtime diagnostics\t2026-10-15T02:25:03Z " + DIAGNOSTICS_JSON + "\n"
)
LOG = AUTHORIZATION_LOG + DIAGNOSTICS_LOG


def metadata() -> dict:
    return {
        "conclusion": "success",
        "event": "workflow_dispatch",
        "headBranch": "main",
        "headSha": SHA,
        "jobs": [
            {
                "conclusion": "success",
                "steps": [
                    {"name": "Authorize owner operation", "conclusion": "success"},
                    {"name": resolver.DEPLOY_STEP, "conclusion": "skipped"},
                    {"name": resolver.DIAGNOSTICS_STEP, "conclusion": "success"},
                    {"name": resolver.EXEC_STEP, "conclusion": "skipped"},
                ],
            }
        ],
        "url": "https://github.com/azumag/docich/actions/runs/123",
    }


def run_list(database_ids: list[int]) -> mock.Mock:
    return mock.Mock(returncode=0, stdout=json.dumps([{"databaseId": value} for value in database_ids]))


def run_metadata(value: dict | None) -> mock.Mock:
    if value is None:
        return mock.Mock(returncode=1, stdout="", stderr="")
    return mock.Mock(returncode=0, stdout=json.dumps(value))


def run_log(value: str) -> mock.Mock:
    return mock.Mock(returncode=0, stdout=value)


class ResolveCanonicalDiagnosticsRunTests(unittest.TestCase):
    def test_resolve_returns_newest_canonical_run(self):
        with mock.patch.object(
            resolver.subprocess,
            "run",
            side_effect=[run_list([123]), run_metadata(metadata()), run_log(LOG)],
        ):
            resolved = resolver.resolve("azumag/docich", "vm-operations.yml", 30)
        assert resolved is not None
        self.assertEqual(resolved["databaseId"], 123)
        self.assertEqual(resolved["url"], "https://github.com/azumag/docich/actions/runs/123")
        self.assertEqual(resolved["authorization"]["operation"], "diagnostics")
        self.assertEqual(resolved["authorization"]["target"], "production")
        self.assertEqual(resolved["authorization"]["ref"], "main")
        self.assertEqual(resolved["authorization"]["confirm"], "")
        self.assertEqual(resolved["steps"][resolver.DEPLOY_STEP], "skipped")
        self.assertEqual(resolved["steps"][resolver.EXEC_STEP], "skipped")
        self.assertEqual(resolved["structuredOutputStatus"], "diagnosed")

    def test_resolve_skips_disappeared_run_and_uses_next_candidate(self):
        with mock.patch.object(
            resolver.subprocess,
            "run",
            side_effect=[
                run_list([404, 123]),
                run_metadata(None),
                run_metadata(metadata()),
                run_log(LOG),
            ],
        ):
            resolved = resolver.resolve("azumag/docich", "vm-operations.yml", 30)
        assert resolved is not None
        self.assertEqual(resolved["databaseId"], 123)

    def test_resolve_returns_none_without_candidate(self):
        with mock.patch.object(resolver.subprocess, "run", side_effect=[run_list([])]):
            self.assertIsNone(resolver.resolve("azumag/docich", "vm-operations.yml", 30))

    def test_non_main_or_non_success_runs_are_rejected(self):
        for change in (
            {"conclusion": "failure"},
            {"event": "push"},
            {"headBranch": "feature"},
            {"headSha": "not-a-sha"},
        ):
            with self.subTest(change=change):
                value = metadata()
                value.update(change)
                self.assertFalse(resolver.is_canonical(value))

    def test_deploy_or_exec_step_must_be_skipped(self):
        for step_name, conclusion in (
            (resolver.DEPLOY_STEP, "success"),
            (resolver.EXEC_STEP, "success"),
        ):
            with self.subTest(step_name=step_name, conclusion=conclusion):
                value = metadata()
                for job in value["jobs"]:
                    for step in job["steps"]:
                        if step["name"] == step_name:
                            step["conclusion"] = conclusion
                self.assertFalse(resolver.is_canonical(value))

    def test_diagnostics_step_must_succeed(self):
        value = metadata()
        for job in value["jobs"]:
            for step in job["steps"]:
                if step["name"] == resolver.DIAGNOSTICS_STEP:
                    step["conclusion"] = "skipped"
        self.assertFalse(resolver.is_canonical(value))

    def test_missing_structured_output_is_rejected(self):
        with mock.patch.object(
            resolver.subprocess,
            "run",
            side_effect=[run_list([123]), run_metadata(metadata()), run_log(AUTHORIZATION_LOG)],
        ):
            self.assertIsNone(resolver.resolve("azumag/docich", "vm-operations.yml", 30))

    def test_authorization_must_prove_readonly_inputs(self):
        deploy_log = LOG.replace(
            "INPUT_OPERATION: diagnostics",
            "INPUT_OPERATION: deploy",
        )
        self.assertTrue(resolver.has_readonly_authorization(LOG))
        self.assertFalse(resolver.has_readonly_authorization(deploy_log))
        self.assertFalse(
            resolver.has_readonly_authorization(
                LOG.replace("INPUT_REF: main", "INPUT_REF: main-extra")
            )
        )

    def test_structured_output_extraction_keeps_mask(self):
        extracted = resolver.extract_structured_output(DIAGNOSTICS_LOG)
        assert extracted is not None
        self.assertEqual(extracted, DIAGNOSTICS_JSON)
        self.assertIn("***", extracted)
        self.assertNotIn("Query read-only runtime diagnostics", extracted)
        self.assertNotIn("2026-10-15T02:25:03Z", extracted)

    def test_dump_json_prints_sanitized_line(self):
        stdout = io.StringIO()
        with mock.patch.object(resolver.subprocess, "run", side_effect=[run_log(DIAGNOSTICS_LOG)]):
            with redirect_stdout(stdout):
                exit_code = resolver.main(["--dump-json", "123"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.getvalue().strip(), DIAGNOSTICS_JSON)

    def test_dump_json_without_output_exits_three(self):
        stdout = io.StringIO()
        with mock.patch.object(resolver.subprocess, "run", side_effect=[run_log(AUTHORIZATION_LOG)]):
            with redirect_stdout(stdout):
                exit_code = resolver.main(["--dump-json", "123"])
        self.assertEqual(exit_code, 3)
        self.assertEqual(stdout.getvalue(), "")

    def test_main_outputs_bounded_json(self):
        stdout = io.StringIO()
        with mock.patch.object(
            resolver.subprocess,
            "run",
            side_effect=[run_list([123]), run_metadata(metadata()), run_log(LOG)],
        ):
            with redirect_stdout(stdout):
                exit_code = resolver.main([])
        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["run"]["databaseId"], 123)
        self.assertNotIn(DIAGNOSTICS_JSON, stdout.getvalue())

    def test_invalid_scan_exits_two(self):
        self.assertEqual(resolver.main(["--scan", "0"]), 2)
        self.assertEqual(resolver.main(["--dump-json", "not-a-run-id"]), 2)


if __name__ == "__main__":
    unittest.main()
