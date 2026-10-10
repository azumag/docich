from types import SimpleNamespace
from unittest.mock import patch
from docich.flyhome import win32, runner
from docich.flyhome.mpc import MpcController

def test_exe_selector_does_not_fall_back_to_discord_title():
    discord = win32.WindowInfo(1, 'Fly Me To The Home! 並走会', 'Discord', 9, 'C:/Discord.exe', (0,0,1,1))
    with patch.object(win32, 'list_windows', return_value=[discord]):
        assert win32.find_window(('Fly Me To The Home',), ('FlyMeToTheHome.exe',)) is None

def test_reused_game_handle_rejects_input():
    backend = runner.WindowsBackend.__new__(runner.WindowsBackend)
    backend.s = SimpleNamespace(input_mode='postmessage')
    backend.hwnd = 1
    backend.info = SimpleNamespace(pid=10, exe='game.exe')
    backend.w = SimpleNamespace(list_windows=lambda: [SimpleNamespace(hwnd=1, pid=11, exe='game.exe')])
    assert not backend.ready()

def test_sparse_path_first_turn_is_slowed():
    ctl = MpcController()
    ctl.path = [(20, 20), (52, 20), (52, 100)]
    ctl._cum = [0, 32, 112]
    ctl._corners = ctl._find_corners()
    assert ctl._corners == [32]
    samples = ctl._sample_path()
    assert samples[15][4] < ctl.cfg.v_ref

def test_spike_corridor_limits_reference_speed():
    ctl = MpcController()
    ctl.path = [(20, 20), (20, 100)]
    ctl._cum = [0, 80]
    ctl.hazards = [(4, 20, 10, 100), (30, 20, 36, 100)]
    samples = ctl._sample_path()
    assert max(p[4] for p in samples) < 15


def test_pending_jet_reversal_keeps_unapplied_first_input():
    import pytest
    ctl = MpcController()
    ctl.cfg.delay_s = 0.12
    ctl._acts.extend([(9.82, 1), (9.92, 2)])
    delay = ctl._pending_inputs(10.0)
    assert delay == pytest.approx(0.12)
    assert [a for _, a in ctl._pending] == [1, 2]
    assert [dt for dt, _ in ctl._pending] == pytest.approx([0.04, 0.08])
    ctl.reset()
    assert ctl._pending is None


def test_live_level39_dark_green_wall_edge_is_solid():
    from docich.flyhome import vision
    assert vision.classify_rgb(11, 38, 0) == vision.GRASS
    assert vision.classify_rgb(10, 10, 10) == vision.DARK


def test_distance_to_sparse_path_measures_segment_not_waypoint():
    from docich.flyhome import planner
    assert planner.path_distance([(20,20), (100,20)], (60,22)) == 2
