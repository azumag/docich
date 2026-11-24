# VERIFY-5351 — [半熟英雄] 月内出撃ゼロ: ch3 戦闘が長引く

- 対象: docich issue #5351
- fingerprint: `73f27554386f23b0` / occurrence: `d9132e685c6487f4e321`
- run: `g1342-1fe0734c` / generation 1342 / 観測版: `hanjuku-chart-v1450-password-dispatch`
- ch3 / 月 `5-7` / 444 観測 / 635.4 秒 / max gap 3.4 秒
- 判定: `zero_sortie` (confidence high) / 確定 0 / 発射 0 / `order_start` 0 / `order_launched` 0
- シグネチャ: `battle_grind` + `no_source`
- 検証範囲: 公開 issue 本文と現行 `main` の read-only 照合。runtime・VM のログ/画面は取得していない。

## 結論: (d) 判定不能 — #5324 と近い防衛待ちだが、この occurrence の根因は特定できない

issue の記録では、月内に次の読み取り専用プローブが行われ、その後に `chart_defense_wait` が記録されている。

1. `グリン` の城一覧を開く → `roster_probe_complete` → `order_done`
2. `メガパタ` の城一覧を開く → `roster_probe_complete` → `order_done`
3. `chart_defense_wait`: 「出撃元に将軍2人以上を残す制約で候補0件」
4. その後 `defense_observed`、戦闘へ遷移

incident の `world.garrison` に表示されるのは `グリン`（アルファルファ）、`コラダイン`（ヘスティア）、`パタゴーヌ`（ゼウス）、`メガパタ`（ジェラート）の4城で、各1名。incident の `world.sorties` は空である。

ただし、issue/incident に **国全体の将軍数、所在確認済み将軍数、本拠の名簿、または本拠を読むプローブの結果が含まれていない**。また、この occurrence では `chart_defense_wait_released` の反復も記録されていない。したがって、#5324 で確認された「読み取り専用プローブが待機を解除し、月次評価をリセットするループ」と同一の欠陥が本件で発火したとは断定できない。4城に各1名しかいない状態で2名維持ルールにより出撃できないこと自体は、安全上の設計どおりでもあり得る。

## 現行 main との照合

現行 `main` は `cf7f1a3c6247a65520e8d919aa410c248eb75eb7`、`BOT_VERSION` は `hanjuku-chart-v1454-sortie-card-type`。#5324 の調査後に入った v1451 の防衛待ち修正が含まれている。

### 1. probe-only 候補で待機を解除しない

`src/docich/hanjuku/strategy/replan.py:2122-2186` の `_defense_gate` は、候補がすべて `roster_probe` の場合を `probe_only` と判定する（:2145-2153）。待機を解除するのは、probe-only ではない実候補がある場合だけ（:2154-2159）。probe-only なら読み取りプローブ自体は許しつつ、`defense_wait` を保持して月次 `cycles` を進める（:2173-2185）。

これは #5324 の「probe → 擬似解除 → 同じ待機」のループを防ぐ修正である。

### 2. 攻撃ルートのない城も読み取り専用プローブの対象にする

`src/docich/hanjuku/strategy/replan.py:1930-2014` では、国全体の将軍数が所在確認済み数を上回る場合に、古い名簿を持つ城を読み取り専用で再確認する。攻撃ルートがない城も候補にできる（:1959-1971）。行軍しないプローブなので、名目上の敵城を target に置いて一覧だけを開く。ルートのない候補では本拠を優先する（:1991-1999）。

ただし、この経路の前提は `country_general_count > located`（:1930）である。#5351 の incident には両方の数値が投影されていないため、本件で本拠プローブが起票される条件だったかは確認できない。

### 3. 現行回帰テスト

`tests/test_hanjuku_defense_wait_probe.py` は以下を合成メモリで検証する。

- probe-only 候補では待機を解除しない（:59-66）
- 月替わりで待機の `cycles` が進む（:69-78）
- 実際の move 候補が現れた場合は従来どおり待機を解除する（:81-91）
- 攻撃ルートのない本拠も読み取りプローブ対象になり、本拠を優先する（:94-119）
- 直近に読んだ城を再プローブしない（:121-126）

このテスト群は現行 main に存在する。ただし合成テストの成功は #5351 の runtime で本拠プローブが実行された証拠ではない。

## 判定

- **(a) 実欠陥:** #5351 の決定行だけでは、probe-only 解除ループや未読将軍の存在を確認できないため確定しない。
- **(b) 既存修正でカバー済み:** v1451 は #5324 の2つの機構を修正しているが、#5351 の必要条件（国全体の将軍数と所在数の差）が不明なため、本 occurrence をカバーしたとは断定しない。
- **(c) 誤検知:** `zero_sortie` の数値自体は issue の集計と一致しており、誤検知とする根拠はない。
- **(d) 判定不能:** 現時点の結論。Issue は runtime 受入まで Open を維持する。

## 次回 runtime で必要な証拠

1. 国全体の将軍数と、名簿/行軍から所在確認できた将軍数。
2. 本拠（ch3 の `chart.home_castle(3)`）の `roster_probe_complete` と、その後の名簿件数。
3. probe-only 後も `defense_wait` が維持され、月替わりで `chart_defense_wait_eval` が進むこと。
4. 2人目が実際に出撃可能になった場合だけ `chart_defense_wait_released` が出ること。
5. 実出撃が成立した場合の `world.sorties` / `order_launched`。追加将軍が見つからない場合は、最後の守備将軍を無理に出さず待機を維持すること。

**実装変更は行わない。** 現行 main に #5324 向けの安全な修正が存在し、#5351 の不足証拠なしに2将軍維持ルールを緩めるのは城を無防備にする危険があるため。
