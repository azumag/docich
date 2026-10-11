"""Hazards behind HUD text in an actual Windows stage 49 capture."""
from pathlib import Path
from docich.flyhome import vision
from docich.flyhome.image import read_png


def test_timer_text_does_not_mask_the_visible_dark_spike_beneath_it():
    image = read_png(Path(__file__).parent / 'fixtures' / 'flyhome-level49-timer-spike.png')
    obs = vision.analyze(image, prev_player=(295.306, 24.242), prev_angle=.174)
    # Actual timer overlaps this tile; its 140-red pixels are still visible.
    assert (275, 7, 285, 17) in obs.hazards
    # Bottom input HUD used to crop this actual tile's lower edge at y157.
    assert (275, 151, 285, 161) in obs.hazards
    assert (275, 151, 285, 157) not in obs.hazards
    assert obs.home_bbox == (179, 69, 221, 100)
    assert obs.player is not None and obs.player.area == 94
    assert -.34 < obs.player.angle < -.31  # Full body is clear of the HUD; follow its actual turn.
    assert not obs.wipe

def test_player_touching_the_timer_mask_boundary_keeps_the_hud_guard():
    image = read_png(Path(__file__).parent / 'fixtures' / 'flyhome-level49-timer-boundary.png')
    obs = vision.analyze(image, prev_player=(286.924, 25.337), prev_angle=.174)
    assert obs.player is not None
    assert obs.player.bbox[1] == 17  # Exactly at the timer mask's lower boundary.
    assert obs.player.angle == .174