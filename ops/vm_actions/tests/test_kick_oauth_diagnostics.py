"""Read-only, secret-free projection of the fixed Kick OAuth status record."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from temp_roots import real_tempdir

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "oauth_diagnostics", ROOT / "ops/vm_actions/collect_diagnostics.py")
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)
NOW = 1792562400  # 2026-10-21T06:00:00Z


class KickOAuthDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = real_tempdir()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "kick_oauth").mkdir(mode=0o700)
        self.path = self.root / "kick_oauth/status.json"
        patch = mock.patch.object(collector, "_program_state_dir", return_value=self.root)
        patch.start()
        self.addCleanup(patch.stop)

    def write(self, **changes):
        data = {"status": "ok", "action": "no_change",
                "checked_at": "2026-10-21T05:59:00+00:00",
                "expires_at": "2026-10-21T07:00:00+00:00",
                "broadcaster_user_id": "126249739", "missing_scopes": []}
        data.update(changes)
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.path.chmod(0o600)

    def collect(self):
        return collector._collect_kick_oauth(NOW)

    def test_normal_record_and_no_side_effects(self):
        self.write(access_token="PRIVATE", secret="PRIVATE")
        before = self.path.read_bytes()
        with mock.patch.object(collector.subprocess, "run", side_effect=AssertionError), \
                mock.patch.object(collector.urllib.request, "urlopen", side_effect=AssertionError):
            value = self.collect()
        self.assertEqual(value["status"], "ok")
        self.assertEqual(value["age_sec"], 60)
        self.assertFalse(value["checked_at_future"])
        self.assertTrue(value["missing_scopes_known"])
        self.assertEqual(value["broadcaster_user_id"], "126249739")
        self.assertNotIn("PRIVATE", json.dumps(value))
        self.assertEqual(self.path.read_bytes(), before)

    def test_producer_statuses_are_projected(self):
        for status in collector.KICK_OAUTH_STATUSES:
            with self.subTest(status=status):
                self.write(status=status, action=None, expires_at=None)
                value = self.collect()
                self.assertEqual(value["status"], status)
                self.assertIsNone(value["expires_at"])
                self.assertNotIn("expired", value)

    def test_unknown_fields_do_not_escape(self):
        for value in ("PRIVATE", [], {}, 1, True, None):
            with self.subTest(value=value):
                self.write(status=value, action=value, checked_at=value,
                           expires_at=value, missing_scopes=[value],
                           broadcaster_user_id=value)
                result = self.collect()
                self.assertEqual(result["status"], "unknown")
                self.assertIsNone(result["action"])
                self.assertIsNone(result["checked_at"])
                self.assertIsNone(result["expires_at"])
                self.assertFalse(result["missing_scopes_known"])
                self.assertNotIn("broadcaster_user_id", result)
                self.assertNotIn("PRIVATE", json.dumps(result))

    def test_broadcaster_id_shape_is_enforced(self):
        for bad in ("0", "-1", "abc", "1" * 20, "12x", "", 126249739):
            with self.subTest(bad=bad):
                self.write(broadcaster_user_id=bad)
                self.assertNotIn("broadcaster_user_id", self.collect())
        self.write(broadcaster_user_id="1")
        self.assertEqual(self.collect()["broadcaster_user_id"], "1")

    def test_missing_record_is_not_healthy(self):
        value = self.collect()
        self.assertFalse(value["present"])
        self.assertFalse(value["readable"])
        self.assertEqual(value["status"], "unknown")

    def test_invalid_records(self):
        for raw in (b"{", b"[]", b"null", b"\xff", b"[" * 2000 + b"]" * 2000,
                    b"{}" + b" " * 4095):
            with self.subTest(raw=raw[:10]):
                self.path.write_bytes(raw)
                value = self.collect()
                self.assertTrue(value["present"])
                self.assertFalse(value["readable"])
                self.assertEqual(value["status"], "unknown")

    def test_exact_size_boundary(self):
        self.path.write_bytes(b"{}" + b" " * 4094)
        self.assertTrue(self.collect()["readable"])
        self.path.write_bytes(b"{}" + b" " * 4095)
        self.assertFalse(self.collect()["readable"])

    def test_leaf_symlink_is_not_followed(self):
        target = self.root / "unrelated"
        target.write_text('{"status":"ok"}')
        self.path.symlink_to(target)
        self.assertFalse(self.collect()["readable"])

    def test_parent_symlink_is_not_followed(self):
        self.path.parent.rmdir()
        target = self.root / "unrelated"
        target.mkdir()
        (target / "status.json").write_text('{"status":"ok"}')
        self.path.parent.symlink_to(target, target_is_directory=True)
        self.assertFalse(self.collect()["readable"])

    def test_nonregular_and_hardlinked_records(self):
        self.path.mkdir()
        self.assertFalse(self.collect()["readable"])
        self.path.rmdir()
        os.mkfifo(self.path)
        self.assertFalse(self.collect()["readable"])
        self.path.unlink()
        self.write()
        os.link(self.path, self.root / "alias")
        self.assertFalse(self.collect()["readable"])

    def test_writable_by_others_rejected(self):
        self.write()
        self.path.chmod(0o666)
        self.assertFalse(self.collect()["readable"])
        self.path.chmod(0o600)
        self.path.parent.chmod(0o777)
        self.assertFalse(self.collect()["readable"])

    def test_timezone_and_invalid_calendar(self):
        for stamp in ("2026-10-21T05:59:00", "2026-10-21T05:59:00+09:00",
                      "2026-02-30T05:59:00Z", "PRIVATE"):
            with self.subTest(stamp=stamp):
                self.write(checked_at=stamp, expires_at=stamp)
                value = self.collect()
                self.assertIsNone(value["checked_at"])
                self.assertIsNone(value["expires_at"])
                self.assertIsNone(value["age_sec"])

    def test_future_and_old_observations_are_not_current_health(self):
        self.write(checked_at="2026-10-21T06:00:01Z")
        value = self.collect()
        self.assertTrue(value["checked_at_future"])
        self.assertIsNone(value["age_sec"])
        self.write(checked_at="1970-01-01T00:00:00Z", expires_at=None)
        self.assertEqual(self.collect()["age_sec"], NOW)
        self.write(checked_at="0001-01-01T00:00:00Z")
        self.assertEqual(self.collect()["age_sec"], 2**31 - 1)

    def test_scope_list_is_bounded_and_all_or_unknown(self):
        self.write(missing_scopes=["channel:write", "channel:read"])
        self.assertEqual(self.collect()["missing_scopes"], ["channel:read", "channel:write"])
        for scopes in (["channel:read", "PRIVATE"], "channel:read",
                       ["channel:read", "channel:write", "channel:read"], None):
            self.write(missing_scopes=scopes)
            value = self.collect()
            self.assertFalse(value["missing_scopes_known"])
            self.assertEqual(value["missing_scopes"], [])

    def test_atomic_replacement_during_read_is_unknown(self):
        self.write()
        original = collector.os.stat
        def replaced(path, *args, **kwargs):
            if path == "status.json" and kwargs.get("dir_fd") is not None:
                replacement = self.path.with_suffix(".new")
                replacement.write_text('{"status":"reauth_required"}')
                replacement.replace(self.path)
            return original(path, *args, **kwargs)
        with mock.patch.object(collector.os, "stat", side_effect=replaced):
            self.assertFalse(self.collect()["readable"])

    def test_scope_allowlist_tracks_producer_without_importing_it(self):
        import ast
        source = (ROOT / "src/docich/kick_oauth.py").read_text()
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "REQUIRED_SCOPES" for t in n.targets))
        self.assertEqual(collector.KICK_OAUTH_SCOPES, frozenset(ast.literal_eval(node.value)))

    def test_status_allowlist_tracks_producer_error_codes(self):
        import ast
        source = (ROOT / "src/docich/kick_oauth.py").read_text()
        tree = ast.parse(source)
        codes = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "KickOAuthError" and node.args \
                    and isinstance(node.args[0], ast.Constant):
                codes.add(str(node.args[0].value))
        codes -= {"env_unreadable", "token_format_rejected"}
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                for value in node.values:
                    if isinstance(value, ast.Constant) and value.value == "new_token_":
                        codes |= {"new_token_not_broadcaster", "new_token_missing_scopes",
                                  "new_token_token_invalid", "new_token_token_inactive",
                                  "new_token_not_user_token", "new_token_principal_unreadable"}
        for code in codes | {"ok", "not_configured"}:
            self.assertIn(code, collector.KICK_OAUTH_STATUSES)

    def test_main_wires_fixed_projection(self):
        import ast
        tree = ast.parse((ROOT / "ops/vm_actions/collect_diagnostics.py").read_text())
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        calls = [n for n in ast.walk(main) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "_collect_kick_oauth"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(ast.unparse(calls[0]), "_collect_kick_oauth(now)")


if __name__ == "__main__":
    unittest.main()
