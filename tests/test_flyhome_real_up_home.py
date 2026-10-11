"""Off-screen HOME waypoint from the actual Windows stage 50 start."""
from pathlib import Path
from docich.flyhome import planner, vision
from docich.flyhome.image import read_png

FIXTURES = Path(__file__).parent / 'fixtures'


def test_actual_upward_home_arrow_provides_a_route_without_inventing_a_house():
    obs = vision.analyze(read_png(FIXTURES / 'flyhome-level50-up-home.png'))
    assert obs.home is None and obs.home_bbox is None
    assert obs.home_exit_up == (152, 24)
    assert not obs.wipe
    assert obs.player is not None
    path = planner.astar(planner.build_grid(obs), (obs.player.x, obs.player.y), obs.home_exit_up)
    assert path and path[-1] == (152, 24)
    assert path[-1][1] < obs.player.y - 100


def test_actual_visible_house_does_not_activate_the_viewport_waypoint():
    obs = vision.analyze(read_png(FIXTURES / 'flyhome-level49-timer-spike.png'))
    assert obs.home == (200, 90.6)
    assert obs.home_exit_up is None

def test_actual_up_arrow_animation_keeps_the_same_waypoint():
    obs = vision.analyze(read_png(FIXTURES / 'flyhome-level50-up-home-bob.png'))
    assert obs.home is None and obs.home_bbox is None
    assert obs.home_exit_up == (152, 24)

def test_actual_danger_text_is_not_a_spike_and_small_tiles_are_not_a_house():
    obs = vision.analyze(read_png(FIXTURES / 'flyhome-level50-danger-text.png'))
    # Four actual dark-red tiles enter at the bottom; pure-red warning glyphs stay at the top.
    assert set(obs.hazards) == {(27,174,37,180), (107,174,117,180),
                                (187,174,197,180), (267,174,277,180)}
    assert obs.home is None and obs.home_bbox is None
    assert not obs.wipe

def test_actual_warning_arrows_allow_an_early_side_step_without_blocking_the_text():
    obs = vision.analyze(read_png(FIXTURES / 'flyhome-level50-danger-text.png'))
    assert set(obs.warning_lanes) == {22, 92, 162, 232}
    target = planner.upward_warning_waypoint((152, 24), obs.warning_lanes)
    assert target == (144, 24)
    assert all(abs(target[0] - lane) >= 16 for lane in obs.warning_lanes)
    start = vision.analyze(read_png(FIXTURES / 'flyhome-level50-up-home.png'))
    assert planner.upward_warning_waypoint(start.home_exit_up, start.warning_lanes) == (152, 24)

def test_actual_warning_waypoint_replaces_a_clear_route_to_the_previous_goal():
    from docich.flyhome.route_worker import RouteWorker
    obs = vision.analyze(read_png(FIXTURES / 'flyhome-level50-danger-text.png'))
    pos = (obs.player.x, obs.player.y)
    old_path = planner.astar(planner.build_grid(obs), pos, (152, 24))
    worker = RouteWorker()
    try:
        result = worker._solve(1, obs, obs, pos, (144, 24), old_path, (4, 2, 3))
        assert result.path[-1] == (144, 24)
        assert not planner.path_blocked(result.grid, result.path)
    finally:
        worker.close()

def test_actual_initial_ascent_can_start_left_of_the_repeated_first_warning_column():
    obs = vision.analyze(read_png(FIXTURES / 'flyhome-level50-up-home.png'))
    target = (obs.home_exit_up[0] - 18, obs.home_exit_up[1])
    assert target == (134, 24)
    path = planner.astar(planner.build_grid(obs), (obs.player.x, obs.player.y), target)
    assert path and path[-1] == target
    # First actual warning columns from four trials; the early lane is already clear of them.
    assert planner.upward_warning_waypoint(target, (72, 152, 232)) == target