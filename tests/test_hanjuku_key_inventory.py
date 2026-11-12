"""Flat mem-key inventory: every flat write must be declared (docich#3991).

W 系列契約テスト (test_hanjuku_state_regions の AST 静的検査) と同型。
#3193 の領域移行完了後も ``mem['key'] = ...`` のフラット書き込みは残っており、
未宣言キー (代表例: corridor.py の attack_company_hold) が章遷移を跨いで
残留し得た。本テストは新規フラットキーの出現をコンパイル時に検出する。

ルール:

- ``mem['k']`` への書き込み・削除・``mem.setdefault`` のキーは、
  モジュール定数解決を含めて全て ``KEY_OWNERSHIP`` 宣言済みでなければならない
  (region dict 名 world/plan/flow/journal 自身は除く)。
- lifetime='chapter'/'procedure' の宣言キーは ``CHAPTER_SCOPE_KEYS`` に
  含まれなければならない (宣言とリセットの一致)。
- lifetime='run' の宣言キーは ``CHAPTER_SCOPE_KEYS`` に含まれてはならない。
"""
import ast
import pathlib

import docich.hanjuku
from docich.hanjuku.state.regions import KEY_OWNERSHIP, chapter_scoped_keys
from docich.hanjuku.strategy.campaign import CHAPTER_SCOPE_KEYS

_REGION_DICT_NAMES = {'world', 'plan', 'flow', 'journal'}
_SRC = pathlib.Path(docich.hanjuku.__file__).parent


def _flat_writes():
    """(file, lineno, key) for literal/constant-resolved flat mem writes."""
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

        for node in ast.walk(tree):
            if (isinstance(node, ast.Subscript)
                    and isinstance(node.value, ast.Name) and node.value.id == 'mem'
                    and isinstance(node.ctx, (ast.Store, ast.Del))):
                key = key_of(node.slice)
            elif (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == 'mem'
                    and node.func.attr in ('setdefault',)
                    and node.args):
                key = key_of(node.args[0])
            else:
                continue
            if key is not None:
                hits.append((path.name, node.lineno, key))
    return hits


def test_every_flat_write_key_is_declared():
    undeclared = sorted({key for _, _, key in _flat_writes()
                         if key not in KEY_OWNERSHIP
                         and key not in _REGION_DICT_NAMES})
    assert not undeclared, (
        'KEY_OWNERSHIP 未宣言のフラット書き込みキー: '
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
