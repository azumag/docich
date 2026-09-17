"""Explicit Union Alpha chains: configuration is the source of truth, not a clock."""

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config, webui  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
PREFIX = ["opencode-go:union-alpha", "openrouter:stealth/union-alpha"]
CHAIN_KEYS = ("AI_COMMON_AGENTS", "MODEL_IMPROVE_LIST", "PEAK_HOURS_AGENT_PREFERENCE")


class TestExplicitUnionAlphaConfig(unittest.TestCase):
    def test_ui_defaults_match_soren_core(self):
        core = ROOT / "games/soviet_now/core/config.sh"
        if not core.exists():
            self.skipTest("Soren submodule is not initialized")
        source = core.read_text(encoding="utf-8")

        def shell_default(key, operator=":-"):
            # Read tracked constants only; never source config/.env or initialize runtime state.
            match = re.search(
                rf'^{key}="\$\{{{key}{re.escape(operator)}(.*)\}}"$',
                source, re.MULTILINE,
            )
            self.assertIsNotNone(match, key)
            return match.group(1)

        vercel = ",".join(shell_default(f"VERCEL_CATEGORY_{category}_AGENTS", "-")
                          for category in ("A", "B")) + ","
        for key in CHAIN_KEYS:
            with self.subTest(key=key):
                expected = shell_default(key).replace("${_VERCEL_FREE_CHAIN}", vercel)
                self.assertEqual(webui.DEFAULTS[key], expected)
                self.assertEqual(webui._effective_value(key, {}), expected)
                self.assertEqual(expected.split(",")[:2], PREFIX)
                webui._validate_value(key, expected)

    def test_auxiliary_defaults_match_core_and_parents(self):
        source = (ROOT / "games/soviet_now/core/config.sh").read_text()
        for parent, default in webui.AUX_PARENT_DEFAULTS.items():
            with self.subTest(parent=parent):
                match = re.search(rf'^{parent}="\$\{{{parent}:-(.*?)\}}"$', source, re.MULTILINE)
                self.assertIsNotNone(match)
                self.assertEqual(match.group(1), default)
        for key, parents in webui.AUX_CHAIN_PARENTS.items():
            with self.subTest(key=key):
                match = re.search(rf'^{key}="\$\{{{key}-(.*)\}}"$', source, re.MULTILINE)
                self.assertIsNotNone(match)
                expected = match.group(1)
                expected = re.sub(r'\$\{(\w+):\+,\$\{\1\}\}',
                                  lambda m: ',' + webui.AUX_PARENT_DEFAULTS[m[1]]
                                  if webui.AUX_PARENT_DEFAULTS[m[1]] else '', expected)
                expected = re.sub(r'\$\{(\w+)\}', lambda m: webui.AUX_PARENT_DEFAULTS[m[1]], expected)
                self.assertEqual(webui.DEFAULTS[key], expected)
                self.assertEqual(webui._effective_value(key, {}), expected)
                self.assertIn(key, webui.WEBUI_ALLOWLIST)
                webui._validate_value(key, expected)
                # Parents are leaves: inheritance cannot cycle.
                self.assertFalse(set(parents) & set(webui.AUX_CHAIN_PARENTS))
                custom = {parent: f'opencode:fixture-{i}' for i, parent in enumerate(parents)}
                chain = ','.join(custom.values())
                if key in webui.AUX_CHAIN_STATIC_SUFFIX:
                    self.assertEqual(webui._effective_value(key, custom), chain + ',' + ','.join(PREFIX))
                else:
                    self.assertEqual(webui._effective_value(key, custom), ','.join(PREFIX) + ',' + chain)
                self.assertEqual(webui._effective_value(key, {**custom, key: ''}), chain)
                self.assertEqual(webui._effective_value(key, {key: 'local'}), 'local')
                default_parents = ','.join(webui.AUX_PARENT_DEFAULTS[p] for p in parents
                                           if webui.AUX_PARENT_DEFAULTS[p])
                self.assertEqual(webui._effective_value(key, {key: ''}), default_parents)

    def test_empty_auxiliary_chain_survives_save_as_inheritance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for key in webui.AUX_CHAIN_PARENTS:
                webui._atomic_env_update(root, {key: ''}, None)
            saved = webui._read_dotenv_dict(root)
            for key in webui.AUX_CHAIN_PARENTS:
                self.assertIn(key, saved)
                self.assertEqual(saved[key], '')
                self.assertNotIn('union-alpha', webui._effective_value(key, saved))

    def test_translation_cap_is_positive_integer_not_chain(self):
        key = 'COMMENT_TRANSLATION_MAX_ATTEMPTS'
        source = (ROOT / 'games/soviet_now/core/config.sh').read_text()
        self.assertIn('COMMENT_TRANSLATION_MAX_ATTEMPTS="${COMMENT_TRANSLATION_MAX_ATTEMPTS:-4}"', source)
        self.assertIn(key, webui.WEBUI_ALLOWLIST)
        self.assertEqual(webui.DEFAULTS[key], '4')
        for value in ('', '1', '2', '4', '12'):
            webui._validate_value(key, value)
            self.assertEqual(webui._effective_value(key, {key: value}), value or '4')
        for value in ('0', '-1', '1.5', '1,2', 'opencode:fixture', '$(id)', ' 4 '):
            with self.subTest(value=value), self.assertRaises(ValueError):
                webui._validate_value(key, value)
        for key in webui.AUX_CHAIN_PARENTS:
            for value in ('local,,local', '$(id)', 'local,'):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    webui._validate_value(key, value)

    def test_custom_and_inherited_chains_are_not_prefixed_at_runtime(self):
        custom = "opencode:custom,local"
        for key in CHAIN_KEYS:
            with self.subTest(key=key):
                self.assertEqual(webui._effective_value(key, {key: custom}), custom)
        for key in ("RADIO_AGENTS", "RADIO_PREPASS_AGENTS", "COMMENT_AGENTS",
                    "COMMENT_TRANSLATION_AGENTS", "MODEL_IMPROVE_PEAK_LIST"):
            with self.subTest(key=key):
                self.assertEqual(webui.DEFAULTS[key], "")
                parent = "MODEL_IMPROVE_LIST" if key == "MODEL_IMPROVE_PEAK_LIST" else "AI_COMMON_AGENTS"
                self.assertEqual(webui._effective_value(key, {parent: custom}), custom)
                self.assertEqual(webui._effective_value(key, {}), webui.DEFAULTS[parent])

    def test_single_agent_stays_single(self):
        key = "PEAK_HOURS_PRIORITY_AGENT"
        self.assertNotIn(",", webui.DEFAULTS[key])
        webui._validate_value(key, PREFIX[0])
        webui._validate_value(key, PREFIX[1])
        with self.assertRaises(ValueError):
            webui._validate_value(key, ",".join(PREFIX))

    def test_live_paper_chains_preserve_tail_and_disabled_inheritance(self):
        live = config._load_toml_file(ROOT / "config/docich.soren-live.toml")
        tails = {
            "script_agents": ["opencode:muse-spark-1.3-contributor-free",
                              "opencode-go:muse-spark-1.3-contributor", "amd:DeepSeek-V4-Flash",
                              "opencode-go:deepseek-v4.1-flash", "opencode-go:deepseek-v4-flash"],
            "improve_agents": ["opencode-go:deepseek-v4.1-flash", "amd:DeepSeek-V4-Flash",
                               "opencode-go:deepseek-v4-flash", "opencode-go:muse-spark-1.3-contributor"],
        }
        for key, tail in tails.items():
            with self.subTest(key=key):
                self.assertEqual(live["paper_corner"][key].split(","), PREFIX + tail)
        self.assertEqual(live["retro_corner"]["improve_agents"], "")
        self.assertNotIn("market_paper", live)
        for key in ("paper_corner", "retro_corner", "soren91_corner"):
            self.assertIs(live[key]["enabled"], True)
        self.assertIs(live["trading"]["paper_worker_enabled"], True)
        self.assertIs(live["watchdog"]["enabled"], False)
        self.assertIs(live["audio"]["enabled"], False)


if __name__ == "__main__":
    unittest.main()
