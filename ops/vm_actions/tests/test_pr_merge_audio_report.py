import base64
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
AUTH = ROOT / "ops/vm_actions/authorize_pr_merge_audio.py"
REPORT = ROOT / "ops/vm_actions/pr_merge_audio_report.py"
WF = ROOT / ".github/workflows/pr-merge-audio-report.yml"
REF = "azumag/docich/.github/workflows/pr-merge-audio-report.yml@refs/heads/main"
MERGE_SHA = "b" * 40
BEFORE_SHA = "a" * 40


def push_event(**overrides):
    event = {
        "ref": "refs/heads/main",
        "before": BEFORE_SHA,
        "after": MERGE_SHA,
        "forced": False,
        "deleted": False,
        "repository": {"full_name": "azumag/docich", "id": 1327276249},
        "commits": [{"id": MERGE_SHA}],
        "head_commit": {"id": MERGE_SHA},
    }
    event.update(overrides)
    return event


def auth_env(event_path, output_path):
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
        "GITHUB_WORKFLOW_REF": REF,
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_SHA": MERGE_SHA,
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_OUTPUT": str(output_path),
    }


def run_auth(tmp, event=None, *, raw=None, **overrides):
    event_path = Path(tmp) / "event.json"
    if raw is not None:
        event_path.write_text(raw, encoding="utf-8")
    else:
        event_path.write_text(json.dumps(push_event() if event is None else event), encoding="utf-8")
    out = Path(tmp) / "auth.out"
    out.write_text("")
    env = auth_env(event_path, out)
    env.update(overrides)
    result = subprocess.run([sys.executable, str(AUTH)], capture_output=True, text=True, env=env)
    return result, out.read_text()


def run_report(tmp, pulls=None, *, raw=None, merge_sha=MERGE_SHA):
    path = Path(tmp) / "pulls.json"
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
    else:
        path.write_text(json.dumps(pulls), encoding="utf-8")
    out = Path(tmp) / "report.out"
    out.write_text("")
    result = subprocess.run(
        [sys.executable, str(REPORT), str(path), "--merge-sha", merge_sha],
        capture_output=True,
        text=True,
        env={"GITHUB_OUTPUT": str(out)},
    )
    return result, out.read_text()


def outputs(raw):
    values = {}
    for line in raw.splitlines():
        key, _, value = line.partition("=")
        values[key] = value
    return values


def decoded(out):
    return base64.b64decode(outputs(out)["text_b64"]).decode("utf-8")


def pr(number=4686, title="fix(hanjuku): メニューの行対応付けを直す", **overrides):
    item = {
        "number": number,
        "title": title,
        "merged_at": "2026-11-19T07:00:00Z",
        "base": {"ref": "main"},
        "merge_commit_sha": MERGE_SHA,
    }
    item.update(overrides)
    return item


class AuthorizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_owner_push_to_protected_main_passes(self):
        result, output = run_auth(self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"operation": "pr-merge-audio-report", "target": "production", "ref": "main"},
        )
        self.assertEqual(
            outputs(output),
            {"operation": "pr-merge-audio-report", "target": "production", "ref": "main"},
        )

    def test_wrong_context_fails_closed(self):
        cases = (
            {"GITHUB_ACTOR": "other", "GITHUB_ACTOR_ID": "42"},
            {"GITHUB_TRIGGERING_ACTOR": "other"},
            {"GITHUB_REPOSITORY": "other/docich"},
            {"GITHUB_REPOSITORY_ID": "1"},
            {"GITHUB_REPOSITORY_OWNER": "other"},
            {"GITHUB_REPOSITORY_OWNER_ID": "1"},
            {"GITHUB_REF": "refs/heads/feature"},
            {"GITHUB_REF_PROTECTED": "false"},
            {"GITHUB_DEFAULT_BRANCH": "release"},
            {"GITHUB_WORKFLOW_REF": "azumag/docich/.github/workflows/other.yml@refs/heads/main"},
            {"GITHUB_WORKFLOW_REF": REF.replace("refs/heads/main", "refs/heads/feature")},
            {"GITHUB_WORKFLOW_REF": ""},
            {"GITHUB_EVENT_NAME": "pull_request"},
            {"GITHUB_EVENT_NAME": "workflow_dispatch"},
            {"GITHUB_SHA": "not-a-sha"},
            {"GITHUB_SHA": "0" * 40},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                result, output = run_auth(self.tmp, **overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output, "")

    def test_wrong_push_payload_fails_closed(self):
        cases = (
            push_event(ref="refs/heads/feature"),
            push_event(before=MERGE_SHA),
            push_event(after=BEFORE_SHA),
            push_event(forced=True),
            push_event(deleted=True),
            push_event(commits=[]),
            push_event(head_commit={"id": BEFORE_SHA}),
            push_event(head_commit=None),
            push_event(repository={"full_name": "other/docich", "id": 1327276249}),
            push_event(repository={"full_name": "azumag/docich", "id": 1}),
            push_event(repository=None),
            push_event(before="short"),
            [],
        )
        for event in cases:
            with self.subTest(event=event):
                result, output = run_auth(self.tmp, event)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output, "")

    def test_missing_or_malformed_event_file_fails_closed(self):
        result, output = run_auth(self.tmp, raw="{not json")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output, "")
        result, output = run_auth(self.tmp, GITHUB_EVENT_PATH="")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output, "")


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_merged_pr_title_is_read_as_one_japanese_sentence(self):
        result, output = run_report(self.tmp, [pr()])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outputs(output)["found"], "true")
        self.assertEqual(
            decoded(output),
            "mainにプルリクエスト4686「fix(hanjuku): メニューの行対応付けを直す」がマージされました。",
        )
        summary = json.loads(result.stdout)
        self.assertEqual(summary["pr"], 4686)
        self.assertEqual(summary["chars"], len(decoded(output)))

    def test_unmerged_wrong_base_and_malformed_entries_are_ignored(self):
        pulls = [
            {"number": 1, "title": "unmerged", "merged_at": None, "base": {"ref": "main"}},
            {"number": 2, "title": "wrong base", "merged_at": "2026-11-19T07:00:00Z", "base": {"ref": "develop"}},
            {"number": 3, "title": 4, "merged_at": "2026-11-19T07:00:00Z", "base": {"ref": "main"}},
            {"number": True, "title": "bool", "merged_at": "2026-11-19T07:00:00Z", "base": {"ref": "main"}},
            "not a dict",
            pr(number=4687, title="選ばれるPR"),
        ]
        result, output = run_report(self.tmp, pulls)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            decoded(output),
            "mainにプルリクエスト4687「選ばれるPR」がマージされました。",
        )

    def test_exact_merge_commit_wins_over_newer_associated_merged_pr(self):
        exact = pr(number=10, title="exact", merged_at="2026-11-19T06:00:00Z")
        newer = pr(number=11, title="newer", merged_at="2026-11-19T07:00:00Z", merge_commit_sha="c" * 40)
        result, output = run_report(self.tmp, [newer, exact])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(decoded(output), "mainにプルリクエスト10「exact」がマージされました。")

    def test_push_without_a_merged_pr_is_a_clean_skip(self):
        result, output = run_report(self.tmp, [])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outputs(output), {"found": "false"})
        self.assertEqual(json.loads(result.stdout), {"operation": "pr-merge-audio-report", "found": False})

    def test_markdown_urls_and_control_characters_are_removed(self):
        title = "**Fix** `x` [link](https://example.com) <b>tag</b> a\u202eb #123"
        result, output = run_report(self.tmp, [pr(title=title)])
        self.assertEqual(result.returncode, 0, result.stderr)
        text = decoded(output)
        self.assertIn("「Fix x link tag ab 123」", text)
        self.assertNotIn("http", text)
        self.assertNotIn("\u202e", text)
        self.assertFalse(any(ord(ch) < 32 for ch in text))

    def test_long_title_is_bounded_to_the_vm_limit(self):
        result, output = run_report(self.tmp, [pr(title="あ" * 400)])
        self.assertEqual(result.returncode, 0, result.stderr)
        text = decoded(output)
        self.assertEqual(len(text), 240)
        self.assertTrue(text.startswith("mainにプルリクエスト4686「"))
        self.assertTrue(text.endswith("」がマージされました。"))
        self.assertIn("…", text)

    def test_empty_or_symbol_only_title_falls_back(self):
        for title in ("", "***", "----", "「」"):
            with self.subTest(title=title):
                result, output = run_report(self.tmp, [pr(title=title)])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(decoded(output), "mainにプルリクエスト4686がマージされました。")

    def test_the_title_is_never_printed(self):
        title = "秘密ではないがログに出さない題名"
        result, _ = run_report(self.tmp, [pr(title=title)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(title, result.stdout + result.stderr)

    def test_invalid_input_fails_closed(self):
        cases = (
            {"raw": "{not json"},
            {"raw": "{}"},
            {"raw": "null"},
            {"raw": "[]", "merge_sha": "not-a-sha"},
            {"raw": "[]", "merge_sha": "b" * 39},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                raw = kwargs.pop("raw")
                result, output = run_report(self.tmp, raw=raw, **kwargs)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output, "")

    def test_text_matches_the_vm_script_contract(self):
        result, output = run_report(self.tmp, [pr()])
        self.assertEqual(result.returncode, 0, result.stderr)
        b64 = outputs(output)["text_b64"]
        self.assertRegex(b64, r"^[A-Za-z0-9+/]+={0,2}$")
        text = decoded(output)
        self.assertNotIn("\n", text)
        self.assertNotIn("\r", text)
        self.assertEqual(text, text.strip())
        self.assertTrue(0 < len(text) <= 240)


class WorkflowPolicyTests(unittest.TestCase):
    def test_workflow_is_owner_only_protected_main_and_uses_reviewed_helpers(self):
        text = WF.read_text(encoding="utf-8")
        for required in (
            "push:",
            "branches: [main]",
            "github.actor_id == 9018513",
            "github.triggering_actor == 'azumag'",
            "github.ref == 'refs/heads/main'",
            "github.ref_protected == true",
            "environment: vm-operations",
            "pull-requests: read",
            "control/ops/vm_actions/authorize_pr_merge_audio.py",
            "control/ops/vm_actions/pr_merge_audio_report.py",
            "control/ops/vm_actions/check_operator_parity.py",
            "--allow-newer-production",
            "control/ops/vm_actions/report_pr_merge_audio.sh",
            "GH_TOKEN: ${{ github.token }}",
            "MERGE_SHA: ${{ github.sha }}",
            "TEXT_B64: ${{ steps.report.outputs.text_b64 }}",
            "exec docich production $SHA",
            "persist-credentials: false",
            "timeout-minutes: 8",
            "grep -Fxq 'VM operation rejected: gateway_busy'",
            "for attempt in $(seq 1 18)",
            "sleep $(( attempt <= 6 ? 5 : 15 ))",
        ):
            self.assertIn(required, text)
        for forbidden in ("pull_request_target", "workflow_dispatch", "ssh-keyscan", "inputs."):
            self.assertNotIn(forbidden, text)
        self.assertEqual(text.count("if: steps.report.outputs.found == 'true'"), 3)

    def test_no_shared_concurrency_group_drops_pending_reports(self):
        text = WF.read_text(encoding="utf-8")
        self.assertNotIn("concurrency:", text)
        self.assertIn("Actions keeps only one running plus one pending", text)

    def test_vm_steps_run_only_after_a_merged_pr_is_found(self):
        text = WF.read_text(encoding="utf-8")
        self.assertLess(
            text.index("Find the merged pull request"),
            text.index("Configure pinned SSH client"),
        )
        self.assertLess(
            text.index("Stop when the push has no merged pull request"),
            text.index("Configure pinned SSH client"),
        )
        self.assertNotIn("${{ steps.report.outputs.text", text.split("TEXT_B64:")[0])


if __name__ == "__main__":
    unittest.main()
