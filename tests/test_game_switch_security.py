import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.naming import (  # noqa: E402
    NameValidationError,
    ensure_contained,
    runtime_directory,
    runtime_id_generation,
    runtime_names,
    validate_game_name,
    validate_tmux_target,
)


class TestNames(unittest.TestCase):
    def test_valid_game_names(self):
        for name in ("robots", "hanjuku-hero", "game_2", "0ad"):
            self.assertEqual(validate_game_name(name), name)

    def test_invalid_game_names(self):
        for name in ("../robots", "Robots", "robot game", "", "a" * 65, "robots;kill"):
            with self.subTest(name=name):
                with self.assertRaises(NameValidationError):
                    validate_game_name(name)

    def test_runtime_names_only_use_generation(self):
        names = runtime_names(42)
        self.assertEqual(names.game_window, "game-g42")
        self.assertEqual(names.agent_window, "agent-g42")
        self.assertEqual(names.adapter_session, "docich-game-g42")
        self.assertEqual(runtime_id_generation("g42-abcdef"), 42)

    def test_tmux_target_rejects_command_syntax(self):
        for target in ("docich:game-g2", "docich-game-g2"):
            self.assertEqual(validate_tmux_target(target), target)
        for target in ("docich;kill-server", "$(id)", "docich:../game"):
            with self.assertRaises(NameValidationError):
                validate_tmux_target(target)

    def test_runtime_directory_is_contained(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = runtime_directory(Path(tmp) / "run", "g7-abcdef")
            self.assertEqual(path, Path(tmp).resolve() / "run" / "runtimes" / "g7-abcdef")

    def test_containment_rejects_parent_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            with self.assertRaises(NameValidationError):
                ensure_contained(base, base / ".." / "outside")


class TestGameConfigBoundary(unittest.TestCase):
    def _global(self, root: Path) -> config.GlobalConfig:
        return config.load_global(root)

    def _write_game(self, root: Path, filename: str, declared_name: str | None = None) -> Path:
        games = root / "config" / "games"
        games.mkdir(parents=True, exist_ok=True)
        name_line = f'name = "{declared_name}"\n' if declared_name is not None else ""
        path = games / filename
        path.write_text(f"[game]\n{name_line}adapter = \"cli\"\n", encoding="utf-8")
        return path

    def test_load_game_rejects_path_traversal_before_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(config.ConfigError):
                config.load_game(self._global(root), "../outside")

    def test_declared_name_must_match_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_game(root, "robots.toml", "nethack")
            with self.assertRaises(config.ConfigError):
                config.load_game(self._global(root), "robots")

    def test_symlinked_game_definition_cannot_escape_games_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside.toml"
            outside.write_text('[game]\nname="robots"\nadapter="cli"\n', encoding="utf-8")
            games = root / "config" / "games"
            games.mkdir(parents=True)
            os.symlink(outside, games / "robots.toml")
            with self.assertRaises(config.ConfigError):
                config.load_game(self._global(root), "robots")

    def test_list_games_skips_invalid_filename_and_reports_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_game(root, "Good.toml")
            error = io.StringIO()
            with redirect_stderr(error):
                games = config.list_games(self._global(root))
            self.assertEqual(games, [])
            self.assertIn("Good.toml", error.getvalue())

    def test_rotation_rejects_unsafe_game_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "docich.toml"
            config_path.write_text('[rotation]\ngames=["../outside"]\n', encoding="utf-8")
            with self.assertRaises(config.ConfigError):
                config.load_global(root, config_path)


if __name__ == "__main__":
    unittest.main()
