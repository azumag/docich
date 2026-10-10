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


def test_confirmed_home_anchor_survives_a_missing_roof_observation():
    from dataclasses import replace
    from docich.flyhome.tracker import Tracker
    from docich.flyhome.image import Image
    img = read_png(FIXTURES / 'flyhome-level38-home-overlap.png')
    first = vision.analyze(img, Settings(), prev_player=(262.054, 82.337), prev_angle=-.077)
    tracker = Tracker()
    assert tracker.known_home_bbox is None
    tracker.update(first, 0)
    assert tracker.known_home_bbox == (243, 101, 285, 132)
    # Static partial-capture fixture: character and HOME untouched, roof unavailable.
    partial = Image(img.width, img.height, bytearray(img.data))
    partial.fill_rect(243, 101, 285, 133, (0, 0, 0))
    obs = vision.analyze(partial, Settings(), prev_player=tracker.prev_pos,
                         prev_angle=tracker.prev_angle, known_home_bbox=tracker.known_home_bbox)
    assert obs.player is not None
    assert obs.home_text_overlap
    assert -.2 < obs.player.angle < 0
    tracker.update(replace(obs, home=None, home_bbox=None), .05)
    assert tracker.known_home_bbox == (243, 101, 285, 132)
    assert Tracker().known_home_bbox is None  # A new level starts with a fresh anchor.


def test_actual_split_roof_does_not_disable_home_overlap_heading_guard():
    img = read_png(FIXTURES / 'flyhome-level38-home-split-roof.png')
    previous = (262.9, 86)
    uncached = vision.analyze(img, Settings(), prev_player=previous, prev_angle=.152)
    assert uncached.home_bbox == (243, 101, 264, 132)
    assert not uncached.home_text_overlap
    assert uncached.player.angle > 2.9
    cached = vision.analyze(img, Settings(), prev_player=previous, prev_angle=.152,
                            known_home_bbox=(243, 101, 285, 132))
    assert cached.home_text_overlap
    assert -.2 < cached.player.angle < 0
