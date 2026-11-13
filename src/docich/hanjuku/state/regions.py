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
P1 で order_context / sortie_general を plan 領域へ移行済み。P2 で orders / active も
plan 領域へ位置移行 (migrated=True)。active は手順の作業ポインタでもあり、
PLAN 契約の将来的な精緻化候補 (docich#3193 P2)。

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
    'camp_hunt_tick': {'region': FLOW, 'lifetime': 'procedure', 'migrated': False},  # 野営探索の記録間隔(v810)
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
    'orders':           {'region': PLAN, 'lifetime': 'chapter', 'migrated': True},
    'active':           {'region': PLAN, 'lifetime': 'chapter', 'migrated': True},
    'order_context':    {'region': PLAN, 'lifetime': 'chapter', 'migrated': True},
    'sortie_general':   {'region': PLAN, 'lifetime': 'chapter', 'migrated': True},
    'chart_plan':       {'region': PLAN, 'lifetime': 'chapter', 'migrated': True},
    'general_override': {'region': PLAN, 'lifetime': 'chapter', 'migrated': True},
    'plan_step_rearm':  {'region': PLAN, 'lifetime': 'chapter', 'migrated': True},
    'source_override':  {'region': PLAN, 'lifetime': 'chapter', 'migrated': True},
    'card_override':    {'region': PLAN, 'lifetime': 'chapter', 'migrated': True},
    'attack':           {'region': PLAN, 'lifetime': 'chapter', 'migrated': True},
    # --- 出撃/帰還が観測する WorldState ---
    'garrison':        {'region': WORLD, 'lifetime': 'chapter', 'migrated': True},
    'garrison_observed_at': {'region': WORLD, 'lifetime': 'chapter', 'migrated': True},
    'garrison_tick':    {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},
    'garrison_partial_tick': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},
    'general_location_unknown': {'region': WORLD, 'lifetime': 'chapter', 'migrated': True},
    'sorties':         {'region': WORLD, 'lifetime': 'chapter', 'migrated': True},
    'launched_orders': {'region': WORLD, 'lifetime': 'chapter', 'migrated': True},
    'retired_sorties': {'region': WORLD, 'lifetime': 'chapter', 'migrated': True},
    'launched':        {'region': WORLD, 'lifetime': 'chapter', 'migrated': True},
    # --- ラン同一性は常に ExecutionJournal ---
    '_run_identity':   {'region': JOURNAL, 'lifetime': 'run', 'migrated': False},
    # --- 棚卸し宣言 (docich#3991): 位置移行していないフラットキー ---
    # migrated=False のまま宣言のみ。region は所有ドメインの記録、lifetime が
    # 章リセット (campaign.CHAPTER_SCOPE_KEYS) の正本になる。
    # --- WORLD: 観測事実 ---
    '_world_units_prev': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 前回観測したマップ部隊リストの差分用保持
    'anchor': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 測定済みアンカー位置
    'army_count_check': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 軍数確認観測
    'arrival_general_observations': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 到着ステータス画面から観測した将軍情報
    'balmung_held': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # ショップで観測したバルムング在庫ホールド(月単位の購入管理)
    'battle': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 現在戦闘の観測事実
    'battle_seen': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 戦闘観測フラグ
    'boss_absent': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # ボス不在観測
    'boss_cancel_cell': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # ボスキャンセル観測セル
    'boss_cell': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # ボス観測セル
    'boss_cell_streak': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 同上連続観測
    'boss_defeated': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # ボス撃破観測
    'boss_hp_history': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 観測したボスHP履歴
    'boss_hp_seen': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 観測ボスHP
    'boss_no_damage': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # ボス無ダメージ観測
    'boss_victories': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # ボス勝利観測
    'camp_status_seen': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 観測済み野営ステータス
    'captured': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 捕獲城の観測事実
    'card_bought_since_read': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 最終パネル読み以降の購入枚数台帳
    'card_bought_unseen': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 未確認購入カード台帳
    'card_held_floor': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 商人拒否で証明された所持下限台帳
    'card_stock': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 観測した所持カード枚数
    'castle_defenders': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 城防衛者観測
    'castle_income': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 城収入観測
    'castle_ownership': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 城所有観測
    'castle_verified': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 城確認済み観測
    'chapter': {'region': WORLD, 'lifetime': 'run', 'migrated': False},  # 現在章番号(観測事実・章跨ぎで保持)
    'country_general_count': {'region': WORLD, 'lifetime': 'run', 'migrated': False},  # 国ステータス観測値・ラン同一性リセットで管理
    'country_income': {'region': WORLD, 'lifetime': 'run', 'migrated': False},  # 「keep until next read」コメント明示の最終観測
    'country_soldier_count': {'region': WORLD, 'lifetime': 'run', 'migrated': False},  # 国ステータス観測値・ラン同一性リセットで管理
    'country_wages': {'region': WORLD, 'lifetime': 'run', 'migrated': False},  # 同上・明示的保持
    'cursor': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 測定済みマップカーソル位置
    'duel_event': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 観測した決闘イベント
    'egg_recheck': {'region': WORLD, 'lifetime': 'run', 'migrated': False},  # 卵再確認・ラン同一性リセット管理
    'egg_types': {'region': WORLD, 'lifetime': 'run', 'migrated': False},  # 観測した卵種類・ラン同一性リセット管理
    'egg_uses': {'region': WORLD, 'lifetime': 'run', 'migrated': False},  # 観測した卵使用回数・ラン同一性リセット管理
    'egg_zero_owners': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 卵0保有者の観測
    'garrison_empty_streak': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 守備空の連続観測
    'general_statuses': {'region': WORLD, 'lifetime': 'run', 'migrated': False},  # 観測した将軍ステータス・ラン同一性リセット管理
    'goal_anchor_lock': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 目標アンカーロック観測
    'gold': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 観測した所持金(month/soldiers_seenと同じ観測面)
    'hanjuku_level': {'region': WORLD, 'lifetime': 'run', 'migrated': False},  # 観測した半熟レベル・次回読み取りまで保持と推測
    'hanjuku_level_seen': {'region': WORLD, 'lifetime': 'run', 'migrated': False},  # 同上の既読管理
    'hanjuku_level_unread': {'region': WORLD, 'lifetime': 'run', 'migrated': False},  # 未読フラグ
    'hanjuku_value': {'region': WORLD, 'lifetime': 'run', 'migrated': False},  # 観測した半熟値
    'hero_max_hp': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 観測した主人公最大HP
    'home_lost': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 本城喪失観測
    'house_eggs': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # ハウス卵保有観測
    'lost': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 喪失城の観測事実
    'map_discovery': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 実測地図発見データ
    'monster_ally_max_hp': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 観測した味方モンスター最大HP
    'month': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 観測した月
    'month_start_tick': {'region': WORLD, 'lifetime': 'run', 'migrated': False},  # 月の開始tick(v802)
    'month_durations': {'region': WORLD, 'lifetime': 'run', 'migrated': False},  # 直近の月の長さ(v802)
    'moving_boss': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 移動ボス観測(リセット登録のみ)
    'moving_boss_streak': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 同上連続
    'off_castle': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 城外観測
    'owner_streak': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 所有者連続観測
    'recruit_roster': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 採用roster観測
    'roofs_seen': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 観測した屋根(os_layer/world)
    'roster_last_complete': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # roster最終完了観測
    'roster_pending_joins': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # roster加入待ち観測
    'season_king_seen': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 季節サイトの王観測
    'season_site_streak': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 季節サイト連続観測
    'season_sites': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 季節サイト観測
    'season_sites_refused': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 拒否済み季節サイト
    'soldiers_seen': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 観測兵数
    'soldiers_seen_basis': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 同上根拠
    'soldiers_seen_key': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 同上キー
    'soldiers_seen_scope': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 同上スコープ
    'sortie_history': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 出撃結果履歴(章の戦績)
    'source_mismatch_castle': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 出元不一致城の観測記録
    'stats': {'region': WORLD, 'lifetime': 'run', 'migrated': False},  # 最終ステータス観測・new_game時previous_statsへ保持
    'world': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 領域dict自体の防御的pop
    'world_flags': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 観測世界フラグ(os_layer/world)
    'world_map_flags': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 全体マップで観測した城別所有フラグ累積 (v700 #3992)
    'world_map_signature': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 全体マップ観測署名
    'world_map_pending': {'region': WORLD, 'lifetime': 'chapter', 'migrated': False},  # 所有フラグの2回一致待ち(v705)
    'previous_stats': {'region': WORLD, 'lifetime': 'run', 'migrated': False},  # new_game時に退避される前ランstats (mem.update経由)
    # --- PLAN: 戦略層の計画・ホールド ---
    'attack_company_hold': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # chapter9 corridor専用の突撃保留(戦略層)
    'attack_defeat_hold': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # chapter9 corridorの連続敗北クールダウン(v717)
    'attack_underpowered': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # chapter9 corridorの攻撃見送り記録(v725)
    'boss_cell_probe': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # ボスセルプローブ割当(リセット登録のみ・兄弟のboss_step_probeに同じ)
    'boss_egg_wait': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # replan戦略層のボス卵待ち計画
    'boss_probe_rotation': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # ボスプローブ割当の戦略管理
    'boss_relaunch': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # ボス再出撃の戦略管理
    'boss_step_probe': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 同上
    'boss_step_rearm': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 同上
    'cards_capped': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # カード上限の戦略記録
    'chapter_transition_pending': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # finish_chapterで立ち次章突入で消費される遷移フラグ
    'chart_adjust': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # チャート補正計画
    'chart_adjust_signature': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 適用済みチャート補正の署名(章地図に紐付く)
    'chart_edition': {'region': PLAN, 'lifetime': 'run', 'migrated': False},  # 新規ゲーム開始時にピンされるチャート版
    'chart_plan_tick': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # チャート計画の策定tick
    'corridor_drained': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # chapter9 corridorの枯渇管理(定数DRAINED経由)
    'corridor_probe': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # chapter9 corridorの城一覧確認予算(v707)
    'front_guard_hold': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # chapter9 前線1人の待ち時計(v735)
    'front_guard_expired_tick': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 同tick内の期限切れ固定(v769)
    'front_mass_hold': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # chapter9 2人同時出撃の待ち時計(v757)
    'front_mass_expired_tick': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 同tick内の期限切れ固定(v769)
    'front_mass_count': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 前線の人数変化で待ち時計をリセット(v776)
    'defense_seen': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 直近にせめこまれた城(v765)
    'corridor_wave': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # chapter9 corridorのwave管理(定数WAVE経由)
    'defense_break_blocked': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 防衛突破阻止の戦略ガード
    'defense_wait': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 防衛待ち計画
    'failed_local_step': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 失敗したローカルstepの戦略記録
    'goal_departed': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 目標出発済みの計画記録
    'goals': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 戦略目標
    'goals_overflow': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 同上オーバーフロー
    'hero_repair_deferral': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 主人公修復延期(リセット登録のみ)
    'home_alias_repaired': {'region': PLAN, 'lifetime': 'run', 'migrated': False},  # 本城名エイリアス一回限り修復フラグ
    'kit_spent': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # キット消費の戦略記録
    'last_card_assessment': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 判定層のカード評価キャッシュ
    'map_idle_tick': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # マップアイドルtick(goals計画管理)
    'order_start_tick': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 指示開始tickの計画簿記
    'plan': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 領域dict自体の防御的pop
    'recruit_hold': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 採用保留(経済戦略も書く計画ホールド)
    'recruit_payroll_pending': {'region': PLAN, 'lifetime': 'run', 'migrated': False},  # 章突入時に前章rosterから設定され次章へ持ち越す
    'recruit_roster_floor': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 採用roster下限(リセット登録のみ)
    'retake_failures': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 奪還失敗の戦略記録
    'retries_retired': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 退役リトライの戦略記録
    'route_probe_read_tick': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 経路プローブ読み取りtick
    'shop': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 購入計画(戦略economyが書く)
    'site_step_rearm': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # サイトstep再武装の戦略管理
    'sortie_cancelled_observed_counts': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 出撃キャンセル観測カウント(orders調整用)
    'sortie_cancelled_observed_gates': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 同上ゲート
    'sortie_held_source_defender_cancel_counts': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 兄弟キー(orders書き)に同じ戦略カウント
    'sortie_held_source_defender_counts': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 出撃保留元防衛者カウント(orders戦略)
    'sortie_held_source_defender_gates': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 同上ゲート
    'sortie_relaunch': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 出撃再発進の戦略管理
    'sortie_source_defender_cancel_counts': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 同上
    'sortie_source_defender_cancel_gates': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 同上
    'source_defender_cancel_counts': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 同上
    'source_defender_cancel_gates': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 同上
    'source_miss': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 出元ミスの戦略記録
    'unplaced_refuted': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 配置否認の戦略記録
    'unplaced_refuted_castles': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 同上城集合
    'variant': {'region': PLAN, 'lifetime': 'run', 'migrated': False},  # チャート有無バリアント・ラン全域の戦略変数
    'plan_stale_signature': {'region': PLAN, 'lifetime': 'chapter', 'migrated': False},  # 陳腐計画署名の防御的pop対象
    # --- FLOW: 手順セッション状態 ---
    '_map_registration_hold': {'region': FLOW, 'lifetime': 'procedure', 'migrated': False},  # decide冒頭で毎回popされる一時ホールド
    'align_offset': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # ナビゲーションのアライン補正セッション状態
    'align_stall': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # ナビゲーション停滞検出
    'align_steps': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # アラインstep数(ナビセッション)
    'approx_fallback': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 近似マップフォールバック
    'approx_localize_miss': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 近似ローカライズミス
    'arrival_probe_tick': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 到着確認プローブの時刻管理
    'arrival_status_probe': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 到着ステータス確認手順のプローブ状態
    'arrival_status_probe_history': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # プローブ履歴(手順セッション付随)
    'battle_draw_hold': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 描画途中ホールドの自己消去型カウンタ
    'battle_menu_hold': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 戦闘メニュー停滞監視の自己消去型カウンタ
    'battle_menu_pending_ticks': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 戦闘メニューpending手順の待機tick
    'bijin_gift_hold': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 美女贈物プロンプト手順のホールド
    'bijin_gift_waits': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上の待機カウンタ
    'bijin_guard': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 美女イベントのプロンプトガード
    'boss_camp_exempt': {'region': FLOW, 'lifetime': 'run', 'migrated': False},  # ラン同一性リセットリスト(orchestrator:586)で管理される月次免除
    'boss_camp_exempt_scope': {'region': FLOW, 'lifetime': 'run', 'migrated': False},  # 同上のスコープ
    'boss_card_hold': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 出撃手順のボス用カードホールド
    'boss_card_hold_scope': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上スコープ
    'boss_entry_hold': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # ボス突入ホールド手順状態
    'boss_sortie_menu_hold': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # ボス出撃メニューのエスケープ状態
    'boss_stall_escape_tick': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # ボス停滞脱出の時刻
    'boss_stock_cancelled': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # ボス用カード在庫キャンセル手順
    'boss_stock_restart': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上再起
    'camp_hp_probe': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 野営HP確認プローブ手順状態
    'camp_hp_probe_counts': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上カウント
    'card_drop': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # カードドロップ手順状態
    'card_miss': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # カードミス手順状態
    'card_scroll': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # カードスクロール手順状態
    'card_scroll_last': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上最終値
    'card_skip_departure': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # カード選択スキップ出撃の手順状態
    'card_unreadable': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # カード読取不能手順状態
    'card_upper_boundary': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # カードリスト上端検出の手順状態
    'card_zero_at_start': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 開始時カード0検出手順状態
    'carry_fallback_scope': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 月次carry手順のフォールバックスコープ
    'carry_fallback_window': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上ウィンドウ
    'carry_manifest_failed_scope': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # carry明細失敗スコープ
    'carry_manifest_scope': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # carry明細スコープ
    'carry_tracking': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # carry追跡の手順状態
    'castle_jump': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 城ジャンプナビゲーション状態
    'castle_jump_skip': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上スキップ
    'castle_status_as_list': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 城ステータスをリストとして読む手順状態
    'chapter_end_save': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 章末セーブ手順状態
    'chapter_probe': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 章確認プローブ手順状態
    'chikujou_leftover': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 築城残りテキスト処理の手順状態
    'concert_picker_watch': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # コンサートpicker停滞監視の自己消去型
    'cursor_card_downs': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # カード選択カーソル下移動の手順状態
    'cursor_pick_downs': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上
    'cursor_still': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # カーソル停滞検出
    'discharge': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 解雇メニュー手順セッション
    'dropper_card_kit': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # ドロップ用カードキット手順状態
    'egg_action': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 卵メニューアクション手順
    'egg_battle': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 卵戦闘手順セッション
    'egg_battle_row_dead': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 卵戦闘メニュー行の死亡管理
    'egg_choice': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 卵選択メニュー手順状態
    'egg_key': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上キー
    'egg_menu_stage': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 卵メニュー段階
    'egg_needed': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 必要卵判定の手順状態
    'egg_retreat_flow': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 卵退避手順セッション
    'egg_row_dead': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 卵行死亡管理
    'egg_row_unavailable_logged': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 行不可ログ重複防止フラグ
    'equivalent_launch_miss': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同等発進ミス手順状態
    'equivalent_sortie_cancels': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同等出撃キャンセルガード手順状態
    'event_text_watch': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # イベント文面停滞監視の自己消去型
    'expect_menu': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # メニュー期待のナビ状態
    'flow': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 領域dict自体の防御的pop
    'general_list_hold': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 将軍リスト画面ホールド
    'general_list_scan': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 将軍リスト走査手順状態
    'held_phase': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 保留フェーズ記録の自己消去型
    'hero_broken_sortie_cancel': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 主人公破損出撃キャンセル手順状態
    'hero_broken_sortie_gates': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上ゲート
    'hero_select_miss': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 主人公選択ミス
    'house': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # ハウス手順セッション
    'house_entry_failures': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # ハウス侵入失敗カウンタ
    'house_entry_miss': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上ミス
    'house_field_scan': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # ハウスフィールド走査手順状態
    'house_nav': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # ハウスナビ(リセット登録のみ)
    'house_scan_month': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上月
    'house_scan_tick': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上tick
    'house_status_recheck': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # ハウスステータス再確認(roster手順)
    'indep_menu': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 独立メニュー手順状態
    'indep_menu_action': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上アクション
    'indep_menu_key': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上キー
    'lampkin_cycle': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # ランプキン周期手順状態
    'last_castle_hold': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 最終城ホールド手順状態
    'last_house_deferral': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # ハウス延期記録
    'map_registration': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 地図登録手順セッション
    'melee_opening_candidate': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 戦闘開始候補の手順状態
    'menu_hold': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # メニューホールド
    'menu_miss': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # メニューミス
    'monster_menu_choice': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # モンスターメニュー選択手順
    'monster_menu_choice_hp': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # モンスターメニュー選択HP記録
    'monster_menu_choice_key': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上キー
    'monster_menu_choice_policy_epoch': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上ポリシーepoch
    'monster_menu_choice_scope': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上スコープ
    'monster_menu_cursor': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上カーソル
    'monster_menu_cursor_recheck': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上再確認
    'monster_menu_hold': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上ホールド
    'monster_menu_key': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上キー
    'monster_menu_pending_ticks': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # pending待機tick
    'monster_own_choice': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # モンスター選択の手順状態
    'monster_panel': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # モンスターパネル手順状態
    'monster_stall': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # モンスターメニュー停滞
    'monster_unchosen': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 未選択モンスター管理
    'month_exit': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 月退出手順状態
    'month_exit_refusals': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 月メニュー退出拒否カウンタ
    'month_nav': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 月ナビ(リセット登録のみ)
    'month_roster_scan': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 月roster走査手順状態
    'month_status_complete': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 月ステータス完了フラグ
    'month_sub': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 月サブ状態
    'month_text_advance': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 月テキスト送り手順状態
    'name': {'region': FLOW, 'lifetime': 'run', 'migrated': False},  # 名付け手順のdoneが新規ゲーム検出に使われラン跨ぎ
    'nav_last': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 直近ナビゲーション状態
    'nav_pressed': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 押下済みナビ
    'nav_prev': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 前回ナビ
    'nav_recovers': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # ナビゲーション回復カウント
    'nav_search': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # ナビ探索
    'nav_search_leg': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上レグ
    'nav_still': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # ナビ停滞
    'near_goal': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 目標近接ナビ状態
    'orphan_battle_menu': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 孤児戦闘メニュー管理
    'payroll_membership_hold': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # roster手順の給与メンバーシップホールド
    'picked': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 選択済み手順状態
    'post_month_status': {'region': FLOW, 'lifetime': 'run', 'migrated': False},  # 月次ステータス手順・ラン同一性リセット管理
    'post_month_status_last': {'region': FLOW, 'lifetime': 'run', 'migrated': False},  # 同上の最終値
    'rare_card_kit': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # レアカードキット手順状態
    'rare_scan': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # レア走査手順状態
    'rare_scan_month': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # レア走査月(リセット登録のみ)
    'reanchor_resume': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 再アンカー後の復帰手順状態
    'recruit_field_scan_attempts': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 採用フィールド走査試行
    'recruit_month_scan_attempts': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上月度
    'recruit_roster_attempts': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 採用roster試行(リセット登録のみ)
    'recruit_roster_recheck': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 採用roster再確認手順
    'recruit_verification': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 採用検証手順状態
    'resume_intro_clues': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 再開イントロ手がかりの手順状態
    'retries': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 戦闘リトライ手順状態
    'retry_context': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上コンテキスト
    'roster_carry_source': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # roster手順のcarry元簿記
    'roster_read_due': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # roster読み取り期限
    'roster_status_walk': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # rosterステータス巡回手順
    'roster_survey': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # roster調査手順状態
    'roster_survey_scope': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上スコープ
    'select_centered': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # SELECT中央揃えナビゲーション状態
    'select_focus_tick': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上のtick
    'select_tick': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # SELECT tick管理
    'select_used': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # SELECT使用済み
    'sortie_actor_miss': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 出撃者ミス手順状態
    'sortie_confirm_blind': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 出撃確認ブラインド手順状態
    'sortie_confirm_miss': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 出撃確認ミス
    'source_location_rechecks': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 出元位置再確認手順
    'starter_party': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 初期パーティ手順状態
    'strong_card_kit': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 強カードキット手順状態
    'strong_card_reserved': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 強カード予約手順状態
    'summer_bonus_choice': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 夏ボーナス選択手順状態
    'summer_bonus_plan': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上計画
    'summon_dialogue': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 召喚ダイアログペース管理
    'target_cancel': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 目標キャンセルナビ
    'target_miss': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 目標ミス
    'trade_guard': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 交易ガード(phase!=dialogueで毎回解放)
    'unanchored': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 未アンカーナビ状態
    'uncertain': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 不確実ナビフラグ
    'unknown_hold_watch': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 不明画面停滞監視の自己消去型
    'unverified': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 未検証ナビ状態
    'world_map_close': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 全体マップ閉鎖手順
    'world_map_due': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 全体マップ期限
    'world_map_tick': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上tick
    'world_map_wait': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上待機
    'y_jump': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # Yジャンプナビ
    'y_jump_last': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # Yジャンプナビゲーション状態
    'y_jump_return': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上復帰
    'y_jump_target_closed': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上目標閉鎖
    'y_jump_target_confirmed': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上目標確認
    'y_jump_unreachable': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上到達不能管理
    'y_jumped': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上完了
    'y_jumps': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上回数
    'y_jumps_total': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上累計
    'boss_stock_cancelled_identity': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # ボス在庫キャンセル同一性マーカー
    'enemy_general_menu': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 敵将軍メニュー手順の保留状態
    'enemy_general_menu_pending': {'region': FLOW, 'lifetime': 'chapter', 'migrated': False},  # 同上pending待機
    # --- JOURNAL: 注入入力・ラン同一性・集計 ---
    '_adjusted': {'region': JOURNAL, 'lifetime': 'run', 'migrated': False},  # 決定ごとに注入される入力バッファで宣言済み _departure_input/_recall_inputs と同列
    '_card_scroll_input': {'region': JOURNAL, 'lifetime': 'run', 'migrated': False},  # 同上・注入入力バッファ
    '_experience': {'region': JOURNAL, 'lifetime': 'run', 'migrated': False},  # 経験メモ注入バッファでnew_game時も保持される
    '_interim': {'region': JOURNAL, 'lifetime': 'run', 'migrated': False},  # 注入入力バッファ
    '_records': {'region': JOURNAL, 'lifetime': 'run', 'migrated': False},  # decisionレコードの出力バッファ
    'chapter_evidence': {'region': JOURNAL, 'lifetime': 'run', 'migrated': False},  # 章確定の証拠をラン全域で保持
    'obs_cost': {'region': JOURNAL, 'lifetime': 'run', 'migrated': False},  # 観測コスト集計・決定から読まれない診断
    'obs_cost_dropped': {'region': JOURNAL, 'lifetime': 'run', 'migrated': False},  # 同上ドロップ集計
    'survey_run_identity': {'region': JOURNAL, 'lifetime': 'run', 'migrated': False},  # 調査済みラン同一性
    'tally': {'region': JOURNAL, 'lifetime': 'run', 'migrated': False},  # decision_log集計
    'tick': {'region': JOURNAL, 'lifetime': 'run', 'migrated': False},  # ラン内観測時計(ラン本体が注入する実行メタ)
    'resume_battle_menu': {'region': JOURNAL, 'lifetime': 'run', 'migrated': False},  # 復元注入の戦闘メニュー再開フラグ
    'resume_chapter_pending': {'region': JOURNAL, 'lifetime': 'run', 'migrated': False},  # 復元注入の章未確定フラグ
    'resume_egg_ritual': {'region': JOURNAL, 'lifetime': 'run', 'migrated': False},  # 復元注入の卵儀式再開フラグ
    'resume_sortie_cancel': {'region': JOURNAL, 'lifetime': 'run', 'migrated': False},  # 復元注入の出撃キャンセル再開フラグ
}

_REGION_KEY = {WORLD: 'world', PLAN: 'plan', FLOW: 'flow', JOURNAL: 'journal'}

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


def plan(mem):
    """PlanState: 戦略層の計画 (order_context・sortie_general 等)。

    P1 (docich#3193): order_context + sortie_general を mem['plan'] へ位置移行。
    未存在時は空 dict を返し、``mem`` を汚さない。書き込みは
    ``_plan_region(mem).setdefault(...)`` 経由で行う（``_plan`` は
    ``strategy.economy._plan`` と衝突するため、層モジュール側の別名は
    ``_plan_region`` を使う）。
    """
    migrate_legacy_state(mem)
    return region(mem, PLAN)


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
        # この置換意味論は src 側にフラットアクセスが残らないことが前提:
        # tests/test_hanjuku_state_regions.py::test_src_has_no_flat_access_to_migrated_keys
        # が移行済みキーへの直接 mem アクセスを静的に禁止する (docich#3921)。
        store[key] = mem[key]
        del mem[key]


def chapter_scoped_keys():
    """章遷移で消える宣言キー。_enter_chapter のクール対象の宣言元。"""
    return {key for key, spec in KEY_OWNERSHIP.items()
            if spec['lifetime'] in ('chapter', 'procedure')}
