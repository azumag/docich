"""State ownership regions: one owner, one lifetime, explicit migration.

docich#3193 C stage 1: the shared flat ``mem`` used to let any procedure
touch any key. ``state.regions`` is now the single declaration of which
region (world / plan / journal / flow) owns each departure/recall key and
how long it lives; migrated keys physically move into ``mem['flow']`` /
``mem['journal']`` and legacy flat leftovers are relocated by the
accessors themselves.
"""
import ast
from pathlib import Path

import pytest

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


_MEM_KEY_CALLS = ('get', 'pop', 'setdefault', 'update')


def _is_mem(node):
    # ``mem`` itself or an attribute spelling such as ``self.mem`` — both were
    # matched by the previous regex's ``\bmem`` and must stay covered.
    return (isinstance(node, ast.Name) and node.id == 'mem') or \
        (isinstance(node, ast.Attribute) and node.attr == 'mem')


def _is_migrated_key_literal(node, migrated):
    return isinstance(node, ast.Constant) and \
        isinstance(node.value, str) and node.value in migrated


def _flat_access_lines(source, migrated):
    """Return sorted line numbers of flat ``mem`` access to migrated keys.

    AST-based (docich#3923): unlike the regex guard this sees multi-line
    calls, ``mem.update({...})`` / keyword arguments, and is not confused by
    ``#`` inside string literals.
    """
    tree = ast.parse(source)
    hits = set()

    for node in ast.walk(tree):
        # mem['key'] — read / write / del are all a Subscript on mem.
        if isinstance(node, ast.Subscript) and _is_mem(node.value):
            if _is_migrated_key_literal(node.slice, migrated):
                hits.add(node.lineno)
            continue

        # mem.get('key', ...) / pop / setdefault / update.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and _is_mem(node.func.value) \
                and node.func.attr in _MEM_KEY_CALLS:
            if node.args:
                first = node.args[0]
                if _is_migrated_key_literal(first, migrated):
                    hits.add(node.lineno)
                elif isinstance(first, ast.Dict) and any(
                        _is_migrated_key_literal(k, migrated)
                        for k in first.keys if k is not None):
                    hits.add(node.lineno)   # mem.update({'key': ...})
            if any(kw.arg in migrated for kw in node.keywords):
                hits.add(node.lineno)       # mem.update(key=...)

        # 'key' in mem / 'key' not in mem.
        elif isinstance(node, ast.Compare) \
                and _is_migrated_key_literal(node.left, migrated):
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, (ast.In, ast.NotIn)) and _is_mem(comp):
                    hits.add(node.lineno)
                    break

    # ``mem.get(k) for k in (<literal tuple>)`` — the key arrives via the
    # comprehension variable (the form used by hanjuku_policy/house
    # pre-#3921).  Names are only collected from generators whose iterable
    # is a literal tuple/list containing a migrated key, so an unrelated
    # comprehension reusing the same variable name elsewhere is not
    # flagged; ``mem.get(k) for k in keys`` (non-literal iterable) stays a
    # known blind spot, matching the old guard.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.ListComp, ast.SetComp,
                               ast.GeneratorExp, ast.DictComp)):
            continue
        bound = set()
        for gen in node.generators:
            if isinstance(gen.target, ast.Name) \
                    and isinstance(gen.iter, (ast.Tuple, ast.List)) \
                    and any(_is_migrated_key_literal(e, migrated)
                            for e in gen.iter.elts):
                bound.add(gen.target.id)
        if not bound:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Subscript) and _is_mem(sub.value) \
                    and isinstance(sub.slice, ast.Name) \
                    and sub.slice.id in bound:
                hits.add(sub.lineno)
            elif isinstance(sub, ast.Call) \
                    and isinstance(sub.func, ast.Attribute) \
                    and _is_mem(sub.func.value) \
                    and sub.func.attr in _MEM_KEY_CALLS \
                    and sub.args and isinstance(sub.args[0], ast.Name) \
                    and sub.args[0].id in bound:
                hits.add(sub.lineno)

    return sorted(hits)


_FLAT_ACCESS_POSITIVE_SNIPPETS = [
    "mem['sorties']",
    "mem['sorties'] = {}",
    "del mem['orders']",
    "self.mem['sorties']",
    "v = mem.get('sorties')",
    "v = mem.pop('orders', None)",
    "mem.setdefault('active', {})",
    "mem.update('sorties')",
    "mem.update({'active': 1})",
    "mem.update(active=1)",
    "v = mem.get(\n    'sorties'\n)",                       # multi-line call
    "v = mem.get(\n    'sorties',\n    {}\n)",
    "flag = 'sorties' in mem",
    "flag = 'orders' not in mem",
    "v = mem.get('sorties')  # trailing comment",
    "s = '# not code'; v = mem['sorties']",                # '#' inside a literal
    "vals = [mem.get(k) for k in ('sorties', 'other')]",
    "vals = [mem[k] for k in ('orders',)]",
    "vals = {k: mem.pop(k) for k in ('active',)}",
    "vals = (mem.get(k) for k in ['sorties'])",
]


@pytest.mark.parametrize('snippet', _FLAT_ACCESS_POSITIVE_SNIPPETS)
def test_flat_access_guard_detects_each_pattern(snippet):
    migrated = {k for k, spec in KEY_OWNERSHIP.items() if spec['migrated']}
    assert _flat_access_lines(snippet, migrated), snippet


_FLAT_ACCESS_NEGATIVE_SNIPPETS = [
    "world(mem)['sorties']",
    "plan(mem).get('orders')",
    "flow(mem).pop('recall', None)",
    "journal(mem).setdefault('_recall_inputs', {})",
    "mem['flow']['recall']",                    # region bucket, not a flat key
    "v = (mem.get(k) for k in keys)",           # non-literal iterable: blind spot
    "s = \"mem['sorties']\"",                 # code inside a string literal
    "s = \"# mem.get('sorties')\"",           # comment-looking text in a literal
    "v = memo.get('sorties')",                  # different variable name
]


@pytest.mark.parametrize('snippet', _FLAT_ACCESS_NEGATIVE_SNIPPETS)
def test_flat_access_guard_ignores_accessor_routed_code(snippet):
    migrated = {k for k, spec in KEY_OWNERSHIP.items() if spec['migrated']}
    assert not _flat_access_lines(snippet, migrated), snippet


def test_src_has_no_flat_access_to_migrated_keys():
    # #3921 (follow-up #3918): migrate_legacy_state replaces the whole region
    # value with any leftover flat value, so a single missed conversion that
    # writes ``mem['sortie_general']`` flat silently drops every other step's
    # data.  Flat *reads* are the same class of bug in reverse: after
    # migration they always observe ``None`` and ignore the region copy.
    # This scan forbids direct ``mem`` access to any migrated key in src/ so
    # the replace semantics can be kept safely: producers must go through the
    # region accessors (``world/plan/flow/journal``), which is also what makes
    # whole-value replacement correct for legacy snapshots.
    # AST-based since #3923; known blind spot: ``mem.get(var)`` where ``var``
    # is a computed (non-literal-iterable) key name cannot be resolved
    # statically — the literal-tuple comprehension form is covered.
    migrated = {k for k, spec in KEY_OWNERSHIP.items() if spec['migrated']}

    src_root = Path(__file__).resolve().parents[1] / 'src' / 'docich'
    violations = []
    for path in sorted(src_root.rglob('*.py')):
        if path.name == 'regions.py' and path.parent.name == 'state':
            continue  # migrate_legacy_state itself owns the flat->region move
        source = path.read_text(encoding='utf-8')
        lines = source.splitlines()
        for lineno in _flat_access_lines(source, migrated):
            violations.append(f"{path}:{lineno}: {lines[lineno - 1].strip()}")
    assert not violations, (
        "flat access to migrated keys must go through the region accessors:\n"
        + '\n'.join(violations))
