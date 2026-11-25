# VERIFY-5257

## 結論
(b) 設計どおり・修正不要

「合法的に出撃源ゼロ」家族（#5190・#5213・#5236・#5264）と同一機構。同一ラン g1342 の #5236（ch1 1-9）・#5264（ch2 2-8）に挟まれた ch2 2-6 で、機構も同じ `_sole_starved` → `chart_defense_wait` 系列。召喚戦の連続は地図フレームの占有因だが、戦闘が無くても開始可能な注文は存在しなかった。#5153 型の probe ループ停滞（(a) 実欠陥、PR #5182 修正済み）ではない。同一 fingerprint の再発コメント（ch2 2-9）も同機構と判定する。

## 判定
- シグネチャ: zero_sortie（戦闘が長引く / 未読画面で保留 / 出撃元の将軍がいない）/ signals `[battle_grind, unread_hold, no_source]` / signature `battle`
- ラン: `g1342-1fe0734c` / generation `1342` / `hanjuku-chart-v1423-sortie-cell-after-battle`
- 月: ch2 2-6（観測 1069回 / 1502.7秒 / max gap 3.6秒、tick 7133→8201）
- 出撃: 確定 0 / 発射 0 / order_start 0 / order_launched 0 / sortie_* 系すべて 0
- 決定内訳: `monster_menu_choice` 30 / `battle_command_open_wait` 17 / `enemy_general_menu_wait` 17 / `battle_melee` 18 / `situation_held` 26 など、battles.decisions=133（`battle_grind` 閾値 ≥20、`month_audit.py:2898`）。`unread_hold` は `situation_held` 26 + `unknown_hold_advance` 4 = 30 ≥ 30（同 2900-2902）。`no_source` は在城名簿の最大が 1（同 2910-2913）
- `stats.battles_started 61` は run 累計。月内の記録には `chart_defense_wait` / `chart_interim_hold` / `plan_starved_rearm` / `order_dispatch_starved` / `chart_adjust_request` が一切ない
- 翌5分: 決定 98件 / 出撃 1件 / 回復 yes / 初出撃まで 180.8秒（`order_start` 2、`chart_adjust_applied` 2、`soldier_refill`・`house_scan_started` を経て回復）

## 機構の切り分け

開始可能な注文は月内に一度も存在しなかった。#5236 の「注文オブジェクト 0 件」より一段浅く、本件は「走査対象は存在するが ready を満たさない」形。

### 層1: next_order の走査対象はわずか2件で、どちらも開始条件を満たさない

- `next_order`（`orders.py:1846-1942`）の走査対象 `(*current, *retries, *base)`:
  - `current` = `_orders()`（`orders.py:785-801`）= `chart_plan.orders` = `A:22096aa2:M2719b9` 1件のみ（+ 暫定注文があればそれ。監査 JSON に `chart_adjust.interim_order` は出ないが、あっても層2で同じ結論）
  - `retries` = `launched_orders` のうち pending のもの（`orders.py:1894-1895`）= `A:22096aa2:M97f113`（キャラウェイ attack アルマムーン→アウスパジア、tick 7711 の敗北再試行）のみ。もう一方の `M25e288` は `launched_unconfirmed` で非該当
  - `base` = `chart_plan` 存在のため `chart.orders` は使われず、かつ ch2 は機械モード（`speedrun_enabled(2)` = False、`hanjuku_chart.py:1329-1334`）で `chart.orders(2)` = ()（同 1352-1354）→ []
- `plan.orders` の他の pending ステータス 6件（`M06a93b`/`M7dc037`/`Mb1cbfe`/`M55fc1f`/`fd5d:M7dc037`/`fd5d:Mc5f404`）は注文オブジェクトを持たない過去計画の残存ステータスで、走査対象に入らない（#5236 と同型）
- `M2719b9` は機械計画の roster probe（note「暫定: 膠着のため…将軍一覧を読んでから…出撃する」は `replan.py:1997/2067` の定型）で、状態は `done` → ステータスゲート（`orders.py:1925`）で対象外
- `M97f113` は pending・purpose attack・対象アウスパジアは未所持・出撃元アルマムーンは自軍・名簿 `['どうし']` で `_source_list_open` True（`orders.py:870-887`）まで通るが、`_source_spare_missing`（`orders.py:2297-2353`）で確実に止まる:
  - `present` = `['どうし']`、自由将軍 `{どうし} - unavailable` = 1 < 2 → 2333-2337 の pre-flight ガードまたは 2352-2353 の `_reserve_source_guard`（`A:` = PLAN_PREFIX、`orders.py:2177-2195`、`hanjuku_chart_adjust.py:117`）でブロック
  - 指名将軍キャラウェイは `present` におらず `unavailable`（敗北後の帰還行軍中または位置未確定）と読まれる状態で、2322-2328 の named ガードでもブロックされうる
- よって `order_start`（`flows/map.py:2617`）が 0 なのは「開始可能な注文がなかった」ためで、合法的な零。

### 層2: 出撃候補が 0 件（法的出撃源ゼロ）

- 在城はアルマムーン(`どうし`)・フーリック(`ゼウス`)の2城のみで、自由将軍は各1。`_sole_starved`（`replan.py:2082-2097`）成立（非ボス敵城 = ハドリバーグ/ドミノーラ/アウスパジア/グロン等が残る）
- `sender_min(2)` = 2（`hanjuku_chart.py:1303-1305`、guard_free は ch9 のみ）。1将軍城からの出撃はゲーム自体が拒否し、`allow_sole=False`（`replan.py:2570`、オーナー決定 2026-11-06・#3566）
- `_defense_gate`（`replan.py:2105-2152`）は `'hold'`。`_off_chart`（`replan.py:3067-3069`）では `gate == 'hold'` かつ `chart_plan` の pending が 0 件 → `_plan_pending` False → 無言待機で return
- 月内に `chart_defense_wait` 記録がないのは `_off_chart` 自体が一度も走らなかったためで、待機台帳が始まらなかっただけ。次月以降では同系列の `chart_defense_wait` が実記録される（#5264 ch2 2-8 で `chart_defense_wait_released` → `chart_adjust_applied` → probe `order_start` → 再度 `chart_defense_wait` の一巡を実測）

### 層3: 未確認行軍が検証枠と再計画ガードを合法に保持

- `A:22096aa2:M25e288`（ガルバンゾー attack アルマムーン→グロン、月終了時 1125t）は `launched_unconfirmed` のまま生きている
- 地図観測で `order is None` のとき、dispatch ブロックは先にこの未確認行軍を検証用に order 枠へ選ぶ（`flows/map.py:2529-2533`）→ `next_order`/`_off_chart` より検証が先。検証枠は有限で解放されるが、月内に解放記録がない＝検証経路は停滞していない
- `plan_starved_rearm`（`orchestrator.py:641-729`、decide ループから `orchestrator.py:1689` で全フェーズに呼ばれる唯一の戦闘中再計画経路）は、live な `en_route`/`launched_unconfirmed` があると return False（`orchestrator.py:716-719`「a march still walking keeps the plan's reservation」）。1125t は `stuck_march` 閾値 3000t（`month_audit.py:2915-2917`）およびゾンビ期限（`SPENT_SORTIE_TICKS` 系 ~6400t）を大きく下回り、予約保持は合法

### 検証観点への直接回答

1. **出撃可能な注文は一度も存在しなかった**。走査対象の注文オブジェクトは 0 件ではなく 2 件（done の probe `M2719b9` + pending 再試行 `M97f113`）あったが、`M97f113` は出撃元の自由将軍が 1 人で `_source_spare_missing` に常時ブロックされ、`next_order` は必ず None を返す。「ready にならなかった」形で、#5236 の「0 件」より一段浅い。残りの pending ステータスは注文オブジェクトを持たない残存記録
2. **長引く召喚戦による占有**: `battles.decisions` 133・`battle_*`/`monster_*` 系が決定の大半を占め、フィールド地図の注文選択（`flows/map.py:2524-2546`）に届くフレームがほぼ無かった。届いた数回は定期全城確認（`world_map_open` 5回）と SELECT フォーカスに割かれ、dispatch ブロックへ進まなかった。戦闘中の計画経路は存在する — `plan_starved_rearm`（orchestrator.py:641、#4584 由来）— が、層3 の live 行軍ガードで抑止されていた。戦闘直後は battle_result → 地図復帰 → 全城確認 → 未確認行軍検証 → `_defense_gate` の順で通常経路に戻る設計で、翌5分で実際に `chart_adjust_applied` 2・`order_start` 2・出撃 1 へ回復した。占有はシグネチャとして正確だが、層1・層2 により奪われた出撃自体は存在しない
3. **#5190・#5213・#5236・#5264 の (b) 家族との異同**: 同一機構。#5236（同一ラン g1342、ch1 1-9、(b) 確定）との違いは、#5236 が `chart_plan` 不在で注文オブジェクト 0 件だったのに対し、本件は機械計画 + 再試行オブジェクトが存在するが開始条件を満たさない点（一段浅い）。#5264（同一ラン、ch2 2-8）では同じ `_sole_starved` → `chart_defense_wait` サイクルが月内に実記録されており、本件は `_off_chart` に届かず待機台帳すら始まらなかった差異のみ。#5190・#5213 は「開始可能な注文なし・戦闘占有」で最も近い同型
4. **#5153 型 probe ループ停滞との異同**: #5153 は `order_start` 72 / `order_launched` 0 で、開始した roster probe が繰り返し再採用されるループ（(a) 実欠陥、PR #5182 で修正済み — `replan.py:1899` 付近の in-flight 考慮がその修正）。本件は `order_start` 0 でループの前提（繰り返し開始）が存在せず、probe 注文 `M2719b9` も `done` で完走済み。別機構

## 補足: 判定不能になりうる一点

出撃元名簿の陳腐化だけは監査 JSON から完全には否めない。アルマムーンの名簿 `['どうし']` は probe `M2719b9` の読取結果だが、その読取は月より前の `done` であり、敗北したキャラウェイが月内に帰還していれば名簿は `[どうし, キャラウェイ]` に変わり再試行が開始可能だった可能性がある。ただし、(i) 帰還の証拠は監査 JSON に存在しない、(ii) 再読取 probe は `country_general_count > located` のときだけ発行され（`replan.py` probe_stuck 系、`STALE_ROSTER_PROBE_INTERVAL` 300 で制限済み）、その発行経路も `_off_chart` 経由で今月は走っていない、(iii) 仮に名簿が陳腐でも設計上の staleness 許容内で、翌5分の月次スキャン（`house_scan_started`・`soldier_refill`）で正規に回復した、の3点から、この可能性は結論を変えない。#5153/#4689 の「probe を出せない滞留」は `plan_starved_rearm` の導入で既にカバーされており、本件はその live ガードが設計どおり機能した事例。

## 既検証シグネチャとの突き合わせ
- #5236（同一ラン g1342、ch1 1-9、(b) 確定、VERIFY-5236.md）: 同機構、上記の通り一段深いだけ
- #5264（同一ラン g1342、ch2 2-8、(b) 家族）: 同機構、待機サイクルの記録有無の差のみ
- #5213（ch8 10-11、(b)）: `order_start` 0・戦闘占有・在城全1名。同型
- #5190（ch2 3-5、(b)）: `order_start` 0・開始可能な注文なし。同型
- #5153（ch8、(a) 実欠陥・PR #5182 修正済み）: probe ループ。別機構、上述

## 関連
- #5236 / VERIFY-5236.md: 同一ランの先行検証（(b) 家族の基準記録）
- #5190 / #5213 / #5264: (b) 合法的出撃源ゼロ家族
- #5153 / PR #5182: probe ループ停滞（(a) 実欠陥・修正済み）
- #4584（g1190 65-10..66-10）: `plan_starved_rearm` 導入の由来（`orchestrator.py:644-646`）
- `flow.recall_verification {await_dispatch, dispatch_unconfirmed, steps:6}` は CAMP RECALL の終端記録で、出撃注文系とは無関係（VERIFY-5236 と同じ処理）

## 受入条件
- コード変更なし（read-only 検証、VERIFY-5257.md の追加のみ）
- 本番 SSH 使用なし
- 秘密情報なし
- 検証記録: VERIFY-5257.md をブランチ `verify/5257-zero-sortie` へコミットし PR を作成
