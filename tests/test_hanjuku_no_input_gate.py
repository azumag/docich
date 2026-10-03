"""保留 is never an end state: a frozen screen sends its phase's default input.

The gate counts only observations whose frame digest is one of the two most
recent distinct images -- the same evidence ``hanjuku_run.screen_stalled``
treats as one unchanged screen -- so fades, animations and marching units are
counted as progress, while a screen that simply sits there with no planned
input is not.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich.hanjuku_bot import BOT_VERSION, NO_INPUT_HOLD_MAX, classify, decide, pad
from docich.hanjuku_pixels import Frame

from test_hanjuku_chart_bot import Canvas, monster_menu_frame


def flat(color):
    return Frame(256, 224, bytes(color) * (256 * 224))


FIELD = flat((40, 140, 20))       # grass: classify -> field, parse -> unknown
DARK = flat((0, 0, 0))            # classify -> transition


def red_border_menu():
    """A map menu identified by its stable red border (sortie/roster/castle)."""
    rgb = bytearray(bytes((40, 140, 20)) * (256 * 224))
    for y in (16, 17):
        for x in range(35, 110):
            i = (y * 256 + x) * 3
            rgb[i:i + 3] = bytes((240, 50, 50))
    return Frame(256, 224, bytes(rgb))


def unreadable_name():
    """The name grid's greens and left gutter with no readable characters."""
    rgb = bytearray(bytes((20, 80, 60)) * (256 * 224))
    for y in range(80, 217):
        for x in range(8):
            i = (y * 256 + x) * 3
            rgb[i:i + 3] = bytes((0, 0, 0))
    return Frame(256, 224, bytes(rgb))


def field_text():
    c = Canvas((40, 140, 20))
    c.text(24, 40, 'いばらが きえた!')
    return c.frame()


def hold(frame, count, state=None):
    """Run ``count`` observations of one frozen screen; return actions and state."""
    state = {} if state is None else state
    seen = []
    for _ in range(count):
        actions, state = decide(frame, state)
        seen.append(actions)
    return seen, state


def test_a_frozen_bare_map_waits_the_bounded_turns_then_recentres():
    seen, state = hold(FIELD, NO_INPUT_HOLD_MAX)
    assert all(actions == [] for actions in seen)
    assert state['no_input_streak'] == NO_INPUT_HOLD_MAX
    assert classify(FIELD) == 'field'

    actions, state = decide(FIELD, state)
    # SELECT recentres the cursor on the hero; a blind A would open a sortie
    # menu at an unknown cell.
    assert actions == [pad('select')]
    assert state['no_input_streak'] == 0
    fallback = [r for r in state['_records'] if r['decision'] == 'no_input_fallback']
    assert len(fallback) == 1
    assert (fallback[0]['phase'], fallback[0]['screen']) == ('field', 'unknown')
    assert fallback[0]['observed_metric']['frozen_observations'] > NO_INPUT_HOLD_MAX
    # The recentre is treated as camera motion we did not measure.
    assert state['policy']['nav_last'] is None or 'nav_last' not in state['policy']
    assert state['policy']['uncertain'] is True


def test_a_redrawing_screen_is_never_counted_as_a_hold():
    seq = [FIELD, flat((20, 140, 60)), DARK]
    state = {}
    for i in range(2 * NO_INPUT_HOLD_MAX):
        actions, state = decide(seq[i % 3], state)
        assert actions == []
    assert state['no_input_streak'] == 1


def test_a_two_image_blink_counts_as_one_frozen_screen():
    # g358: a menu whose cursor blinks alternates two images forever, and
    # screen_stalled already treats that as unchanged. A gate that demanded a
    # single digest would restart on every flip and never fire on exactly the
    # menus that hold forever.
    state = {}
    for i in range(NO_INPUT_HOLD_MAX):
        actions, state = decide((FIELD, DARK)[i % 2], state)
        assert actions == []
    assert state['no_input_streak'] >= NO_INPUT_HOLD_MAX - 1
    for _ in range(3):
        actions, state = decide(FIELD, state)
        if actions:
            break
    assert actions == [pad('select')]


def test_the_black_transition_still_waits_on_its_own_first_frozen_turn():
    # Restates the script contract: a green run followed by one black frame
    # must still hold, because the digest change restarts the count.
    seen, state = hold(FIELD, NO_INPUT_HOLD_MAX)
    assert all(actions == [] for actions in seen)
    actions, state = decide(DARK, state)
    assert actions == []
    assert state['no_input_streak'] == 1
    # ...and only a further full bounded wait gets input, as confirm here.
    _, state = hold(DARK, NO_INPUT_HOLD_MAX - 1, state)
    actions, state = decide(DARK, state)
    assert actions == [pad('a')]


def test_a_frozen_map_menu_backs_out_instead_of_confirming():
    frame = red_border_menu()
    assert classify(frame) == 'field_menu'
    seen, state = hold(frame, NO_INPUT_HOLD_MAX)
    assert all(actions == [] for actions in seen)
    actions, state = decide(frame, state)
    assert actions == [pad('b')]
    assert state['_records'][-1]['decision'] == 'no_input_fallback'
    assert state['_records'][-1]['phase'] == 'field_menu'


def test_a_frozen_unreadable_name_screen_never_types_or_confirms():
    frame = unreadable_name()
    assert classify(frame) == 'name'
    seen, state = hold(frame, NO_INPUT_HOLD_MAX + 1)
    assert all(actions == [] for actions in seen[:-1])
    assert seen[-1] == [pad('b')]          # delete only; never A or START
    decisions = [r['decision'] for r in state['_records']]
    assert 'name_wait' in decisions and 'no_input_fallback' in decisions
    assert state['_records'][-1]['phase'] == 'name'


def test_a_message_over_a_field_is_acknowledged_not_recentred():
    frame = field_text()
    assert classify(frame) == 'field'
    seen, state = hold(frame, NO_INPUT_HOLD_MAX + 1)
    assert all(actions == [] for actions in seen[:-1])
    assert seen[-1] == [pad('a')]


def test_the_bounded_enemy_menu_wait_still_finishes_on_its_own():
    # MONSTER_MENU_HOLD_LIMIT waits for the enemy AI and then sends A on the
    # same frame. The gate must not preempt that bounded wait.
    frame = monster_menu_frame(['ダイナマイト', 'ミサイルくん'],
                               ally=('どうし', 90), enemy=('セクシーボンバー', 77),
                               cursor=0)
    state = {'policy': {'chapter': 1}}
    for _ in range(30):
        actions, state = decide(frame, state)
        assert actions == []
    actions, state = decide(frame, state)
    assert actions == [pad('a')]
    assert not [r for r in state['_records'] if r['decision'] == 'no_input_fallback']
    assert state['no_input_streak'] == 0


def test_the_bound_sits_above_every_bounded_wait_and_below_the_stall_terminal():
    from docich import hanjuku_house as house
    from docich import hanjuku_policy as policy
    from docich import hanjuku_run as run
    finite = (policy.RECALL_LIMIT, policy.EGG_RITUAL_LIMIT, policy.RARE_SCAN_LIMIT,
              policy.RECRUIT_CANDIDATE_LIMIT, policy.MONSTER_MENU_HOLD_LIMIT,
              house.STEP_LIMIT)
    assert NO_INPUT_HOLD_MAX > max(finite)
    # 100 observations at the normal 1500 ms interval is 150 s: the fallback is
    # always sent before hanjuku_run ends the run at STALL_SECONDS.
    assert NO_INPUT_HOLD_MAX * 1.5 < run.STALL_SECONDS


def test_the_gate_keeps_its_bookkeeping_small_and_versioned():
    _, state = hold(FIELD, NO_INPUT_HOLD_MAX + 1)
    assert state['bot_version'] == BOT_VERSION
    assert state['no_input_streak'] == 0
    assert 0 < len(state['no_input_frames']) <= 2
    assert all(isinstance(d, str) for d in state['no_input_frames'])
