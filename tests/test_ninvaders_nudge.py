"""砲台未描画 (新試合直後) の共通回避策: 爆発の一瞬では動かず、持続したら Space で再描画。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.ninvaders.nudge import Nudge  # noqa: E402


def blind():
    return {"kind": "play", "player": None}


def seen():
    return {"kind": "play", "player": [10, 22]}


def test_short_cannon_explosion_is_left_alone():
    n = Nudge()
    assert [n.keys(blind()) for _ in range(7)] == [[]] * 7  # < 0.8 s at 10 Hz


def test_persistent_missing_cannon_gets_a_space_then_repeats():
    n = Nudge()
    out = [n.keys(blind()) for _ in range(15)]
    fired = [i for i, k in enumerate(out) if k == ["Space"]]
    assert fired == [7, 10, 13]


def test_seeing_the_cannon_resets_the_counter():
    n = Nudge()
    for _ in range(7):
        n.keys(blind())
    assert n.keys(seen()) == []
    assert [n.keys(blind()) for _ in range(7)] == [[]] * 7


def test_non_play_frames_never_trigger():
    n = Nudge()
    for kind in ("title", "gameover", "other"):
        assert n.keys({"kind": kind, "player": None}) == []
    assert n.blind == 0
