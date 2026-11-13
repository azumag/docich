"""A scene that cuts a camp recall keeps the intent for the next observation.

g1163 2026-11-12 03:5x-04:1x: six recall attempts an hour, none completed —
every one was cut by the month's start scene while it was choosing a
destination, and the recall was dropped, so the next attempt began from
finding the camp again and hit the next month's end just as often.
"""
from docich.hanjuku import orchestrator as O


def memory(resumes=0):
    recall = {'stage': 'dest', 'steps': 4, 'target': [175, 84], 'goal': 'アルマムーン',
              'input_sent': True, 'scene_resumes': resumes}
    return {'tick': 100, 'month': '24-9', 'flow': {'recall': dict(recall)},
            '_records': []}


def interrupt(mem):
    """Run the orchestrator's own interruption helper."""
    return O._recall_after_scene(mem, mem['flow']['recall'], 'text', 'concert')


def test_the_first_interruption_keeps_the_recall_on_the_map():
    mem = memory()
    recall = interrupt(mem)
    assert recall is not None
    assert mem['flow']['recall']['stage'] == 'to_camp'
    assert mem['flow']['recall']['resumed_after_scene'] is True
    # the closed picker's evidence never survives
    assert 'input_sent' not in mem['flow']['recall']


def test_the_second_interruption_still_resumes():
    mem = memory(resumes=1)
    assert interrupt(mem) is not None


def test_a_recall_cut_three_times_is_skipped_instead_of_looping():
    mem = memory(resumes=2)
    assert interrupt(mem) is None
    assert 'recall' not in mem['flow']
    abandoned = next(r for r in mem['_records'] if r['decision'] == 'camp_recall_scene_abandoned')
    assert abandoned['observed_metric']['stage'] == 'dest'
    assert abandoned['observed_metric']['goal'] == 'アルマムーン'
    assert abandoned['observed_metric']['target'] == [175, 84]
