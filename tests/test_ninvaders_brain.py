import json
import subprocess
import sys
import unittest
from pathlib import Path

BRAIN = Path(__file__).resolve().parents[1] / "brains" / "ninvaders" / "brain.py"

GAMEPLAY = """\
                              .-..-..-..-..-..-..-..-..-..-.
                              -O_-O_-O_-O_-O_-O_-O_-O_-O_-O_
                              -O_-O_-O_-O_-O_-O_-O_-O_-O_-O_
                              /o\\/o\\/o\\/o\\/o\\/o\\/o\\/o\\/o\\/
                              /o\\/o\\/o\\/o\\/o\\/o\\/o\\/o\\/o\\/
        ###                 ###                 ###                 ###
       #####               #####               #####               #####
      #######             #######             #######             #######
      ##   ##             ##   ##             ##   ##             ##   ##
/-^-\\
                Level: 01 Score: 0000000 Lives: /-\\ /-\\
"""


def _pane(player_x: int, bullets: list[tuple[int, int]] | None = None) -> str:
    lines = GAMEPLAY.splitlines()
    # 自機行を player_x へ移動
    body = [l for l in lines if "/-^-\\" not in l and not l.startswith("                Level")]
    player_line = " " * player_x + "/-^-\\"
    status = "                Level: 01 Score: 0000000 Lives: /-\\ /-\\"
    grid = [list(l.ljust(80)) for l in body]
    for bx, by in bullets or []:
        if 0 <= by < len(grid) and 0 <= bx < 80:
            grid[by][bx] = "!"
    return "\n".join("".join(l) for l in grid) + "\n" + player_line + "\n" + status


def _decide(text: str) -> list:
    obs = {"game": "ninvaders", "text": text}
    proc = subprocess.run(
        [sys.executable, str(BRAIN)],
        input=json.dumps(obs),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["actions"]


def _keys(actions) -> list:
    return [a["keys"][0] for a in actions]


class NinvadersBrainTest(unittest.TestCase):
    def test_fires_every_turn(self):
        actions = _decide(_pane(8))
        self.assertIn("Space", _keys(actions))

    def test_dodges_bullet_on_left(self):
        # 自機の左上に敵弾 → 右へ回避
        actions = _decide(_pane(8, bullets=[(7, 8)]))
        keys = _keys(actions)
        self.assertIn("Right", keys)
        self.assertIn("Space", keys)

    def test_dodges_bullet_on_right(self):
        # '^' は行頭+2 ('/-^-\' の3文字目)。自機の右上に敵弾 → 左へ回避
        actions = _decide(_pane(8, bullets=[(11, 8)]))
        self.assertIn("Left", _keys(actions))

    def test_ignores_distant_bullets(self):
        # 遠方の弾は無視して invader 列へ寄せる (列は右側なので Right)
        actions = _decide(_pane(8, bullets=[(70, 0)]))
        keys = _keys(actions)
        self.assertIn("Right", keys)
        self.assertIn("Space", keys)

    def test_title_is_silent(self):
        _, = (None,)
        actions = _decide("Press SPACE to start\nLevel: 01 Score: 0000000")
        self.assertEqual(actions, [])

    def test_bad_input_is_json_error(self):
        proc = subprocess.run(
            [sys.executable, str(BRAIN)],
            input="not json",
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
