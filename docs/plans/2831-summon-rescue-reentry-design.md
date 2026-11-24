# #2831 / #2863 / #2864 救済再評価 — descope 後の実績記録

> **状態**: PR #2882 merge 時点 (head `618e427`, 2026-10-28) で DSH 指示の
> option A descope が適用済み。本書は descope 後の実態に同期した記録であり、
> 当初設計のうち実装されなかった部分は「未実装 (descope)」と明記する。
> 「未実装」と書かれた経路・kind・テストを実装済みと読み替えないこと。

## 0. スコープと最終状態

- #2831 (召喚戦 rescue re-entry): **descope**。単一基準述語・三段接続は未実装。
- #2864 (低 HP での奥の手安全候補): **実装済み** (#2864, #2882, #3076)。
- #2863 (conserve 温存): **実装済み** (#3065、本書旧 §9 の draft を実装)。
- #2896 (full-HP advantage guards): **実装済み** (#3160)。

## 1. 問題 (g1059)

- 06:27 月次で卵全回復済み (在庫不足ではない)。
- 06:29:54 戦闘開始時に救済手段を確認 (一度きり)。
- 06:30〜06:36 の召喚戦は「きゅうしゅう」反復選択のみ。
- 06:36:24 1 戦目終了 (敗北)。#2864 ガード拒否文言なし。
- 06:36:29 2 戦目開始、不利判定あり。観測継続。
- 原本 #2831 の城攻め二連戦実例は証跡が取れない可能性が高い → native 再現で担保。

## 2. descope 後に実装されているもの

### 2.1 okunote 低 HP 分岐の candidate-first モデル (実装済み)

`flows/battle_menus.py::okunote_step` の低 HP 分岐 (hp unread or
`hp <= OKUNOTE_MAX_SELF_DAMAGE`、かつ last_resort/egg_preempt 以外):

- 画面で観測済みの `OKUNOTE_CHOICES` から `OKUNOTE_SELF_DAMAGE_CHOICES`
  (`{'ヤケクソ'}`, os_layer/screen.py) を除いた安全候補を先に列挙する
  (#2102 整合: 未観測は安全とみなさない)。
- 安全候補なし/未観測、または `survival.exhausted` →
  `battle_okunote_risk_declined` を記録して白兵へ戻る。
- 安全候補あり + `_hold_melee_egg` (低 HP でも白兵優位) →
  `battle_okunote_risk_conserved` を記録して白兵へ戻る (#3065)。
- 安全候補あり → `battle_okunote_risk_accepted` を記録して開封へ進む
  (emitter は #2882 descope コミットで追加、kind 登録は #3161)。

### 2.2 re-arm (潮目変化の exhausted 再解除) — 部分実装

- `judgment/battle.py::_rescue_rearm` は残存し、`RESCUE_REARM_LIMIT`
  (= `HERO_SURVIVAL_REOPEN_LIMIT`) で bounded。発火時に
  `battle_rescue_rearmed` を記録する (SPOKEN 登録済み, #2882)。
- **call site は `okunote_step` 低 HP 分岐の 1 箇所のみ** (`{'owner': 'ally'}`)。
  当初設計の三段接続 — ①battle_start 後の初期評価、②monster_menu /
  battle_menu / egg_battle 各 step での再評価、③reset_battle_controls での
  carryover 検出 — は未実装 (descope)。
- `reset_battle_controls` には `rescue_reentry_count` / `rescue_rearm_count`
  の pop のみ残る (carryover 検出・`battle_rescue_carryover_cleared` 記録は
  descope コミットで除去)。`RESCUE_REENTRY_LIMIT` 定数は残るが re-entry
  経路は未実装。

### 2.3 #2863 conserve (温存) — 実装済み (#3065)

低 HP + 観測済み安全候補 + `_hold_melee_egg` 真 → `battle_okunote_risk_conserved`
を記録し白兵へ戻る。`risk_declined` / `risk_conserved` は flow ラッチで
1 戦闘 1 記録、`risk_accepted` のラッチは未実装 (follow-up, 旧 §9.5)。

### 2.4 #2896 full-HP advantage guards — 実装済み (#3160)

`_full_hp_melee_advantage` による `card_damage_rescue_needed` ラッチ抑制と
`_default_retreat_needed` 拡張。`tests/test_hanjuku_issue_2896.py` (6 tests)。

## 3. 未実装のままのもの (descope / follow-up)

- 単一基準述語 `_rescue_decision(mem, cur, context)` と技単位の 2 層候補モデル
  (旧 §2.1/§2.4)。現行は flows 内の inline 分岐。
- 三段接続 (旧 §2.2): ①②③ すべて未実装 (§2.2 参照)。
- carryover 検出と `battle_rescue_carryover_cleared` (旧 §3.3)。
- 未記録 kind: `battle_rescue_reentry`, `rescue_decision`,
  `battle_rescue_carryover_cleared` — emitter なし (descope `618e427`)。
- 回帰テスト: (a) 城攻め二連戦 re-entry、(b) g1059 召喚戦中再評価の
  native 再現は未実装。`battle_rescue_rearmed` emitter の単体テストも未整備。
- `battle_okunote_risk_accepted` のラッチ追加 (旧 §9.5 note)。
- follow-up: #2831 (re-entry 本体)、#2864 の残作業 (開封前候補観測)、
  #2863 thread。各 issue で状態を追跡する。

## 4. decision kind の現状 (旧 §5 更新)

実装済み emitter (decision log に記録される kind):

- `battle_okunote_risk_declined` / `battle_okunote_risk_accepted` /
  `battle_okunote_risk_conserved` — okunote 低 HP 分岐の 3 分岐記録。
  いずれも `INPUT_CONTEXT_DECISIONS` 登録済み。`battle_okunote_risk_accepted`
  は #3161 で `hanjuku_commentary.SPOKEN` に登録。
- `battle_rescue_rearmed` — `_rescue_rearm` の記録。SPOKEN 登録済み (#2882)。

未実装 kind (emitter なし): `battle_rescue_reentry`, `rescue_decision`,
`battle_rescue_carryover_cleared`。

実況面の整合: scene 実況 (`hanjuku_scene.fact`) の receipt whitelist は
意図系 record を事実化しない既存契約があり (`battle_okunote_select` と同様)、
`battle_okunote_risk_accepted` も対象外のまま維持する。
`hanjuku_scene_worker._fact_valid` の required 集合はレシート系 fact 専用で、
本 kind の追加は不要。

## 5. 回帰テストの現状 (旧 §6 更新)

- (a) 城攻め二連戦 re-entry: **未実装** (descope)。
- (b) 召喚戦中の不利 → 再評価 (g1059): **未実装** (descope)。re-arm 経路の
  単体テストも未整備。
- (c) 低 HP でも安全候補ありなら使用 (#2864): **実装済み**。
  `tests/test_hanjuku_survival.py::test_low_hp_opens_okunote_when_safe_candidate_visible`
  他で固定。
- (d) 明らかに有利なら温存 (#2863): **実装済み** (#3065)。
  `test_low_hp_conerves_okunote_when_healthy_melee_advantage` 他で固定。
- (e) #2896 full-HP advantage (g1067): **実装済み** (#3160)。
  `tests/test_hanjuku_issue_2896.py` (6 tests) で固定。

## 6. 観測ソースの設計判断 (#2864, 実装済み方針の記録)

- 救済が必要な低 HP では、観測済みの安全候補 (自傷系以外) がある場合のみ
  奥の手を開く (candidate-first、#2102 整合)。
- 自傷系候補 (`OKUNOTE_SELF_DAMAGE_CHOICES = {'ヤケクソ'}`) は候補集合から
  除外する (flows 選択段でも除外)。
- 候補未観測 (メニュー未表示) の場合は開かず、`battle_okunote_risk_declined`
  を記録して白兵へ戻る (g482 実測との整合)。
- 拒否は `battle_okunote_risk_declined`、受諾は
  `battle_okunote_risk_accepted`、温存は `battle_okunote_risk_conserved`
  として decision log に記録する。
- 前提: プールの自傷は `{'ヤケクソ'}` の 1 つのみ、候補は重複なしで 3 件提示
  (g482 実測)。代替救済 (退却等) は低 HP で確実に到達させる経路を別途維持する
  (#2864 option (ii) の観点、残作業は issue 側で追跡)。

## 7. 参考: descope された当初設計の所在

当初の単一基準設計 (`_rescue_decision` シグネチャ、三段接続、4 新規 kind、
回帰テスト (a)〜(e) の native 再現計画) と、旧 §9 の #2863 conserve
draft (軸ごとの温存基準・述語共有方針) は #2882 / #2942 / #3065 の PR と
各 issue の履歴を参照。本書では実態と異なる設計記述を保持しない
(#3161 で縮約)。
