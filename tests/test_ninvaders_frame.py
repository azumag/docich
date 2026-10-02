"""nInvaders 盤面パーサ: 実ゲームから採取したフレーム (tests/fixtures/ninvaders) で検証する。

記号の意味は実機の 0.1 秒間隔観測で確定したもの:
  '!' = 自機のミサイル (上昇) / ':' = 敵弾 (下降) / 試合終了は '#' ブロック文字の
  GAME OVER 画面 → タイトル。文字列 "Game Over" は出ない。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.ninvaders import frame as F  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "ninvaders"


def load(name: str) -> str:
    return (FIX / f"{name}.txt").read_text(encoding="utf-8")


def test_title_is_title_and_logo_is_not_parsed_as_aliens():
    obs = F.parse(load("title"))
    assert obs["kind"] == "title"
    assert obs["aliens"] == [] and obs["bombs"] == [] and obs["player"] is None


def test_play_frame_reads_cannon_status_and_formation():
    obs = F.parse(load("play_start"))
    assert obs["kind"] == "play"
    assert obs["player"] == (2, 22) and not obs["player_hit"]
    assert (obs["score"], obs["level"], obs["lives"]) == (0, 1, 3)
    assert len(obs["aliens"]) == 50  # 5 rows x 10
    assert len(obs["barriers"]) == 76  # 4 barriers x 19 cells


def test_bomb_drawn_inside_an_alien_does_not_split_the_alien():
    obs = F.parse(load("play_start"))
    assert obs["bombs"] == [(5, 2)]
    assert len([a for a in obs["aliens"] if a[1] == 2]) == 10


def test_bang_is_our_own_missile_not_a_bomb():
    obs = F.parse(load("play_missile"))
    assert obs["missiles"] == [(2, 21)]  # directly above the cannon column
    assert (2, 21) not in obs["bombs"]
    assert obs["bombs"] == [(5, 2)]  # the ':' is the alien bomb


def test_block_letter_game_over_is_its_own_kind_with_the_final_score():
    obs = F.parse(load("gameover"))
    assert obs["kind"] == "gameover"
    assert obs["score"] == 500
    assert obs["aliens"] == [] and obs["barriers"] == [] and obs["player"] is None
    assert "Game Over" not in load("gameover")  # why text matching can never detect the end


def test_return_to_title_after_match_is_title():
    assert F.parse(load("title_after_match"))["kind"] == "title"


def test_cannon_explosion_is_a_play_frame_with_player_hit():
    lines = load("play_start").splitlines()
    lines[22] = "     *#_.~"  # explosion animation replaces the cannon glyph
    obs = F.parse("\n".join(lines))
    assert obs["kind"] == "play"
    assert obs["player"] is None and obs["player_hit"] is True


def test_ufo_is_reported_and_not_counted_as_an_alien():
    lines = load("play_start").splitlines()
    lines[0] = "                          <o o>"
    obs = F.parse("\n".join(lines))
    assert obs["ufo"] is not None and obs["ufo"][1] == 0
    assert all(not (a[1] == 0 and 26 <= a[0] <= 31) for a in obs["aliens"])


def test_parse_never_raises_on_garbage():
    for junk in (None, "", "\x00\xff", "Level: x Score: y", "\n" * 50, 123):
        obs = F.parse(junk)
        assert set(("kind", "player", "bombs", "aliens")) <= set(obs)


def test_policy_view_is_json_serializable_lists():
    view = F.obs_for_policy(F.parse(load("play_missile")))
    assert json.loads(json.dumps(view))["missiles"] == [[2, 21]]
    assert isinstance(view["player"], list)
