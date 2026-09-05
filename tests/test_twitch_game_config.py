"""全ゲーム定義に Twitch 連動設定 [twitch] があることの契約テスト。

ゲーム追加時は config/games/<id>.toml に [twitch] (category_id /
category_name / title_prefix) が必須。IGDB 照合の手順は
games/soviet_now/docs/twitch_game_sync.md。
"""
import sys
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GAMES_DIR = REPO_ROOT / "config" / "games"


class TestTwitchGameConfig(unittest.TestCase):
    def _game_tomls(self):
        tomls = sorted(GAMES_DIR.glob("*.toml"))
        self.assertTrue(tomls, "config/games/*.toml がありません")
        return tomls

    def test_every_game_has_twitch_table(self):
        missing = []
        for path in self._game_tomls():
            with path.open("rb") as fh:
                data = tomllib.load(fh)
            if not isinstance(data.get("twitch"), dict):
                missing.append(path.name)
        self.assertEqual(
            missing, [],
            f"[twitch] が無いゲーム定義: {missing} "
            "(ゲーム追加時は IGDB 照合して category を設定すること)",
        )

    def test_twitch_fields_valid(self):
        for path in self._game_tomls():
            with path.open("rb") as fh:
                data = tomllib.load(fh)
            tw = data.get("twitch")
            if not isinstance(tw, dict):
                continue  # 上のテストで検出する
            with self.subTest(game=path.stem):
                cid = tw.get("category_id")
                self.assertIsInstance(cid, str, "category_id は文字列")
                self.assertTrue(cid.isdigit(), f"category_id は数字ID: {cid!r}")
                self.assertTrue(
                    str(tw.get("category_name") or "").strip(),
                    "category_name (Twitch正式名) が必要",
                )
                self.assertTrue(
                    str(tw.get("title_prefix") or "").strip(),
                    "title_prefix が必要",
                )

    def test_known_category_ids(self):
        # IGDB 照合済みの対応表 (変更時は Twitch 実登録と再照合すること)。
        expected = {
            "sorengame": ("1530787860", "Soren Game"),
            "nethack": ("130", "NetHack"),
            "hanjuku-hero": ("21236", "Hanjuku Hero: Aa Sekai yo Hanjuku Nare...!!"),
            # robots.toml は codex/robots-game 側で未commit。着地時に
            # ("11585", "Robots") を [twitch] へ入れること。
        }
        for name, (cid, cname) in expected.items():
            path = GAMES_DIR / f"{name}.toml"
            if not path.is_file():
                continue
            with path.open("rb") as fh:
                tw = tomllib.load(fh).get("twitch", {})
            with self.subTest(game=name):
                self.assertEqual(tw.get("category_id"), cid)
                self.assertEqual(tw.get("category_name"), cname)


if __name__ == "__main__":
    unittest.main()
