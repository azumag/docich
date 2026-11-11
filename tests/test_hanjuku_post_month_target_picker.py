"""g1163 2026-11-11: the monthly scan must not hold an open sortie target picker.

A battle ended at 08:50:57 while シャルドネ's target picker was open (the Y
jump to ハヤク had just been confirmed). ``post_month_battle_resume`` returned
the monthly scan to 'manifest', which answered [] on map_target, so the target
was never decided; each month rollover recorded launched_unconfirmed and the
same picker came back (08:53, 08:58, 09:02, 09:09) for twenty minutes.
"""
from unittest.mock import patch

import pytest

from docich.hanjuku.flows import post_month_status as post
from docich.hanjuku.os_layer.screen import Screen


def _mem(stage):
    mem = {'chapter': 9, 'month': '16-9', 'tick': 27700, 'gold': 2600, '_records': []}
    post.arm(mem)
    mem['post_month_status']['stage'] = stage
    return mem


@pytest.mark.parametrize('stage', ['manifest', 'await_map', 'open_menu', 'task_menu', 'roster', 'process'])
def test_scan_releases_the_target_picker(stage):
    mem = _mem(stage)
    assert post.step(Screen([], None, '', kind='map_target'), mem, None) is None
    assert mem['post_month_status']['stage'] == stage   # the scan resumes on the next map


def test_repair_wait_keeps_the_picker_for_the_house_flow():
    mem = _mem('wait_repair')
    mem['house'] = {'phase': 'out_target'}
    with patch.object(post.house, 'step', return_value=['house-owned']) as house_step:
        assert post.step(Screen([], None, '', kind='map_target'), mem, None) == ['house-owned']
    house_step.assert_called_once()


def test_recall_wait_keeps_the_picker_for_the_recall_flow():
    mem = _mem('wait_recall')
    mem.setdefault('flow', {})['recall'] = {'stage': 'dest'}
    with patch.object(post.recall_flow, 'camp_recall_step', return_value=['recall-owned']) as recall_step:
        assert post.step(Screen([], None, '', kind='map_target'), mem, None) == ['recall-owned']
    recall_step.assert_called_once()


def test_manifest_still_opens_x_on_the_field_map():
    mem = _mem('manifest')
    assert post.step(Screen([], None, '', kind='map'), mem, None) is not None
