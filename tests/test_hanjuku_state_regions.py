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
                                          KEY_OWNERSHIP, flow, journal,
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


def test_migrated_departure_and_recall_keys_live_in_their_regions():
    # Stage-2: sortie_attempt / launch_probe / recall family moved from
    # declared-only (stage 1) into their owning regions.
    for key, region in (('sortie_attempt', FLOW), ('launch_probe', FLOW),
                        ('launch_miss', FLOW), ('recall', FLOW),
                        ('recall_verification', FLOW), ('recall_retry', FLOW),
                        ('recall_skip', FLOW), ('recall_skip_target', FLOW),
                        ('recall_skip_ticks', FLOW), ('retreat_rechecks', FLOW),
                        ('retreat_camp_episode', FLOW),
                        ('retreat_anonymous_budget', FLOW),
                        ('_recall_inputs', JOURNAL)):
        assert KEY_OWNERSHIP[key]['region'] == region
        assert KEY_OWNERSHIP[key]['migrated'] is True
    # World/plan keys are still declarations only: the procedures read them
    # but the owning layer migrates them in a later stage.
    assert KEY_OWNERSHIP['sorties']['migrated'] is False
    assert KEY_OWNERSHIP['orders']['migrated'] is False
    # W1 (C後続): launched/launched_orders は world 領域へ実移行。
    assert KEY_OWNERSHIP['launched']['region'] == WORLD
    assert KEY_OWNERSHIP['launched_orders']['region'] == WORLD
    assert KEY_OWNERSHIP['launched']['migrated'] is True
    assert KEY_OWNERSHIP['launched_orders']['migrated'] is True
    # W2: retired_sorties も world 領域へ実移行。
    assert KEY_OWNERSHIP['retired_sorties']['region'] == WORLD
    assert KEY_OWNERSHIP['retired_sorties']['migrated'] is True
