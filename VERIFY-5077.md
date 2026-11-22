# VERIFY-5077 — [半熟英雄] ボスフェーズでボス戦闘ゼロ: ch1 クイーン

対象: docich#5077（監査自動起票）/ ラン `g1190-ch1v7` / generation 1190 /
`hanjuku-chart-v1352-password-measured-order` / 月 `ch1 9-10`
調査基盤: worktree `~/work/docich-5077`（main、`8af7f03fe`＝PR #5075 マージ済み、BOT_VERSION
`hanjuku-chart-v1373-egg-receipt-settle`）。read-only 調査。コード・issue 範囲のみ。本番 VM SSH なし。

---

## 結論

**(a) 実欠陥。** ただし欠陥は「ボス戦闘が 0 だったこと」そのものではなく、
**ボスフェーズの 600 秒間・5,622 観測で、実行可能な復旧注文（前線補強 move 2 件）が一度も
`order_start` まで到達しなかった出撃ディスパッチ停滞**にある。

- 「ボス出撃 0 件」は正しい結果である。実測在城が前線 6 城×各 1 将軍・本城未読の状態では、
  `_boss_wave_pick` の `need = 2`（1 将軍城からの出撃はゲームが拒否）を満たす出撃元が
  0 件であり、ボス注文は合法に起票しえない。監査の `no_source` シグナルもこれを指している。
- しかし planner は正しい復旧手順（本城から前線 2 城へ将軍を移し 2 名態勢を作る暫定
  move 計画）を既に立案しており、その 2 件が全期間 `pending` のまま `order_start` 0 件で
  終わった。issue スナップショット上は `next_order` の全フィルタを通過する注文であり、
  600 秒間ディスパッチされなかったのは進行不能（実欠陥）である。
- フェーズ中の観測は防御戦闘（`defense_observed` 21 回・`battle_start` 23 回）・
  月次シーケンス 9 周・全体マップ確認 20 回・野営帰還（`camp_hunt` 16 回）・未読保留
  （`situation_held` 44 回）で占有され、出撃ディスパッチへフレームが回っていない
  （signals: `battle_grind`・`camp_recall_loop`・`unread_hold`・`boss_pursuit_idle`）。
- v1352 → main（v1373＋PR #5075）では同じ停滞系の修正が複数入っており、
  競合する前景ワーク（ステータス画面・召喚 stall・城ジャンプ停滞・野営帰還）は
  軽減されている。ただし本件の残存ギャップ（ディスパッチ保証なし＋未読本城の
  roster probe 非武装）は未対応。

---

## 1. 監査ロジックの検証（issue 数値との対応）

`src/docich/hanjuku/month_audit.py`:

| 数値・事実 | コード | 検証結果 |
|---|---|---|
| 閾値 600.0 秒 | `BOSS_NO_BATTLE_SECONDS = 600`（L51） | 一致 |
| max gap 0.8 秒 | `MAX_GAP_SECONDS = 60`（L58）、`_build_boss_incident` L577 | 60 秒未満 → 高信頼は正当 |
| boss_phase 開始条件 | `_boss_phase`（L446）: 非ボス城がすべて自軍＋ボス城 1 つ以上残存＋章妥当。`captured - lost` に非喪失本城を加算 | captured 6 城＋本城アルマムーン（home_lost なし）＝非ボス城 7/7 保有、残存ボス `けっかい` → フェーズ成立は**正しい** |
| boss_phase 終了条件 | `_track_boss`（L1707）: フェーズ述語が偽・全ボス城保有・`boss_defeated == chapter`・章データ不正 | フェーズ中いずれも非成立 → 継続は正しい |
| ボス戦闘の証拠 | `_boss_battle_seen`（L483）: `boss_defeated`/`boss_victories`/`boss_hp_seen`/`boss_hp_history`/ボス城向け `sortie_history`/ボス敵名の `battle_start`/ボス城 `sortie_outcome`/`boss_defeated*` 決定 | 全経路で 0 件 → 「ボス戦闘 0」は観測ミスではなく実際にボス関係の進行がなかった |
| battle_starts 23 | `_build_boss_incident` L558-563: `battle_start` 全件数と、敵名がボス名/`BOSS_GENERALS` の `boss_battles` を分離 | 23 件は全件通常戦闘（下記 §3） |
| confidence: high | `_confidence`/`_build_boss_incident` L575-583: 観測ギャップ・観測数不足・コンテキスト欠落のみ減点 | 5,622 観測・gap 0.8 秒 → high は正当 |
| signals | `_signals`（L2034）+ `_build_boss_incident` L564-573 | `boss_no_battle`・`boss_hp_missing`（boss_hp 未観測）・`boss_pursuit_idle`（boss_pursuit 系決定 0 件）・`camp_recall_loop`（camp/recall 系 ≥10）・`battle_grind`（battle ≥20）・`unread_hold`（situation_held 等 ≥30）・`no_source`（**在城リストの最大人数 ≤1**, L2068-2070）の 7 件が整合 |

**判定: 監査の分類・信頼度は設計どおり正しい。** 600 秒・5,622 観測の連続データでボス系
エビデンスが全経路 0 件であることは artifact ではなく、実際に「ボスに向けた行動が
一度も起きなかった」ことを意味する。

## 2. ボス出撃が計画され得なかった近因（証明済み）

issue の `world.garrison` 実測値:

```
カストーラ [ロックフォール]  キカンドン [アンディーブ]  ゴーメン [ヴィーナス]
ジョンリギ [ピスタチオ]      スペンソニア [デュオニソス]  ナキューメラ [ゼウス]
アルマムーン （キー自体なし＝将軍リスト未読）
```

- `_boss_wave_pick`（`src/docich/hanjuku/strategy/replan.py` L1168, `need = 2` L1203）:
  コメントどおり「1 将軍しかいない城からの出撃はゲームが拒否する」ため、ボス波には
  出撃元に空き将軍 2 名が必須。全実測城が 1 名、アルマムーンは `garrison.get()` が
  `None`→`()` で 0 名扱い → **`choices` は空。合法なボス波は一件も起票しえない。**
  - probe 経路（`roster_read_due` に基づく roster probe、`arrival_probe_due` に基づく
    到着確認 probe）はどちらも武装条件を欠き、起票対象なし。
- 基本チャートの `1-B1`（主人公・スペンソニア→けっかい、`after=('all_captured',)`）も
  `_ready`（`src/docich/hanjuku/strategy/orders.py` L657）のボス注文ブロックで、
  `_boss_companion_source` が None＋`garrison[source]` 実測あり（[デュオニソス]）で
  **False → 永久に pending**。
- この状況は監査自身が `no_source`（`_signals` L2068-2070: 全在城リストの最大人数 ≤1）と
  `boss_pursuit_idle`（`_build_boss_incident` L568-570: boss_pursuit 系決定 0）として
  正しく診断している。
- planner（`interim_candidates`, replan.py L1025）はこの制約下で正しい代替を選択した:
  pending 計画は暫定 move 2 件（`A:0d4514ed:M17055f` アルテミス アルマムーン→スペンソニア、
  `A:0d4514ed:Mc0c2b5` タルタル アルマムーン→ゴーメン。note「敵城に近い ○○ を厚くする」）。
  前線城を 2 名化すればそこからボス波を起票できる、という設計意図どおりの計画。

## 3. battle_start 23 件の内訳と「発射されなかった」構造

issue の `decisions.counts`（600 秒窓・5622 観測）:

- 戦闘系 ≈330 件: `battle_melee` 114, `monster_menu_choice` 71, `battle_start` 23,
  `battle_result` 21, `battle_draw_hold` 21, `battle_command_open_*` 28,
  `battle_survival` 12, `battle_egg_plan` 11, `summon_dialogue_paced` 9,
  `battle_status_screen_return` 9, `monster_skill_stall`（`before` 尾部で「わらう」「いかる」
  選択と stall 切替を確認）
- 月次 ≈90 件: `month_done`/`month_confirm`/`month_sub_open`/`chikujou`/
  `chikujou_scroll`/`chikujou_max_level_seen`/`post_month_status_*`/`close_panel`
  各 9-36 件（`post_month_status_unverified` 9 件＝どうしの個別状態が読めず
  「通常の出撃計画を保留」を毎週発動）
- 地図補助: `world_map_open`/`world_anchor_from_yview`/`world_map_units` 各 ~20,
  `camp_hunt` 16（野営将軍の探索）、`situation_held` 44、`map_unowned_recenter` 10
- **出撃系 0 件: `order_start` 0, `order_launched` 0, `sortie_retry_deferred` 0,
  `order_precondition_lost` 0, `stale_active_released`/`order_active_orphan` 0,
  `chart_interim_hold` 0, `chart_adjust_applied` 0**

`battle_start` 23 件はすべて通常戦闘: `defense_observed` 21 件（「せめこまれました」
表示＝自城への敵襲）と整合し、残りも召喚・フィールド遭遇系。敵名がボス名／ボス将軍の
`battle_start` は `_build_boss_incident` L560-563 の分類で 0 件と確定。
ボス城 `けっかい` への出撃は「計画されなかった」のではなく、
**起票できない（need=2）＋チャート注文 1-B1 も `_ready` 不通**であり、
かつその前提を作る move 2 件も発射されなかった、が正確な構造。

### 3.1 発射されなかった move 注文のコード経路

`src/docich/hanjuku/flows/map.py` `map_step`:

- L2394 `order = _order(mem)`（orders.py L1827: `plan['active']` 経由。snapshot で
  `active: null` → None）
- L2423 `order is None` かつ `_world_map_wanted` → `world_map_open`（20 件と一致。
  **`next_order` に先立って全体マップ確認が割り込む**）
- L2430 `if order is None:` → `next_order(mem)`（orders.py L1754）→
  `_off_chart`（暫定計画の再構築）→ `next_order` 再評価
- L2450 `_order_start_due`（`ORDER_START_BACKOFF = 12` 観測, L1042-1061）
- L2462 `_sortie_retry_hold`（`flows/house.py` L483: 同一署名取消から
  `SORTIE_RETRY_DEFER_GAP` ≦~100 観測以内のみ延期）
- L2508 `_record('order_start', ...)`

`next_order` のフィルタ（orders.py L1755-1772）を issue スナップショットの値で評価:

| フィルタ | move 2 件への適用 |
|---|---|
| `status ∈ (None,'pending')` | pending → 通過 |
| `_ready` L657 | source/target が章 cells 内・非ボス・非主人公・`after: null` → 通過（抑制ゲート系は月キー保持のため月 9 周で失効） |
| `_source ∈ owned` | アルマムーン（home_lost なし）→ 通過 |
| `_source_list_open` L823 | `garrison.get('アルマムーン')` はキー欠落＝未読 `None`（`!= []`）→ 通過 |
| `_last_castle_held` L2614 | `purpose == 'move'` は対象外 → 通過 |
| `_retake_reserved` L2009 | `_retake_order` 偽（move・非喪失城）→ 通過 |
| `_source_spare_missing` L2105 | `present = None` → 全 `present is not None` 分岐を skip → 通過 |
| `general ∉ _en_route` | `world.sorties` 空 → 通過 |

すなわちエクスポート済み状態の範囲では `next_order` は move を返し、`_order_start_due`
（12 観測）も `_sortie_retry_hold`（~100 観測）も有界のため、**dispatch ブロックへ到達した
フレームが一度でもあれば `order_start` が記録されるはず**である。それが 0 件である以上、

1. `if order is None:` ブロックにほぼ全フレームが到達しなかった（戦闘・月次・
   全体マップ・野営帰還・未読保留という前景ワークによる枯渇）、または
2. `next_order` がエクスポート外状態（`gates`・`general_override`・`source_override`・
   `en_route` の当時値）で None を返し続けた

のいずれかである。`decisions.before` 直近 30 行は「post_month 掃引 → `world_map_open`
（08:20:27）→ `world_map_units` → `defense_observed`（08:20:28）→ `battle_start`（08:20:29）
→ 10 秒超の召喚戦メニュー」と、静かな地図フレームの直後 1 秒以内に防御戦闘が割り込む
パターンを示し、**1. の前景枯渇を強く裏付ける**。`map_unowned_recenter` 10 件
（`orchestrator.py` `map_unowned_input` L284、`MAP_UNOWNED_HOLD_MAX = 10` L49: 所有者なし
map フレームが連続 streak 後に 1 回/11 streak で発火）は、所有者なし map フレームが
存在したがそこでも行動が出なかった（`next_order` が None か静黙 hold）ことを示す。

## 4. 野営帰還・戦闘長引き・未読保留の寄与評価（要件 3）

いずれも `signals`・`decisions.counts` とコードで裏付けられる:

- **戦闘の長引き（主因・最大）**: 防御戦闘 21 回が `battle_melee` 114・
  `monster_menu_choice` 71・`monster_skill_stall`・`battle_draw_hold` 21 を伴い、
  観測の大半を占有。`battle_grind`（battle ≥20）発火。召喚戦の stall 往復
  （「わらう」「いかる」交互選択→2 ターン HP 不変で切替）が個々の戦闘を引き延ばした。
- **月次シーケンス**: 600 秒に `month_done` 9 回（≈66 秒/ゲーム月）。各周で
  `post_month_status_unverified`（どうし）が「通常の出撃計画を保留」を記録し、
  `chikujou_*`・roster 掃引が連続してフレームを所有。
- **野営帰還**: `camp_hunt` 16 件で `camp_recall_loop` 発火（`_signals` L2053-2055,
  camp/recall 系 ≥10）。recall フローが map 系フレームを間欠的に所有。
- **未読画面保留**: `situation_held` 44 ≥30 で `unread_hold` 発火（L2056-2058）。

これらの合算が、実行可能な move 注文のディスパッチを 600 秒間阻害した構造である。

## 5. バージョン差分 v1352 → v1373 ＋ PR #5075（要件 4）

`git log` で v1352（`0eea8379d`/`891709ca5`）から HEAD（`8af7f03fe`）までを確認:

| バージョン / PR | 内容 | 本件との関連 |
|---|---|---|
| v1356 #5006 `b5aa2b875` | 月次確認の地図明け渡しに「試行なし」上限 | 月次の map 占有を抑制（競合ワークの軽減） |
| v1356 #5008 `1705fbd81` | **ボスフェーズ月のゼロボス出撃を issue 起票** | 本 issue の監査機能そのもの（v1352 のランには無い） |
| v1360 #4999/#5014 `771faaa51` | 退却前に卵・切り札を使い切る | 戦闘短縮（battle_grind 軽減） |
| v1362 #5010/#5025 `d76a9d167` | `move_visible` 無し章の野営帰還は `きかん` | camp_recall_loop 軽減 |
| v1366 #5050 `3a01ae711` | おくのて読み取り失敗の固定を防ぐ | 未読保留の軽減 |
| v1367 #5054 `3e3e482f6` | 受理した野営帰還の到着を将軍名で確認 | camp 帰還ループの解消 |
| v1370 #5026 `23741c459` | 築城試行台帳の整合 | 月次ワーク改善 |
| **v1371 #5020/#5062 `a774bcb35`** | **戦闘割り込みによる出撃未確認を独立記録**（`launch_interrupted_by_battle`） | 本件の「発射されなかった」系の可視化手段。v1352 には無く、同種停滞の観測性が向上 |
| v1373 #5045/#5072 `18fe058d5` | 未検証卵回復を後続残高で清算 | 卵管理（間接） |
| **PR #5075 `b2e1407a4`（HEAD）** | ローカルラン停止・進行不能の一括修正: `battle_status_screen_return`（**g1190 2026-11-22 09:00 n=30341 の A 無限進行**）、`battle_menu_plain_advance_limit`、召喚獣「勝てそうな stall で戻らない」＋`battle_summon_card_*`、ボス・召喚戦の切り札先行、将軍ステータス画面 B 復帰＋A 上限、`castle_jump_abandoned`/`chart_interim_abandoned`（**城ジャンプ失敗上限で interim active を abandon**）、camp_world/origin 優先の帰還 | 本件と同じ g1190 系ランで観測された同族の停滞（ステータス画面占有・召喚 stall・ジャンプ停滞・帰還ループ）を直接修正。**競合前景ワークは main で軽減済み** |

**既修正の評価**: 本件の「フェーズ占有ループ」（ステータス画面・召喚 stall・
帰還ハント・ジャンプ停滞）の多くは main で対応済み。一方で (i) `order is None` で
startable 注文があるときのディスパッチ保証、(ii) 未読本城を対象にした roster probe の
非武装（`no_source` 脱出経路が無い）、(iii) `need = 2` のゲーム制約そのものは不変。
**「ボス戦闘 0 が再発しない」とは言い切れない。**

## 6. 修正範囲と方針（(a) 実欠陥のため）

1. **ボスフェーズ中の出撃ディスパッチ保証**（`src/docich/hanjuku/flows/map.py`
   L2423-2430 周辺 + `src/docich/hanjuku/strategy/orders.py` `next_order`）:
   `order is None` かつ startable 注文が存在する状態で `order_start` までの観測数に
   上限を設け、超過時に `order_dispatch_starved` 系の記録＋繰り返し前景ワーク
   （`world_map_open`・`select_focus`・recenter・帰還ハント）の譲歩を入れる。
   現状 `world_map_open` は `next_order` 評価**前**に割り込み（L2423）、
   戦闘・月次・帰還の所有者もフレームを横取りしうるため、dispatch ブロック自体が
   飢餓しうる（本 issue がその実測）。
2. **未読本城の roster probe ギャップ**（`src/docich/hanjuku/strategy/replan.py`
   `_boss_wave_pick` L1168-1220, `interim_candidates` L1025）:
   `no_source`（全実測城 ≤1 将軍）かつ未読城（`garrison` キー欠落）が残るとき、
   `roster_read_due`/`arrival_probe_due` に依らない `source_probe`/`roster_probe` を
   起票して在城リストの実測を取りに行く。本件ではアルマムーンが唯一 2 名化の
   可能性を持つ城でありながら、probe 起票条件（blind launch 依頼・転属到着待ち・
   全城未読）をいずれも満たさず、証拠取得経路が存在しなかった。
3. **監査側**: 検出自体は正しいため変更不要。`signals` は既に `no_source`・
   `boss_pursuit_idle`・`camp_recall_loop`・`battle_grind`・`unread_hold` を正確に
   並べており、`order_start=0` との組み合わせで「計画不足」ではなく
   「dispatch 停滞」と読み分け可能。

## 7. 残る不確定点（判定に影響しないが、原因行の特定に必要）

issue JSON だけでは §3.1 の 1.（フレーム枯渇）と 2.（未エクスポート状態による
`next_order` 抑制）のどちらが支配的かは確定できない。issue の `evidence` が指す
ラン成果物（`hanjuku_decisions.jsonl`・`hanjuku_events.jsonl`・`hanjuku_frames/`、
runtime `g1190-ch1v7`）で次を確認すれば一意に決まる:

- `_tally` 系 `map_step_seen`・`order_start_backoff`・`map_hold_order_due` の件数
  （dispatch ブロック到達回数の直接証拠）
- `hanjuku_events.jsonl` のフレーム `kind` ヒストグラム（'map' フレームが窓内に何回
  あり、各回がどの所有者に消費されたか）
- 当該 tick の `mem` の `gates`・`general_override`・`source_override`・
  `garrison_partial`・`general_location_unknown`・`roster_read_due`・`order_start_tick`
- `cancel_pending`（`obs_cost.sortie_retry.by_key` の `スペンソニア|move`・
  `ゴーメン|move` 行）

## 付録: issue 数値 → コード対応表

| issue の値 | 対応箇所 |
|---|---|
| 継続 600.0 秒 | `month_audit.py` L51 `BOSS_NO_BATTLE_SECONDS`、L1754 閾値比較 |
| 観測 5622 / gap 0.8 | `_track_boss` L1732-1742 の加算、L577 `MAX_GAP_SECONDS` 比較 |
| ボス クイーン / けっかい | `hanjuku_chart` `BOSSES[1]`、`boss_sites(1)` |
| ボス戦闘 0 / boss_hp False | `_boss_battle_seen` L483 の全経路、`boss_hp_seen` L568-569 |
| battle_start 23 / boss_battles 0 | `_build_boss_incident` L558-563 |
| order_start 0 / order_launched 0 | `flows/map.py` L2508 `order_start` 記録、`orders` 集計 L617-618 |
| 出撃元 2 名制約 | `replan.py` L1203 `need = 2`（コメント: 1 将軍城からの出撃はゲームが拒否） |
| 1-B1 未発射 | `orders.py` `_ready` L657 ボスブロック（`_boss_companion_source` is None かつ source 実測あり → False） |
| pending move 2 件 | `plan.chart_plan.orders`（機械計画 `A:0d4514ed:*`、発射ゲートは `map.py` L2448-2457） |
| defense 21 / battle 23 | `defense_observed`→`battle_start` 連鎖（`before` 08:20:28-29 の実例） |
| camp_recall_loop | `_signals` L2053-2055（`camp_hunt` 16 含む camp/recall 系 ≥10） |
| unread_hold | `_signals` L2056-2058（`situation_held` 44 ≥30） |
| no_source | `_signals` L2068-2070（全実測在城 ≤1 将軍） |
