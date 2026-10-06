"""Only the current camp observation in this live UI episode can authorize reuse."""
from copy import deepcopy

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_screen import Screen, parse
from test_hanjuku_retreat_recheck import memory, focus, MAP, FRAME
from test_hanjuku_chart_bot import camp_menu
from test_hanjuku_house import status


def opened(mode='named', actor='ゼウス'):
    mem = memory(); mem['egg_uses'] = {'ゼウス': 2, 'ヴィーナス': 2}
    if mode == 'named':
        focus(mem)
    else:
        mem['retreat_rechecks']['ゼウス']['attempts'] = 3
        p.camp_recall_step(MAP, mem, FRAME)
        p.camp_recall_step(camp_menu(1), mem, FRAME)
    return mem


def damage(mem, case):
    recall = mem['recall']
    if case == 'missing': recall.pop('camp_observation', None)
    elif case == 'malformed': recall['camp_observation'] = {'malformed': True}
    elif case == 'nondict': recall['camp_observation'] = 'bad'
    elif case == 'old_tick': mem['tick'] += p.RECALL_LIMIT + 1
    elif case == 'identity': recall['camp_observation']['identity'] = {**mem['_run_identity'], 'lease_id': 'other'}
    elif case == 'chapter': recall['camp_observation']['chapter'] = 2
    elif case == 'episode': recall['camp_observation']['episode'] = 999
    elif case == 'target': recall['camp_observation']['target'] = [90, 90]
    elif case == 'sha': recall['camp_observation']['frame_sha256'] = 'z' * 64
    elif case == 'episode_missing': recall.pop('camp_episode', None)
    elif case == 'counter_missing': mem.pop('retreat_camp_episode', None)


@pytest.mark.parametrize('case', ['missing', 'malformed', 'nondict', 'old_tick', 'identity', 'chapter',
                                 'episode', 'target', 'sha', 'episode_missing', 'counter_missing'])
@pytest.mark.parametrize('mode,actor', [('named', 'ゼウス'), ('anonymous', 'ヴィーナス')])
def test_invalid_active_camp_proof_cannot_update_quantities_or_authorize_return(case, mode, actor):
    mem = opened(mode, actor)
    damage(mem, case)
    assert p.camp_recall_step(parse(status(actor, 'エラベルエッグ0', main=False, hp=6)), mem, FRAME) == [p.pad('b')]
    assert mem['egg_uses'] == {'ゼウス': 2, 'ヴィーナス': 2}
    assert not mem.get('egg_types') and not mem.get('recall')
    assert not any(r.get('resulting_event') == 'named_camp_observed' for r in mem['_records'])


@pytest.mark.parametrize('case', ['missing', 'malformed', 'nondict', 'old_tick', 'identity', 'chapter',
                                 'episode', 'target', 'sha', 'episode_missing', 'counter_missing'])
@pytest.mark.parametrize('mode,actor', [('named', 'ゼウス'), ('anonymous', 'ヴィーナス')])
def test_status_alone_cannot_replace_a_lost_camp_proof_at_the_return_menu(case, mode, actor):
    mem = opened(mode, actor)
    assert p.camp_recall_step(parse(status(actor, 'エラベルエッグ0', main=False, hp=6)), mem, FRAME) == [p.pad('b')]
    assert mem['egg_uses'][actor] == 0
    damage(mem, case)
    assert p.camp_recall_step(camp_menu(3), mem, FRAME) == [p.pad('b')]
    assert not mem.get('recall') and mem['egg_uses'][actor] == 0


def test_receipt_is_bound_to_actual_current_map_and_same_episode_and_survives_json():
    import json
    mem = opened()
    proof = mem['recall']['camp_observation']
    assert proof['identity'] == mem['_run_identity'] and proof['chapter'] == 1
    assert proof['episode'] == mem['recall']['camp_episode']['serial']
    assert proof['observed_tick'] == mem['tick']
    assert proof['frame_sha256'] == FRAME.digest()
    mem = json.loads(json.dumps(mem))
    assert p.camp_recall_step(parse(status('ゼウス', 'エラベルエッグ0', main=False, hp=6)), mem, FRAME) == [p.pad('b')]
    assert p.camp_recall_step(camp_menu(3), mem, FRAME) == [p.pad('a')]


def test_an_interrupted_receipt_cannot_be_replayed_into_a_later_episode():
    mem = opened(); old_receipt = deepcopy(mem['recall']['camp_observation'])
    old_episode = deepcopy(mem['recall'].get('camp_episode'))
    p.camp_recall_step(Screen([], None, '', kind='month_menu'), mem, FRAME)
    mem['tick'] += 30
    focus(mem)
    mem['recall']['camp_observation'] = old_receipt
    if old_episode is not None: mem['recall']['camp_episode'] = old_episode
    assert p.camp_recall_step(parse(status('ゼウス', 'エラベルエッグ0', main=False, hp=6)), mem, FRAME) == [p.pad('b')]
    assert mem['egg_uses']['ゼウス'] == 2 and not mem.get('recall')


def test_queue_suppression_data_cannot_be_promoted_into_a_new_active_ui_episode():
    mem = opened(); old_receipt = deepcopy(mem['recall']['camp_observation'])
    p.camp_recall_step(Screen([], None, '', kind='month_menu'), mem, FRAME)
    mem['tick'] += 30
    focus(mem)
    mem['recall']['camp_observation'] = old_receipt
    assert p.camp_recall_step(parse(status('ゼウス', 'エラベルエッグ0', main=False, hp=6)), mem, FRAME) == [p.pad('b')]
    assert mem['egg_uses']['ゼウス'] == 2 and not mem.get('recall')


def test_incomplete_anonymous_status_hotload_cannot_bypass_a_blocked_actor():
    mem = memory(); mem['retreat_rechecks']['ゼウス']['attempts'] = 3
    mem['recall'] = {'stage': 'menu', 'steps': 0, 'general': 'ゼウス', 'target': [26, 66],
                     'anonymous_recheck': True, 'anonymous_identity': dict(mem['_run_identity']),
                     'anonymous_chapter': 1, 'named_status': {'tick': 100}}
    assert p.camp_recall_step(camp_menu(3), mem, FRAME) == [p.pad('b')]
    assert not mem.get('recall') and mem['retreat_rechecks']['ゼウス']['attempts'] == 3


@pytest.mark.parametrize('field,value', [('general', 'ヴィーナス'), ('hp', None), ('hp', 0), ('hp', True),
    ('schema', True), ('episode', 999), ('chapter', 2), ('tick', True), ('camp_observed_tick', 0),
    ('camp_frame_sha256', '0' * 64)])
@pytest.mark.parametrize('mode,actor', [('named', 'ゼウス'), ('anonymous', 'ヴィーナス')])
def test_all_status_fields_must_match_the_current_camp_episode(field, value, mode, actor):
    mem = opened(mode, actor)
    p.camp_recall_step(parse(status(actor, 'エラベルエッグ0', main=False, hp=6)), mem, FRAME)
    if field == 'general' and value == actor: value = 'ココット'
    mem['recall']['named_status'][field] = value
    assert p.camp_recall_step(camp_menu(3), mem, FRAME) == [p.pad('b')]
    assert not mem.get('recall')


@pytest.mark.parametrize('kind', ['shop_quantity_prompt', 'shop_quantity', 'shop_exit_confirm', 'gift_request',
    'gift', 'discharge_menu', 'summer_bonus', 'summer_bonus_message', 'yes_no', 'card_select',
    'sortie_confirm', 'castle_info', 'sealed_castle'])
@pytest.mark.parametrize('mode,actor', [('named', 'ゼウス'), ('anonymous', 'ヴィーナス')])
def test_foreground_owner_invalidates_old_background_camp_permission(kind, mode, actor):
    mem = opened(mode, actor)
    p.camp_recall_step(parse(status(actor, 'エラベルエッグ0', main=False, hp=6)), mem, FRAME)
    background = camp_menu(3); background.kind = kind
    assert p.camp_recall_step(background, mem, FRAME) is None
    assert not mem.get('recall')


def test_unknown_foreground_never_uses_a_background_hand():
    mem = opened()
    p.camp_recall_step(parse(status('ゼウス', 'エラベルエッグ0', main=False, hp=6)), mem, FRAME)
    background = camp_menu(3); background.kind = 'unknown'
    for _ in range(3): assert p.camp_recall_step(background, mem, FRAME) == []
    assert not mem.get('recall')


def test_real_quantity_foreground_is_returned_to_shop_owner_in_decide():
    from docich.hanjuku_bot import classify, decide
    from test_hanjuku_retreat_recheck import ID
    from test_hanjuku_chart_bot import Canvas
    mem = opened()
    p.camp_recall_step(parse(status('ゼウス', 'エラベルエッグ0', main=False, hp=6)), mem, FRAME)
    c = Canvas()
    for i, label in enumerate(('いどう', 'ステータス', 'キャンプ', 'きかん')):
        c.text(80, 39 + 16 * i, label)
    c.hand(58, 81); c.text(24, 167, 'よろしいでっか')
    frame = c.frame(); screen = parse(frame, phase=classify(frame))
    assert screen.kind == 'shop_quantity' and screen.selected == 'きかん'
    expected = p.shop_step(screen, mem)
    actions, updated = decide(frame, {'policy': mem}, run_identity=ID)
    assert expected == [] and actions == expected
    assert not updated['policy'].get('recall')


def test_same_actor_hp_zero_keeps_actual_quantity_and_invalidates_only_that_intent():
    from docich import hanjuku_camp_recheck as recheck
    mem = opened()
    recheck.capture(mem, {'ally': 'ヴィーナス', 'side': 'attack', 'ally_hp': 6, 'hero_retreat': {'selected': 1}})
    assert p.camp_recall_step(parse(status('ゼウス', 'エラベルエッグ0', main=False, hp=0)), mem, FRAME) == [p.pad('b')]
    assert mem['egg_uses']['ゼウス'] == 0 and 'ゼウス' not in mem['retreat_rechecks']
    assert mem['retreat_rechecks']['ヴィーナス']['status'] == 'needs_observation'
    assert not mem.get('recall') and not mem.get('garrison')


def test_another_actor_hp_zero_does_not_invalidate_the_requested_actor_or_update_its_quantity():
    mem = opened()
    assert p.camp_recall_step(parse(status('ヴィーナス', 'エラベルエッグ0', main=False, hp=0)), mem, FRAME) == [p.pad('b')]
    assert mem['egg_uses'] == {'ゼウス': 2, 'ヴィーナス': 2}
    assert mem['retreat_rechecks']['ゼウス']['status'] == 'needs_observation'


@pytest.mark.parametrize('field,value', [('general', None), ('general', []), ('general', {}), ('general', True),
    ('stage', None), ('stage', []), ('stage', {}), ('stage', True), ('stage', 'invented'),
    ('steps', None), ('steps', []), ('steps', {}), ('steps', True), ('steps', -1),
    ('chapter', True), ('recheck_tick', None), ('recheck_tick', []), ('recheck_tick', {}), ('recheck_tick', True)])
def test_malformed_named_active_fields_never_raise_or_authorize_an_input(field, value):
    mem = opened(); mem['recall'][field] = value
    actions = p.camp_recall_step(parse(status('ゼウス', 'エラベルエッグ0', main=False, hp=6)), mem, FRAME)
    assert p.pad('a') not in (actions or [])
    assert not mem.get('recall') and mem['egg_uses']['ゼウス'] == 2


@pytest.mark.parametrize('field,value', [('general', None), ('general', []), ('general', {}), ('general', True),
    ('stage', None), ('stage', []), ('stage', {}), ('stage', True), ('stage', 'invented'),
    ('steps', None), ('steps', []), ('steps', {}), ('steps', True), ('steps', -1),
    ('anonymous_chapter', True), ('anonymous_identity', []), ('anonymous_identity', {}),
    ('anonymous_recheck', True), ('anonymous_recheck', 'true')])
def test_malformed_anonymous_active_fields_do_not_promote_a_status_hotload(field, value):
    mem = opened('anonymous', 'ヴィーナス')
    p.camp_recall_step(parse(status('ヴィーナス', 'エラベルエッグ0', main=False, hp=6)), mem, FRAME)
    if field == 'anonymous_recheck' and value is True: value = False
    mem['recall'][field] = value
    actions = p.camp_recall_step(camp_menu(3), mem, FRAME)
    assert p.pad('a') not in (actions or []) and not mem.get('recall')


@pytest.mark.parametrize('field,value', [('camp_episode', None), ('camp_episode', []), ('camp_episode', True),
    ('camp_episode', {'serial': True}), ('retreat_camp_episode', []), ('retreat_camp_episode', True)])
def test_bad_episode_containers_and_scalars_are_rejected_without_exceptions(field, value):
    mem = opened()
    if field == 'retreat_camp_episode': mem[field] = value
    else: mem['recall'][field] = value
    actions = p.camp_recall_step(parse(status('ゼウス', 'エラベルエッグ0', main=False, hp=6)), mem, FRAME)
    assert p.pad('a') not in (actions or [])
    assert not mem.get('recall') and mem['egg_uses']['ゼウス'] == 2


@pytest.mark.parametrize('value', [[], [{'retreat_recheck': True}], True, 'bad'])
def test_non_dictionary_active_state_is_removed_without_a_crash(value):
    mem = memory(); mem['recall'] = value
    assert p.camp_recall_step(camp_menu(3), mem, FRAME) is None
    assert not mem.get('recall')


@pytest.mark.parametrize('value', [[{'retreat_recheck': True}], {'retreat_recheck': True, 'general': [], 'stage': []}])
@pytest.mark.parametrize('kind,text', [('name_entry', ''), ('text', 'ゼウスがはいかにくわわった!')])
def test_scope_change_still_clears_queue_when_active_ui_is_malformed(value, kind, text):
    mem = memory(); mem['recall'] = value
    assert p.camp_recall_step(Screen([], None, text, kind=kind), mem, FRAME) is None
    assert not mem.get('recall') and not mem.get('retreat_rechecks')


@pytest.mark.parametrize('field,limit', [('reads', 3), ('scrolls', 32)])
@pytest.mark.parametrize('value', [[1], {}, '1', True, False, -1, 999])
@pytest.mark.parametrize('mode,actor', [('named', 'ゼウス'), ('anonymous', 'ヴィーナス')])
def test_optional_active_counters_are_strict_and_bounded_before_waiting(field, limit, value, mode, actor):
    mem = opened(mode, actor)
    mem['recall'][field] = value
    actions = p.camp_recall_step(Screen([], None, '', kind='unknown'), mem, FRAME)
    assert p.pad('a') not in (actions or []) and not mem.get('recall')
    assert mem['egg_uses'][actor] == 2


def test_invalid_scroll_counter_cannot_crash_the_named_roster_scan():
    from test_hanjuku_retreat_recheck import main_menu
    from test_hanjuku_house import roster
    mem = memory()
    p.camp_recall_step(MAP, mem, FRAME); p.camp_recall_step(main_menu(), mem, FRAME)
    mem['recall']['scrolls'] = [1]
    actions = p.camp_recall_step(parse(roster(names=('どうし',))), mem, FRAME)
    assert p.pad('a') not in (actions or []) and not mem.get('recall')


@pytest.mark.parametrize('exhausted', [False, True])
def test_an_unrelated_queue_never_adopts_or_delays_the_existing_weak_hero_return(exhausted):
    import json
    from docich import hanjuku_camp_recheck as recheck
    from test_hanjuku_camp_receipts import picker
    mem = memory(); mem['chapter'] = 2
    recheck.capture(mem, {'ally': 'ゼウス', 'side': 'attack', 'ally_hp': 6, 'hero_retreat': {'selected': 1}})
    if exhausted:
        mem['retreat_rechecks']['ゼウス']['attempts'] = 3
        recheck._anonymous_budget(mem, mem['retreat_rechecks'])['reads'] = 32
    mem['recall'] = {'stage': 'hero_focus', 'hero': True, 'steps': 0, 'sorties': ['hero-old']}
    assert p.camp_recall_step(MAP, mem, FRAME) == [p.pad('select')]
    assert p.camp_recall_step(MAP, mem, FRAME) == [p.pad('a')]
    assert not mem['recall'].get('anonymous_recheck') and 'target' not in mem['recall']
    assert p.camp_recall_step(camp_menu(3), mem, FRAME) == [p.pad('a')]
    frame = picker(); screen = parse(frame, phase='field')
    assert p.camp_recall_step(screen, mem, frame) == [p.pad('a'), {'type': 'wait', 'ms': 700}, p.pad('a')]
    assert mem['recall']['stage'] == 'await_dispatch' and mem['recall']['goal'] == 'アルマムーン'
    assert mem['retreat_rechecks']['ゼウス']['status'] == 'needs_observation'
    assert not mem['recall'].get('retreat_recheck') and not mem['recall'].get('anonymous_recheck')


def test_legacy_hero_rescue_without_a_tent_in_view_is_not_blocked_by_the_queue():
    from test_hanjuku_chart_bot import Canvas
    mem = memory()
    mem['recall'] = {'stage': 'hero_focus', 'hero': True, 'steps': 0}
    frame = Canvas().frame()
    assert p.camp_recall_step(MAP, mem, frame) == [p.pad('select')]
    assert p.camp_recall_step(MAP, mem, frame) == [p.pad('a')]
    assert p.camp_recall_step(camp_menu(3), mem, frame) == [p.pad('a')]
    assert mem['recall']['stage'] == 'dest' and mem['retreat_rechecks']['ゼウス']['attempts'] == 0
