import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "run_soren91_daily_improve.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "soren91-daily-improve.yml"
CLASSIFIER = ROOT / "ops" / "vm_actions" / "classify_soren91_daily_gateway.py"


def load_classifier():
    spec = importlib.util.spec_from_file_location("soren91_daily_gateway", CLASSIFIER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gateway_result(exit_code, sha="a" * 40, **overrides):
    data = {
        "status": "executed",
        "sha": sha,
        "exit_code": exit_code,
        "output": "withheld",
        "operation_id": "b" * 32,
    }
    data.update(overrides)
    return json.dumps(data, separators=(",", ":"))


class Soren91DailyImproveOpsTests(unittest.TestCase):
    def test_runner_uses_fixed_live_and_persist_paths(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("runtime=/home/ubuntu/soren/soren91", text)
        self.assertIn("persist=/home/ubuntu/soren-persist", text)
        self.assertIn('runner="$runtime/daily_runtime_improve.mjs"', text)
        self.assertIn('state="$runtime/tmp/state/improve_daily.json"', text)
        self.assertNotIn("eval ", text)
        self.assertNotIn("$@", text)

    def test_runner_uses_gateway_username_not_host_specific_numeric_uid(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('[[ "$(id -un)" == "ubuntu" ]]', text)
        self.assertNotIn('[[ "$(id -u)" -eq 1000 ]]', text)
        self.assertIn("export HOME=/home/ubuntu", text)
        self.assertIn("export PATH=/usr/local/bin:/usr/bin:/bin:/snap/bin", text)

    def test_runner_uses_one_reviewed_fast_model_for_daily_job(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("export AI_COMMON_AGENTS=opencode-go:deepseek-v4.1-flash", text)
        self.assertIn("export SOREN91_IMPROVE_OPENCODE_AGENT=opencode-go:deepseek-v4.1-flash", text)
        self.assertIn("export SOREN91_IMPROVE_OPENCODE_TIMEOUT=90", text)
        self.assertNotIn("muse-spark-1.3-contributor-free", text)

    def test_runner_isolates_daily_opencode_with_proven_agent_schema(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("export OPENCODE_DISABLE_CLAUDE_CODE=true", text)
        self.assertIn("export OPENCODE_DISABLE_PROJECT_CONFIG=true", text)
        self.assertIn("export OPENCODE_CONFIG_CONTENT=", text)
        self.assertIn('"permission":{"*":"deny"}', text)
        self.assertIn(
            '"agent":{"soren-daily-improve":{"mode":"primary","permission":{"*":"deny"},"steps":1}}',
            text,
        )
        self.assertIn('"share":"disabled"', text)
        self.assertIn('"instructions":[]', text)
        self.assertNotIn('"tools":', text)

    def test_runner_uses_fixed_opencode_binary_and_explicit_agent_shim(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('[[ -x /snap/bin/opencode ]]', text)
        self.assertIn('opencode_shim_dir="$(mktemp -d /home/ubuntu/.soren91-opencode-shim.XXXXXX)"', text)
        self.assertIn('[[ "$#" -eq 5 ]]', text)
        self.assertIn('[[ "$1" == "run" ]]', text)
        self.assertIn('[[ "$2" == "--format" && "$3" == "json" ]]', text)
        self.assertIn('[[ "$4" == "--model" ]]', text)
        self.assertIn('[[ "$5" =~ ^[A-Za-z0-9_./:-]{1,160}$ ]]', text)
        self.assertIn(
            'exec /snap/bin/opencode run --format json --agent soren-daily-improve --model "$5"',
            text,
        )
        self.assertIn('export PATH="$opencode_shim_dir:/usr/local/bin:/usr/bin:/bin:/snap/bin"', text)
        self.assertIn('rm -rf "$opencode_shim_dir"', text)
        self.assertNotIn('exec /snap/bin/opencode "$', text)

    def test_runner_serializes_with_existing_persist_lock(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('lock="$persist/.git/persist.lock"', text)
        self.assertIn('flock -w 30 9', text)
        self.assertIn('node "$runner"', text)

    def test_runner_bootstraps_only_missing_state_from_earliest_retained_game(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("if os.path.lexists(state):", text)
        self.assertIn("raise SystemExit(85)", text)
        self.assertIn("raise SystemExit(86)", text)
        self.assertIn("baseline = max(0, min(games) - 1)", text)
        self.assertIn("'pendingPr': None", text)
        self.assertIn("os.replace(tmp, state)", text)
        self.assertIn("os.chmod(state, 0o600)", text)

    def test_runner_preflights_without_model_before_real_run(self):
        text = SCRIPT.read_text(encoding="utf-8")
        dry = text.index("run_daily preflight")
        real = text.index("run_daily run", dry)
        self.assertLess(dry, real)
        self.assertIn("--dry-run", text)
        self.assertIn("classify_private_output 98", text)
        self.assertIn(': >"$out"', text)
        self.assertIn("classify_private_output 99", text)

    def test_runner_runs_private_bounded_opencode_smoke_before_full_prompt(self):
        text = SCRIPT.read_text(encoding="utf-8")
        smoke = text.index("Return exactly OK.")
        real = text.index("run_daily run", smoke)
        self.assertLess(smoke, real)
        self.assertIn('smoke_out="$(mktemp /home/ubuntu/.soren91-opencode-smoke.XXXXXX)"', text)
        self.assertIn('/usr/bin/timeout --kill-after=5s 20s', text)
        self.assertIn('"$opencode_shim_dir/opencode" run --format json --model opencode-go/deepseek-v4.1-flash', text)
        self.assertIn('>"$smoke_out" 2>&1', text)
        self.assertIn('rm -f "$out" "$smoke_out"', text)
        self.assertNotIn('cat "$smoke_out"', text)
        for exit_code in range(107, 114):
            self.assertIn(f"return {exit_code}", text)

    def test_runner_keeps_raw_failure_output_private_and_maps_categories(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("umask 077", text)
        self.assertIn('out="$(mktemp /home/ubuntu/.soren91-daily.XXXXXX)"', text)
        self.assertIn('>"$out" 2>&1', text)
        self.assertIn("grep -Fq 'candidate_invalid:'", text)
        self.assertIn("failure_rc=94", text)
        self.assertIn("grep -Fq 'model_no_candidate'", text)
        self.assertIn("failure_rc=93", text)
        self.assertIn("evidence_file_too_large:", text)
        self.assertIn("failure_rc=95", text)
        self.assertNotIn('cat "$out"', text)

    def test_runner_prechecks_model_cli_and_maps_only_fixed_model_failure_categories(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("[[ -x /snap/bin/opencode ]]", text)
        self.assertIn("exit 87", text)
        for exit_code in range(100, 107):
            self.assertIn(f"failure_rc={exit_code}", text)
        self.assertNotIn('printf "%s" "$(cat "$out")"', text)
        self.assertNotIn('echo "$(cat "$out")"', text)

    def test_gateway_classifier_maps_only_fixed_categories(self):
        classifier = load_classifier()
        sha = "a" * 40
        self.assertEqual(classifier.classify_gateway_result(gateway_result(0), sha, 0), "success")
        self.assertEqual(classifier.classify_gateway_result(gateway_result(85), sha, 85), "state_invalid")
        self.assertEqual(classifier.classify_gateway_result(gateway_result(92), sha, 92), "evidence_blocked")
        self.assertEqual(classifier.classify_gateway_result(gateway_result(94), sha, 94), "candidate_invalid")
        self.assertEqual(classifier.classify_gateway_result(gateway_result(98), sha, 98), "preflight_other")
        self.assertEqual(classifier.classify_gateway_result(gateway_result(99), sha, 99), "runtime_other")
        self.assertEqual(classifier.classify_gateway_result(gateway_result(87), sha, 87), "opencode_missing")
        self.assertEqual(classifier.classify_gateway_result(gateway_result(100), sha, 100), "opencode_provider_failure")
        self.assertEqual(classifier.classify_gateway_result(gateway_result(101), sha, 101), "opencode_output_invalid")
        self.assertEqual(classifier.classify_gateway_result(gateway_result(102), sha, 102), "opencode_cli_failure")
        self.assertEqual(classifier.classify_gateway_result(gateway_result(103), sha, 103), "opencode_failure_other")
        self.assertEqual(classifier.classify_gateway_result(gateway_result(104), sha, 104), "legacy_cli_missing")
        self.assertEqual(classifier.classify_gateway_result(gateway_result(105), sha, 105), "opencode_json_invalid")
        self.assertEqual(classifier.classify_gateway_result(gateway_result(106), sha, 106), "opencode_tool_or_error_event")
        self.assertEqual(classifier.classify_gateway_result(gateway_result(107), sha, 107), "opencode_smoke_model_unavailable")
        self.assertEqual(classifier.classify_gateway_result(gateway_result(108), sha, 108), "opencode_smoke_agent_unavailable")
        self.assertEqual(classifier.classify_gateway_result(gateway_result(109), sha, 109), "opencode_smoke_config_invalid")
        self.assertEqual(classifier.classify_gateway_result(gateway_result(110), sha, 110), "opencode_smoke_auth_failure")
        self.assertEqual(classifier.classify_gateway_result(gateway_result(111), sha, 111), "opencode_smoke_provider_limit")
        self.assertEqual(classifier.classify_gateway_result(gateway_result(112), sha, 112), "opencode_smoke_cli_other")
        self.assertEqual(classifier.classify_gateway_result(gateway_result(113), sha, 113), "opencode_smoke_timeout")
        self.assertEqual(classifier.classify_gateway_result(gateway_result(1), sha, 1), "other")

    def test_gateway_classifier_fails_closed_on_shape_or_transport_mismatch(self):
        classifier = load_classifier()
        sha = "a" * 40
        extra = json.loads(gateway_result(92))
        extra["detail"] = "must never be surfaced"
        self.assertEqual(
            classifier.classify_gateway_result(json.dumps(extra), sha, 92),
            "gateway_response_invalid",
        )
        self.assertEqual(
            classifier.classify_gateway_result(gateway_result(92), sha, 1),
            "gateway_exit_mismatch",
        )
        self.assertEqual(
            classifier.classify_gateway_result(gateway_result(92, sha="c" * 40), sha, 92),
            "gateway_response_invalid",
        )

    def test_workflow_runs_after_corner_with_owner_only_gateway(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("cron: '45 9 * * *'", text)  # 18:45 JST
        self.assertIn("environment: vm-operations", text)
        self.assertIn("github.repository_owner_id == '9018513'", text)
        self.assertIn('"exec docich production $SHA"', text)
        self.assertIn("run_soren91_daily_improve.sh", text)
        self.assertIn("classify_soren91_daily_gateway.py", text)
        self.assertIn('gateway_json="$(cat ops/vm_actions/run_soren91_daily_improve.sh', text)
        self.assertIn('echo "Soren91 daily improvement: result=${category}"', text)
        self.assertNotIn('"exec docich production $SHA" >/dev/null', text)
        self.assertNotIn('echo "$gateway_json"', text)
        self.assertIn("group: soren91-daily-improve-${{ github.repository }}", text)
        self.assertNotIn("group: vm-operations-${{ github.repository }}", text)


if __name__ == "__main__":
    unittest.main()
