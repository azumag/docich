"""A relay-aimed camp recall outlives the retreat/anonymous recheck (#4533).

g1190 2026-11-18 10:01:30-31: relay_from_picker re-aimed camp (140,112) at the
relay cell, and one observation later the camp recheck deferred and popped
`flow.recall`, so the camp menu never came back to choose いどう.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich.hanjuku.flows import camp_recheck as c
from docich.hanjuku.state.regions import flow as _flow


def memory(relay):
    recall = {'stage': 'menu', 'target': [140, 112], 'camp_world': [638, 348]}
    if relay:
        recall.update(relay={'cell': [508, 264], 'hub': 'タタール'},
                      move_visible={'cell': [508, 264], 'relay': True})
    mem = {'chapter': 11, 'tick': 100, '_records': [],
           'retreat_rechecks': {'どうし': {'identity': 'x', 'chapter': 11, 'tick': 1,
                                           'attempts': 0, 'retry_after': 0,
                                           'status': 'needs_observation'}},
           'recall': recall}
    return mem


def test_the_defer_keeps_a_relay_aimed_recall():
    mem = memory(relay=True)
    recall = mem['recall']
    c._defer(mem, {'general': 'どうし'}, 'unconfirmed')
    assert _flow(mem).get('recall') is recall


def test_the_defer_still_clears_an_ordinary_recall():
    mem = memory(relay=False)
    c._defer(mem, {'general': 'どうし'}, 'unconfirmed')
    assert _flow(mem).get('recall') is None
