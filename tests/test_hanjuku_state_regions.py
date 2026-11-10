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


def _flat_migrated_accesses(source, filename='<test>'):
    """AST scan: return ``(lineno, form)`` for direct ``mem`` access to migrated keys.

    docich#3923 (owner review of #3922): the previous line/regex guard missed
    ``#`` inside strings, multi-line calls, and ``mem.update({...})``.  AST
    inspection covers the actual semantics instead of text:

    - ``mem['k']``            subscript read/write/del
    - ``mem.get/pop/setdefault('k'``  call with a migrated key literal
    - ``mem.update({'k': ..})`` / ``mem.update(k=..)``  dict args and kwargs
    - ``'k' in mem`` / ``not in``     Compare against the flat dict
    - ``mem.get(v)`` / ``mem[v]`` where ``v`` is bound by a comprehension or
      ``for`` loop whose iterable is a literal containing migrated keys
      (the hanjuku_policy/house pre-#3921 shape)

    Known blind spot (documented): a key name computed dynamically (e.g.
    ``v = 'act' + 'ive'; mem.get(v)``) is not resolvable statically.
    """
    import ast

    tree = ast.parse(source, filename=filename)
    migrated = {k for k, spec in KEY_OWNERSHIP.items() if spec['migrated']}
    hits = []

    def lit(node):
        return node.value if (isinstance(node, ast.Constant)
                              and isinstance(node.value, str)) else None

    def is_mem(node):
        # ``mem`` itself or an attribute spelling such as ``self.mem`` — the
        # regex guard's ``\bmem`` covered both (#3923).
        return (isinstance(node, ast.Name) and node.id == 'mem') or \
            (isinstance(node, ast.Attribute) and node.attr == 'mem')

    def name_arg(node):
        return node.id if isinstance(node, ast.Name) else None

    # ``mem.get(v)`` / ``mem[v]`` where ``v`` is bound by a literal iterable
    # containing migrated keys.  Scope is limited to the binding construct's
    # own subtree so a reused variable name elsewhere does not flag.
    def scan_indirect(root):
        out = []
        for node in ast.walk(root):
            pairs = []
            if isinstance(node, ast.For):
                pairs.append((node.target, node.iter,
                              node.body + node.orelse))
            elif isinstance(node, (ast.ListComp, ast.SetComp,
                                 ast.GeneratorExp)):
                for g in node.generators:
                    pairs.append((g.target, g.iter,
                                  [node.elt] + g.ifs))
            elif isinstance(node, ast.DictComp):
                for g in node.generators:
                    pairs.append((g.target, g.iter,
                                  [node.key, node.value] + g.ifs))
            for target, iter_node, subtree in pairs:
                var = name_arg(target)
                if not (var and isinstance(iter_node, (ast.Tuple, ast.List, ast.Set))
                        and any(lit(e) in migrated for e in iter_node.elts)):
                    continue
                for use_root in subtree:
                    for use in ast.walk(use_root):
                        if (isinstance(use, ast.Subscript) and is_mem(use.value)
                                and name_arg(use.slice) == var):
                            out.append((use.lineno, f"mem[{var}] via literal iterable"))
                        elif (isinstance(use, ast.Call)
                                and isinstance(use.func, ast.Attribute)
                                and is_mem(use.func.value)
                                and use.func.attr in ('get', 'pop', 'setdefault')
                                and use.args and name_arg(use.args[0]) == var):
                            out.append((use.lineno,
                                        f"mem.{use.func.attr}({var}) via literal iterable"))
        return out

    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and is_mem(node.value):
            key = lit(node.slice)
            if key in migrated:
                hits.append((node.lineno, f"mem[{key!r}]"))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and is_mem(node.func.value):
            attr = node.func.attr
            if attr in ('get', 'pop', 'setdefault'):
                arg = node.args[0] if node.args else None
                key = lit(arg) if arg is not None else None
                if key in migrated:
                    hits.append((node.lineno, f"mem.{attr}({key!r})"))
            elif attr == 'update':
                for arg in node.args:
                    if lit(arg) in migrated:
                        hits.append((node.lineno,
                                     f"mem.update({lit(arg)!r})"))
                    elif isinstance(arg, ast.Dict):
                        for k in arg.keys:
                            if lit(k) in migrated:
                                hits.append((node.lineno, f"mem.update({{{lit(k)!r}: ...}})"))
                for kw in node.keywords:
                    if kw.arg in migrated:
                        hits.append((node.lineno, f"mem.update({kw.arg}=...)"))
        elif isinstance(node, ast.Compare):
            key = lit(node.left)
            if key in migrated and any(
                    isinstance(op, (ast.In, ast.NotIn)) and is_mem(comp)
                    for op, comp in zip(node.ops, node.comparators)):
                hits.append((node.lineno, f"{key!r} in mem"))
    hits.extend(scan_indirect(tree))
    return hits


def test_src_has_no_flat_access_to_migrated_keys():
    # #3921/#3923: migrate_legacy_state replaces the whole region value with
    # any leftover flat value, so a single missed conversion that writes
    # ``mem['sortie_general']`` flat silently drops every other step's data.
    # Flat *reads* are the same class of bug in reverse: after migration they
    # always observe ``None`` and ignore the region copy.  This AST scan
    # forbids direct ``mem`` access to any migrated key in src/ so the
    # replace semantics can be kept safely: producers must go through the
    # region accessors (``world/plan/flow/journal``).
    from pathlib import Path

    src_root = Path(__file__).resolve().parents[1] / 'src' / 'docich'
    violations = []
    for path in sorted(src_root.rglob('*.py')):
        if path.name == 'regions.py' and path.parent.name == 'state':
            continue  # migrate_legacy_state itself owns the flat->region move
        for lineno, form in _flat_migrated_accesses(
                path.read_text(encoding='utf-8'), filename=str(path)):
            violations.append(f"{path}:{lineno}: {form}")
    assert not violations, (
        "flat access to migrated keys must go through the region accessors:\n"
        + '\n'.join(violations))


import pytest


@pytest.mark.parametrize('snippet', [
    "mem['sortie_general'] = {}",
    "x = mem.get('sortie_general')",
    "del mem['orders']",
    "mem.setdefault('order_context', {})",
    "mem.pop('orders', None)",
    "mem.update({'active': None})",
    "mem.update(active=None)",
    "if 'recall' in mem:\n    pass",
    "if 'launched' not in mem:\n    pass",
    # multi-line call: the string guard could not span lines (#3923)
    "mem.get(\n    'launched_orders')",
    # '#' inside a string literal must not truncate the scan (#3923)
    "tag = '#not_a_comment'\nmem['recall'] = {}",
    # key literal reached through a literal-iterable loop variable
    "any(mem.get(k) for k in ('active', 'y_jump'))",
    "for k in ('recall', 'other'):\n    mem[k] = 1",
    # attribute spelling (self.mem) was covered by the regex guard's \bmem
    "self.mem['sorties'] = {}",
    "mem.update('sortie_general')",
])
def test_flat_access_guard_detects_forbidden_shapes(snippet):
    # Each shape is pinned so a future refactor of the scanner cannot
    # silently lose a detection class (#3923).
    assert _flat_migrated_accesses(snippet), snippet


@pytest.mark.parametrize('snippet', [
    "x = _world(mem).get('launched_orders')",
    "_flow(mem)['recall'] = {'stage': 'x'}",
    "mem.get('chart_plan')",                       # declared but unmigrated
    "mem.get('_run_identity')",                    # journal, unmigrated
    "x = mem['chapter']",
    "any(mem.get(k) for k in ('y_jump', 'battle'))",
    "state.get('recall')",
    "x = memo.get('recall')",                    # different variable name
])
def test_flat_access_guard_ignores_accessor_and_unmigrated_forms(snippet):
    assert not _flat_migrated_accesses(snippet), snippet
