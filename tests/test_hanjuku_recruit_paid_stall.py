"""g662 (2026-10-11 02:41-02:46): a paid recruit was aborted on an unread
header and the trio's introduction took 109 B presses until the watchdog."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import hanjuku_policy as policy
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas


def text_frame(gold=None, lines=('「おめにかかれて こうえいです。',)):
    c = Canvas()
    if gold is not None:
        c.text(72, 15, f'1ねん 10のつき {gold}G')
    for i, line in enumerate(lines):
        c.text(24, 151 + 16 * i, line)
    return parse(c.frame())


def recruit_memory(**sub):
    return {'month': '1-10', 'chapter': 1,
            'shop': {'key': '1-10', 'recruit': 'opened', 'egg': 'not_needed', 'items': [],
                     'soldiers': 0},
            'month_sub': {'kind': 'recruit', 'gold_before': 144, 'presses': 3, 'key': '1-10',
                          'left_menu': True, **sub}}


def test_unread_header_after_payment_does_not_abort():
    mem = recruit_memory()
    screen = text_frame(None, ('それではこうこくをうたせていただきます。',))
    assert screen.kind == 'text'
    assert policy.month_sub_step(screen, mem) == [policy.pad('a')]
    assert not mem['month_sub'].get('aborted')
    assert mem['shop']['recruit'] == 'opened'


def test_a_paid_introduction_past_the_bound_still_gets_a_bounded_advance():
    mem = recruit_memory()
    buttons = [policy.month_sub_step(text_frame(94), mem)[0]['buttons'][0] for _ in range(40)]
    assert buttons[:5] == ['a'] * 5          # 8-press bound (3 used before)
    assert 'a' in buttons[20:]               # B is ignored on plain text -> A again


def test_hotloaded_g662_state_recovers_from_the_abort():
    mem = recruit_memory(aborted=True, presses=206)
    mem['shop']['recruit'] = 'deferred_cash'
    assert policy.month_sub_step(text_frame(94), mem) == [policy.pad('a')]
    assert not mem['month_sub'].get('aborted')
    assert mem['shop']['recruit'] == 'opened'
    assert any(r['decision'] == 'recruit_paid_receipt' for r in mem['_records'])


def test_unread_gold_never_confirms_a_payment():
    mem = recruit_memory()
    c = Canvas()
    c.text(24, 183, '50Gかかりますが よろしいでしょうか?')
    c.text(184, 183, 'うむッ!'); c.text(184, 199, 'いかんッ!')
    c.hand(162, 177)
    assert policy.month_sub_step(parse(c.frame()), mem) == []


def test_ignored_backs_on_plain_text_are_escalated_to_a_bounded_advance():
    mem = recruit_memory(aborted=True)
    unpaid = text_frame(144, ('ほかの はなし。',))
    actions = [policy.month_sub_step(unpaid, mem)[0]['buttons'][0] for _ in range(14)]
    assert actions[:12] == ['b'] * 12
    assert actions[12:] == ['b', 'a'] or actions[12:] == ['a', 'b']
    assert any(r['decision'] == 'month_sub_abort_text_advance' for r in mem['_records'])


def test_abort_never_escalates_on_a_yes_no():
    mem = recruit_memory(aborted=True)
    c = Canvas()
    c.text(72, 15, '1ねん 10のつき 144G')
    c.text(24, 183, 'よろしいでしょうか?')
    c.text(184, 183, 'うむッ!'); c.text(184, 199, 'いかんッ!')
    c.hand(162, 177)
    screen = parse(c.frame())
    assert all(policy.month_sub_step(screen, mem) == [policy.pad('b')] for _ in range(40))
