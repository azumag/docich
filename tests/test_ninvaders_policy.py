"""基準線ポリシー (brains/ninvaders/policy.py) と CommandBrain アダプタの振る舞い。"""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.ninvaders import frame as F  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "ninvaders"


def _policy():
    spec = importlib.util.spec_from_file_location("nv_policy", ROOT / "brains" / "ninvaders" / "policy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


P = _policy()


def obs(**kw):
    base = {"tick": 0, "kind": "play", "player": [40, 22], "player_hit": False, "bombs": [],
            "missiles": [], "aliens": [[40, 8]], "barriers": [], "ufo": None,
            "score": 0, "level": 1, "lives": 3, "text": ""}
    base.update(kw)
    return base


def test_fires_when_no_missile_is_in_flight():
    assert "Space" in P.decide(obs(), {})


def test_does_not_fire_while_own_missile_is_flying():
    assert "Space" not in P.decide(obs(missiles=[[40, 15]]), {})


def test_own_missile_above_the_cannon_is_never_dodged():
    keys = P.decide(obs(missiles=[[40, 20]], aliens=[[40, 8]]), {})
    assert "Left" not in keys and "Right" not in keys


def test_dodges_an_incoming_bomb_toward_a_safe_column():
    keys = P.decide(obs(bombs=[[40, 17]], aliens=[[40, 8]]), {})
    assert ("Left" in keys) != ("Right" in keys)


def test_dodge_goes_the_way_that_leaves_the_bomb_column():
    keys = P.decide(obs(player=[40, 22], bombs=[[39, 16], [40, 17]]), {})
    assert "Right" in keys  # both bombs sit at/left of the cannon: leave to the right


def test_far_or_distant_bombs_are_ignored():
    assert P.decide(obs(bombs=[[70, 17]], aliens=[[40, 8]]), {}) == ["Space"]
    assert P.decide(obs(bombs=[[40, 2]], aliens=[[40, 8]]), {}) == ["Space"]  # ~24 ticks away


def test_no_fire_from_directly_under_a_barrier():
    keys = P.decide(obs(barriers=[[40, 17], [40, 18]]), {})
    assert "Space" not in keys


def test_aims_toward_the_target_alien():
    keys = P.decide(obs(player=[10, 22], aliens=[[50, 8]]), {})
    assert "Right" in keys and "Left" not in keys


def test_exploding_cannon_sends_nothing():
    assert P.decide(obs(player=None, player_hit=True), {}) == []


def test_state_is_a_plain_dict_that_persists_between_ticks():
    state = {}
    P.decide(obs(), state)
    P.decide(obs(), state)
    assert state["ticks"] == 2


def test_real_frames_produce_only_valid_keys():
    for name in ("play_start", "play_missile"):
        view = F.obs_for_policy(F.parse((FIX / f"{name}.txt").read_text(encoding="utf-8")))
        keys = P.decide(view, {})
        assert set(keys) <= {"Left", "Right", "Space"}
