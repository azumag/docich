"""Bot-side entry point for the hanjuku-speedrun RTA records.

``observe`` is called from the generated speedrun bot as an ordinary side
effect, next to ``persist`` and ``month_audit``.  It must never change bot
input or raise: every failure is swallowed here and the bot's existing side
effect wrapper prints one stderr line from the exception type it receives.
(The wrapper cannot print anything when this module swallows the error, so
the caller's label is the only trace; that is deliberate -- the bot must
never drop an observation because a record write failed.)
"""
from __future__ import annotations

import os
from pathlib import Path
import time

from . import store, timer as timer_mod

GAME = timer_mod.GAME

_TERMINAL_REASONS = frozenset({
    'game_over', 'game_clear', 'screen_stalled', 'input_stalled',
})


def observe(runtime, records, state, meta, *, screenshot):
    """Record one observation.  Returns the resulting timer or ``None``.

    ``records`` is the bot's decision-record list for this observation (the
    same list handed to ``persist``), ``state`` is the policy memory and
    ``meta`` is the observation meta JSON.  ``screenshot`` is the image path
    whose mtime is the observation time when available.
    """
    try:
        return _observe(runtime, records, state, meta, screenshot=screenshot)
    except Exception:
        # Recording is a side channel.  The caller's loop already labels a
        # failed effect, and no input depends on this return value.
        return None


def _observation_ms(screenshot, run, meta):
    """Epoch milliseconds for this observation, preferring the image mtime."""
    candidates = []
    try:
        stat = os.stat(screenshot)
    except (OSError, TypeError, ValueError):
        stat = None
    if stat is not None and stat.st_mtime > 0:
        candidates.append(int(stat.st_mtime * 1000))
    for source in (run, meta):
        if not isinstance(source, dict):
            continue
        for key in ('observed_at', 'observed_at_ms', 'ts'):
            value = source.get(key)
            if isinstance(value, (int, float)) and value > 0:
                if key.endswith('_ms'):
                    candidates.append(int(value))
                else:
                    candidates.append(int(value * 1000))
    if candidates:
        return candidates[0]
    return int(time.time() * 1000)


def _runtime_id(runtime, run, meta):
    identity = meta.get('hanjuku') if isinstance(meta, dict) else None
    for source in (run, identity, meta):
        if isinstance(source, dict):
            value = source.get('runtime_id')
            if isinstance(value, str) and value:
                return value
    try:
        return Path(runtime).name
    except TypeError:
        return ''


def _bot_version(run, state):
    for source in (run, state):
        if isinstance(source, dict):
            value = source.get('bot_version')
            if isinstance(value, str) and value:
                return value
    return None


def _chart_edition(state):
    if not isinstance(state, dict):
        return None
    policy = state.get('policy')
    if isinstance(policy, dict):
        value = policy.get('chart_edition')
        if isinstance(value, str) and value:
            return value
    value = state.get('chart_edition')
    return value if isinstance(value, str) and value else None


def _name_screen_seen(run, state, records):
    """The earliest reliable new-game signal available in this observation.

    ``hanjuku_run.json`` sets ``name_entered`` on the first image classified
    as the name-entry screen (``hanjuku_run.observe``), which is earlier than
    the bot's ``name_confirm`` record.  The fallbacks keep the hook usable in
    a synthetic test or after a run-state read failure.
    """
    if isinstance(run, dict) and run.get('name_entered') is True:
        return True
    if isinstance(run, dict) and run.get('phase') == 'name':
        return True
    if isinstance(state, dict) and isinstance(state.get('name'), dict):
        return True
    for record in records if isinstance(records, list) else ():
        if not isinstance(record, dict):
            continue
        if record.get('chart_step') == 'name':
            return True
        if record.get('decision') in {
                'name_wait', 'name_type', 'name_confirm', 'name_delete'}:
            return True
    return False


def _chapter(record, state, *, fallback_key=None):
    if fallback_key is not None:
        metric = record.get('observed_metric')
        if isinstance(metric, dict):
            value = metric.get(fallback_key)
            if type(value) is int:
                return value
    value = record.get('chapter')
    if type(value) is int:
        return value
    metric = record.get('observed_metric')
    if isinstance(metric, dict) and type(metric.get('chapter')) is int:
        return metric['chapter']
    if isinstance(state, dict):
        policy = state.get('policy')
        if isinstance(policy, dict) and type(policy.get('chapter')) is int:
            return policy['chapter']
        if type(state.get('chapter')) is int:
            return state['chapter']
    return None


def _signals(records, state):
    """Yield ``(end_index, source)`` for measured chapter transitions.

    ``chapter_end_save`` is the primary signal.  ``chapter_seen`` with the
    ``chapter_end_save_party`` evidence is the fallback when the save prompt
    was missed; it names the *new* chapter, so the chapter that ended is one
    less.
    """
    for record in records if isinstance(records, list) else ():
        if not isinstance(record, dict):
            continue
        decision = record.get('decision')
        variant = record.get('strategy_variant')
        if (decision in {'prompt', 'yes_no_forced_answer'}
                and variant == 'chapter_end_save'):
            chapter = _chapter(record, state)
            if chapter is not None and 1 <= chapter <= 11:
                yield chapter - 1, 'chapter_end_save'
        elif decision == 'chapter_seen':
            metric = record.get('observed_metric')
            evidence = metric.get('evidence') if isinstance(metric, dict) else None
            if evidence != 'chapter_end_save_party':
                continue
            chapter = _chapter(record, state, fallback_key='chapter')
            if chapter is not None and 2 <= chapter <= 12:
                yield chapter - 2, 'fallback'


def _new_name_screen(run, timer):
    """True when a name screen appears after this runtime already played.

    ``name_entered`` stays true for the whole run, so it cannot distinguish a
    restart.  The run state's current phase plus ``gameplay_seen`` (or a
    running attempt that already confirmed a chapter) can: the game is back
    at name entry although gameplay had started.
    """
    if not isinstance(run, dict) or run.get('phase') != 'name':
        return False
    if run.get('gameplay_seen') is True:
        return True
    current = timer.get('current_index') if isinstance(timer, dict) else None
    return type(current) is int and current > 0


def _commit(runtime, timer, records, *, save_records, final=False):
    if save_records:
        store.save_records(runtime, records)
    if final:
        store.append_attempt(runtime, store.attempt_row(
            timer, at_ms=timer.get('ended_at_ms')))
    store.save_timer(runtime, timer)


def _observe(runtime, records, state, meta, *, screenshot):
    runtime = Path(runtime)
    # The bot already validates this, but recording must never create a data
    # directory for an arbitrary path passed in by a test or a future caller.
    if runtime.parent.name != 'runtimes' or not runtime.parent.parent.is_dir():
        return None
    state = state if isinstance(state, dict) else {}
    meta = meta if isinstance(meta, dict) else {}
    observations = records if isinstance(records, list) else []

    run = {}
    try:
        run = store.load_run(runtime)
    except Exception:
        run = {}
    meta_run = meta.get('hanjuku')
    if isinstance(meta_run, dict):
        run = {**run, **meta_run}

    timer = store.load_timer(runtime)
    stored = store.load_records(runtime)
    runtime_id = _runtime_id(runtime, run, meta)
    observed = _observation_ms(screenshot, run, meta)
    same_runtime = bool(isinstance(timer, dict)
                        and timer.get('runtime_id') == runtime_id)
    if same_runtime:
        floor = 0
        for key in ('heartbeat_at_ms', 'started_at_ms'):
            value = timer.get(key)
            if type(value) is int:
                floor = max(floor, value)
        observed = max(observed, floor)

    terminal = meta.get('terminal_reason')
    if not isinstance(terminal, str) or not terminal:
        terminal = run.get('terminal_reason')
    if not isinstance(terminal, str) or not terminal:
        terminal = None
    clear_ending = run.get('clear_ending_seen') is True
    name_seen = _name_screen_seen(run, state, observations)

    if not isinstance(timer, dict) or timer.get('game') != GAME:
        timer = timer_mod.new_timer(runtime_id, stored, observed)
        same_runtime = True

    # A previous runtime that never got its finalize call (VM restart,
    # crash, operator stop without cleanup) is closed before a new attempt.
    if (timer.get('status') == 'running' and not same_runtime):
        # The old attempt ended at its last observation, not at the new
        # runtime's first frame.
        recovery_now = timer.get('heartbeat_at_ms')
        if type(recovery_now) is not int:
            recovery_now = observed
        finalized = store.finalize_attempt(
            runtime, terminal_reason='stopped', now_ms=recovery_now,
            reason='recovered')
        if isinstance(finalized, dict):
            timer = finalized
        # The recovered attempt may have updated PB/gold records.
        stored = store.load_records(runtime)

    same_runtime = timer.get('runtime_id') == runtime_id
    status = timer.get('status')
    recovered_same = False

    # The same runtime generation can be restarted (bot/VM crash) and show a
    # new name screen after gameplay.  Close the stale attempt before it.
    if status == 'running' and same_runtime and _new_name_screen(run, timer):
        recovery_now = timer.get('heartbeat_at_ms')
        if type(recovery_now) is not int:
            recovery_now = observed
        finalized = store.finalize_attempt(
            runtime, terminal_reason='stopped', now_ms=recovery_now,
            reason='recovered')
        if isinstance(finalized, dict) and finalized.get('status') in {
                'finished', 'dnf'}:
            timer = finalized
            stored = store.load_records(runtime)
            status = timer.get('status')
            recovered_same = True

    if status == 'running':
        changed_records = False
        for end_index, source in _signals(observations, state):
            before = len(timer.get('splits') or [])
            previous_records = stored
            candidate, updated = timer_mod.confirm_split(
                timer, stored, observed, end_index, source)
            if len(candidate.get('splits') or []) == before:
                continue
            timer, stored = candidate, updated
            changed_records = updated != previous_records
            break  # one transition per observation; repeats are ignored
        if clear_ending or terminal == 'game_clear':
            timer, updated = timer_mod.finish(timer, stored, observed)
            stored = updated
            _commit(runtime, timer, stored, save_records=True, final=True)
            return timer
        if terminal in _TERMINAL_REASONS:
            timer, updated = timer_mod.finish_or_dnf(
                timer, stored, observed, terminal, reason=terminal)
            # A split measured in the same observation may have advanced a
            # segment best; save it before the attempt row is written.
            _commit(runtime, timer, updated, save_records=changed_records,
                    final=True)
            return timer
        if not changed_records:
            timer = timer_mod.heartbeat(timer, observed)
            _commit(runtime, timer, stored, save_records=False)
        else:
            _commit(runtime, timer, stored, save_records=True)
        return timer

    start_here = (
        status == 'pending' or not same_runtime or recovered_same
        or (status in {'finished', 'dnf'} and run.get('phase') == 'name')
    )
    if start_here:
        if name_seen and not clear_ending and terminal not in _TERMINAL_REASONS:
            # start_attempt copies the timer, so give it the new runtime id
            # when the previous attempt belonged to an older generation.
            timer = {**timer, 'runtime_id': runtime_id}
            timer, stored = timer_mod.start_attempt(
                timer, stored, observed,
                attempt_no=stored.get('next_attempt_no'),
                bot_version=_bot_version(run, state),
                chart_edition=_chart_edition(state))
            _commit(runtime, timer, stored, save_records=True)
            return timer
        if status == 'pending':
            if timer.get('runtime_id') != runtime_id:
                timer = {**timer, 'runtime_id': runtime_id,
                         'comparison': timer_mod.comparison(stored)}
            timer = timer_mod.heartbeat(timer, observed)
            _commit(runtime, timer, stored, save_records=False)
            return timer

    # Finished/DNF for this runtime: a later teardown observation must not
    # start a second attempt on the same generation.
    return timer
