from dataclasses import replace
from pathlib import Path
from docich.flyhome import vision
from docich.flyhome.image import Image, read_png
from docich.flyhome.tracker import Tracker, PLAYING, DEAD, CLEARED


def recorded_death():
    image = read_png(Path(__file__).parent / 'fixtures/flyhome-level37-dead-prompt.png')
    return vision.analyze(image, prev_player=(170.636,164.273), prev_angle=-1.092)


def test_actual_level37_dead_prompt_matches_but_plain_background_does_not():
    obs = recorded_death()
    assert obs.control_prompt
    assert not vision._control_prompt_present(Image(320,180,bytes(320*180*3)))


def test_frozen_actual_death_prompt_rejects_a_visible_fragment_and_stays_dead():
    obs = recorded_death()
    assert obs.player is not None and obs.player.area == 44  # actual corpse pixels from the PNG
    tracker = Tracker()
    assert tracker.update(obs,0).phase == PLAYING
    assert tracker.update(obs,3.9).phase == PLAYING
    assert tracker.update(obs,4.1).phase == DEAD
    assert tracker.update(obs,4.5).phase == DEAD
    # The fresh game clock and player allow the next attempt to begin.
    assert tracker.update(replace(obs,timer_sig=obs.timer_sig+1),5).phase == PLAYING


def test_start_prompt_and_running_clock_are_not_death():
    obs = recorded_death()
    body = vision.Player(87.924,43.337,0,(84,34,93,53),90)
    obs = replace(obs,player=body)
    tracker = Tracker()
    assert tracker.update(obs,0).phase == PLAYING
    assert tracker.update(obs,5).phase == PLAYING  # no clock movement yet, full initial body
    for t in (6,7,12):
        assert tracker.update(replace(obs,timer_sig=obs.timer_sig+int(t)),t).phase == PLAYING


def test_party_has_priority_over_frozen_prompt_and_home_wait_needs_no_prompt():
    obs = recorded_death()
    tracker = Tracker()
    assert tracker.update(obs,0).phase == PLAYING
    assert tracker.update(replace(obs,wipe=True),5).phase == CLEARED
    tracker = Tracker()
    obs = replace(obs, control_prompt=False)
    assert tracker.update(obs,0).phase == PLAYING
    assert tracker.update(obs,5).phase == PLAYING  # static clock alone cannot kill a visible body


def test_original_positional_wipe_argument_keeps_its_clear_meaning():
    obs = recorded_death()
    positional = vision.Observation(obs.width,obs.height,obs.player,obs.home,obs.home_bbox,
                                    obs.hazards,obs.coins,obs.hud_left,obs.hud_right,
                                    obs.timer_sig,obs.level_sig,obs.solid,obs.labels,True)
    assert positional.wipe
    assert not positional.control_prompt
