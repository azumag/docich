"""Stage 1 camp HP probe: flag OFF is inert, ON reads HP only while idle, and yields to retreats."""
import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.flows import camp_recheck
from docich.hanjuku_screen import Screen, parse
from test_hanjuku_chart_bot import _camp_frame, camp_menu
from test_hanjuku_house import status

ID = {'game': 'hanjuku-hero', 'runtime_id': 'probe-test', 'generation': 1, 'lease_id': 'lease-test'}
MAP = Screen([], None, '', kind='map', cursor=(26, 66))
FRAME = _camp_frame()
TARGET = list(camp_recheck._camp_under_cursor(MAP, FRAME))


def memory(actor='ゼウス', reads=0):
    mem = {'chapter': 1, 'tick': 100, '_run_identity': dict(ID), '_records': []}
    queue = {actor: {'identity': ID, 'chapter': 1, 'tick': 90, 'attempts': 1, 'retry_after': 90,
                     'status': 'arrival_unconfirmed', 'camp_observation': {'target': TARGET}}}
    mem['_queue'] = queue
    if reads:
        mem['camp_hp_probe_counts'] = {f'{actor}:90': reads}
    return mem


@pytest.fixture
def probe_on(monkeypatch):
    monkeypatch.setattr(camp_recheck, 'CAMP_HP_PROBE_ENABLED', True)
    monkeypatch.setattr(camp_recheck, '_scope', lambda mem: mem['_queue'])


def status_screen(name='ゼウス', hp=40):
    screen = parse(status(name, hp=hp, max_hp=85))
    screen.kind = 'text'
    return screen


def test_flag_off_never_acts(monkeypatch):
    monkeypatch.setattr(camp_recheck, 'CAMP_HP_PROBE_ENABLED', False)
    monkeypatch.setattr(camp_recheck, '_scope', lambda mem: mem['_queue'])
    mem = memory()
    assert camp_recheck.camp_hp_probe_step(MAP, mem, FRAME) is None
    assert 'camp_hp_probe' not in mem and not mem.get('camp_hp_probe_counts')


def test_idle_camp_starts_and_reads_hp(probe_on):
    mem = memory()
    assert camp_recheck.camp_hp_probe_step(MAP, mem, FRAME) == [p.pad('a')]
    assert mem['camp_hp_probe']['stage'] == 'menu'
    assert camp_recheck.camp_hp_probe_step(camp_menu(1), mem, FRAME) == [p.pad('a')]
    assert camp_recheck.camp_hp_probe_step(status_screen(hp=40), mem, FRAME) == [p.pad('b')]
    records = [r for r in mem['_records'] if r['decision'] == 'camp_hp_observation']
    assert len(records) == 1 and records[0]['observed_metric']['hp'] == 40
    assert camp_recheck.camp_hp_probe_step(camp_menu(1), mem, FRAME) == [p.pad('b')]
    assert camp_recheck.camp_hp_probe_step(MAP, mem, FRAME) is None
    assert 'camp_hp_probe' not in mem


def test_unexpected_screen_drops_probe_without_input(probe_on):
    mem = memory()
    assert camp_recheck.camp_hp_probe_step(MAP, mem, FRAME) == [p.pad('a')]
    assert camp_recheck.camp_hp_probe_step(MAP, mem, FRAME) is None  # camp menu never opened
    assert 'camp_hp_probe' not in mem
    assert not [r for r in mem['_records'] if r['decision'] == 'camp_hp_observation']


def test_due_retreat_recheck_has_priority(probe_on):
    mem = memory()
    mem['_queue']['ゼウス'].update(status='needs_observation', retry_after=100, attempts=0)
    assert camp_recheck.camp_hp_probe_step(MAP, mem, FRAME) is None
    assert 'camp_hp_probe' not in mem


def test_read_limit_is_respected(probe_on):
    mem = memory(reads=camp_recheck.CAMP_HP_PROBE_LIMIT)
    assert camp_recheck.camp_hp_probe_step(MAP, mem, FRAME) is None
    assert 'camp_hp_probe' not in mem


def test_active_recall_is_never_touched(probe_on):
    mem = memory()
    mem['recall'] = {'stage': 'menu', 'general': 'ゼウス'}
    assert camp_recheck.camp_hp_probe_step(MAP, mem, FRAME) is None


def test_wrong_general_in_status_is_not_recorded(probe_on):
    mem = memory()
    camp_recheck.camp_hp_probe_step(MAP, mem, FRAME)
    camp_recheck.camp_hp_probe_step(camp_menu(1), mem, FRAME)
    assert camp_recheck.camp_hp_probe_step(status_screen(name='ヴィーナス'), mem, FRAME) is None
    assert not [r for r in mem['_records'] if r['decision'] == 'camp_hp_observation']


def test_due_recheck_mid_probe_takes_the_frame(probe_on):
    mem = memory()
    assert camp_recheck.camp_hp_probe_step(MAP, mem, FRAME) == [p.pad('a')]
    mem['_queue']['ゼウス'].update(status='needs_observation', retry_after=100, attempts=0)
    assert camp_recheck.camp_hp_probe_step(camp_menu(1), mem, FRAME) is None
    assert 'camp_hp_probe' not in mem


def test_aborted_probe_is_not_retried_in_same_camp(probe_on):
    mem = memory()
    assert camp_recheck.camp_hp_probe_step(MAP, mem, FRAME) == [p.pad('a')]
    assert camp_recheck.camp_hp_probe_step(MAP, mem, FRAME) is None  # aborted: camp menu never opened
    assert camp_recheck.camp_hp_probe_step(MAP, mem, FRAME) is None  # no second start in the same camp
    assert 'camp_hp_probe' not in mem
