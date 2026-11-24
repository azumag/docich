# VERIFY-5236

## 結論
(b) 設計どおり・修正不要

「合法的に出撃源ゼロ」家族（#5190・#5213・#5264）と同一機構。戦闘の長引きは地図フレームの占有因だが、戦闘が無くても出撃可能な注文・候補が存在しなかったため、占有で奪われた出撃はない。#5153 型の probe ループ停滞（実欠陥）ではない。

## 判定
- シグネチャ: zero_sortie（戦闘が長引く）/ signals `[battle_grind]` / signature `battle`
- ラン: `g1342-1fe0734c` / generation `1342` / `hanjuku-chart-v1414-retreat-ban`
- 月: ch1 1-9（観測 307回 / 450.6秒 / max gap 3.5秒、tick 2261→2567）
- 出撃: 確定 0 / 発射 0 / order_start 0 / order_launched 0 / sortie_* 系すべて 0
- 決定内訳: `battle_*` 系 32件（`battle_grind` 閾値 ≥20、`month_audit.py:2868`）・ chikujou 系・月次メニュー・全城確認（world_map_open 等）
- `stats.battles_started 21` は run 累計（tally lifetime 'run'）。月内の `battle_start` 決定は 3 件（迎撃戦・月一の一騎打ち・召喚戦）
- 翌5分: 決定 99件 / 出撃 1件 / 回復 yes / 初出撃まで 183.7秒。15:19:46Z に `chart_defense_wait` が新規記録

## 機構の切り分け

発射可能な注文は月内に一度も存在しなかった。独立した 3 層で成立する。

### 層1: 注文オブジェクトが 0 件

- 月終了時 `plan.chart_plan` は不在（監査 JSON の `orders: [] / request_id: null / source: null` は `month_audit.py:357-366` の不在レンダリング）。月内に計画採用・破棄系の記録（`chart_adjust_applied` / `chart_plan_dropped_foreign_source` / `chart_plan_invalidated` / `chart_plan_stale_request`）が一切なく、月初から `chart_plan` なしと読める。
- ch1 は機械モード: `speedrun_enabled(1)` = False（`hanjuku_chart.py:1328-1334`、`CHART_ONLY_CHAPTERS={4}`）→ `chart.orders(1)` = ()（`hanjuku_chart.py:1352-1354`）。
- `next_order`（`orders.py:1846-1942`）の走査対象 `(*current, *retries, *base)`:
  - `current` = `_orders()`（`orders.py:785-801`）= chart_plan 不在かつ interim なし → ()
  - `retries` = `launched_orders` の pending（`orders.py:1894-1895`）→ 唯一の launched_order `A:55234f40:Mdae8ba` は `launched_unconfirmed` で非該当 → ()
  - `base` = `chart_plan` 不在 → []（`orders.py:1917-1918`）
- `plan.orders` の pending ステータス 7 件（`A:4ab4f978:*` / `A:55234f40:*` / `A:ff1dcf73:*`）は注文オブジェクトを持たない過去計画の残存ステータスであり、走査対象に入らない。
- よって `next_order` は仮に全地図フレームで走っても必ず None を返す。`order_start`（`flows/map.py`）が 0 なのは「ready にならなかった」ではなく「走査する注文が存在しなかった」ため。

### 層2: 出撃候補が 0 件（法的出撃源ゼロ）

- 在城 5 城の自由将軍はすべて ≤1: アルマムーン（名簿 どうし・アルファルファ のうちアルファルファは `launched_unconfirmed` で busy、自由は どうし のみ）/ カストーラ（ゼウス）/ キカンドン（ヴィーナス）/ ジョンリギ（ココット）/ ナキューメラ（シェーブル）。`_en_route` は `launched_unconfirmed` を busy に含める（`os_layer/world.py:453-471`）。
- `sender_min(1)` = 2（`hanjuku_chart.py:1300-1305`、guard_free は ch9 のみ）。ゲーム自体が1将軍城からの出撃を拒否（`replan.py:110-115`・`replan.py:1201-1204` コメント）。`allow_sole=False`（`replan.py:2570`、オーナー決定 2026-11-06・#3566 で唯一城を空にする出撃は廃止済み）。
- `interim_candidates` は全既読名簿・自由将軍 ≤1 により空 → `_sole_starved` 成立（`replan.py:2082-2097`）→ `_defense_gate` は `'hold'`（`replan.py:2105-2152`）。
- 実測一致: 翌月 15:19:46Z の `chart_defense_wait` 記録理由「出撃元に将軍2人以上を残す制約で候補0件。2人目の参入を待つ（LLM要求なし）」は `replan.py:2140-2142` の文言そのもの。
- 発射側でも `_source_spare_missing`（`orders.py:2297-2353`、非 probe は `present - unavailable < 2` でブロック）と `_last_castle_held` が同一制約を担保。
- `signals` に `no_source` が立たないのは en_route 中のアルファルファがアルマムーン名簿に残り max staffed=2 と読まれるため（`month_audit.py:2880-2883`）。`stuck_march` も 1145t < 3000t で非該当（同 2884-2887）。

### 層3: 未確認行軍が計画系の予約を合法に保持

- `A:55234f40:Mdae8ba`（アルファルファ attack アルマムーン→ゴーメン、月終了時 1145t）は `launched_unconfirmed` のまま生きている。
- 地図観測で `order is None` のとき、先にこの未確認行軍が検証用に order 枠を占有（`flows/map.py:2509-2513`）→ `next_order`/`_off_chart` へ進まない。検証枠は有限で解放される（`deploy.py:95` の verification_reads≥3 → `verification_unavailable`、`orders.py:2499` の `sortie_verification_held`）。月内に解放記録がない＝検証経路は停滞していない。
- `plan_starved_rearm`（`orchestrator.py:641-729`）は戦闘・月次フェーズ中に `_off_chart` を走らせられる唯一の再計画経路だが、live な en_route/launched_unconfirmed があると return False（`orchestrator.py:716-719`「a march still walking keeps the plan's reservation」）。Mdae8ba は `launched_orders` 所属の plan 系ステップで、ゾンビ期限は `2×SPENT_SORTIE_TICKS` = 6400t（`orders.py:1506-1509, 1530-1536`、`SPENT_SORTIE_TICKS=3200`）。1145t は期限内で、予約保持は合法。
- 月内に `plan_starved_rearm`・`chart_defense_wait`・`chart_defense_wait_eval`・`order_dispatch_starved` の記録がないのはこの保持と層1（計画なし）と整合する。

### 検証観点への直接回答

1. **出撃可能な注文は一度も存在しなかった**。`next_order` が ready にならなかったのではなく、走査対象の注文オブジェクトが 0 件（層1）。仮に計画経路が動いても `_sole_starved` で候補 0 件（層2）。
2. **戦闘占有の効果**: `battle_*` 32件のフレームは battle フローが所有し map の注文選択は走らない。占有はシグネチャとして正確（battles.decisions=32 ≥ 20）だが、層1・層2 により奪われた出撃自体は存在しない。戦闘中も `plan_starved_rearm` が再計画経路として存在するが、層3 の live 行軍ガードで抑止されていた。戦闘直後は battle_result → 地図復帰 → 全城確認 → 未確認行軍の検証 → `_defense_gate` の順で通常経路に戻る。翌月で実際に `chart_defense_wait` まで到達した。
3. **#5264 との異同**: 同一ラン g1342・同一機構（`_sole_starved` → `chart_defense_wait`）。違いは #5264 で roster_probe 注文が存在し order_start 4・完走＋月内 wait 記録だったのに対し、本件は注文オブジェクト 0 件（chart_plan 不在）で probe すら発行されず、wait 記録が翌月まで遅れた点。機構は同じで層1 が一段深いだけ。(b) 家族。
4. **#5153 との異同**: #5153 は order_start 72 / order_launched 0 で、開始した注文が発射せず繰り返し再採用される probe ループ（(a) 実欠陥、PR #5182 で修正済み）。本件は order_start 0 でループの前提（繰り返し開始）が存在せず、`sortie_launch_failed`/`unverified` 系も 0。別機構。

## 既検証シグネチャとの突き合わせ
- #5264（同一ラン g1342、ch2 2-8、(b) 確定）: 同機構、上述。
- #5213（ch8 10-11、(b)）: order_start 0・戦闘占有・在城全1名。最も近い同型。
- #5190（ch2 3-5、(b)）: order_start 0・開始可能な注文なし。同型。
- #5191（(b)）: boss_retry_exhausted 系、別機構。
- #5153（ch8、(a) 実欠陥・PR #5182 修正済み）: probe ループ。別機構、上述。

## 関連
- #5190 / #5191 / #5213 / #5264: (b) 合法的出撃源ゼロ家族
- #5153 / PR #5182: probe ループ停滞（(a) 実欠陥・修正済み）
- #4584（g1190 65-10..66-10）: `plan_starved_rearm` 導入の由来（`orchestrator.py:644-646`）
- `flow.recall_verification {await_dispatch, arrival_unconfirmed, steps:6}` は CAMP RECALL の終端記録（`flows/recall.py:2289-2355`）で、出撃注文系とは無関係。

## 受入条件
- コード変更なし（read-only 検証、VERIFY-5236.md の追加のみ）
- 本番 SSH 使用なし
- 秘密情報なし
- 検証記録: VERIFY-5236.md をブランチ `verify/5236-zero-sortie` へコミットし PR を作成
