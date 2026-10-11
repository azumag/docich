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