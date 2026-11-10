"""State ownership regions: one owner, one lifetime, explicit migration.

docich#3193 C stage 1: the shared flat ``mem`` used to let any procedure
touch any key. ``state.regions`` is now the single declaration of which
region (world / plan / journal / flow) owns each departure/recall key and
how long it lives; migrated keys physically move into ``mem['flow']`` /
``mem['journal']`` and legacy flat leftovers are relocated by the
accessors themselves.
"""
from docich.hanjuku.state import regions
from docich.hanjuku.state.regions import (FLOW, JOURNAL, PLAN, WORLD,
                                          KEY_OWNERSHIP, flow, journal, plan, world,
                                          migrate_legacy_state)
from test_hanjuku_execution_binding import confirm, memory


def test_every_inventoried_key_has_exactly_one_region_and_lifetime():
    # The departure/recall inventory (issue #3193 C proposal) must be fully
    # covered: no key may be unowned or ambiguous between regions.
    for key, spec in KEY_OWNERSHIP.items():
        assert spec['region'] in (WORLD, PLAN, JOURNAL, FLOW), key
        assert spec['lifetime'] in ('run', 'chapter', 'procedure'), key
        assert isinstance(spec['migrated'], bool), key
    assert len(set(KEY_OWNERSHIP)) == len(KEY_OWNERSHIP)
    # Procedure-lifetime flow keys must never be world/plan state.
    for key, spec in KEY_OWNERSHIP.items():
        if spec['lifetime'] == 'procedure':
            assert spec['region'] == FLOW, key


def test_legacy_flat_keys_migrate_into_their_region_once():
    mem = memory()
    mem['departure_request'] = {'step': '1-A3'}
    mem['departure_miss'] = {'1-A3': 1}
    mem['_departure_input'] = {'request_trace': {'d': 1}, 'a_inputs': 1}
    migrate_legacy_state(mem)
    assert flow(mem)['departure_request'] == {'step': '1-A3'}
    assert flow(mem)['departure_miss'] == {'1-A3': 1}
    assert journal(mem)['_departure_input'] == {'request_trace': {'d': 1}, 'a_inputs': 1}
    assert 'departure_request' not in mem
    assert 'departure_miss' not in mem
    assert '_departure_input' not in mem


def test_migration_overwrites_stale_region_value_with_new_flat_output():
    # Per-decision injected receipts: a newer flat write always wins over
    # the region copy left from the previous decision.
    mem = memory()
    mem['_departure_input'] = {'request_trace': {'d': 1}, 'a_inputs': 1}
    assert journal(mem)['_departure_input']['a_inputs'] == 1
    mem['_departure_input'] = {'request_trace': {'d': 2}, 'a_inputs': 0}
    assert journal(mem)['_departure_input']['request_trace'] == {'d': 2}
    migrate_legacy_state(mem)          # idempotent: nothing left to move
    assert journal(mem)['_departure_input']['request_trace'] == {'d': 2}


def test_flow_session_keys_die_with_the_chapter():
    # _enter_chapter clears the 'flow' region wholesale: procedure-owned
    # state can never leak a reservation into the next run.
    from docich.hanjuku.strategy import campaign
    mem = memory(); confirm(mem)
    flow(mem)['departure_request'] = {'step': '1-A3'}
    flow(mem)['departure_miss'] = {'1-A3': 1}
    campaign._enter_chapter(mem, 2, reason='test')
    assert 'flow' not in mem or not mem['flow'].get('departure_request')
    assert 'departure_request' not in mem
    assert 'departure_miss' not in mem


def test_country_income_survives_the_chapter_until_the_next_read():
    # owner 2026-11-04: 現在収入 is a last observation, so a chapter change
    # must not overwrite it with 不明; the next country-status page rewrites it.
    from docich.hanjuku.strategy import campaign
    mem = memory(); confirm(mem)
    mem['country_income'] = {'scope': [1, '1-7'], 'tick': 20, 'income': 300}
    campaign._enter_chapter(mem, 2, reason='test')
    assert mem['chapter'] == 2
    assert mem['country_income'] == {'scope': [1, '1-7'], 'tick': 20, 'income': 300}


def test_country_wages_survive_the_chapter_until_the_next_read():
    # 総賃金 is the same kind of last observation as 現在収入.
    from docich.hanjuku.strategy import campaign
    mem = memory(); confirm(mem)
    mem['country_wages'] = {'scope': [1, '1-7'], 'tick': 20, 'wages': 150}
    campaign._enter_chapter(mem, 2, reason='test')
    assert mem['chapter'] == 2
    assert mem['country_wages'] == {'scope': [1, '1-7'], 'tick': 20, 'wages': 150}


def test_world_region_dies_with_the_chapter_but_journal_survives():
    # W1 follow-up: _enter_chapter must clear the world region (chapter
    # lifetime) while the journal region (run lifetime) is untouched.
    from docich.hanjuku.strategy import campaign
    mem = memory(); confirm(mem)
    world(mem)['launched'] = {'キカンドン': {'general': 'どうし', 'step': '1-A1'}}
    world(mem)['launched_orders'] = {'1-A1': {'step': '1-A1'}}
    journal(mem)['_departure_input'] = {'request_trace': {'d': 2}}
    campaign._enter_chapter(mem, 2, reason='test')
    assert 'world' not in mem
    assert 'launched' not in mem and 'launched_orders' not in mem
    assert mem['journal']['_departure_input']['request_trace'] == {'d': 2}


def test_migrated_departure_and_recall_keys_live_in_their_regions():
    # Stage-2: sortie_attempt / launch_probe / recall family moved from
    # declared-only (stage 1) into their owning regions.
    for key, region in (('sortie_attempt', FLOW), ('launch_probe', FLOW),
                        ('launch_miss', FLOW), ('recall', FLOW),
                        ('recall_verification', FLOW), ('recall_retry', FLOW),
                        ('recall_skip', FLOW), ('recall_skip_target', FLOW),
                        ('recall_skip_ticks', FLOW), ('recall_skip_history', FLOW), ('retreat_rechecks', FLOW),
                        ('retreat_camp_episode', FLOW),
                        ('retreat_anonymous_budget', FLOW),
                        ('_recall_inputs', JOURNAL)):
        assert KEY_OWNERSHIP[key]['region'] == region
        assert KEY_OWNERSHIP[key]['migrated'] is True
    # W4: sorties is now physically owned by world; P2 moved orders to plan too.
    assert KEY_OWNERSHIP['sorties']['region'] == WORLD
    assert KEY_OWNERSHIP['sorties']['lifetime'] == 'chapter'
    assert KEY_OWNERSHIP['sorties']['migrated'] is True
    assert KEY_OWNERSHIP['orders']['migrated'] is True
    # W1 (C後続): launched/launched_orders は world 領域へ実移行。
    assert KEY_OWNERSHIP['launched']['region'] == WORLD
    assert KEY_OWNERSHIP['launched_orders']['region'] == WORLD
    assert KEY_OWNERSHIP['launched']['migrated'] is True
    assert KEY_OWNERSHIP['launched_orders']['migrated'] is True
    # W2: retired_sorties も world 領域へ実移行。
    assert KEY_OWNERSHIP['retired_sorties']['region'] == WORLD
    assert KEY_OWNERSHIP['retired_sorties']['migrated'] is True
    # W3: garrison と派生2キーも world 領域へ実移行（DSH ①②同梱可）。
    for key in ('garrison', 'garrison_observed_at', 'general_location_unknown'):
        assert KEY_OWNERSHIP[key]['region'] == WORLD
        assert KEY_OWNERSHIP[key]['lifetime'] == 'chapter'
        assert KEY_OWNERSHIP[key]['migrated'] is True
    # P1: order_context＋sortie_general は plan 領域へ実移行（deploy.py 単独所有）。
    for key in ('order_context', 'sortie_general'):
        assert KEY_OWNERSHIP[key]['region'] == PLAN
        assert KEY_OWNERSHIP[key]['lifetime'] == 'chapter'
        assert KEY_OWNERSHIP[key]['migrated'] is True
    # P2: orders・active は plan 領域へ実移行。
    for key in ('orders', 'active'):
        assert KEY_OWNERSHIP[key]['region'] == PLAN
        assert KEY_OWNERSHIP[key]['lifetime'] == 'chapter'
        assert KEY_OWNERSHIP[key]['migrated'] is True
    # P3 は未移行のまま（chart_plan 等は宣言のみ）。
    for key in ('chart_plan', 'general_override',
                'source_override', 'card_override', 'attack'):
        assert KEY_OWNERSHIP[key]['migrated'] is False


def test_sorties_flat_migration_prefers_latest_observation_and_is_idempotent():
    # W4: old runtime snapshots may still contain flat sorties. The latest
    # flat observation must replace an older world copy, not coexist with it.
    mem = {'world': {'sorties': {'old': {'status': 'en_route'}}},
           'sorties': {'new': {'status': 'arrived'}}}
    migrate_legacy_state(mem)
    assert 'sorties' not in mem
    assert world(mem)['sorties'] == {'new': {'status': 'arrived'}}
    migrate_legacy_state(mem)
    assert world(mem)['sorties'] == {'new': {'status': 'arrived'}}


def test_sorties_world_read_does_not_create_empty_region():
    # A read-only probe must not mutate an empty state snapshot.
    mem = {}
    assert world(mem).get('sorties') is None
    assert 'world' not in mem


def test_plan_flat_migration_prefers_latest_observation_and_is_idempotent():
    # P1: old runtime snapshots may still contain flat order_context /
    # sortie_general. The latest flat output must replace an older plan
    # copy, not coexist with it.
    mem = {'plan': {'order_context': {'old': {'actual_general': 'old'}},
                    'sortie_general': {'old': 'old'}},
           'order_context': {'1-A3': {'actual_general': 'ゼウス'}},
           'sortie_general': {'1-A3': 'ゼウス'}}
    migrate_legacy_state(mem)
    assert 'order_context' not in mem and 'sortie_general' not in mem
    assert plan(mem)['order_context'] == {'1-A3': {'actual_general': 'ゼウス'}}
    assert plan(mem)['sortie_general'] == {'1-A3': 'ゼウス'}
    migrate_legacy_state(mem)
    assert plan(mem)['order_context'] == {'1-A3': {'actual_general': 'ゼウス'}}
    assert plan(mem)['sortie_general'] == {'1-A3': 'ゼウス'}


def test_plan_read_does_not_create_empty_region():
    # A read-only probe must not mutate an empty state snapshot.
    mem = {}
    assert plan(mem).get('order_context') is None
    assert 'plan' not in mem


def test_plan_region_dies_with_the_chapter_but_journal_survives():
    # P1: _enter_chapter must clear the plan region (chapter lifetime)
    # while the journal region (run lifetime) is untouched.
    from docich.hanjuku.strategy import campaign
    mem = memory(); confirm(mem)
    plan(mem)['order_context'] = {'1-A3': {'actual_general': 'ゼウス'}}
    plan(mem)['sortie_general'] = {'1-A3': 'ゼウス'}
    journal(mem)['_departure_input'] = {'request_trace': {'d': 2}}
    campaign._enter_chapter(mem, 2, reason='test')
    assert 'plan' not in mem
    assert 'order_context' not in mem and 'sortie_general' not in mem
    assert mem['journal']['_departure_input']['request_trace'] == {'d': 2}
