"""Bounded named re-observation after a planned retreat, without inferred arrival.

The roster/SELECT/status route reuses the measured broken-egg repair UI.
No game/provider I/O lives here. Only the existing recall sender owns dispatch.
"""
from . import hanjuku_roster as roster_receipts

RETRY_TICKS = 30
ATTEMPT_LIMIT = 3
READ_LIMIT = 3


def _clean_name(name):
    return (isinstance(name, str) and 1 <= len(name) <= 32
            and '\ufffd' not in name and not any(ch.isspace() for ch in name))


_ALLOWED_KINDS = {
    'recheck_roster_open': {'main_menu', 'map', 'unknown'},
    'recheck_pick': {'text', 'main_menu', 'unknown'},
    'recheck_focus': {'map', 'text', 'unknown'},
    'recheck_status': {'text', 'map', 'unknown'},
    'recheck_read': {'text', 'unknown'},
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
            or identity is None or any(type(state[key]) is not bool for key in
                                      ('retreat_recheck', 'anonymous_recheck') if key in state)
            or not (state.get('retreat_recheck') or state.get('anonymous_recheck'))):
        return False
    if any(key in state and (type(state[key]) is not int or not 0 <= state[key] <= limit)
           for key, limit in (('reads', READ_LIMIT), ('scrolls', roster_receipts.MAX_GENERALS))):
        return False
    if state.get('retreat_recheck'):
        return (_clean_name(actor) and roster_receipts.run_identity(state.get('identity')) == identity
                and type(state.get('chapter')) is int and state['chapter'] == chapter
                and type(state.get('recheck_tick')) is int and 0 <= state['recheck_tick'] <= now)
    return (stage in ('recheck_status', 'recheck_read', 'menu', 'dest', 'await_dispatch')
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
    from . import hanjuku_policy as p
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
            and 0 <= mem['tick'] - proof['observed_tick'] <= p.RECALL_LIMIT)


def _observe_camp(screen, mem, frame, state, origin):
    from . import hanjuku_policy as p
    if screen.kind != 'map' or frame is None or not _valid_episode(mem, state):
        return None
    cursor = p._cursor(screen)
    under = [camp for camp in p.own_camps(frame) if not camp.get('clipped') and cursor
             and all(abs(camp['target'][axis] - cursor[axis]) <= p.RECALL_ARRIVE_PX for axis in (0, 1))]
    if len(under) != 1 or list(under[0]['target']) != state.get('target'):
        return None
    roofs = sorted([r['kind'], *r['target']] for r in p.castle_roofs(frame))
    episode = state['camp_episode']
    return {'schema': 1, 'identity': episode['identity'], 'chapter': episode['chapter'],
            'episode': episode['serial'], 'episode_tick': episode['started_tick'],
            'observed_tick': mem['tick'], 'origin': origin, 'target': list(under[0]['target']),
            'frame_sha256': frame.digest(), 'roofs': roofs if len(roofs) >= 2 else []}


def _valid_status(mem, state):
    from . import hanjuku_policy as p
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
            and mem['tick'] - proof['tick'] <= p.RECALL_LIMIT)


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
    from . import hanjuku_policy as p
    queue = _scope(mem)
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
        p._record(mem, 'retreat_recheck_invalidated', chart_step=None, general=actor,
                  resulting_event='intent_invalidated_location_unconfirmed', reason=reason)


def capture(mem, battle):
    """A selected retreat and surviving named actor justify checking, not camp facts."""
    from . import hanjuku_policy as p
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
    p._record(mem, 'retreat_recheck_pending', chart_step=None, general=actor,
              resulting_event='location_unconfirmed',
              reason='生存する本人の退却選択を記録し、野営化や帰還を断定せず所在を再確認する')


def _defer(mem, state, reason, *, status='needs_observation'):
    from . import hanjuku_policy as p
    actor = state.get('general') if isinstance(state, dict) and _clean_name(state.get('general')) else None
    row = _scope(mem).get(actor)
    if row is not None:
        row.update(status=status, retry_after=mem['tick'] + RETRY_TICKS)
        if _valid_camp(mem, state):
            row['camp_observation'] = state['camp_observation']
    mem.pop('recall', None)
    mem['uncertain'] = True
    p._record(mem, 'retreat_recheck_unconfirmed', chart_step=None, general=actor,
              resulting_event=status, reason=reason)


def _wait(mem, state, reason, *, close=False):
    from . import hanjuku_policy as p
    state['reads'] = state.get('reads', 0) + 1
    if state['reads'] < READ_LIMIT:
        return []
    _defer(mem, state, reason)
    return [p.pad('b')] if close else []


def _advance(state, stage):
    state.update(stage=stage, steps=0, reads=0)


def _observe_egg(mem, info):
    """Store only a verified named panel's real quantity; never derive a zero."""
    from . import hanjuku_policy as p
    actor, uses, egg = info['general'], info.get('uses'), info.get('egg')
    if type(uses) is not int or not 0 <= uses <= 4 or not _clean_name(egg):
        return
    counts, types = mem.setdefault('egg_uses', {}), mem.setdefault('egg_types', {})
    changed = counts.get(actor) != uses or types.get(actor) != egg
    counts[actor], types[actor] = uses, egg
    if isinstance(mem.get('egg_recheck'), list):
        mem['egg_recheck'] = [name for name in mem['egg_recheck'] if name != actor]
    if changed:
        # Do not leave an older complete roster's egg row silently reusable.
        roster_receipts.dirty_status(mem, [actor], fields=('egg',))
        p._record(mem, 'egg_uses_seen', chart_step=None, general=actor, egg=egg,
                  observed_metric=uses, reason='本人と現在tentのfield statusで実卵数量を確認。消費や回復は推測しない')


def _named_step(screen, mem, frame, state):
    from . import hanjuku_policy as p, hanjuku_house as house
    stage, actor = state['stage'], state.get('general')
    if stage in ('recheck_roster_open', 'recheck_pick', 'recheck_focus') and not _valid_episode(mem, state):
        _defer(mem, state, '本人focusのUI episodeを確認できず旧状態では操作しない')
        return [p.pad('b')] if screen.kind in ('text', 'main_menu') else []
    if stage in ('recheck_status', 'recheck_read') and not _valid_camp(mem, state):
        _defer(mem, state, '同run・同UI episodeの現在tent receiptを確認できず数量や帰還を更新しない')
        return [p.pad('b')] if screen.kind == 'text' else []
    if screen.kind == 'unknown':
        return _wait(mem, state, '未知前景では旧背景のmenuを選択しない')
    state['steps'] = state['steps'] + 1
    if state['steps'] > p.RECALL_LIMIT:
        _defer(mem, state, '本人再確認の観測上限に達したため古いUI位置では操作しない')
        return [p.pad('b')] if screen.kind in ('main_menu', 'text') else []
    if stage == 'recheck_roster_open':
        if screen.kind == 'main_menu':
            move = p.menu_to(screen, 'しょうぐん')
            if move is not None:
                if move == 'here':
                    _advance(state, 'recheck_pick')
                return [p.pad('a')] if move == 'here' else [move]
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
                return [p.pad('b')]
            return [p.pad('down')]
        move = p.menu_to(screen, actor)
        if move == 'here':
            _advance(state, 'recheck_focus')
            return [p.pad('select')]  # roster SELECT focuses the selected named general
        return [move] if move else _wait(mem, state, '本人の実カーソルを確認できない', close=True)
    if stage == 'recheck_focus':
        cursor = p._cursor(screen)
        camps = p.own_camps(frame) if frame is not None and screen.kind == 'map' else []
        under = [camp for camp in camps if not camp.get('clipped') and cursor
                 and all(abs(camp['target'][axis] - cursor[axis]) <= p.RECALL_ARRIVE_PX
                         for axis in (0, 1))]
        if len(under) != 1:
            return _wait(mem, state, '本人focus後の現在tentとcursorが一致せず決定しない')
        state['target'] = list(under[0]['target'])
        state['camp_observation'] = _observe_camp(screen, mem, frame, state, 'named_focus')
        if not _valid_camp(mem, state):
            _defer(mem, state, '現在tentと同episodeを束縛できず選択しない')
            return []
        _advance(state, 'recheck_status')
        return [p.pad('a')]
    if stage == 'recheck_status':
        if not p.is_camp_menu(screen):
            return _wait(mem, state, '本人の野営ステータスメニューを確認できない')
        move = p.menu_to(screen, 'ステータス')
        if move == 'here':
            _advance(state, 'recheck_read')
            return [p.pad('a')]
        return [move] if move else _wait(mem, state, 'ステータス項目の実カーソルを確認できない', close=True)
    if stage == 'recheck_read':
        info = house.general_status(screen)
        if info is None:
            return _wait(mem, state, '本人ステータスの読取りが未確認', close='しょうぐん' in screen.text)
        field_panel = any(line.y == 31 and line.span(16, 80).strip() == info['general']
                          and line.span(80, 128) == 'しょうぐん' for line in screen.lines)
        if not field_panel:
            _defer(mem, state, '校正済みの部隊field status構造を確認できず場所のfallbackを使わない')
            return [p.pad('b')]
        if state.get('hero') and info['general'] != p.NAME:
            _defer(mem, state, '主人公focusと実ステータスの本人が異なるため操作しない')
            return [p.pad('b')]
        if not state.get('anonymous_recheck') and info['general'] != actor:
            _defer(mem, state, '指定本人と実ステータスが異なるため数量や帰還を更新しない')
            return [p.pad('b')]
        if info.get('location') != 'castle':
            _observe_egg(mem, info)  # a blocked return must not discard an actual zero
        if type(info.get('hp')) is int and info['hp'] == 0:
            _invalidate(mem, '現在tentの本人HP0を実観測し生存帰還intentを無効化。死亡や到着は断定しない', info['general'])
            return [p.pad('b')]
        if state.get('anonymous_recheck'):
            # A changed view does not identify a camp. Read its real occupant
            # before allowing it to bypass a named actor's exhausted budget.
            actor = state['general'] = info['general']
            row = _scope(mem).get(actor)
            if row is not None:
                if row['attempts'] >= ATTEMPT_LIMIT or mem['tick'] < row['retry_after']:
                    _defer(mem, state, '実本人が再確認待ちまたは予算上限のため帰還Aを再送しない',
                           status=row['status'])
                    return [p.pad('b')]
                row['attempts'] += 1
                state.update(retreat_recheck=True, identity=row['identity'], chapter=row['chapter'],
                             recheck_tick=row['tick'])
        if (info['general'] != actor or type(info.get('hp')) is not int or info['hp'] <= 0
                or info.get('location') == 'castle'):
            _defer(mem, state, '本人・生存HP・観測した野営との整合を確認できず帰還を決定しない')
            return [p.pad('b')]
        camp, episode = state['camp_observation'], state['camp_episode']
        state['named_status'] = {'schema': 1, 'general': actor, 'hp': info['hp'], 'tick': mem['tick'],
                                 'identity': episode['identity'], 'chapter': episode['chapter'],
                                 'episode': episode['serial'], 'camp_frame_sha256': camp['frame_sha256'],
                                 'camp_observed_tick': camp['observed_tick']}
        _advance(state, 'menu')
        p._record(mem, 'retreat_recheck_status', chart_step=None, general=actor,
                  observed_metric=state['named_status'],
                  resulting_event='named_camp_observed',
                  reason='現tentを選択した後の本人ステータスと生存HPを確認。回復や到着は未確認')
        return [p.pad('b')]
    return None


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
    from . import hanjuku_policy as p
    if not queue:
        return None  # leave the existing anonymous FSM and its read budget unchanged
    camps = p.own_camps(frame) if frame is not None else []
    if not camps:
        return camps
    roofs = sorted([r['kind'], *r['target']] for r in p.castle_roofs(frame))
    for row in queue.values():
        observed = row.get('camp_observation') or {}
        target = observed.get('target')
        # No durable camp ID is inferred from a screen coordinate. Suppress
        # only the same unchanged image or a view with two unchanged roofs.
        same_view = (observed.get('frame_sha256') == frame.digest()
                     or bool(observed.get('roofs')) and observed['roofs'] == roofs)
        if target and same_view and (mem['tick'] < row['retry_after'] or row['attempts'] >= ATTEMPT_LIMIT):
            camps = [camp for camp in camps if any(
                abs(camp['target'][axis] - target[axis]) > p.RECALL_ARRIVE_PX for axis in (0, 1))]
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


def step(screen, mem, frame, existing):
    """Keep named observation retries separate from the anonymous camp cooldown."""
    from . import hanjuku_policy as p
    if interrupt(screen, mem):
        return [] if mem.get('battle') and screen.kind == 'map' else None
    queue = _scope(mem)
    state = mem.get('recall') or {}
    named = bool(state.get('retreat_recheck'))
    if named and (state.get('general') not in queue
                  or state.get('identity') != queue[state['general']]['identity']
                  or state.get('chapter') != queue[state['general']]['chapter']
                  or state.get('recheck_tick') != queue[state['general']]['tick']):
        mem.pop('recall', None)
        return [p.pad('b')] if p.is_camp_menu(screen) else None
    if not state and screen.kind == 'map' and frame is not None and not any(
            mem.get(key) for key in ('battle', 'month_sub', 'house', 'y_jump', 'sortie_attempt', 'attack')):
        actor = next((name for name, row in queue.items() if row['attempts'] < ATTEMPT_LIMIT
                      and mem['tick'] >= row['retry_after']), None)
        if actor is not None:
            queue[actor]['attempts'] += 1
            mem['recall'] = {'stage': 'recheck_roster_open', 'steps': 0, 'general': actor,
                             'retreat_recheck': True, 'identity': queue[actor]['identity'],
                             'chapter': queue[actor]['chapter'], 'recheck_tick': queue[actor]['tick'],
                             'camp_episode': _new_episode(mem)}
            return [p.pad('x')]
    if not named:
        if state.get('hero') and not _new_owner(state):
            # The independent emergency hero return owns its measured unit UI;
            # an unrelated retreat queue must not adopt or delay that rescue.
            return p.world_map_step(screen, mem, frame) if screen.kind == 'world_map' else existing(screen, mem, frame)
        budget = _anonymous_budget(mem, queue) if queue else None
        if not state and budget is not None and budget['reads'] >= roster_receipts.MAX_GENERALS:
            return None  # unknown camps stay unknown; ordinary map work is not starved
        if state and queue and state['stage'] == 'menu' and not state.get('named_status'):
            if budget['reads'] >= roster_receipts.MAX_GENERALS:
                _defer(mem, state, '未知の匿名camp追加観測の有限予算に達したため帰還を確定しない',
                       status='anonymous_recheck_exhausted')
                return [p.pad('b')] if p.is_camp_menu(screen) else []
            budget['reads'] += 1
            state.update(anonymous_recheck=True, anonymous_identity=roster_receipts.run_identity(mem.get('_run_identity')),
                         anonymous_chapter=mem.get('chapter'))
            _advance(state, 'recheck_status')
        if state.get('anonymous_recheck') and (state.get('anonymous_identity') != roster_receipts.run_identity(mem.get('_run_identity'))
                                               or state.get('anonymous_chapter') != mem.get('chapter')):
            mem.pop('recall', None)
            return [p.pad('b')] if p.is_camp_menu(screen) else None
        if state.get('anonymous_recheck') and state['stage'].startswith('recheck_'):
            return _named_step(screen, mem, frame, state)
        if state.get('anonymous_recheck'):
            if not _valid_status(mem, state) or state.get('general') in queue:
                _defer(mem, state, '匿名campの現在tent・本人status・episodeが一致せず帰還Aを送らない')
                return [p.pad('b')] if p.is_camp_menu(screen) or screen.kind in ('world_map', 'map_target') else []
            if screen.kind == 'unknown' and state['stage'] != 'await_dispatch':
                return _wait(mem, state, '未知前景では帰還menuを選択しない')
        actions = (p.world_map_step(screen, mem, frame) if screen.kind == 'world_map'
                   else existing(screen, mem, frame, observed_camps=_visible_camps(mem, frame, queue)))
        current = mem.get('recall') or {}
        if queue and current.get('stage') == 'menu' and screen.kind == 'map' and actions == [p.pad('a')]:
            current['camp_episode'] = _new_episode(mem)
            current['camp_observation'] = _observe_camp(screen, mem, frame, current, 'anonymous_map_open')
        return actions
    if screen.kind == 'text' and state['stage'] != 'recheck_read' and not (
            p.is_camp_menu(screen) or 'Aボタンでステータスひょうじ' in screen.text):
        _defer(mem, state, '別のtext表示で本人UIを失ったため通常の表示処理へ戻す')
        return None
    if state['stage'].startswith('recheck_'):
        return _named_step(screen, mem, frame, state)
    if not _valid_status(mem, state):
        _defer(mem, state, '同run・同UI episodeの現在tentと本人statusがなく帰還入力を確定しない')
        return [p.pad('b')] if p.is_camp_menu(screen) or screen.kind in ('world_map', 'map_target') else []
    if screen.kind == 'unknown' and state['stage'] != 'await_dispatch':
        return _wait(mem, state, '未知前景では帰還menuを選択しない')
    if state['stage'] == 'dest' and screen.kind == 'map_target':
        _defer(mem, state, '名前付き帰還先を実R pickerで確認できないため確定しない')
        return [p.pad('b')]
    skip_before = mem.get('recall_skip')
    actions = (p.world_map_step(screen, mem, frame) if screen.kind == 'world_map'
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
        _defer(mem, state, '操作の有限終了後も本人の到着は未確認として再観測へ残す',
               status=status if status in ('arrival_unconfirmed', 'dispatch_unconfirmed') else 'needs_observation')
    return actions
