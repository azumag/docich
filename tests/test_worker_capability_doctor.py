"""scripts/worker_capability_doctor.py の単体テスト (issue #40)。

doctor 自体が read-only であることが要件なので、テストも実プロセスの起動/kill・
systemd操作を一切行わない。一時ディレクトリへの書き込みのみで検証する。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import worker_capability_doctor as doctor  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_MANIFEST = REPO_ROOT / "docs" / "adr" / "0001-worker-capability-manifest.json"


class TestManifestIsValid(unittest.TestCase):
    """リポジトリに実際に置かれている manifest 自体がスキーマを満たすことを保証する。"""

    def test_real_manifest_loads_and_has_no_schema_problems(self):
        manifest = doctor.load_manifest(REAL_MANIFEST)
        problems = doctor.validate_manifest_schema(manifest)
        self.assertEqual(problems, [])

    def test_real_manifest_has_workers_and_credentials(self):
        manifest = doctor.load_manifest(REAL_MANIFEST)
        workers = doctor.all_worker_entries(manifest)
        self.assertGreaterEqual(len(workers), 15)
        self.assertGreaterEqual(len(manifest.get("credentials", [])), 10)

    def test_every_worker_entrypoint_token_is_extractable(self):
        # entrypoint フィールドから最低1つはファイル候補を抽出できること
        # (自由文だけで実体が全く抜き出せないエントリが紛れ込むのを防ぐ回帰テスト)。
        manifest = doctor.load_manifest(REAL_MANIFEST)
        for w in doctor.all_worker_entries(manifest):
            files = doctor.extract_entrypoint_files(w.get("entrypoint", ""))
            self.assertTrue(files, f"{w.get('name')} の entrypoint からファイル候補を抽出できない")


class TestValidateManifestSchema(unittest.TestCase):
    def test_missing_required_key_is_reported(self):
        manifest = {
            "workers": [{"name": "x"}],  # read_paths等が無い
            "auxiliary_workers": [],
            "credentials": [{"var": "V", "purpose": "p", "provider": "p", "consumers": [], "rotation_owner": "o"}],
        }
        problems = doctor.validate_manifest_schema(manifest)
        self.assertTrue(any("read_paths" in p for p in problems))
        self.assertTrue(any("proposed_identity" in p for p in problems))

    def test_missing_credentials_section_is_reported(self):
        manifest = {"workers": [], "auxiliary_workers": []}
        problems = doctor.validate_manifest_schema(manifest)
        self.assertTrue(any("credentials" in p for p in problems))


class TestExtractEntrypointFiles(unittest.TestCase):
    def test_plain_path(self):
        self.assertEqual(doctor.extract_entrypoint_files("./soren_loop.sh"), ["./soren_loop.sh"])

    def test_path_with_shell_arg(self):
        got = doctor.extract_entrypoint_files("./workers/chat_worker.sh ${TWITCH_CHANNEL:-azumagbanjo}")
        self.assertEqual(got, ["./workers/chat_worker.sh"])

    def test_does_not_falsely_join_two_filenames_separated_by_word(self):
        # "improve_daemon.sh または eloop_improve.sh" のように単語で区切られていれば
        # 誤って 1つの path として結合されない (回帰テスト: 過去に '/' 区切りの
        # 説明文を1つの相対パスと誤認した実バグがあった)。
        got = doctor.extract_entrypoint_files(
            "strategy/sandbox.sh (関数ライブラリ、improve_daemon.sh または eloop_improve.sh からsourceされて呼ばれる)"
        )
        self.assertIn("strategy/sandbox.sh", got)
        self.assertIn("improve_daemon.sh", got)
        self.assertIn("eloop_improve.sh", got)
        self.assertNotIn("improve_daemon.sh/eloop_improve.sh", got)

    def test_multiple_paths_all_extracted(self):
        got = doctor.extract_entrypoint_files(
            "./generate_status_overlay.sh watch 2 / ./generate_show_status_overlay.sh watch 2"
        )
        self.assertEqual(
            got, ["./generate_status_overlay.sh", "./generate_show_status_overlay.sh"]
        )


class TestExpandShellDefault(unittest.TestCase):
    def test_plain_path_passthrough(self):
        self.assertEqual(doctor.expand_shell_default("tmp/state/chat_worker.pid"), "tmp/state/chat_worker.pid")

    def test_shell_default_expanded(self):
        got = doctor.expand_shell_default("${IMPROVE_DAEMON_PID_FILE:-tmp/state/improve_daemon.pid}")
        self.assertEqual(got, "tmp/state/improve_daemon.pid")

    def test_descriptive_japanese_text_returns_none(self):
        self.assertIsNone(doctor.expand_shell_default("なし (専用workerプロセスではない)"))

    def test_empty_returns_none(self):
        self.assertIsNone(doctor.expand_shell_default(""))
        self.assertIsNone(doctor.expand_shell_default(None))


class TestCredentialVarNames(unittest.TestCase):
    def test_plain_and_compound_names_extracted(self):
        manifest = {
            "credentials": [
                {"var": "TWITCH_BOT_TOKEN"},
                {"var": "ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL"},
            ]
        }
        names = doctor.credential_var_names(manifest)
        self.assertEqual(names, {"TWITCH_BOT_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"})

    def test_parenthetical_placeholder_is_excluded(self):
        # "(gcloud ADC、環境変数ではなく...)" のような説明文プレースホルダは
        # 実在の環境変数名として扱わない (ADC/CLI等のノイズトークンを生成しない回帰テスト)。
        manifest = {
            "credentials": [
                {"var": "(gcloud ADC、環境変数ではなくホストログイン状態)"},
                {"var": "(codex CLI認証状態、変数名不明)"},
                {"var": "(RTMP/RTMPSストリームキー、Twitch・Kick ingest)"},
            ]
        }
        names = doctor.credential_var_names(manifest)
        self.assertEqual(names, set())


class TestReadEnvKeysOnly(unittest.TestCase):
    """.env の値を絶対に返さない・保持しないことを検証する (最重要の安全要件)。"""

    def test_returns_keys_only_never_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            secret_value = "sk-super-secret-do-not-leak-9f8e7d"
            env_path.write_text(
                "\n".join(
                    [
                        "# comment line",
                        "",
                        f"TWITCH_BOT_TOKEN={secret_value}",
                        "export YOUTUBE_API_KEY=AIzaAnotherSecretValue",
                        "MALFORMED_LINE_NO_EQUALS",
                    ]
                ),
                encoding="utf-8",
            )
            keys = doctor.read_env_keys_only(env_path)
            self.assertEqual(keys, {"TWITCH_BOT_TOKEN", "YOUTUBE_API_KEY"})
            # 値そのもの・断片が戻り値のどこにも含まれないことを確認する。
            for key in keys:
                self.assertNotIn(secret_value, key)
            self.assertNotIn(secret_value, json.dumps(sorted(keys)))

    def test_missing_file_returns_empty_set(self):
        self.assertEqual(doctor.read_env_keys_only(Path("/nonexistent/.env")), set())


class TestResolveSovietNowRoot(unittest.TestCase):
    def test_override_must_contain_eloop_lib(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # eloop_lib.sh が無ければ None (誤検出しない)
            self.assertIsNone(doctor.resolve_soviet_now_root(str(root)))
            (root / "eloop_lib.sh").write_text("# stub\n", encoding="utf-8")
            resolved = doctor.resolve_soviet_now_root(str(root))
            self.assertEqual(resolved, root.resolve())

    def test_no_override_and_no_env_falls_back_to_repo_layout_or_none(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            # 環境変数を空にした状態でも例外を出さない (games/soviet_now が
            # チェックアウトされていなくても None を返すだけ)。
            result = doctor.resolve_soviet_now_root(None)
            self.assertTrue(result is None or (result / "eloop_lib.sh").is_file())


class TestSystemdAvailable(unittest.TestCase):
    def test_false_when_systemctl_missing(self):
        with mock.patch("worker_capability_doctor.shutil.which", return_value=None):
            self.assertFalse(doctor.systemd_available())

    def test_false_on_non_linux_even_if_systemctl_present(self):
        with mock.patch("worker_capability_doctor.platform.system", return_value="Darwin"):
            with mock.patch("worker_capability_doctor.shutil.which", return_value="/usr/bin/systemctl"):
                self.assertFalse(doctor.systemd_available())


class TestBuildReportNeverCrashes(unittest.TestCase):
    """doctor の中核要件: どんな環境でも例外を出さず read-only に完走すること。"""

    def test_build_report_without_soviet_now_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_root = Path(tmp) / "does_not_exist"
            report = doctor.build_report(REAL_MANIFEST, str(fake_root))
            self.assertIsNone(report["soviet_now_root"])
            self.assertEqual(report["schema_problems"], [])
            self.assertIn("systemd", report)

    def test_build_report_with_fake_soviet_now_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "eloop_lib.sh").write_text("# stub\n", encoding="utf-8")
            # manifest記載のentrypointを1つも置かないので missing_entrypoints が出るはず。
            report = doctor.build_report(REAL_MANIFEST, str(root))
            self.assertEqual(report["soviet_now_root"], str(root.resolve()))
            self.assertTrue(len(report["missing_entrypoints"]) > 0)
            # .env が無い場合のメッセージ
            self.assertIn(".env が見つからない", report["env_credential_gap"]["note"])

    def test_fatal_error_on_missing_manifest_does_not_raise(self):
        report = doctor.build_report(Path("/nonexistent/manifest.json"), None)
        self.assertIn("fatal_error", report)

    def test_format_report_text_handles_fatal_error(self):
        report = {"manifest_path": "x", "fatal_error": "boom"}
        text = doctor.format_report_text(report)
        self.assertIn("FATAL", text)

    def test_main_returns_zero_on_success(self):
        rc = doctor.main(["--manifest", str(REAL_MANIFEST)])
        self.assertEqual(rc, 0)

    def test_main_returns_nonzero_on_fatal_error(self):
        rc = doctor.main(["--manifest", "/nonexistent/manifest.json"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
