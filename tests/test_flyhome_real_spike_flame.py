"""Regression from an actual Windows capture with a jet touching a stage 41 spike."""
from pathlib import Path
from docich.flyhome import vision
from docich.flyhome.image import read_png
from docich.flyhome.settings import Settings


def test_actual_jet_does_not_raise_the_lower_spike_boundary():
    image = read_png(Path(__file__).parent/'fixtures'/'flyhome-level41-before-lower-spike.png')
    obs = vision.analyze(image, Settings(), prev_player=(145.924,125.337),prev_angle=0)
    assert obs.player is not None and obs.player.area == 92
    assert (147,135,157,145) in obs.hazards  # Stable 217-red tile; hot flame at y134 is separate.
    assert (147,134,157,145) not in obs.hazards
    assert obs.player.flame_left
