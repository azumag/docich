"""Flat mem-key inventory: every flat mutation must be declared (docich#3991).

W 系列契約テスト (test_hanjuku_state_regions の AST 静的検査) と同型。
#3193 の領域移行完了後も ``mem['key'] = ...`` のフラット書き込みは残っており、
未宣言キー (代表例: corridor.py の attack_company_hold、v700 #3992 の
world_map_flags) が章遷移を跨いで残留し得た。本テストは新規フラットキーの
出現をコンパイル時に検出する。

ルール:

- ``mem['k']`` への代入・del・``mem.setdefault``・``mem.update``・
  ``mem.__setitem__``・``mem.pop`` で触れるキーは、モジュール定数解決を
  含めて全て ``KEY_OWNERSHIP`` 宣言済みでなければならない
  (領域 dict 名 world/plan/flow/journal 自身は除く)。
- kind='write' (代入/setdefault/update/__setitem__) と kind='delete'
  (del/pop) は別集計する: pop は「存在すれば消す」防御的変更だが、
  未宣言キーの pop は宣言か除外理由が必要という点では同等。
- lifetime='chapter'/'procedure' の宣言キーは ``CHAPTER_SCOPE_KEYS`` に
  含まれなければならない (宣言とリセットの一致)。
- lifetime='run' の宣言キーは ``CHAPTER_SCOPE_KEYS`` に含まれてはならない。

静的検査の非対応範囲 (既知の blind spot、test_src_has_no_flat_access_to_migrated_keys
と同じ限界): 動的に組み立てたキー名 (``mem[v]`` の ``v`` が変数・f-string)、
``mem`` を別名で受けた変数 (``def f(state): state['k']=...``)、関数経由で
返る辞書への書き込み、import された別モジュール定数、および ``**kwargs``
展開は解決しない。新規にその形でキーが追加された場合は本テストを拡張するか、
除外理由をレビューで示すこと。
"""
import ast
import pathlib

import docich.hanjuku
from docich.hanjuku.state.regions import KEY_OWNERSHIP, chapter_scoped_keys
from docich.hanjuku.strategy.campaign import CHAPTER_SCOPE_KEYS

_REGION_DICT_NAMES = {'world', 'plan', 'flow', 'journal'}
_SRC = pathlib.Path(docich.hanjuku.__file__).parent


def _flat_writes():
    """(file, lineno, kind, key) for literal/constant-resolved flat mem writes.

    kind は 'write' (Subscript Store, setdefault, update, __setitem__) か
    'delete' (Subscript Del, pop)。
    """
    hits = []
    for path in sorted(_SRC.rglob('*.py')):
        if '__pycache__' in path.parts:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        # module-level string constants (e.g. corridor.DRAINED='corridor_drained')
        consts = {}
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                consts[node.targets[0].id] = node.value.value

        def key_of(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.Name):
                return consts.get(node.id)
            return None

        def is_mem_call(node, attr=None):
            return (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == 'mem'
                    and (attr is None or node.func.attr == attr))

        for node in ast.walk(tree):
            keys, kind = [], None
            if (isinstance(node, ast.Subscript)
                    and isinstance(node.value, ast.Name) and node.value.id == 'mem'
                    and isinstance(node.ctx, (ast.Store, ast.Del))):
                key = key_of(node.slice)
                keys, kind = [key], 'write' if isinstance(node.ctx, ast.Store) else 'delete'
            elif is_mem_call(node, 'setdefault') and node.args:
                keys, kind = [key_of(node.args[0])], 'write'
            elif is_mem_call(node, 'pop') and node.args:
                keys, kind = [key_of(node.args[0])], 'delete'
            elif is_mem_call(node, '__setitem__') and node.args:
                keys, kind = [key_of(node.args[0])], 'write'
            elif is_mem_call(node, 'update'):
                kind = 'write'
                for a in node.args:
                    if isinstance(a, ast.Dict):
                        keys += [key_of(k) for k in a.keys]
                keys += [kw.arg for kw in node.keywords if kw.arg]
            if kind is None:
                continue
            for key in keys:
                if key is not None:
                    hits.append((path.name, node.lineno, kind, key))
    return hits


def test_every_flat_write_key_is_declared():
    undeclared = sorted({key for _, _, _, key in _flat_writes()
                         if key not in KEY_OWNERSHIP
                         and key not in _REGION_DICT_NAMES})
    assert not undeclared, (
        'KEY_OWNERSHIP 未宣言のフラット変更キー: '
        + ', '.join(undeclared))


def test_chapter_lifetime_declarations_match_reset_tuple():
    declared_chapter = chapter_scoped_keys()
    reset = set(CHAPTER_SCOPE_KEYS)
    # 章/手順寿命の宣言キーは全てリセット対象 (領域dict名は実体化名として別枠)。
    missing = declared_chapter - reset
    assert not missing, f'リセット未登録の章寿命キー: {sorted(missing)}'
    # run 寿命の宣言キーがリセットに混入してはならない。
    run_leaked = {k for k, spec in KEY_OWNERSHIP.items()
                  if spec['lifetime'] == 'run'} & reset
    assert not run_leaked, f'章リセットに混入したラン寿命キー: {sorted(run_leaked)}'


def test_corridor_keys_are_chapter_scoped_plan_state():
    # docich#3991 の起点: v687 (#3970) で新設された corridor 独自キー群が
    # 未宣言のまま章を跨いで残留し得た。chapter 寿命の PLAN 状態として宣言する。
    for key in ('attack_company_hold', 'corridor_drained', 'corridor_wave'):
        spec = KEY_OWNERSHIP[key]
        assert spec['region'] == 'plan', key
        assert spec['lifetime'] == 'chapter', key
        assert key in CHAPTER_SCOPE_KEYS, key


def test_chapter_transition_clears_corridor_state():
    # _enter_chapter が corridor キーを実際に消すこと (宣言だけでなく挙動)。
    from docich.hanjuku.strategy import campaign
    mem = {'chapter': 9,
           'attack_company_hold': {'target': 'x', 'tick': 1},
           'corridor_drained': {'x': {'kind': 'move', 'tick': 1}},
           'corridor_wave': {'target': 'x', 'tick': 1}}
    campaign._enter_chapter(mem, 10, reason='test')
    for key in ('attack_company_hold', 'corridor_drained', 'corridor_wave'):
        assert key not in mem, key
    assert mem['chapter'] == 10


def test_chapter_transition_clears_representative_leak_keys():
    # +124 の新規リセット対象から代表を選び、章跨ぎで実際に消えることを固定する
    # (旧タプル未登録だった真の漏洩キー)。逆に run 寿命キーが残ることも確認する。
    from docich.hanjuku.strategy import campaign
    chapter_keys = ['defense_wait', 'retake_failures', 'sortie_history',
                    'gold', 'summer_bonus_plan', 'world_map_signature',
                    'enemy_general_menu', 'boss_stock_cancelled_identity']
    run_keys = ['tick', 'stats', 'country_income', 'country_wages',
                'recruit_payroll_pending', 'home_alias_repaired',
                'resume_chapter_pending', 'previous_stats']
    mem = {'chapter': 3}
    for key in chapter_keys + run_keys:
        mem[key] = {'sentinel': True} if key != 'tick' else 7
    campaign._enter_chapter(mem, 4, reason='test')
    for key in chapter_keys:
        assert key not in mem, f'{key} が章跨ぎで残留'
    for key in run_keys:
        assert key in mem, f'{key} が章遷移で消えた (run 寿命のはず)'
    assert mem['chapter'] == 4
