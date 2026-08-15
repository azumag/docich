import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402


class TestLoadGlobalDefaults(unittest.TestCase):
    """config 無しでの既定値ロード。"""

    def test_defaults_without_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            g = config.load_global(repo_root)

            # soren (:99 稼働中) との衝突回避で既定は :98
            self.assertEqual(g.display.number, 98)
            self.assertEqual(g.display.name, ":98")
            self.assertEqual(g.display.width, 1280)
            self.assertEqual(g.display.height, 720)
            self.assertEqual(g.display.color_depth, 24)

            self.assertTrue(g.audio.enabled)
            self.assertEqual(g.audio.sink_name, "docich_sink")
            self.assertFalse(g.audio.set_default)

            self.assertEqual(g.stream.mode, "null")
            self.assertEqual(g.stream.ffmpeg_bin, "ffmpeg")
            self.assertEqual(g.stream.framerate, 30)
            self.assertEqual(g.stream.gop_seconds, 2)

            self.assertFalse(g.captions.enabled)
            self.assertTrue(g.captions.socket_path.endswith("/docich/ffmpeg-cc.sock"))

            self.assertEqual(g.agent.default_interval_ms, 2000)
            self.assertEqual(g.agent.brain_timeout_s, 120)

            self.assertEqual(g.state_dir, repo_root / "run")
            self.assertEqual(g.games_dir, repo_root / "config" / "games")
            self.assertEqual(g.roms_dir, repo_root / "games" / "roms")

    def test_repo_default_config_path_is_used_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / "config").mkdir(parents=True)
            (repo_root / "config" / "docich.toml").write_text(
                "[display]\nnumber = 55\n", encoding="utf-8"
            )
            g = config.load_global(repo_root)
            self.assertEqual(g.display.number, 55)


class TestLoadGlobalFromToml(unittest.TestCase):
    def test_loads_values_from_toml(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            toml_path = repo_root / "custom.toml"
            toml_path.write_text(
                """
[display]
number = 42
width = 640
height = 480

[audio]
enabled = false
sink_name = "custom_sink"
set_default = true

[stream]
ffmpeg_bin = "/opt/docich-ffmpeg/bin/ffmpeg"
mode = "file"
file_path = "run/custom.flv"
framerate = 24
gop_seconds = 3

[captions]
enabled = true
socket_path = "/run/user/1001/docich/ffmpeg-cc.sock"

[agent]
default_interval_ms = 500

[paths]
state_dir = "custom_run"
games_dir = "custom_games"
roms_dir = "custom_roms"
""",
                encoding="utf-8",
            )
            g = config.load_global(repo_root, config_path=toml_path)

            self.assertEqual(g.config_path, toml_path.resolve())

            self.assertEqual(g.display.number, 42)
            self.assertEqual(g.display.name, ":42")
            self.assertEqual(g.display.width, 640)
            self.assertEqual(g.display.height, 480)

            self.assertFalse(g.audio.enabled)
            self.assertEqual(g.audio.sink_name, "custom_sink")
            self.assertTrue(g.audio.set_default)

            self.assertEqual(g.stream.mode, "file")
            self.assertEqual(g.stream.ffmpeg_bin, "/opt/docich-ffmpeg/bin/ffmpeg")
            self.assertEqual(g.stream.file_path, "run/custom.flv")
            self.assertEqual(g.stream.framerate, 24)
            self.assertEqual(g.stream.gop_seconds, 3)

            self.assertTrue(g.captions.enabled)
            self.assertEqual(
                g.captions.socket_path, "/run/user/1001/docich/ffmpeg-cc.sock"
            )

            self.assertEqual(g.agent.default_interval_ms, 500)

            self.assertEqual(g.state_dir, repo_root / "custom_run")
            self.assertEqual(g.games_dir, repo_root / "custom_games")
            self.assertEqual(g.roms_dir, repo_root / "custom_roms")

    def test_env_var_config_path_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            toml_path = repo_root / "env.toml"
            toml_path.write_text("[display]\nnumber = 77\n", encoding="utf-8")
            import os

            old = os.environ.get("DOCICH_CONFIG")
            os.environ["DOCICH_CONFIG"] = str(toml_path)
            try:
                g = config.load_global(repo_root)
                self.assertEqual(g.display.number, 77)
            finally:
                if old is None:
                    os.environ.pop("DOCICH_CONFIG", None)
                else:
                    os.environ["DOCICH_CONFIG"] = old

    def test_explicit_config_path_wins_over_env_var(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            env_toml = repo_root / "env.toml"
            env_toml.write_text("[display]\nnumber = 77\n", encoding="utf-8")
            arg_toml = repo_root / "arg.toml"
            arg_toml.write_text("[display]\nnumber = 11\n", encoding="utf-8")
            import os

            old = os.environ.get("DOCICH_CONFIG")
            os.environ["DOCICH_CONFIG"] = str(env_toml)
            try:
                g = config.load_global(repo_root, config_path=arg_toml)
                self.assertEqual(g.display.number, 11)
            finally:
                if old is None:
                    os.environ.pop("DOCICH_CONFIG", None)
                else:
                    os.environ["DOCICH_CONFIG"] = old

    def test_invalid_stream_mode_raises_config_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            toml_path = repo_root / "bad.toml"
            toml_path.write_text('[stream]\nmode = "youtube"\n', encoding="utf-8")
            with self.assertRaises(config.ConfigError):
                config.load_global(repo_root, config_path=toml_path)

    def test_malformed_toml_raises_config_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            toml_path = repo_root / "broken.toml"
            toml_path.write_text("this is not [valid toml", encoding="utf-8")
            with self.assertRaises(config.ConfigError):
                config.load_global(repo_root, config_path=toml_path)

    def test_caption_socket_must_be_safe_absolute_unix_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            toml_path = repo_root / "bad-caption.toml"
            toml_path.write_text(
                '[captions]\nenabled = true\nsocket_path = "relative.sock"\n',
                encoding="utf-8",
            )
            with self.assertRaises(config.ConfigError):
                config.load_global(repo_root, config_path=toml_path)

    def test_caption_environment_overrides_toml(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            toml_path = repo_root / "caption.toml"
            toml_path.write_text("[captions]\nenabled = false\n", encoding="utf-8")
            old = {name: os.environ.get(name) for name in (
                "DOCICH_CC_ENABLED", "DOCICH_CC_SOCKET", "DOCICH_FFMPEG_BIN"
            )}
            os.environ.update({
                "DOCICH_CC_ENABLED": "1",
                "DOCICH_CC_SOCKET": "/tmp/docich-test/cc.sock",
                "DOCICH_FFMPEG_BIN": "/opt/docich/bin/ffmpeg",
            })
            try:
                g = config.load_global(repo_root, config_path=toml_path)
            finally:
                for name, value in old.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
            self.assertTrue(g.captions.enabled)
            self.assertEqual(g.captions.socket_path, "/tmp/docich-test/cc.sock")
            self.assertEqual(g.stream.ffmpeg_bin, "/opt/docich/bin/ffmpeg")


class TestLoadGame(unittest.TestCase):
    def _make_global(self, tmp: Path) -> config.GlobalConfig:
        return config.load_global(Path(tmp))

    def test_load_game_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            games_dir = repo_root / "config" / "games"
            games_dir.mkdir(parents=True)
            (games_dir / "nethack.toml").write_text(
                """
[game]
name = "nethack"
title = "NetHack"
adapter = "cli"

[cli]
command = "nethack"

[agent]
enabled = true
brain = "random"
interval_ms = 1500
""",
                encoding="utf-8",
            )
            g = self._make_global(tmp)
            game = config.load_game(g, "nethack")
            self.assertEqual(game.name, "nethack")
            self.assertEqual(game.title, "NetHack")
            self.assertEqual(game.adapter, "cli")
            self.assertTrue(game.agent.enabled)
            self.assertEqual(game.agent.brain, "random")
            self.assertEqual(game.agent.interval_ms, 1500)
            self.assertEqual(game.raw["cli"]["command"], "nethack")

    def test_load_game_missing_raises_config_error_mentioning_games_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = self._make_global(tmp)
            with self.assertRaises(config.ConfigError) as ctx:
                config.load_game(g, "does-not-exist")
            self.assertIn("docich games", str(ctx.exception))

    def test_load_game_missing_adapter_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            games_dir = repo_root / "config" / "games"
            games_dir.mkdir(parents=True)
            (games_dir / "broken.toml").write_text('[game]\nname = "broken"\n', encoding="utf-8")
            g = self._make_global(tmp)
            with self.assertRaises(config.ConfigError):
                config.load_game(g, "broken")

    def test_game_agent_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            games_dir = repo_root / "config" / "games"
            games_dir.mkdir(parents=True)
            (games_dir / "minimal.toml").write_text(
                '[game]\nadapter = "cli"\n', encoding="utf-8"
            )
            g = self._make_global(tmp)
            game = config.load_game(g, "minimal")
            self.assertEqual(game.name, "minimal")
            self.assertEqual(game.title, "minimal")
            self.assertFalse(game.agent.enabled)
            self.assertEqual(game.agent.brain, "command")
            self.assertIsNone(game.agent.interval_ms)


class TestListGames(unittest.TestCase):
    def test_list_games_skips_broken_files_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            games_dir = repo_root / "config" / "games"
            games_dir.mkdir(parents=True)
            (games_dir / "good.toml").write_text(
                '[game]\nname = "good"\nadapter = "cli"\n', encoding="utf-8"
            )
            (games_dir / "bad.toml").write_text("not valid toml [[[", encoding="utf-8")
            g = config.load_global(repo_root)

            import io
            from contextlib import redirect_stderr

            buf = io.StringIO()
            with redirect_stderr(buf):
                games = config.list_games(g)

            names = [game.name for game in games]
            self.assertEqual(names, ["good"])
            self.assertIn("bad.toml", buf.getvalue())

    def test_list_games_empty_when_dir_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = config.load_global(Path(tmp))
            self.assertEqual(config.list_games(g), [])


if __name__ == "__main__":
    unittest.main()
