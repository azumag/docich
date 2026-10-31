"""Bounded named re-observation after a planned retreat, without inferred arrival.

The roster/SELECT/status route reuses the measured broken-egg repair UI.
No game/provider I/O lives here. Only the existing recall sender owns dispatch.
"""
from ..os_layer import roster as roster_receipts
from ...hanjuku_egg_stock import observed_uses

RETRY_TICKS = 30
ATTEMPT_LIMIT = 3
READ_LIMIT = 3
STATUS_CLOSE_READ_LIMIT = 4
ARRIVAL_READ_LIMIT = 3
ARRIVAL_RETRY_TICKS = 120


def _clean_name(name):
    return (isinstance(name, str) and 1 <= len(name) <= 32
            and '\ufffd' not in name and not any(ch.isspace() for ch in name))


_ALLOWED_KINDS = {
    'recheck_roster_open': {'main_menu', 'map', 'unknown'},
    'recheck_pick': {'text', 'main_menu', 'unknown'},
    'recheck_roster_read': {'text', 'unknown'},
    'recheck_focus': {'map', 'text', 'unknown'},
    'recheck_status': {'text', 'map', 'unknown'},
    'recheck_read': {'text', 'unknown'},
    'recheck_close': {'text', 'unknown'},
    'menu': {'text', 'unknown'},
    'dest': {'world_map', 'map_target', 'map', 'text', 'unknown'},
    'await_dispatch': {'world_map', 'map_target', 'map', 'text', 'unknown'},
}


def _new_owner(state):
    return isinstance(state, dict) and ('retreat_recheck' in state or 'anonymous_recheck' in state)


def _owner_shape(mem, state):
    stage, actor = state.get('stage'), state.get('general')
    identity = roster_receipts.run_identity(mem.get('_run_identity'))
    now, chapter = mem.get('tick'), mem.get('chapter')
    if (not isinstance(stage, str) or stage not in _ALLOWED_KINDS
            or type(state.get('steps')) is not int or state['steps'] < 0
            or type(now) is not int or now < 0 or type(chapter) is not int or chapter <= 0
            or (stage == 'recheck_close' and 'reads' not in state)
            or identity is None or any(type(state[key]) is not bool for key in
                                      ('retreat_recheck', 'anonymous_recheck', 'arrival_check_only') if key in state)
            or not (state.get('retreat_recheck') or state.get('anonymous_recheck'))):
        return False
    if any(key in state and (type(state[key]) is not int or not 0 <= state[key] <= limit)
           for key, limit in (('reads', STATUS_CLOSE_READ_LIMIT if stage == 'recheck_close' else READ_LIMIT),
                              ('scrolls', roster_receipts.MAX_GENERALS))):
        return False
    if state.get('retreat_recheck'):
        return (_clean_name(actor) and roster_receipts.run_identity(state.get('identity')) == identity
                and type(state.get('chapter')) is int and state['chapter'] == chapter
                and type(state.get('recheck_tick')) is int and 0 <= state['recheck_tick'] <= now)
    return (stage in ('recheck_status', 'recheck_read', 'recheck_close', 'menu', 'dest', 'await_dispatch')
            and (_clean_name(actor) or actor is None and stage in ('recheck_status', 'recheck_read'))
            and roster_receipts.run_identity(state.get('anonymous_identity')) == identity
            and type(state.get('anonymous_chapter')) is int and state['anonymous_chapter'] == chapter)


def owns_dialog(screen, mem):
    state = mem.get('recall') or {}
    return (_new_owner(state) and _owner_shape(mem, state) and isinstance(screen.kind, str)
            and screen.kind in _ALLOWED_KINDS[state['stage']])


def _new_episode(mem):
    identity, chapter, now = (roster_receipts.run_identity(mem.get('_run_identity')),
                              mem.get('chapter'), mem.get('tick'))
    if identity is None or type(chapter) is not int or chapter <= 0 or type(now) is not int or now < 0:
        return None
    old = mem.get('retreat_camp_episode') or {}
    serial = (old.get('serial') if isinstance(old, dict) and old.get('identity') == identity
              and type(old.get('chapter')) is int and old['chapter'] == chapter
              and type(old.get('serial')) is int and old['serial'] > 0 else 0)
    episode = {'identity': identity, 'chapter': chapter, 'serial': serial + 1, 'started_tick': now}
    mem['retreat_camp_episode'] = dict(episode)
    return episode


def _valid_episode(mem, state):
    episode, current = state.get('camp_episode'), mem.get('retreat_camp_episode')
    identity = roster_receipts.run_identity(mem.get('_run_identity'))
    now, chapter = mem.get('tick'), mem.get('chapter')
    return (identity is not None and isinstance(episode, dict) and isinstance(current, dict)
            and roster_receipts.run_identity(episode.get('identity')) == identity
            and roster_receipts.run_identity(current.get('identity')) == identity
            and type(chapter) is int and chapter > 0
            and type(episode.get('chapter')) is int and episode['chapter'] == chapter
            and type(current.get('chapter')) is int and current['chapter'] == chapter
            and type(episode.get('serial')) is int and episode['serial'] > 0
            and type(current.get('serial')) is int and current['serial'] == episode['serial']
            and type(now) is int and type(episode.get('started_tick')) is int
            and type(current.get('started_tick')) is int
            and current['started_tick'] == episode['started_tick']
            and 0 <= episode['started_tick'] <= now)


def _camp_shape(proof):
    return (isinstance(proof, dict) and type(proof.get('schema')) is int and proof['schema'] == 1
            and roster_receipts.run_identity(proof.get('identity')) is not None
            and type(proof.get('chapter')) is int and proof['chapter'] > 0
            and type(proof.get('episode')) is int and proof['episode'] > 0
            and type(proof.get('episode_tick')) is int and type(proof.get('observed_tick')) is int
            and 0 <= proof['episode_tick'] <= proof['observed_tick']
            and proof.get('origin') in ('named_focus', 'anonymous_map_open')
            and isinstance(proof.get('target'), list) and len(proof['target']) == 2
            and all(type(value) is int for value in proof['target'])
            and isinstance(proof.get('frame_sha256'), str) and len(proof['frame_sha256']) == 64
            and all(ch in '0123456789abcdef' for ch in proof['frame_sha256'])
            and isinstance(proof.get('roofs'), list))


def _valid_camp(mem, state):
    from .recall import RECALL_LIMIT
    if not isinstance(state, dict):
        return False
    proof = state.get('camp_observation')
    if not _camp_shape(proof) or not _valid_episode(mem, state):
        return False
    episode = state['camp_episode']
    return (roster_receipts.run_identity(proof['identity']) == episode['identity']
            and proof['chapter'] == episode['chapter'] and proof['episode'] == episode['serial']
            and proof['episode_tick'] == episode['started_tick']
            and proof['target'] == state.get('target')
            and 0 <= mem['tick'] - proof['observed_tick'] <= RECALL_LIMIT)


def _observe_camp(screen, mem, frame, state, origin):
    from .recall import RECALL_ARRIVE_PX
    from ..os_layer.readers import _cursor
    from ..os_layer.readers import castle_roofs, own_camps
    if screen.kind != 'map' or frame is None or not _valid_episode(mem, state):
        return None
    cursor = _cursor(screen)
    under = [camp for camp in own_camps(frame) if not camp.get('clipped') and cursor
             and all(abs(camp['target'][axis] - cursor[axis]) <= RECALL_ARRIVE_PX for axis in (0, 1))]
    if len(under) != 1 or list(under[0]['target']) != state.get('target'):
        return None
    roofs = sorted([r['kind'], *r['target']] for r in castle_roofs(frame))
    episode = state['camp_episode']
    return {'schema': 1, 'identity': episode['identity'], 'chapter': episode['chapter'],
            'episode': episode['serial'], 'episode_tick': episode['started_tick'],
            'observed_tick': mem['tick'], 'origin': origin, 'target': list(under[0]['target']),
            'frame_sha256': frame.digest(), 'roofs': roofs if len(roofs) >= 2 else []}


def _valid_status(mem, state):
    from .recall import RECALL_LIMIT
    proof = state.get('named_status')
    if not _valid_camp(mem, state) or not isinstance(proof, dict):
        return False
    camp, episode = state['camp_observation'], state['camp_episode']
    return (type(proof.get('schema')) is int and proof['schema'] == 1
            and _clean_name(proof.get('general')) and proof['general'] == state.get('general')
            and type(proof.get('hp')) is int and proof['hp'] > 0
            and roster_receipts.run_identity(proof.get('identity')) == episode['identity']
            and type(proof.get('chapter')) is int and proof['chapter'] == episode['chapter']
            and type(proof.get('episode')) is int and proof['episode'] == episode['serial']
            and proof.get('camp_frame_sha256') == camp['frame_sha256']
            and type(proof.get('camp_observed_tick')) is int and proof['camp_observed_tick'] == camp['observed_tick']
            and type(proof.get('tick')) is int and camp['observed_tick'] <= proof['tick'] <= mem['tick']
            and mem['tick'] - proof['tick'] <= RECALL_LIMIT)


def _scope(mem):
    identity = roster_receipts.run_identity(mem.get('_run_identity'))
    queue, valid = mem.get('retreat_rechecks'), {}
    chapter, now = mem.get('chapter'), mem.get('tick')
    statuses = {'needs_observation', 'dispatch_unconfirmed', 'arrival_unconfirmed',
                'anonymous_recheck_exhausted'}
    if (identity is not None and type(chapter) is int and chapter > 0
            and type(now) is int and now >= 0 and isinstance(queue, dict)):
        for name, row in queue.items():
            if len(valid) >= roster_receipts.MAX_GENERALS:
                break
            if (not _clean_name(name) or not isinstance(row, dict)
                    or roster_receipts.run_identity(row.get('identity')) != identity
                    or type(row.get('chapter')) is not int or row['chapter'] != chapter
                    or any(type(row.get(key)) is not int or row[key] < 0
                           for key in ('tick', 'retry_after', 'attempts'))
                    or row['tick'] > now or row['retry_after'] < row['tick']
                    or row['attempts'] > ATTEMPT_LIMIT or not isinstance(row.get('status'), str)
                    or row['status'] not in statuses):
                continue
            clean = {**row, 'identity': identity}
            observed = row.get('camp_observation')
            if observed is not None and not _camp_shape(observed):
                clean.pop('camp_observation', None)  # discard malformed suppression, never restore facts
            valid[name] = clean
    if valid:
        mem['retreat_rechecks'] = valid
    else:
        mem.pop('retreat_rechecks', None)
        mem.pop('retreat_anonymous_budget', None)
    return valid


def _invalidate(mem, reason, actor=None, *, new_game=False):
    from ..decision_log import _record
    queue = _scope(mem)
    if new_game:
        mem.pop('camp_status_seen', None)
    raw_state = mem.get('recall')
    state = raw_state if isinstance(raw_state, dict) else {}
    affected = bool(queue) if actor is None else actor in queue
    if actor is None:
        mem.pop('retreat_rechecks', None)
    else:
        queue.pop(actor, None)
        if not queue:
            mem.pop('retreat_rechecks', None)
    if affected or actor is None:
        mem.pop('retreat_anonymous_budget', None)
    drop_ui = (bool(raw_state) and (new_game or not isinstance(raw_state, dict)
                                  or state.get('retreat_recheck') or state.get('anonymous_recheck'))
               and (actor is None or state.get('general') in (None, actor)))
    if drop_ui:
        mem.pop('recall', None)
    if affected or drop_ui:
        _record(mem, 'retreat_recheck_invalidated', chart_step=None, general=actor,
                resulting_event='intent_invalidated_location_unconfirmed', reason=reason)


def observe_departure(mem, actor, started_tick):
    """A newly observed departure ends this actor's completed camp episode.

    Call only after receipt, never from a plan or the A-side en_route cache.
    An old pending departure cannot replenish a later camp search budget.
    """
    from ..decision_log import _record
    identity = roster_receipts.run_identity(mem.get('_run_identity'))
    chapter, now = mem.get('chapter'), mem.get('tick')
    seen = mem.get('camp_status_seen')
    if (not _clean_name(actor) or identity is None or type(chapter) is not int or chapter <= 0
            or type(now) is not int or type(started_tick) is not int
            or not isinstance(seen, dict)
            or seen.get('scope') != {'identity': identity, 'chapter': chapter}
            or not isinstance(seen.get('actors'), dict)):
        return
    old = seen['actors'].get(actor)
    if (not isinstance(old, dict) or type(old.get('tick')) is not int
            or not 0 <= old['tick'] < started_tick <= now):
        return
    _invalidate(mem, '本人の新しい出発を観測したため前任務の在営再確認を終了', actor)
    seen['actors'].pop(actor)
    verification = mem.get('recall_verification') or {}
    if (isinstance(verification, dict) and verification.get('general') == actor
            and roster_receipts.run_identity(verification.get('identity')) == identity
            and verification.get('chapter') == chapter):
        verification['status'] = 'superseded_by_departure'
    _record(mem, 'camp_status_episode_departed', general=actor,
            observed_metric={'started_tick': started_tick},
            reason='本人の新しい出発を実観測。前任務と同じHP・卵でも次の在営を再確認できる')


def departure_requested(mem, actor, started_tick):
    """The target A arms a short observation, not a new camp episode yet."""
    seen = mem.get('camp_status_seen') or {}
    scope = {'identity': roster_receipts.run_identity(mem.get('_run_identity')), 'chapter': mem.get('chapter')}
    actors = seen.get('actors') if isinstance(seen, dict) and seen.get('scope') == scope else None
    old = actors.get(actor) if isinstance(actors, dict) and _clean_name(actor) else None
    now = mem.get('tick')
    if (isinstance(old, dict) and type(old.get('tick')) is int and type(now) is int
            and type(started_tick) is int and 0 <= old['tick'] < started_tick <= now):
        old['departure_pending'] = {'tick': started_tick, 'requested_tick': now, 'reads': 0}


def observe_departure_return(screen, mem):
    """A target marker must disappear into the field after the requested A.

    Three unknown fade readings are allowed. An unchanged picker or another
    foreground never proves departure. This receipt creates no arrival facts.
    """
    seen = mem.get('camp_status_seen') or {}
    scope = {'identity': roster_receipts.run_identity(mem.get('_run_identity')), 'chapter': mem.get('chapter')}
    actors = seen.get('actors') if isinstance(seen, dict) and seen.get('scope') == scope else None
    if scope['identity'] is None or not isinstance(actors, dict):
        return
    for actor, old in list(actors.items()):
        pending = old.pop('departure_pending', None) if isinstance(old, dict) else None
        if (not isinstance(pending, dict) or type(pending.get('reads')) is not int
                or not 0 <= pending['reads'] <= 3 or type(pending.get('requested_tick')) is not int
                or type(mem.get('tick')) is not int or not 0 <= mem['tick'] - pending['requested_tick'] <= 4):
            continue
        if screen.kind == 'map' and not mem.get('battle'):
            observe_departure(mem, actor, pending.get('tick'))
        elif screen.kind == 'unknown' and pending['reads'] < 3:
            old['departure_pending'] = {**pending, 'reads': pending['reads'] + 1}


def observe_camp_status(screen, mem):
    """A current named camp status requests SELECT-based discovery, not a location.

    Repeated status pixels never refresh a pending intent or its budgets.
    Keep a bounded actor episode after completion too. Only a new observed
    departure releases it; HP/egg changes alone do not replenish any budget.
    """
    from . import house
    from ..decision_log import _record
    if screen.kind != 'text' or mem.get('battle'):
        return
    info = house.general_status(screen)
    identity = roster_receipts.run_identity(mem.get('_run_identity'))
    now, chapter = mem.get('tick'), mem.get('chapter')
    header_chapter = (screen.header or {}).get('chapter')
    main_panel = info and any(r.y == 39 and r.span(24, 88).strip() == info['general']
                             and r.span(88, 136) == 'しょうぐん' for r in screen.lines)
    if (not main_panel or identity is None
            or type(now) is not int or now < 0 or type(chapter) is not int or chapter <= 0
            or type(header_chapter) is not int or header_chapter != chapter
            or not _clean_name(info['general'])):
        return
    actor = info['general']
    owner = mem.get('recall') or mem.get('house') or {}
    if isinstance(owner, dict) and owner.get('general') not in (None, actor):
        return  # another actor's requested status cannot adopt this page
    house.observe_hero_repair_status(info, mem)
    if type(info.get('hp')) is not int or info['hp'] <= 0:
        return
    scope = {'identity': identity, 'chapter': chapter}
    seen = mem.get('camp_status_seen')
    if not isinstance(seen, dict) or seen.get('scope') != scope:
        if info.get('camp_observed') is not True:
            return
        seen = mem['camp_status_seen'] = {'scope': scope, 'actors': {}}
    actors = seen.get('actors')
    if not isinstance(actors, dict):
        return  # malformed saved suppression must not reset into permission
    old = actors.get(actor)
    location = next((r.span(24, 232).replace(' ', '') for r in screen.lines if r.y == 151), None)
    if old is not None and location == 'しろのなかにいます':
        # Upgrade old list-shaped saved signatures only through a new receipt.
        if isinstance(old, list):
            old = actors[actor] = {'signature': old, 'tick': now}
        if isinstance(old, dict) and type(old.get('tick')) is int and old['tick'] <= now:
            old['castle_tick'] = now
    elif isinstance(old, dict) and location == 'いどうしています':
        castle_tick = old.get('castle_tick')
        if (type(castle_tick) is int and type(old.get('tick')) is int
                and 0 <= old['tick'] <= castle_tick < now):
            observe_departure(mem, actor, now)
    if info.get('camp_observed') is not True:
        return
    queue = _scope(mem)
    if actor not in queue and len(queue) >= roster_receipts.MAX_GENERALS:
        return
    if actor in actors or len(actors) >= roster_receipts.MAX_GENERALS:
        return
    actors[actor] = {'signature': [info[key] for key in ('hp', 'max_hp', 'egg', 'uses', 'broken')],
                     'tick': now}
    if actor in queue:
        return
    verification = mem.get('recall_verification') or {}
    if (isinstance(verification, dict) and verification.get('general') == actor
            and verification.get('input_sent') is True
            and verification.get('status') in ('arrival_unconfirmed', 'dispatch_unconfirmed')
            and roster_receipts.run_identity(verification.get('identity')) == identity
            and type(verification.get('chapter')) is int and verification['chapter'] == chapter):
        return
    queue[actor] = {'identity': identity, 'chapter': chapter, 'tick': now, 'attempts': 0,
                    'retry_after': now, 'status': 'needs_observation', 'trigger': 'observed_camp_status'}
    mem['retreat_rechecks'] = queue
    _record(mem, 'camp_status_recall_pending', general=actor,
            resulting_event='camp_observed_position_unconfirmed',
            reason='本人ステータスの在営を確認。位置を推定せず実名簿の本人SELECTから現在tentを探す')


def capture(mem, battle):
    """A selected retreat and surviving named actor justify checking, not camp facts."""
    from ..decision_log import _record
    actor, hp = battle.get('ally'), battle.get('ally_hp')
    if _clean_name(actor) and type(hp) is int and hp == 0:
        _invalidate(mem, '本人の後続HP0観測で古い退却intentを無効化。死亡や所在は断定しない', actor)
        return
    selected = (battle.get('hero_retreat') or {}).get('selected')
    identity = roster_receipts.run_identity(mem.get('_run_identity'))
    if (battle.get('side') != 'attack' or type(selected) is not int or selected <= 0
            or type(hp) is not int or hp <= 0 or not _clean_name(actor)
            or identity is None or type(mem.get('chapter')) is not int or mem['chapter'] <= 0
            or type(mem.get('tick')) is not int):
        return
    queue = _scope(mem)
    if actor not in queue and len(queue) >= roster_receipts.MAX_GENERALS:
        return
    queue[actor] = {'identity': identity, 'chapter': mem['chapter'], 'tick': mem['tick'],
                    'attempts': 0, 'retry_after': mem['tick'], 'status': 'needs_observation',
                    'trigger': 'planned_retreat_selection'}
    mem['retreat_rechecks'] = queue
    _record(mem, 'retreat_recheck_pending', chart_step=None, general=actor,
            resulting_event='location_unconfirmed',
            reason='生存する本人の退却選択を記録し、野営化や帰還を断定せず所在を再確認する')


def _defer(mem, state, reason, *, status='needs_observation'):
    from ..decision_log import _record
    actor = state.get('general') if isinstance(state, dict) and _clean_name(state.get('general')) else None
    row = _scope(mem).get(actor)
    if row is not None:
        row.update(status=status, retry_after=mem['tick'] + RETRY_TICKS)
        if state.get('arrival_check_only'):
            row.update(status='arrival_unconfirmed', retry_after=mem['tick'] + ARRIVAL_RETRY_TICKS)
        if _valid_camp(mem, state):
            row['camp_observation'] = state['camp_observation']
    mem.pop('recall', None)
    mem['uncertain'] = True
    _record(mem, 'retreat_recheck_unconfirmed', chart_step=None, general=actor,
            resulting_event=status, reason=reason)


def _wait(mem, state, reason, *, close=False):
    from ..os_layer.commands import pad
    state['reads'] = state.get('reads', 0) + 1
    if state['reads'] < READ_LIMIT:
        return []
    _defer(mem, state, reason)
    return [pad('b')] if close else []


def _advance(state, stage):
    state.update(stage=stage, steps=0, reads=0)


def _observe_egg(mem, info):
    """Store only a verified named panel's real quantity; never derive a zero."""
    from ..decision_log import _record
    actor, uses, egg = info['general'], info.get('uses'), info.get('egg')
    if observed_uses(uses) is None or not _clean_name(egg):
        return
    counts, types = mem.setdefault('egg_uses', {}), mem.setdefault('egg_types', {})
    changed = counts.get(actor) != uses or types.get(actor) != egg
    counts[actor], types[actor] = uses, egg
    if isinstance(mem.get('egg_recheck'), list):
        mem['egg_recheck'] = [name for name in mem['egg_recheck'] if name != actor]
    if changed:
        # Do not leave an older complete roster's egg row silently reusable.
        roster_receipts.dirty_status(mem, [actor], fields=('egg',))
        _record(mem, 'egg_uses_seen', chart_step=None, general=actor, egg=egg,
                observed_metric=uses, reason='本人と現在tentのfield statusで実卵数量を確認。消費や回復は推測しない')


def _named_step(screen, mem, frame, state):
    from ..decision_log import _record
    from .recall import RECALL_ARRIVE_PX, RECALL_LIMIT, _observe_recall_origin
    from ..os_layer.commands import menu_to, pad
    from ..os_layer.readers import _cursor, is_camp_menu
    from ..os_layer.readers import own_camps
    from ..os_layer.world import NAME
    from . import house
    stage, actor = state['stage'], state.get('general')
    if stage in ('recheck_roster_open', 'recheck_pick', 'recheck_roster_read', 'recheck_focus') and not _valid_episode(mem, state):
        _defer(mem, state, '本人focusのUI episodeを確認できず旧状態では操作しない')
        return [pad('b')] if screen.kind in ('text', 'main_menu', 'world_map', 'map_target') else []
    if stage in ('recheck_status', 'recheck_read') and not _valid_camp(mem, state):
        _defer(mem, state, '同run・同UI episodeの現在tent receiptを確認できず数量や帰還を更新しない')
        return [pad('b')] if screen.kind in ('text', 'world_map', 'map_target') else []
    if screen.kind == 'unknown':
        return _wait(mem, state, '未知前景では旧背景のmenuを選択しない')
    state['steps'] = state['steps'] + 1
    if state['steps'] > RECALL_LIMIT:
        _defer(mem, state, '本人再確認の観測上限に達したため古いUI位置では操作しない')
        return [pad('b')] if screen.kind in ('main_menu', 'text', 'world_map', 'map_target') else []
    if stage == 'recheck_roster_open':
        if screen.kind == 'main_menu':
            move = menu_to(screen, 'しょうぐん')
            if move is not None:
                if move == 'here':
                    _advance(state, 'recheck_pick')
                return [pad('a')] if move == 'here' else [move]
        return _wait(mem, state, '将軍メニューの実カーソルを確認できない', close=screen.kind == 'main_menu')
    if stage == 'recheck_pick':
        names = house.roster(screen)
        if names is None:
            return _wait(mem, state, '本人選択の名簿または実カーソルを確認できない',
                         close='Aボタンでステータスひょうじ' in screen.text)
        if actor not in names:
            state['scrolls'] = state.get('scrolls', 0) + 1
            if state['scrolls'] > roster_receipts.MAX_GENERALS:
                _defer(mem, state, '有限の名簿確認で本人が見つからない')
                return [pad('b')]
            return [pad('down')]
        move = menu_to(screen, actor)
        if move == 'here':
            if state.get('arrival_check_only'):
                _advance(state, 'recheck_roster_read')
                return [pad('a')]  # current named status, not SELECT/camp inference
            _advance(state, 'recheck_focus')
            return [pad('select')]  # roster SELECT focuses the selected named general
        return [move] if move else _wait(mem, state, '本人の実カーソルを確認できない', close=True)
    if stage == 'recheck_roster_read':
        info = house.general_status(screen)
        main_panel = (info is not None and any(line.y == 39
            and line.span(24, 88).strip() == actor
            and line.span(88, 136) == 'しょうぐん' for line in screen.lines))
        if not main_panel or info['general'] != actor:
            return _wait(mem, state, '帰還後の本人の現名簿ステータスが未確認', close=True)
        if type(info.get('hp')) is not int or info['hp'] < 0:
            return _wait(mem, state, '帰還後の本人HPが未確認', close=True)
        if info['hp'] == 0:
            _invalidate(mem, '帰還後再確認で本人HP0を観測。死亡や到着は断定しない', actor)
            return [pad('b')]
        if info.get('location') == 'castle' and info.get('location_observed') is True:
            observation = {'identity': state['identity'], 'chapter': state['chapter'],
                           'general': actor, 'hp': info['hp'], 'tick': mem['tick'],
                           'location': 'castle', 'destination_confirmed': False}
            verification = mem.get('recall_verification') or {}
            if verification.get('general') == actor:
                mem['recall_verification'] = {**verification, 'status': 'in_castle_observed',
                                              'observation': observation}
            _invalidate(mem, '本人の現在ステータスで城内を観測し再帰還のintentを終了', actor)
            _record(mem, 'recall_in_castle_observed', chart_step=None, general=actor,
                    observed_metric=observation, resulting_event='in_castle_destination_unconfirmed',
                    reason='本人の実ステータスで城内を確認。指定城到着や帰還成立とは断定せず再指示を終了')
            return [pad('b')]
        _defer(mem, state, '本人の城内を確認できず帰還成立とはしない。帰還Aは再送せず後で再観測',
               status='arrival_unconfirmed')
        return [pad('b')]
    if stage == 'recheck_focus':
        cursor = _cursor(screen)
        camps = own_camps(frame) if frame is not None and screen.kind == 'map' else []
        under = [camp for camp in camps if not camp.get('clipped') and cursor
                 and all(abs(camp['target'][axis] - cursor[axis]) <= RECALL_ARRIVE_PX
                         for axis in (0, 1))]
        if len(under) != 1:
            return _wait(mem, state, '本人focus後の現在tentとcursorが一致せず決定しない')
        state['target'] = list(under[0]['target'])
        _observe_recall_origin(screen, mem, frame, state)
        state['camp_observation'] = _observe_camp(screen, mem, frame, state, 'named_focus')
        if not _valid_camp(mem, state):
            _defer(mem, state, '現在tentと同episodeを束縛できず選択しない')
            return []
        _advance(state, 'recheck_status')
        return [pad('a')]
    if stage == 'recheck_status':
        if not is_camp_menu(screen):
            return _wait(mem, state, '本人の野営ステータスメニューを確認できない')
        move = menu_to(screen, 'ステータス')
        if move == 'here':
            _advance(state, 'recheck_read')
            return [pad('a')]
        return [move] if move else _wait(mem, state, 'ステータス項目の実カーソルを確認できない', close=True)
    if stage == 'recheck_read':
        info = house.general_status(screen)
        if info is None:
            return _wait(mem, state, '本人ステータスの読取りが未確認', close='しょうぐん' in screen.text)
        field_panel = any(line.y == 31 and line.span(16, 80).strip() == info['general']
                          and line.span(80, 128) == 'しょうぐん' for line in screen.lines)
        if not field_panel:
            _defer(mem, state, '校正済みの部隊field status構造を確認できず場所のfallbackを使わない')
            return [pad('b')]
        if state.get('hero') and info['general'] != NAME:
            _defer(mem, state, '主人公focusと実ステータスの本人が異なるため操作しない')
            return [pad('b')]
        if not state.get('anonymous_recheck') and info['general'] != actor:
            _defer(mem, state, '指定本人と実ステータスが異なるため数量や帰還を更新しない')
            return [pad('b')]
        house.observe_hero_repair_status(info, mem, in_camp=True)
        if info.get('location') != 'castle':
            _observe_egg(mem, info)  # a blocked return must not discard an actual zero
        if type(info.get('hp')) is int and info['hp'] == 0:
            _invalidate(mem, '現在tentの本人HP0を実観測し生存帰還intentを無効化。死亡や到着は断定しない', info['general'])
            return [pad('b')]
        if state.get('anonymous_recheck'):
            # A changed view does not identify a camp. Read its real occupant
            # before allowing it to bypass a named actor's exhausted budget.
            actor = state['general'] = info['general']
            row = _scope(mem).get(actor)
            if row is not None:
                sent = _arrival_request(mem, actor, row) is not None
                if sent or row['attempts'] >= ATTEMPT_LIMIT or mem['tick'] < row['retry_after']:
                    _defer(mem, state, '実本人が再確認待ちまたは予算上限のため帰還Aを再送しない',
                           status='arrival_unconfirmed' if sent else row['status'])
                    return [pad('b')]
                row['attempts'] += 1
                state.update(retreat_recheck=True, identity=row['identity'], chapter=row['chapter'],
                             recheck_tick=row['tick'])
        if (info['general'] != actor or type(info.get('hp')) is not int or info['hp'] <= 0
                or info.get('location') == 'castle'):
            _defer(mem, state, '本人・生存HP・観測した野営との整合を確認できず帰還を決定しない')
            return [pad('b')]
        camp, episode = state['camp_observation'], state['camp_episode']
        state['named_status'] = {'schema': 1, 'general': actor, 'hp': info['hp'], 'tick': mem['tick'],
                                 'identity': episode['identity'], 'chapter': episode['chapter'],
                                 'episode': episode['serial'], 'camp_frame_sha256': camp['frame_sha256'],
                                 'camp_observed_tick': camp['observed_tick']}
        _advance(state, 'recheck_close')
        _record(mem, 'retreat_recheck_status', chart_step=None, general=actor,
                observed_metric=state['named_status'],
                resulting_event='named_camp_observed',
                reason='現tentを選択した後の本人ステータスと生存HPを確認。回復や到着は未確認')
        return [pad('b')]
    return None


def _status_close_step(screen, mem, state):
    """B is a request; observe the real unit menu before choosing return."""
    from ..decision_log import _record
    from ..os_layer.commands import pad
    from ..os_layer.readers import is_camp_menu
    from . import house
    if not _valid_status(mem, state):
        panel = house.general_status(screen) if screen.kind == 'text' else None
        _defer(mem, state, 'status閉鎖待ちの本人・現在tent・episodeを確認できず帰還しない')
        if is_camp_menu(screen) or panel:
            return [pad('b')]
        return [] if screen.kind == 'unknown' else None
    if is_camp_menu(screen):
        _record(mem, 'retreat_recheck_status_closed', general=state['general'],
                observed_metric={'reads': state['reads']}, resulting_event='unit_menu_observed',
                reason='現在の部隊メニューを観測してstatus閉鎖を確認。帰還先や到着は未確認')
        _advance(state, 'menu')
        return None
    if screen.kind != 'unknown':
        info = house.general_status(screen)
        field_panel = info and any(line.y == 31 and line.span(16, 80).strip() == info['general']
                                   and line.span(80, 128) == 'しょうぐん' for line in screen.lines)
        if field_panel and info['general'] == state['general'] and type(info.get('hp')) is int and info['hp'] == 0:
            _invalidate(mem, 'status閉鎖待ちで本人HP0を観測。死亡や到着を断定せず帰還intentを終了', state['general'])
            return [pad('b')]
        if (not field_panel or info['general'] != state['general']
                or type(info.get('hp')) is not int or info['hp'] != state['named_status']['hp']):
            _defer(mem, state, '閉鎖待ち中に本人status以外を観測したため旧UIを再利用しない')
            return [pad('b')] if field_panel else None
    state['reads'] += 1
    if state['reads'] >= STATUS_CLOSE_READ_LIMIT:
        _defer(mem, state, 'status閉鎖を有限観測で確認できないため再確認予算を保って保留')
        return [pad('b')] if screen.kind in ('text', 'world_map', 'map_target') else []
    if state['reads'] == 2 and screen.kind == 'text':
        _record(mem, 'retreat_recheck_status_close_retry', general=state['general'],
                resulting_event='close_requested_not_observed',
                reason='同じ本人statusが残るためBを一度だけ再提案。閉鎖観測の上限は延ばさない')
        return [pad('b')]
    return []


def interrupt(screen, mem):
    """Invalidate named UI proof before another foreground can consume input."""
    if screen.kind == 'name_entry':
        _invalidate(mem, '新しい名前入力画面を確認し古い退却intentを新ゲームへ持ち越さない', new_game=True)
        return True
    if 'がはいかにくわわった!' in screen.text.replace('！', '!'):
        _invalidate(mem, '加入の実表示で名簿が変わったため古い退却intentを再利用しない')
        return True
    raw_state = mem.get('recall')
    if raw_state is not None and not isinstance(raw_state, dict):
        _defer(mem, {}, '再確認のactive state型が不正なため操作権限を破棄')
        return True
    if _new_owner(raw_state) and not _owner_shape(mem, raw_state):
        _defer(mem, raw_state, '再確認の本人・stage・tickの型またはscopeが不正なため操作権限を破棄')
        return True
    state = mem.get('recall') or {}
    if (state.get('retreat_recheck') or state.get('anonymous_recheck')) and (
            mem.get('battle') or mem.get('month_sub') or not owns_dialog(screen, mem)):
        _defer(mem, state, '戦闘や別メニューの割込みで本人UIを失ったため再観測へ戻す')
        return True
    return False


def _visible_camps(mem, frame, queue):
    from .recall import RECALL_ARRIVE_PX
    from ..os_layer.readers import castle_roofs, own_camps
    if not queue:
        return None  # leave the existing anonymous FSM and its read budget unchanged
    camps = own_camps(frame) if frame is not None else []
    if not camps:
        return camps
    roofs = sorted([r['kind'], *r['target']] for r in castle_roofs(frame))
    for actor, row in queue.items():
        observed = row.get('camp_observation') or {}
        target = observed.get('target')
        # No durable camp ID is inferred from a screen coordinate. Suppress
        # only the same unchanged image or a view with two unchanged roofs.
        same_view = (observed.get('frame_sha256') == frame.digest()
                     or bool(observed.get('roofs')) and observed['roofs'] == roofs)
        if target and same_view and (_arrival_request(mem, actor, row) is not None
                                     or mem['tick'] < row['retry_after'] or row['attempts'] >= ATTEMPT_LIMIT):
            camps = [camp for camp in camps if any(
                abs(camp['target'][axis] - target[axis]) > RECALL_ARRIVE_PX for axis in (0, 1))]
    return camps


def _anonymous_budget(mem, queue):
    key = {'identity': roster_receipts.run_identity(mem.get('_run_identity')), 'chapter': mem.get('chapter'),
           'pending': sorted([name, row['tick']] for name, row in queue.items())}
    budget = mem.get('retreat_anonymous_budget') or {}
    if not isinstance(budget, dict) or budget.get('key') != key:
        budget = mem['retreat_anonymous_budget'] = {'key': key, 'reads': 0}
    if type(budget.get('reads')) is not int or not 0 <= budget['reads'] <= roster_receipts.MAX_GENERALS:
        budget['reads'] = roster_receipts.MAX_GENERALS  # malformed budgets fail closed, never reset into permission
    return budget


def _arrival_request(mem, actor, row):
    """A scoped actual dispatch permits bounded reads, never another return A."""
    proof = row.get('arrival_request', mem.get('recall_verification'))
    identity = roster_receipts.run_identity(mem.get('_run_identity'))
    if (not isinstance(proof, dict) or identity is None
            or proof.get('input_sent') is not True or proof.get('status') != 'arrival_unconfirmed'
            or proof.get('general') != actor
            or roster_receipts.run_identity(proof.get('identity')) != identity
            or type(proof.get('chapter')) is not int or proof['chapter'] != mem.get('chapter')
            or type(proof.get('requested_tick')) is not int
            or not row['tick'] <= proof['requested_tick'] <= mem['tick']):
        return None
    trace = proof.get('request_trace')
    if (not isinstance(trace, dict) or roster_receipts.run_identity(trace) != identity
            or not isinstance(trace.get('decision_id'), str)
            or not trace['decision_id'].startswith(f"{identity['runtime_id']}:{identity['generation']}:")
            or not trace['decision_id'].rsplit(':', 1)[-1].isdigit()
            or not isinstance(trace.get('frame_sha256'), str) or len(trace['frame_sha256']) != 64
            or any(c not in '0123456789abcdef' for c in trace['frame_sha256'])):
        return None
    return {k: proof[k] for k in ('identity', 'chapter', 'general', 'input_sent',
                                 'status', 'requested_tick', 'request_trace')}


def step(screen, mem, frame, existing):
    """Keep named observation retries separate from the anonymous camp cooldown."""
    from .map import world_map_step
    from ..os_layer.commands import pad
    from ..os_layer.readers import is_camp_menu
    if interrupt(screen, mem):
        return [] if mem.get('battle') and screen.kind == 'map' else None
    probe = camp_hp_probe_step(screen, mem, frame)
    if probe is not None:
        return probe
    queue = _scope(mem)
    state = mem.get('recall') or {}
    named = bool(state.get('retreat_recheck'))
    if named and (state.get('general') not in queue
                  or state.get('identity') != queue[state['general']]['identity']
                  or state.get('chapter') != queue[state['general']]['chapter']
                  or state.get('recheck_tick') != queue[state['general']]['tick']):
        mem.pop('recall', None)
        return [pad('b')] if (is_camp_menu(screen) or screen.kind in ('world_map', 'map_target')) else None
    if not state and screen.kind == 'map' and frame is not None and not any(
            mem.get(key) for key in ('battle', 'month_sub', 'house', 'y_jump', 'sortie_attempt', 'attack')):
        arrival_actor = next((name for name, row in queue.items()
            if _arrival_request(mem, name, row) is not None
            and type(row.get('arrival_reads', 0)) is int
            and 0 <= row.get('arrival_reads', 0) < ARRIVAL_READ_LIMIT
            and mem['tick'] >= row['retry_after']), None)
        actor = arrival_actor or next((name for name, row in queue.items() if row['attempts'] < ATTEMPT_LIMIT
                      and _arrival_request(mem, name, row) is None
                      and mem['tick'] >= row['retry_after']), None)
        if actor is not None:
            if arrival_actor:
                queue[actor]['arrival_request'] = _arrival_request(mem, actor, queue[actor])
                queue[actor]['arrival_reads'] = queue[actor].get('arrival_reads', 0) + 1
                from ..decision_log import _record
                _record(mem, 'recall_arrival_status_started', chart_step=None, general=actor,
                        observed_metric={'check_number': queue[actor]['arrival_reads'],
                                         'request_trace': queue[actor]['arrival_request']['request_trace']},
                        resulting_event='read_planned_location_unconfirmed',
                        reason='帰還Aの同run送信記録をもとに本人状態を再読する。帰還Aは再送しない')
            else:
                queue[actor]['attempts'] += 1
            mem['recall'] = {'stage': 'recheck_roster_open', 'steps': 0, 'general': actor,
                             'arrival_check_only': arrival_actor is not None,
                             'retreat_recheck': True, 'identity': queue[actor]['identity'],
                             'chapter': queue[actor]['chapter'], 'recheck_tick': queue[actor]['tick'],
                             'camp_episode': _new_episode(mem)}
            return [pad('x')]
    # Old saved state moved straight to menu after proposing B. Migrate only
    # that untouched phase; a previously operated menu cannot regain a budget.
    if (state.get('stage') == 'menu' and state.get('steps') == 0 and state.get('reads') == 0
            and _new_owner(state) and _valid_status(mem, state) and not is_camp_menu(screen)):
        state['stage'] = 'recheck_close'
    if state.get('stage') == 'recheck_close':
        actions = _status_close_step(screen, mem, state)
        if actions is not None or state['stage'] != 'menu':
            return actions
    if not named:
        if state.get('hero') and not _new_owner(state):
            # The independent emergency hero return owns its measured unit UI;
            # an unrelated retreat queue must not adopt or delay that rescue.
            return world_map_step(screen, mem, frame) if screen.kind == 'world_map' else existing(screen, mem, frame)
        budget = _anonymous_budget(mem, queue) if queue else None
        if not state and budget is not None and budget['reads'] >= roster_receipts.MAX_GENERALS:
            return None  # unknown camps stay unknown; ordinary map work is not starved
        if state and queue and state['stage'] == 'menu' and not state.get('named_status'):
            if budget['reads'] >= roster_receipts.MAX_GENERALS:
                _defer(mem, state, '未知の匿名camp追加観測の有限予算に達したため帰還を確定しない',
                       status='anonymous_recheck_exhausted')
                return [pad('b')] if (is_camp_menu(screen) or screen.kind in ('world_map', 'map_target')) else []
            budget['reads'] += 1
            state.update(anonymous_recheck=True, anonymous_identity=roster_receipts.run_identity(mem.get('_run_identity')),
                         anonymous_chapter=mem.get('chapter'))
            _advance(state, 'recheck_status')
        if state.get('anonymous_recheck') and (state.get('anonymous_identity') != roster_receipts.run_identity(mem.get('_run_identity'))
                                               or state.get('anonymous_chapter') != mem.get('chapter')):
            mem.pop('recall', None)
            return [pad('b')] if (is_camp_menu(screen) or screen.kind in ('world_map', 'map_target')) else None
        if state.get('anonymous_recheck') and state['stage'].startswith('recheck_'):
            return _named_step(screen, mem, frame, state)
        if state.get('anonymous_recheck'):
            if not _valid_status(mem, state) or state.get('general') in queue:
                _defer(mem, state, '匿名campの現在tent・本人status・episodeが一致せず帰還Aを送らない')
                return [pad('b')] if is_camp_menu(screen) or screen.kind in ('world_map', 'map_target') else []
            if screen.kind == 'unknown' and state['stage'] != 'await_dispatch':
                return _wait(mem, state, '未知前景では帰還menuを選択しない')
        actions = (world_map_step(screen, mem, frame) if screen.kind == 'world_map'
                   else existing(screen, mem, frame, observed_camps=_visible_camps(mem, frame, queue)))
        current = mem.get('recall') or {}
        if queue and current.get('stage') == 'menu' and screen.kind == 'map' and actions == [pad('a')]:
            current['camp_episode'] = _new_episode(mem)
            current['camp_observation'] = _observe_camp(screen, mem, frame, current, 'anonymous_map_open')
        return actions
    if screen.kind == 'text' and state['stage'] not in ('recheck_read', 'recheck_roster_read') and not (
            is_camp_menu(screen) or 'Aボタンでステータスひょうじ' in screen.text):
        _defer(mem, state, '別のtext表示で本人UIを失ったため通常の表示処理へ戻す')
        return None
    if state['stage'].startswith('recheck_'):
        return _named_step(screen, mem, frame, state)
    if not _valid_status(mem, state):
        _defer(mem, state, '同run・同UI episodeの現在tentと本人statusがなく帰還入力を確定しない')
        return [pad('b')] if is_camp_menu(screen) or screen.kind in ('world_map', 'map_target') else []
    if screen.kind == 'unknown' and state['stage'] != 'await_dispatch':
        return _wait(mem, state, '未知前景では帰還menuを選択しない')
    if state['stage'] == 'dest' and screen.kind == 'map_target':
        _defer(mem, state, '名前付き帰還先を実R pickerで確認できないため確定しない')
        return [pad('b')]
    skip_before = mem.get('recall_skip')
    actions = (world_map_step(screen, mem, frame) if screen.kind == 'world_map'
               else existing(screen, mem, frame))
    if not mem.get('recall'):
        # Preserve only a pre-existing anonymous cooldown; this named failure
        # must never put all other camps on a new 400-observation hold.
        if skip_before is None:
            mem.pop('recall_skip', None)
        else:
            mem['recall_skip'] = skip_before
        verification = mem.get('recall_verification') or {}
        status = verification.get('status') if verification.get('general') == state['general'] else None
        row = _scope(mem).get(state['general'])
        if row is not None:
            request = _arrival_request(mem, state['general'], {**row, 'arrival_request': verification})
            if request is not None and row.get('arrival_request') != request:
                row.update(arrival_request=request, arrival_reads=0)
        _defer(mem, state, '操作の有限終了後も本人の到着は未確認として再観測へ残す',
               status=status if status in ('arrival_unconfirmed', 'dispatch_unconfirmed') else 'needs_observation')
    return actions


# Stage 1 ships OFF (DSH 2026-10-31 approval). Stage 2 flips it in its own commit;
# revert = set back to False. A read-only observation: it records HP, never changes a decision.
CAMP_HP_PROBE_ENABLED = False
CAMP_HP_PROBE_LIMIT = 2  # per retreat: one baseline read plus one change check
PROBE_BUSY_KEYS = ('battle', 'month_sub', 'house', 'y_jump', 'sortie_attempt', 'attack')


def _camp_under_cursor(screen, frame):
    """The single own camp under the real map cursor, as its target tuple, else None."""
    from .recall import RECALL_ARRIVE_PX
    from ..os_layer.readers import _cursor, own_camps
    cursor = _cursor(screen)
    under = [camp for camp in own_camps(frame) if not camp.get('clipped') and cursor
             and all(abs(camp['target'][axis] - cursor[axis]) <= RECALL_ARRIVE_PX for axis in (0, 1))]
    return tuple(under[0]['target']) if len(under) == 1 else None


def _probe_abort(mem):
    mem.pop('camp_hp_probe', None)
    return None


def camp_hp_probe_step(screen, mem, frame):
    """Read the camped general's HP from the camp status menu while idle.

    Only the route named rechecks already use (map a -> ステータス a -> b, b).
    Returns None whenever it does not act, so existing flows keep their inputs.
    Any unexpected screen drops the probe and hands the frame back to the old logic.
    """
    from ..decision_log import _record
    from ..os_layer.commands import menu_to, pad
    from ..os_layer.readers import is_camp_menu
    from . import house
    if not CAMP_HP_PROBE_ENABLED:
        mem.pop('camp_hp_probe', None)
        return None
    probe = mem.get('camp_hp_probe')
    if probe is None:
        if mem.get('recall') or screen.kind != 'map' or frame is None or any(mem.get(k) for k in PROBE_BUSY_KEYS):
            return None
        queue = _scope(mem)
        if any(row['status'] == 'needs_observation' and row['retry_after'] <= mem['tick']
               and row['attempts'] < ATTEMPT_LIMIT for row in queue.values()):
            return None  # a due retreat recheck always goes first (boundary condition 2)
        target = _camp_under_cursor(screen, frame)
        counts = mem.get('camp_hp_probe_counts') or {}
        actor = next((name for name, row in queue.items()
                      if target is not None and (row.get('camp_observation') or {}).get('target') == list(target)
                      and counts.get(f"{name}:{row['tick']}", 0) < CAMP_HP_PROBE_LIMIT), None)
        if actor is None:
            return None
        key = f"{actor}:{queue[actor]['tick']}"
        mem['camp_hp_probe_counts'] = {**counts, key: counts.get(key, 0) + 1}
        mem['camp_hp_probe'] = {'stage': 'menu', 'general': actor, 'key': key, 'steps': 0}
        return [pad('a')]
    probe['steps'] = probe.get('steps', 0) + 1
    if probe['steps'] > 6:
        return _probe_abort(mem)
    actor = probe['general']
    if probe['stage'] == 'menu':
        if not is_camp_menu(screen):
            return _probe_abort(mem)
        move = menu_to(screen, 'ステータス')
        if move is None:
            return _probe_abort(mem)
        if move == 'here':
            probe['stage'] = 'read'
            return [pad('a')]
        return [move]
    if probe['stage'] == 'read':
        info = house.general_status(screen)
        main_panel = info is not None and any(line.y == 39 and line.span(24, 88).strip() == actor
                                              and line.span(88, 136) == 'しょうぐん' for line in screen.lines)
        if not main_panel or info['general'] != actor or type(info.get('hp')) is not int or info['hp'] < 0:
            return _probe_abort(mem)
        _record(mem, 'camp_hp_observation', chart_step=None, general=actor,
                observed_metric={'hp': info['hp'], 'max_hp': info.get('max_hp'), 'tick': mem['tick'],
                                 'read': mem['camp_hp_probe_counts'][probe['key']]},
                resulting_event='camp_hp_observed',
                reason='野営中の本人HPを状態画面で読み取り記録。HPによる挙動変更はしない')
        probe['stage'] = 'exit_menu'
        return [pad('b')]
    if probe['stage'] == 'exit_menu':
        if not is_camp_menu(screen):
            return _probe_abort(mem)
        probe['stage'] = 'exit_map'
        return [pad('b')]
    mem.pop('camp_hp_probe', None)
    return None
