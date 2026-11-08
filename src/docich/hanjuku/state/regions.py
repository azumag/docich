"""領域別の状態所有権: 共有 mem を 4 領域に分割する宣言層 (docich#3193 C).

A 段階で入力成立と観測事実の確定を分離し、B 段階で手順の所有権を
FlowResult 契約にした。C 段階は残る共有状態 ``mem`` のフラットなキー空間に
所有者と寿命を与える。本モジュールはその唯一の宣言場所であり、各領域は
``mem`` 内の専用 dict (``mem['flow']``・``mem['journal']``) として実体化する。

領域と寿命:

- ``WORLD``  観測事実 (garrison・sorties・launched_orders 等)。
             観測層だけが書き、手順は読むのみ。寿命は章。
- ``PLAN``   戦略層の指示・計画 (orders・order_context・chart_plan 等)。
             手順は読むのみ。寿命は章。
- ``JOURNAL`` orchestrator が注入する送信receipt・ラン同一性。
             手順は照合するだけで書かない。寿命はラン (runtime identity)。
- ``FLOW``   手順自身が所有するセッション状態 (departure_request・
             departure_miss 等)。手順終了・中断・章遷移で死ぬ。

段階2で出撃・帰還ドメインの手順セッション状態を実移行済み (migrated=True)。
world/plan キーは観測層・戦略層の所有物として宣言のみのまま (振り分け案:
issue #3193 issuecomment-6585024310)。

``flow(mem)``/``journal(mem)`` の戻り値は遅延ビュー: 読み取りのみの経路で
空領域を ``mem`` に実体化しない。呼び出し側は戻り値を保持せず、都度
アクセサ経由で参照・更新する (ビューは実体化時に mem へ取り付くが、
他経路で領域が作られると古いビューは参照先を失う)。
"""
from __future__ import annotations

WORLD, PLAN, JOURNAL, FLOW = 'world', 'plan', 'journal', 'flow'

# lifetime: 'run' はプロセス/ラン単位、'chapter' は章遷移 (_enter_chapter) で消える、
# 'procedure' は手順の終了・中断で消える (章遷移でも必ず消える)。
KEY_OWNERSHIP = {
    # --- 出撃 FlowSession (段階1: migrated) ---
    'departure_request': {'region': FLOW, 'lifetime': 'procedure', 'migrated': True},
    'departure_miss':    {'region': FLOW, 'lifetime': 'procedure', 'migrated': True},
    # --- 出撃 ExecutionJournal (段階1: migrated) ---
    '_departure_input':  {'region': JOURNAL, 'lifetime': 'run', 'migrated': True},
    # --- 出撃 FlowSession (段階2へ宣言) ---
    'sortie_attempt':    {'region': FLOW, 'lifetime': 'procedure', 'migrated': True},
    'launch_probe':      {'region': FLOW, 'lifetime': 'procedure', 'migrated': True},
    'launch_miss':       {'region': FLOW, 'lifetime': 'procedure', 'migrated': True},
    # --- 帰還 FlowSession (段階2へ宣言) ---
    'recall':               {'region': FLOW, 'lifetime': 'procedure', 'migrated': True},
    'recall_verification':  {'region': FLOW, 'lifetime': 'procedure', 'migrated': True},
    'recall_retry':         {'region': FLOW, 'lifetime': 'procedure', 'migrated': True},
    'recall_skip':          {'region': FLOW, 'lifetime': 'procedure', 'migrated': True},
    'recall_skip_target':   {'region': FLOW, 'lifetime': 'procedure', 'migrated': True},
    'recall_skip_ticks':    {'region': FLOW, 'lifetime': 'procedure', 'migrated': True},
    'recall_skip_history':  {'region': FLOW, 'lifetime': 'procedure', 'migrated': True},
    'retreat_rechecks':     {'region': FLOW, 'lifetime': 'procedure', 'migrated': True},
    'retreat_camp_episode': {'region': FLOW, 'lifetime': 'procedure', 'migrated': True},
    'retreat_anonymous_budget': {'region': FLOW, 'lifetime': 'procedure', 'migrated': True},
    '_recall_inputs':       {'region': JOURNAL, 'lifetime': 'run', 'migrated': True},
    # --- 出撃/帰還が読む PlanState ---
    'orders':           {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},
    'active':           {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},
    'order_context':    {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},
    'sortie_general':   {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},
    'chart_plan':       {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},
    'general_override': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},
    'plan_step_rearm':  {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},
    'source_override':  {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},
    'card_override':    {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},
    'attack':           {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},
    # --- 出撃/帰還が観測する WorldState ---
    'garrison':        {'region': WORLD, 'lifetime': 'chapter', 'migrated': True},
    'garrison_observed_at': {'region': WORLD, 'lifetime': 'chapter', 'migrated': True},
    'general_location_unknown': {'region': WORLD, 'lifetime': 'chapter', 'migrated': True},
    'sorties':         {'region': WORLD, 'lifetime': 'chapter', 'migrated': True},
    'launched_orders': {'region': WORLD, 'lifetime': 'chapter', 'migrated': True},
    'retired_sorties': {'region': WORLD, 'lifetime': 'chapter', 'migrated': True},
    'launched':        {'region': WORLD, 'lifetime': 'chapter', 'migrated': True},
    # --- ラン同一性は常に ExecutionJournal ---
    '_run_identity':   {'region': JOURNAL, 'lifetime': 'run', 'migrated': False},
}

_REGION_KEY = {WORLD: 'world', FLOW: 'flow', JOURNAL: 'journal'}

# 実移行したキー → 収容領域 (migrate_legacy_state の対象表)。
_MIGRATED = {key: spec['region'] for key, spec in KEY_OWNERSHIP.items()
             if spec['migrated']}


class _LazyRegion(dict):
    """まだ実体化していない領域のビュー。

    読み取り系の問い合わせでは ``mem`` を汚さず、最初の実際の変更
    (``__setitem__``・``setdefault``・削除・``update``) で ``mem`` へ
    取り付く。``pop`` は対象キーがあるときだけ実体化するので、
    存在しないキーの no-op ``pop`` は領域を作らない。
    """

    def __init__(self, mem, name):
        super().__init__()
        self._mem, self._name = mem, name

    def _attach(self):
        self._mem[_REGION_KEY[self._name]] = self

    def __setitem__(self, key, value):
        self._attach(); super().__setitem__(key, value)

    def __delitem__(self, key):
        self._attach(); super().__delitem__(key)

    def setdefault(self, key, default=None):
        if key in self:
            return super().__getitem__(key)
        self._attach()
        return super().setdefault(key, default)

    def pop(self, key, *default):
        if key not in self:
            if default:
                return default[0]
            raise KeyError(key)
        self._attach()
        return super().pop(key, *default)

    def update(self, *args, **kwargs):
        if args or kwargs:
            self._attach()
        super().update(*args, **kwargs)

    def clear(self):
        if self:
            self._attach()
        super().clear()


def region(mem, name):
    """領域 dict を返す。存在すれば実体、なければ書込み時に実体化する遅延ビュー。"""
    store = mem.get(_REGION_KEY[name])
    if isinstance(store, dict):
        return store
    return _LazyRegion(mem, name)


def world(mem):
    """WorldState: 観測層が書き、手順は読むのみの観測事実。"""
    migrate_legacy_state(mem)
    return region(mem, WORLD)


def sorties(mem):
    """sorties 読み取りアクセサ (docich#3193 W4 accessor 化)。

    未存在時は空 dict を返し、``mem`` を汚さない。手順側は読み取り専用。
    書き込みは ``_world(mem).setdefault('sorties', {})`` 経由で行う。
    """
    return world(mem).get('sorties') or {}


def flow(mem):
    """FlowSession: 手順が所有するセッション状態。"""
    migrate_legacy_state(mem)
    return region(mem, FLOW)


def journal(mem):
    """ExecutionJournal: 送信receipt・ラン同一性。"""
    migrate_legacy_state(mem)
    return region(mem, JOURNAL)


def migrate_legacy_state(mem):
    """旧フラットキーを宣言領域へ冪等に移す (明示移行)。

    旧スキーマの mem に移行済み宣言キーがフラットに残っていれば領域へ退避
    し、元キーを除去する。領域側に既に値がある場合はフラットの値を優先する
    (未移行プロデューサが毎判断注入する receipt は新しい側が正しいため)。アクセサから遅延呼びするので、テスト等で
    旧形式を直接組み立てた mem でも利用側の変更なく動作する。
    """
    for key, region_name in _MIGRATED.items():
        if key not in mem:
            continue
        store = mem.get(_REGION_KEY[region_name])
        if not isinstance(store, dict):
            store = mem[_REGION_KEY[region_name]] = {}
        # フラットに残っている値は未移行プロデューサ (旧形式テスト注入・
        # orchestrator の毎判断注入) の最新出力とみなし、領域へ上書きする。
        store[key] = mem[key]
        del mem[key]


def chapter_scoped_keys():
    """章遷移で消える宣言キー。_enter_chapter のクール対象の宣言元。"""
    return {key for key, spec in KEY_OWNERSHIP.items()
            if spec['lifetime'] in ('chapter', 'procedure')}
