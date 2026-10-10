"""Static images from actual Windows stage 38; no movement simulator."""
import json
import math
from pathlib import Path
from docich.flyhome import vision
from docich.flyhome.image import read_png
from docich.flyhome.settings import Settings

FIXTURES = Path(__file__).parent / 'fixtures'

def test_actual_home_letter_overlap_keeps_head_side_during_descent():
    img = read_png(FIXTURES / 'flyhome-level38-home-overlap.png')
    obs = vision.analyze(img, Settings(), prev_player=(262.054, 82.337), prev_angle=-.077)
    assert obs.player is not None
    assert obs.player.area == 86
    assert obs.home_bbox == (243, 101, 285, 132)
    assert -.2 < obs.player.angle < 0  # Old recognition: +3.046 rad.
    assert not obs.wipe


def test_actual_home_overlap_sequence_tracks_rotation_without_half_turn_jumps():
    root = FIXTURES / 'flyhome-level38-home-overlap'
    rows = [json.loads(line) for line in (root / 'observations.jsonl').read_text().splitlines()]
    prev_pos, prev_angle = rows[0]['prev'], rows[0]['prev_angle']
    for row in rows:
        obs = vision.analyze(read_png(root / row['file']), Settings(),
                             prev_player=prev_pos, prev_angle=prev_angle)
        assert obs.player is not None
        assert abs(math.remainder(obs.player.angle - prev_angle, math.tau)) < .3
        prev_pos = (obs.player.x, obs.player.y)
        prev_angle = obs.player.angle
    assert -.99 < prev_angle < -.93  # Actual later turn is followed, not frozen upright.


def test_home_overlap_preserves_an_already_inverted_head_side():
    obs = vision.analyze(read_png(FIXTURES / 'flyhome-level38-home-overlap.png'), Settings(),
                         prev_player=(262.054, 82.337), prev_angle=3.05)
    assert obs.player is not None
    assert 2.9 < obs.player.angle < math.pi  # No forced upright assumption.
