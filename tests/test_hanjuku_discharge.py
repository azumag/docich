from docich import hanjuku_policy as policy
from docich.hanjuku_screen import Screen


def test_forced_discharge_initializes_counter_without_month_header():
    screen = Screen(lines=[], hand=None, text='どのしょうぐんをかいこに', selected='ミント', kind='discharge_menu')
    mem = {}
    actions = policy.discharge_step(screen, mem)
    assert actions == [policy.pad('a')]
    assert mem['discharge'] == {'key': None, 'presses': 1}
    assert mem['_records'][-1]['observed_metric']['month'] is None
