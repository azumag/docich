import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
GATEWAY = ROOT / "ops/vm_actions/gateway.py"
spec = importlib.util.spec_from_file_location("github_auth_diagnostics_gateway", GATEWAY)
gw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gw)

SHA = "a" * 40
ROOT_CONFIG = {"repos": {"docich": {"production": "/srv/docich"}}}
PRIVATE_SENTINEL = "must-not-leak-private-field"


def result(state="ok", returncode=0, stdout=b"", stderr=b""):
    return {"state": state, "returncode": returncode, "stdout": stdout, "stderr": stderr}


class GitHubAuthDiagnosticsTests(unittest.TestCase):
    def test_authenticated_api_read_returns_only_account_and_allowlisted_scopes(self):
        api = (b"HTTP/2.0 200 OK\r\nX-OAuth-Scopes: read:user, repo\r\n"
               b"Content-Type: application/json\r\n\r\n"
               b'{"login":"azumag","email":"private@example.invalid","private_field":"' +
               PRIVATE_SENTINEL.encode() + b'","remote":"https://example.invalid/private-remote"}')
        with mock.patch.object(gw, "_github_diagnostics_git_preflight", return_value={"state":"ok","sha":SHA,"clean":True}), \
             mock.patch.object(gw.socket, "gethostname", return_value="docich-prod"), \
             mock.patch.object(gw.pwd, "getpwuid", return_value=mock.Mock(pw_name="ubuntu")), \
             mock.patch.object(gw.os, "geteuid", return_value=1000), \
             mock.patch.object(gw, "_github_api_reachable", return_value={"state":"reachable","http_status":200}), \
             mock.patch.object(gw.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(gw, "_bounded_process", side_effect=[result(), result(stdout=api, stderr=PRIVATE_SENTINEL.encode())]) as run:
            output = gw.github_auth_diagnostics(ROOT_CONFIG, "docich", "production", SHA)
        self.assertEqual(output["gh_auth"], "authenticated")
        self.assertEqual(output["api_read"], "verified")
        self.assertEqual(output["account"], "azumag")
        self.assertEqual(output["scopes"], ["read:user", "repo"])
        self.assertEqual(output["execution_user"], "ubuntu")
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0], ["/usr/bin/gh", "auth", "status", "--hostname", "github.com"])
        self.assertEqual(run.call_args_list[1].args[0], ["/usr/bin/gh", "api", "--include", "--hostname", "github.com", "user"])
        self.assertTrue(all(call.kwargs["env"]["GH_PROMPT_DISABLED"] == "1" for call in run.call_args_list))
        rendered = json.dumps(output)
        for secret in (PRIVATE_SENTINEL, "private@example.invalid", "https://example.invalid/private-remote", "/srv/docich"):
            self.assertNotIn(secret, rendered)

    def test_absent_auth_is_distinguished_from_network_failure(self):
        no_auth = result(returncode=1, stderr=b"You are not logged into any GitHub hosts.")
        with mock.patch.object(gw, "_github_diagnostics_git_preflight", return_value={"state":"ok","sha":SHA,"clean":True}), \
             mock.patch.object(gw, "_github_api_reachable", return_value={"state":"reachable","http_status":200}), \
             mock.patch.object(gw.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(gw, "_bounded_process", return_value=no_auth) as run:
            output = gw.github_auth_diagnostics(ROOT_CONFIG, "docich", "production", SHA)
        self.assertEqual(output["gh_auth"], "not_authenticated")
        self.assertEqual(output["network"], "reachable")
        self.assertEqual(output["api_read"], "not_attempted_no_auth")
        self.assertEqual(run.call_count, 1)
        self.assertNotIn(PRIVATE_SENTINEL, json.dumps(output))

    def test_unknown_auth_with_unavailable_network_is_not_reported_as_no_auth(self):
        transient = result(returncode=1, stderr=b"temporary connection problem: " + PRIVATE_SENTINEL.encode())
        with mock.patch.object(gw, "_github_diagnostics_git_preflight", return_value={"state":"ok","sha":SHA,"clean":True}), \
             mock.patch.object(gw, "_github_api_reachable", return_value={"state":"unavailable","http_status":None}), \
             mock.patch.object(gw.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(gw, "_bounded_process", return_value=transient):
            output = gw.github_auth_diagnostics(ROOT_CONFIG, "docich", "production", SHA)
        self.assertEqual(output["gh_auth"], "unknown_network_failure")
        self.assertEqual(output["network"], "unavailable")
        self.assertEqual(output["api_read"], "not_attempted_network_unavailable")
        self.assertNotIn(PRIVATE_SENTINEL, json.dumps(output))

    def test_missing_cli_and_sha_mismatch_fail_closed(self):
        with mock.patch.object(gw, "_github_diagnostics_git_preflight", return_value={"state":"ok","sha":SHA,"clean":True}), \
             mock.patch.object(gw, "_github_api_reachable", return_value={"state":"reachable","http_status":200}), \
             mock.patch.object(gw.shutil, "which", return_value=None):
            output = gw.github_auth_diagnostics(ROOT_CONFIG, "docich", "production", SHA)
        self.assertFalse(output["gh_available"])
        self.assertEqual(output["gh_auth"], "gh_not_installed")
        self.assertEqual(output["api_read"], "not_attempted_gh_missing")
        for args, message in (( (ROOT_CONFIG,"docich","preview",SHA), "production-only" ),
                              ( (ROOT_CONFIG,"docich","production","b"*40), "SHA mismatch" )):
            with self.subTest(message=message), mock.patch.object(gw, "_github_diagnostics_git_preflight", return_value={"state":"ok","sha":SHA,"clean":True}):
                with self.assertRaisesRegex(ValueError, message):
                    gw.github_auth_diagnostics(*args)

    def test_api_error_and_scopes_are_allowlisted(self):
        raw = (b"HTTP/2.0 403 Forbidden\r\nX-OAuth-Scopes: read:user, unexpected scope with spaces" +
               b"\r\nX-Accepted-OAuth-Scopes: admin:org\r\n\r\n" + PRIVATE_SENTINEL.encode())
        output = gw._github_user_response(raw)
        self.assertEqual(output["http_status"], 403)
        self.assertIsNone(output["account"])
        self.assertEqual(output["scopes"], ["read:user"])
        self.assertEqual(output["scopes_status"], "partially_filtered")
        rendered = json.dumps(output)
        self.assertNotIn("unexpected scope", rendered)
        self.assertNotIn("admin:org", rendered)
        self.assertNotIn(PRIVATE_SENTINEL, rendered)

    def test_bounded_process_enforces_timeout_output_limit_and_argv_only(self):
        oversized = gw._bounded_process([sys.executable, "-c", "import sys; sys.stdout.write('x'*1000000)"],
                                        timeout=2, output_max=128)
        self.assertEqual(oversized["state"], "output_limit")
        slow = gw._bounded_process([sys.executable, "-c", "import time; time.sleep(2)"], timeout=0.05, output_max=128)
        self.assertEqual(slow["state"], "timeout")
        ok = gw._bounded_process([sys.executable, "-c", "print('safe')"], timeout=2, output_max=128)
        self.assertEqual(ok["state"], "ok")
        self.assertEqual(ok["stdout"].strip(), b"safe")

    def test_checkout_preflight_git_calls_are_bounded_and_timeout_is_sanitized(self):
        for phase, outputs in (
            ("rev-parse", [result(state="timeout")]),
            ("status", [result(stdout=SHA.encode()), result(state="timeout")]),
        ):
            with self.subTest(phase=phase), \
                 mock.patch.object(gw.shutil, "which", return_value="/usr/bin/git"), \
                 mock.patch.object(gw, "_bounded_process", side_effect=outputs) as run:
                preflight = gw._github_diagnostics_git_preflight(Path("/srv/docich"))
            self.assertEqual(preflight["state"], "timeout")
            self.assertEqual(run.call_count, 1 if phase == "rev-parse" else 2)
            expected_command = "rev-parse" if phase == "rev-parse" else "status"
            self.assertIn(expected_command, run.call_args_list[-1].args[0])
            for call in run.call_args_list:
                self.assertLessEqual(call.kwargs["timeout"], gw.GH_DIAGNOSTIC_PREFLIGHT_TIMEOUT)
                self.assertEqual(call.kwargs["output_max"], gw.GH_DIAGNOSTIC_OUTPUT_MAX)
                self.assertIsInstance(call.args[0], list)
                self.assertEqual(call.args[0][0], "/usr/bin/git")

        timeout = {"state":"timeout","sha":None,"clean":False}
        with mock.patch.object(gw, "_github_diagnostics_git_preflight", return_value=timeout), \
             mock.patch.object(gw, "_github_api_reachable") as network:
            with self.assertRaisesRegex(ValueError, "preflight timeout") as raised:
                gw.github_auth_diagnostics(ROOT_CONFIG, "docich", "production", SHA)
        self.assertEqual(gw.reason_code(raised.exception), "github_auth_preflight_timeout")
        network.assert_not_called()

    def test_scope_header_missing_is_not_claimed_as_no_permissions(self):
        raw = b'HTTP/2.0 200 OK\r\nContent-Type: application/json\r\n\r\n{"login":"azumag"}'
        output = gw._github_user_response(raw)
        self.assertEqual(output["scopes_status"], "not_reported")
        self.assertEqual(output["scopes"], [])

    def test_operation_is_fixed_gateway_only_and_not_a_public_action_input(self):
        config = {"repos":{"docich":{} }}
        command = f"github_auth_diagnostics docich production {SHA}"
        with mock.patch.dict(gw.os.environ, {"SSH_ORIGINAL_COMMAND":command}):
            self.assertEqual(gw.parse_command(config), ("github_auth_diagnostics","docich","production",SHA))
        for command in (f"github_auth_diagnostics docich production {SHA};id",
                        f"github_auth_diagnostics docich production {SHA} /tmp/anything"):
            with self.subTest(command=command), mock.patch.dict(gw.os.environ, {"SSH_ORIGINAL_COMMAND":command}):
                with self.assertRaisesRegex(ValueError, "invalid forced command"):
                    gw.parse_command(config)
        authorize=(ROOT/"ops/vm_actions/authorize.py").read_text()
        workflow=(ROOT/".github/workflows/vm-operations.yml").read_text()
        self.assertNotIn("github_auth_diagnostics", authorize)
        self.assertNotIn("github_auth_diagnostics", workflow)


if __name__ == "__main__":
    unittest.main()
