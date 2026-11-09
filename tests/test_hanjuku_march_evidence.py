"""March chart/strategy must not mistake the old 400-tick TTL for arrival."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.hanjuku.os_layer.world import _en_route, SORTIE_BUSY_TICKS, march_arrival_limit
from docich.hanjuku.strategy.orders import _arrival_limit, _reserved_targets, _retake_reserved


def memory(chapter=6, age=500, status="en_route"):
    return {
        "chapter": chapter, "tick": 5000,
        "sorties": {"6-X1": {
            "general": "ナストリーニ", "source": "アルマムーン",
            "target": "セレーネ", "status": status, "tick": 5000 - age,
        }},
    }



def test_strategy_and_world_share_the_same_arrival_deadline():
    mem = memory()
    sortie = mem["sorties"]["6-X1"]
    assert _arrival_limit(mem, sortie) == march_arrival_limit(mem, sortie)



def test_long_march_remains_busy_after_old_display_deadline():
    mem = memory()
    sortie = mem["sorties"]["6-X1"]
    assert _arrival_limit(mem, sortie) > SORTIE_BUSY_TICKS
    assert _en_route(mem) == ({"ナストリーニ"}, {"セレーネ"})
    assert "セレーネ" in _reserved_targets(mem)


def test_reconciliation_window_is_bounded():
    mem = memory()
    limit = _arrival_limit(mem, mem["sorties"]["6-X1"])
    mem["tick"] = mem["sorties"]["6-X1"]["tick"] + limit - 1
    assert "ナストリーニ" in _en_route(mem)[0]
    mem["tick"] += 1
    assert "ナストリーニ" not in _en_route(mem)[0]



def test_duplicate_retake_remains_reserved_after_400_observations():
    mem = {
        "chapter": 1, "tick": 1000,
        "sorties": {"earlier": {
            "general": "ヴィーナス", "source": "カストーラ",
            "target": "ジョンリギ", "status": "en_route", "tick": 500,
        }},
    }
    order = {"step": "I:new", "target": "ジョンリギ", "purpose": "retake"}
    assert _retake_reserved(order, mem)


def test_explicitly_overdue_or_arrived_is_not_claimed_to_be_marching():
    for status in ("overdue", "arrived", "cancelled"):
        assert _en_route(memory(status=status)) == (set(), set())
        assert not _reserved_targets(memory(status=status))


def test_bad_or_future_tick_does_not_make_live_route():
    mem = memory()
    mem["sorties"]["6-X1"]["tick"] = mem["tick"] + 1
    assert _en_route(mem) == (set(), set())
    assert not _reserved_targets(mem)


def test_overlay_absence_is_not_observed_march_completion():
    source = (Path(__file__).resolve().parents[1] /
              "overlays/direct_broadcast_overlay.html").read_text(encoding="utf-8")
    assert "の行軍が終了`" not in source
    assert "の追跡記録が消失（行軍終了は未確認）" in source


def test_the_measured_slow_march_still_fits_the_limit():
    """g1133: 603 chart cells took up to 32 min; the limit must cover that."""
    mem = memory()
    sortie = mem["sorties"]["6-X1"]
    limit = march_arrival_limit(mem, sortie)
    # アルマムーン (392,144) → セレーネ (616,704) is a 603-cell march.
    assert limit >= 1920          # 32 minutes of observations
    # The other measured march (740 cells, 34 min) also fits under the cap.
    far = {"source": "アルマムーン", "target": "グルーポフ"}
    assert march_arrival_limit(mem, far) >= 2040
    # Nothing exceeds the bounded reconciliation window.
    assert march_arrival_limit(mem, far) <= 6 * SORTIE_BUSY_TICKS


def test_a_boss_probe_is_not_waited_out_for_the_full_cap():
    """A wrong candidate must rotate faster than 2400 ticks when it is near."""
    mem = memory()
    near = march_arrival_limit(mem, {"source": "オケアナ", "target": "ボス"})
    far = march_arrival_limit(mem, {"source": "アルマムーン", "target": "ボス"})
    assert 1200 <= near < 2400          # 203 cells with the 2x detour factor
    assert far == 2400                  # a long boss route keeps the cap
    # Every limit stays under the reconciliation window.
    assert max(near, far) <= 6 * SORTIE_BUSY_TICKS
