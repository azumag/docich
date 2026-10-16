from __future__ import annotations

import importlib.util
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "configure_jev_route.py"
CLASSIFIER_SCRIPT = ROOT / "ops" / "vm_actions" / "configure_comment_classifier_jev.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


route = load_module(SCRIPT, "configure_jev_route")
classifier = load_module(CLASSIFIER_SCRIPT, "configure_comment_classifier_jev_for_route_tests")


class GatewayStdinExecutionTests(unittest.TestCase):
    def test_reviewed_stdin_execution_can_load_classifier_sibling(self):
        result = subprocess.run(
            [sys.executable, "-", "--help"],
            input=SCRIPT.read_bytes(),
            cwd=ROOT,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        self.assertIn(b"--route", result.stdout)
        self.assertIn(b"--disable", result.stdout)


class OwnershipBoundaryTests(unittest.TestCase):
    def test_managed_keys_never_overlap_with_678s_own_script(self):
        # The two owner-only scripts must never fight over the same key.
        self.assertEqual(set(route.MANAGED_KEYS + route.RETIRED_KEYS) & set(classifier.MANAGED_KEYS), set())

    def test_managed_keys_are_exactly_the_route_keys_the_classifier_reads(self):
        self.assertEqual(set(route.MANAGED_KEYS), {"DOCICH_JEV_ROUTE", "DOCICH_JEV_VERCEL_API_KEY"})
        self.assertEqual(route.RETIRED_KEYS, ("DOCICH_SEMANTIC_BACKEND",))


class ConfigureDirectEnvTests(unittest.TestCase):
    def test_sets_route_without_touching_678_keys(self):
        with tempfile.TemporaryDirectory(prefix="jev-route-") as raw:
            env_file = Path(raw) / ".env"
            env_file.write_text(
                "KEEP=value\n"
                "COMMENT_CLASSIFIER_BACKEND=jev\n"
                "TYPESAFE_API_KEY=already-configured\n",
                encoding="utf-8",
            )
            env_file.chmod(0o600)
            backup = route.configure_direct_env(env_file)

            current = env_file.read_text(encoding="utf-8")
            self.assertIn("KEEP=value\n", current)
            self.assertIn("COMMENT_CLASSIFIER_BACKEND=jev\n", current)
            self.assertIn("TYPESAFE_API_KEY=already-configured\n", current)
            self.assertNotIn("DOCICH_SEMANTIC_BACKEND", current)
            self.assertIn("DOCICH_JEV_ROUTE=direct\n", current)
            self.assertNotIn("DOCICH_JEV_VERCEL_API_KEY", current)
            self.assertEqual(stat.S_IMODE(env_file.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)

    def test_replaces_its_own_prior_assignment_and_drops_the_retired_switch(self):
        with tempfile.TemporaryDirectory(prefix="jev-route-") as raw:
            env_file = Path(raw) / ".env"
            env_file.write_text(
                "DOCICH_SEMANTIC_BACKEND=jev\n"
                "DOCICH_JEV_ROUTE=vercel\n"
                "DOCICH_JEV_VERCEL_API_KEY=old-vercel-key\n",
                encoding="utf-8",
            )
            route.configure_direct_env(env_file)
            current = env_file.read_text(encoding="utf-8")
            self.assertEqual(current, "DOCICH_JEV_ROUTE=direct\n")
            self.assertNotIn("old-vercel-key", current)


class ConfigureVercelEnvTests(unittest.TestCase):
    def test_vercel_op_keeps_direct_primary_and_adds_vercel_as_fallback(self):
        with tempfile.TemporaryDirectory(prefix="jev-route-") as raw:
            env_file = Path(raw) / ".env"
            env_file.write_text("KEEP=value\n", encoding="utf-8")
            route.configure_vercel_env(env_file, "TEST_VERCEL_KEY_123")
            current = env_file.read_text(encoding="utf-8")
            self.assertNotIn("DOCICH_SEMANTIC_BACKEND", current)
            self.assertIn("DOCICH_JEV_ROUTE=direct,vercel\n", current)
            self.assertIn("DOCICH_JEV_VERCEL_API_KEY=TEST_VERCEL_KEY_123\n", current)

    def test_quotes_shell_punctuation_in_key(self):
        with tempfile.TemporaryDirectory(prefix="jev-route-") as raw:
            env_file = Path(raw) / ".env"
            env_file.write_text("KEEP=value\n", encoding="utf-8")
            key = "value;$(true)'quoted'"
            route.configure_vercel_env(env_file, key)
            result = subprocess.run(
                ["bash", "-c", "set -a; . \"$1\"; printf %s \"$DOCICH_JEV_VERCEL_API_KEY\"", "bash", str(env_file)],
                check=True, capture_output=True, text=True,
            )
            self.assertEqual(result.stdout, key)

    def test_rejects_invalid_key_without_touching_env(self):
        with tempfile.TemporaryDirectory(prefix="jev-route-") as raw:
            env_file = Path(raw) / ".env"
            original = "KEEP=value\n"
            env_file.write_text(original, encoding="utf-8")
            for bad_key in ("", "bad key", "bad\nkey", "x" * 4097, "é"):
                with self.subTest(bad_key=bad_key), self.assertRaises(route.ConfigureError):
                    route.configure_vercel_env(env_file, bad_key)
                self.assertEqual(env_file.read_text(encoding="utf-8"), original)


class DisableEnvTests(unittest.TestCase):
    def test_disable_returns_to_default_route_and_removes_vercel_key(self):
        with tempfile.TemporaryDirectory(prefix="jev-route-") as raw:
            env_file = Path(raw) / ".env"
            env_file.write_text(
                "KEEP=value\n"
                "COMMENT_CLASSIFIER_BACKEND=jev\n"
                "TYPESAFE_API_KEY=untouched\n"
                "DOCICH_SEMANTIC_BACKEND=jev\n"
                "DOCICH_JEV_ROUTE=vercel\n"
                "DOCICH_JEV_VERCEL_API_KEY=SECRET_TO_REMOVE\n",
                encoding="utf-8",
            )
            backup = route.disable_env(env_file)
            current = env_file.read_text(encoding="utf-8")
            self.assertEqual(current,
                             "KEEP=value\n"
                             "COMMENT_CLASSIFIER_BACKEND=jev\n"
                             "TYPESAFE_API_KEY=untouched\n")
            self.assertNotIn("SECRET_TO_REMOVE", current)
            self.assertIn("SECRET_TO_REMOVE", backup.read_text(encoding="utf-8"))

    def test_default_direct_preflight_requires_typesafe_key_only_when_jev_is_live(self):
        route._require_default_direct_route_ready({})
        route._require_default_direct_route_ready({"COMMENT_CLASSIFIER_BACKEND": "heuristic"})
        route._require_default_direct_route_ready({
            "COMMENT_CLASSIFIER_BACKEND": "jev",
            "TYPESAFE_API_KEY": "present",
        })
        with self.assertRaisesRegex(
            route.ConfigureError,
            "direct_route_requires_existing_typesafe_api_key",
        ):
            route._require_default_direct_route_ready({"COMMENT_CLASSIFIER_BACKEND": "jev"})


class VerifyJevRouteTests(unittest.TestCase):
    def test_default_route_rejects_a_worker_still_pinned_or_holding_the_vercel_key(self):
        verify = route.verify_jev_route(None)
        verify({})  # Jev disabled: no direct credential is required.
        verify({"DOCICH_JEV_ROUTE": ""})  # no error
        verify({"DOCICH_SEMANTIC_BACKEND": "jev"})  # retired, ignored
        verify({"COMMENT_CLASSIFIER_BACKEND": "jev", "TYPESAFE_API_KEY": "present"})
        with self.assertRaises(route.ConfigureError):
            verify({"DOCICH_JEV_ROUTE": "vercel"})
        with self.assertRaises(route.ConfigureError):
            verify({"DOCICH_JEV_VERCEL_API_KEY": "present"})
        with self.assertRaisesRegex(
            route.ConfigureError,
            "direct_route_requires_existing_typesafe_api_key",
        ):
            verify({"COMMENT_CLASSIFIER_BACKEND": "jev"})

    def test_direct_requires_route_and_typesafe_key(self):
        verify = route.verify_jev_route("direct")
        verify({"DOCICH_JEV_ROUTE": "direct", "TYPESAFE_API_KEY": "present"})
        with self.assertRaises(route.ConfigureError):
            verify({"DOCICH_JEV_ROUTE": "vercel", "TYPESAFE_API_KEY": "present"})
        with self.assertRaises(route.ConfigureError):
            verify({"DOCICH_JEV_ROUTE": "direct"})

    def test_vercel_op_verifies_the_live_chain_and_its_own_key(self):
        verify = route.verify_jev_route("vercel")
        verify({"DOCICH_JEV_ROUTE": "direct,vercel", "DOCICH_JEV_VERCEL_API_KEY": "present"})
        with self.assertRaises(route.ConfigureError):
            verify({"DOCICH_JEV_ROUTE": "direct,vercel", "TYPESAFE_API_KEY": "present"})
        for stale in ("vercel", "direct", "vercel,direct"):
            with self.subTest(stale=stale), self.assertRaises(route.ConfigureError):
                verify({"DOCICH_JEV_ROUTE": stale, "DOCICH_JEV_VERCEL_API_KEY": "present"})

    def test_every_written_route_value_is_one_the_classifier_accepts(self):
        sys.path.insert(0, str(ROOT / "src"))
        from docich.semantic_decision.routes import parse_route_chain
        self.assertEqual(parse_route_chain(route.ROUTE_VALUES["direct"]), ("direct",))
        self.assertEqual(parse_route_chain(route.ROUTE_VALUES["vercel"]), ("direct", "vercel"))
        self.assertEqual(parse_route_chain(route.ROUTE_VALUES["clef"]), ("clef",))


class ReuseExistingCloudflareCredentialsTests(unittest.TestCase):
    """#2301: first-time Clef provisioning from this VM's own issued pair."""

    ACCOUNT = "0123456789abcdef0123456789abcdef"
    OTHER_ACCOUNT = "fedcba9876543210fedcba9876543210"

    def test_every_source_is_one_provider_pair_and_never_crosses_accounts(self):
        # A token must never be combined with another source's account: each
        # entry is one provider's own (account, token) pair. An already
        # provisioned Clef pair is first, then this VM's Workers AI capable
        # provider pairs.
        self.assertEqual(route.CLEF_REUSE_SOURCES, (
            route.CLEF_KEYS,
            ("DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"),
            ("DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_ACCOUNT_ID",
             "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_API_TOKEN"),
        ))
        # The other providers' keys are read only: they must never be managed
        # (rewritten or removed) by this script.
        other = {name for pair in route.CLEF_REUSE_SOURCES[1:] for name in pair}
        self.assertEqual(set(route.MANAGED_KEYS + route.CLEF_KEYS) & other, set())
        # Only the reviewed names (and their _FILE forms) are ever read.
        self.assertEqual(set(route.CLEF_REUSE_NAMES), {
            "DOCICH_JEV_CLOUDFLARE_ACCOUNT_ID", "DOCICH_JEV_CLOUDFLARE_ACCOUNT_ID_FILE",
            "DOCICH_JEV_CLOUDFLARE_API_TOKEN", "DOCICH_JEV_CLOUDFLARE_API_TOKEN_FILE",
            "DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID", "DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID_FILE",
            "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_API_TOKEN_FILE",
            "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_ACCOUNT_ID",
            "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_ACCOUNT_ID_FILE",
            "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_API_TOKEN",
            "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_API_TOKEN_FILE",
        })

    def test_dotenv_pair_is_used_when_the_live_worker_does_not_have_it_yet(self):
        # Path A (#2301): the owner installs the pair in soren's .env, so no
        # secret has to transit GitHub, and the running worker (started before
        # the edit) cannot be the only source.
        with tempfile.TemporaryDirectory(prefix="clef-dotenv-") as raw:
            env_file = Path(raw) / ".env"
            env_file.write_text(
                "KEEP=value\n"
                "TYPESAFE_API_KEY=untouched\n"
                f"DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID={self.ACCOUNT}\n"
                "CLOUDFLARE_API_TOKEN=SYNTHETIC_WORKERS_AI_TOKEN\n",
                encoding="utf-8",
            )
            dotenv = route._read_dotenv_values(env_file)
        self.assertEqual(dotenv, {"DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID": self.ACCOUNT,
                                  "CLOUDFLARE_API_TOKEN": "SYNTHETIC_WORKERS_AI_TOKEN"})
        # The live worker environ has neither, yet the pair is found.
        self.assertEqual(route.clef_credentials_from_existing({}, dotenv),
                         (self.ACCOUNT, "SYNTHETIC_WORKERS_AI_TOKEN"))

    def test_dotenv_wins_over_the_live_worker_for_the_same_pair(self):
        dotenv = {"DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID": self.ACCOUNT,
                  "CLOUDFLARE_API_TOKEN": "SYNTHETIC_NEW_TOKEN"}
        worker = {"DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID": self.OTHER_ACCOUNT,
                  "CLOUDFLARE_API_TOKEN": "SYNTHETIC_OLD_TOKEN"}
        self.assertEqual(route.clef_credentials_from_existing(worker, dotenv),
                         (self.ACCOUNT, "SYNTHETIC_NEW_TOKEN"))

    def test_a_pair_is_never_half_from_dotenv_and_half_from_the_worker(self):
        # The account exists in .env, the token only on the worker: that is not
        # a pair, so the source is skipped rather than completed.
        dotenv = {"DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID": self.ACCOUNT}
        worker = {"CLOUDFLARE_API_TOKEN": "SYNTHETIC_WORKERS_AI_TOKEN",
                  "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_ACCOUNT_ID": self.OTHER_ACCOUNT,
                  "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_API_TOKEN": "SYNTHETIC_SEARCH_TOKEN"}
        self.assertEqual(route.clef_credentials_from_existing(worker, dotenv),
                         (self.OTHER_ACCOUNT, "SYNTHETIC_SEARCH_TOKEN"))

    def test_dotenv_values_are_unquoted_and_only_the_reviewed_names_are_read(self):
        with tempfile.TemporaryDirectory(prefix="clef-dotenv-") as raw:
            env_file = Path(raw) / ".env"
            env_file.write_text(
                "UNRELATED_SECRET=SHOULD_NEVER_BE_READ\n"
                'DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID="%s"\n'
                "CLOUDFLARE_API_TOKEN='SYNTHETIC_QUOTED_TOKEN'\n"
                "# CLOUDFLARE_API_TOKEN=COMMENTED_OUT\n"
                "OTHER_CLOUDFLARE_THING=nope\n" % self.ACCOUNT,
                encoding="utf-8",
            )
            dotenv = route._read_dotenv_values(env_file)
        self.assertEqual(dotenv, {"DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID": self.ACCOUNT,
                                  "CLOUDFLARE_API_TOKEN": "SYNTHETIC_QUOTED_TOKEN"})
        self.assertNotIn("UNRELATED_SECRET", dotenv)
        self.assertNotIn("OTHER_CLOUDFLARE_THING", dotenv)
        self.assertEqual(route.clef_credentials_from_existing({}, dotenv),
                         (self.ACCOUNT, "SYNTHETIC_QUOTED_TOKEN"))

    def test_dotenv_file_indirection_and_ambiguity_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="clef-dotenv-") as raw:
            token_file = Path(raw) / "token"
            token_file.write_text("SYNTHETIC_FROM_FILE\n", encoding="utf-8")
            token_file.chmod(0o600)
            env_file = Path(raw) / ".env"
            env_file.write_text(
                f"DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID={self.ACCOUNT}\n"
                f"CLOUDFLARE_API_TOKEN_FILE={token_file}\n",
                encoding="utf-8",
            )
            self.assertEqual(route.clef_credentials_from_existing(
                {}, route._read_dotenv_values(env_file)), (self.ACCOUNT, "SYNTHETIC_FROM_FILE"))

            # An ambiguous (multi-word, unquoted) reviewed value is refused
            # instead of being guessed, and an unrelated line is not affected.
            env_file.write_text(
                f"DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID={self.ACCOUNT}\n"
                "CLOUDFLARE_API_TOKEN=one two\n",
                encoding="utf-8",
            )
            with self.assertRaises(route.ConfigureError) as caught:
                route._read_dotenv_values(env_file)
            self.assertEqual(str(caught.exception), "env_unreadable")

    def test_a_missing_dotenv_is_no_values_but_a_non_regular_one_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="clef-dotenv-") as raw:
            self.assertEqual(route._read_dotenv_values(Path(raw) / "absent.env"), {})
            # A directory (or any non-regular file) named .env is a
            # misconfiguration, never silently treated as "no values".
            with self.assertRaises(route.ConfigureError) as caught:
                route._read_dotenv_values(Path(raw))
            self.assertEqual(str(caught.exception), "env_not_regular")

    def test_an_already_provisioned_clef_pair_in_dotenv_is_reused_as_is(self):
        with tempfile.TemporaryDirectory(prefix="clef-dotenv-") as raw:
            env_file = Path(raw) / ".env"
            env_file.write_text(
                f"DOCICH_JEV_ROUTE=clef\n"
                f"DOCICH_JEV_CLOUDFLARE_ACCOUNT_ID={self.ACCOUNT}\n"
                "DOCICH_JEV_CLOUDFLARE_API_TOKEN=SYNTHETIC_CLEF_TOKEN\n",
                encoding="utf-8",
            )
            self.assertEqual(route.clef_credentials_from_existing(
                {}, route._read_dotenv_values(env_file)), (self.ACCOUNT, "SYNTHETIC_CLEF_TOKEN"))

    def test_prefers_the_workers_ai_pair_and_returns_it_unchanged(self):
        env = {"DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID": self.ACCOUNT,
               "CLOUDFLARE_API_TOKEN": "SYNTHETIC_WORKERS_AI_TOKEN",
               "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_ACCOUNT_ID": self.OTHER_ACCOUNT,
               "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_API_TOKEN": "SYNTHETIC_SEARCH_TOKEN"}
        self.assertEqual(route.clef_credentials_from_existing(env),
                         (self.ACCOUNT, "SYNTHETIC_WORKERS_AI_TOKEN"))

    def test_falls_back_to_the_reviewed_web_search_pair_only(self):
        env = {"DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_ACCOUNT_ID": self.OTHER_ACCOUNT,
               "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_API_TOKEN": "SYNTHETIC_SEARCH_TOKEN"}
        self.assertEqual(route.clef_credentials_from_existing(env),
                         (self.OTHER_ACCOUNT, "SYNTHETIC_SEARCH_TOKEN"))

    def test_a_half_configured_source_is_skipped_not_completed(self):
        # The workers-ai source has an account but no token: it must not borrow
        # the search pair's token, and the complete search pair is only used
        # because its own account is present too.
        env = {"DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID": self.ACCOUNT,
               "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_ACCOUNT_ID": self.OTHER_ACCOUNT,
               "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_API_TOKEN": "SYNTHETIC_SEARCH_TOKEN"}
        self.assertEqual(route.clef_credentials_from_existing(env),
                         (self.OTHER_ACCOUNT, "SYNTHETIC_SEARCH_TOKEN"))

    def test_missing_or_unusable_sources_fail_closed_with_fixed_codes(self):
        for env in ({},
                    {"DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID": self.ACCOUNT},
                    {"CLOUDFLARE_API_TOKEN": "SYNTHETIC_WORKERS_AI_TOKEN"},
                    {"DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID": "not-hex",
                     "CLOUDFLARE_API_TOKEN": "SYNTHETIC_WORKERS_AI_TOKEN"}):
            with self.subTest(env=sorted(env)):
                with self.assertRaises(route.ConfigureError) as caught:
                    route.clef_credentials_from_existing(env)
                self.assertIn(str(caught.exception),
                              {"clef_source_credentials_missing", "clef_source_account_invalid"})

    def test_account_value_that_is_not_32_lowercase_hex_is_rejected_not_guessed(self):
        for bad in ("A" * 32, "0" * 31, "0" * 33, "0123456789abcdef0123456789abcde-"):
            with self.subTest(bad=bad):
                with self.assertRaises(route.ConfigureError) as caught:
                    route.clef_credentials_from_existing(
                        {"DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID": bad,
                         "CLOUDFLARE_API_TOKEN": "SYNTHETIC_WORKERS_AI_TOKEN"})
                self.assertEqual(str(caught.exception), "clef_source_account_invalid")

    def test_file_indirection_is_resolved_like_the_runtime_does(self):
        with tempfile.TemporaryDirectory(prefix="clef-reuse-") as raw:
            token_file = Path(raw) / "token"
            token_file.write_text("SYNTHETIC_FROM_FILE\n", encoding="utf-8")
            token_file.chmod(0o600)
            env = {"DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID": self.ACCOUNT,
                   "CLOUDFLARE_API_TOKEN_FILE": str(token_file)}
            self.assertEqual(route.clef_credentials_from_existing(env),
                             (self.ACCOUNT, "SYNTHETIC_FROM_FILE"))
            # A value and its file form together are ambiguous, never merged.
            env["CLOUDFLARE_API_TOKEN"] = "SYNTHETIC_INLINE_TOKEN"
            with self.assertRaises(route.ConfigureError) as caught:
                route.clef_credentials_from_existing(env)
            self.assertEqual(str(caught.exception), "clef_source_credentials_conflicting")

    def test_world_accessible_or_non_regular_token_file_is_refused(self):
        with tempfile.TemporaryDirectory(prefix="clef-reuse-") as raw:
            token_file = Path(raw) / "token"
            token_file.write_text("SYNTHETIC_FROM_FILE\n", encoding="utf-8")
            token_file.chmod(0o644)
            with self.assertRaises(route.ConfigureError) as caught:
                route.clef_credentials_from_existing(
                    {"DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID": self.ACCOUNT,
                     "CLOUDFLARE_API_TOKEN_FILE": str(token_file)})
            self.assertEqual(str(caught.exception), "clef_source_credentials_unreadable")
            link = Path(raw) / "link"
            link.symlink_to(token_file)
            with self.assertRaises(route.ConfigureError):
                route.clef_credentials_from_existing(
                    {"DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID": self.ACCOUNT,
                     "CLOUDFLARE_API_TOKEN_FILE": str(link)})

    def test_reuse_flag_requires_the_clef_route(self):
        for extra in (["--route", "direct"], ["--route", "vercel"], ["--disable"]):
            with self.subTest(extra=extra):
                result = subprocess.run(
                    [sys.executable, "-", "--soren-root", "/nonexistent",
                     *extra, "--reuse-existing-cloudflare-credentials"],
                    input=SCRIPT.read_bytes(), cwd=ROOT, capture_output=True, timeout=30,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"clef_reuse_requires_clef_route", result.stderr)

    def test_reuse_environment_values_are_never_printed(self):
        # The helper only ever returns the pair to its caller; the fixed error
        # codes and the success JSON of this script contain no value.
        env = {"DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID": self.ACCOUNT,
               "CLOUDFLARE_API_TOKEN": "SYNTHETIC_WORKERS_AI_TOKEN"}
        account, token = route.clef_credentials_from_existing(env)
        self.assertEqual((account, token), (self.ACCOUNT, "SYNTHETIC_WORKERS_AI_TOKEN"))
        for error in ("clef_source_credentials_missing", "clef_source_account_invalid",
                      "clef_source_credentials_conflicting", "clef_source_credentials_unreadable",
                      "clef_reuse_requires_clef_route"):
            self.assertNotIn("SYNTHETIC", error)
            self.assertNotIn(self.ACCOUNT, error)

    def test_reuse_writes_the_same_managed_lines_as_the_explicit_pair(self):
        # The resulting .env must be identical either way: reuse changes only
        # where the pair came from, never what is written.
        with tempfile.TemporaryDirectory(prefix="clef-reuse-") as raw:
            direct = Path(raw) / "direct.env"
            reused = Path(raw) / "reused.env"
            for path in (direct, reused):
                path.write_text("KEEP=value\nTYPESAFE_API_KEY=untouched\n", encoding="utf-8")
            env = {"DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID": self.ACCOUNT,
                   "CLOUDFLARE_API_TOKEN": "SYNTHETIC_WORKERS_AI_TOKEN"}
            account, token = route.clef_credentials_from_existing(env)
            route.configure_clef_env(direct, *route.clef_settings(
                {"DOCICH_JEV_CLOUDFLARE_ACCOUNT_ID": self.ACCOUNT,
                 "DOCICH_JEV_CLOUDFLARE_API_TOKEN": "SYNTHETIC_WORKERS_AI_TOKEN"}))
            route.configure_clef_env(reused, account, token)
            self.assertEqual(direct.read_text(encoding="utf-8"), reused.read_text(encoding="utf-8"))
            written = reused.read_text(encoding="utf-8")
            self.assertIn("DOCICH_JEV_ROUTE=clef\n", written)
            self.assertIn("TYPESAFE_API_KEY=untouched\n", written)
            # The source pair is another service's credential: no assignment
            # for it is added, rewritten or removed here.
            self.assertNotIn("\nCLOUDFLARE_API_TOKEN", written)
            self.assertNotIn("CLOUDFLARE_API_TOKEN_FILE", written)
            self.assertNotIn("DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID", written)
            self.assertNotIn("DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE", written)


class RefactoredClassifierVerificationTests(unittest.TestCase):
    """Regression coverage for the extract-method refactor in #678's script.

    This logic previously lived inline inside restart_chat_worker and had no
    direct unit coverage at all (only the real-process restart, which no
    test here exercises); extracting it as _verify_comment_classifier_jev
    makes it directly testable without a real PID.
    """

    def test_enable_requires_backend_key_and_model(self):
        verify = classifier._verify_comment_classifier_jev(True)
        verify({"COMMENT_CLASSIFIER_BACKEND": "jev", "TYPESAFE_API_KEY": "k",
                "COMMENT_CLASSIFIER_JEV_MODEL": classifier.MODEL})
        with self.assertRaises(classifier.ConfigureError):
            verify({"COMMENT_CLASSIFIER_BACKEND": "legacy"})
        with self.assertRaises(classifier.ConfigureError):
            verify({"COMMENT_CLASSIFIER_BACKEND": "jev"})
        with self.assertRaises(classifier.ConfigureError):
            verify({"COMMENT_CLASSIFIER_BACKEND": "jev", "TYPESAFE_API_KEY": "k",
                    "COMMENT_CLASSIFIER_JEV_MODEL": "jev-9.9.9"})

    def test_disable_requires_backend_and_key_gone(self):
        verify = classifier._verify_comment_classifier_jev(False)
        verify({})
        with self.assertRaises(classifier.ConfigureError):
            verify({"COMMENT_CLASSIFIER_BACKEND": "jev"})
        with self.assertRaises(classifier.ConfigureError):
            verify({"TYPESAFE_API_KEY": "still-here"})


if __name__ == "__main__":
    unittest.main()
