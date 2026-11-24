# VERIFY-5226 — [半熟英雄] 切り札在庫があるのに非携行で出撃: ch1 ゼウス

- 対象: azumag/docich issue #5226 / fingerprint `7641c039849071dd` / occurrence `98e0584b67d4e173a533`
- ラン: `g1342-1fe0734c` / generation 1342 / `hanjuku-chart-v1412-summon-card-resume`（main 6d00a1f と同系、v1412 = 8e6889783）
- 検証範囲: read-only（本ファイルの作成以外の編集なし）

## 結論: (c) 誤検知

出撃は実際に **イッテツーン×2 を携行して** いた。「在庫があるのに非携行」の前提が成り立たない。
誤検知の原因は月次監査の携行検知 `_sortie_carried_cards` が、確定済みの実測携行
（`plan.order_context[step].observed_metric.cards`）を読まない古い実装のまま main に残っている点。
同一修正は **open PR #5258**（`fix/5249-sortie-measured-kit-v1424-rebased`、"use measured sortie card receipts"）で既に進行中。

## 根拠となるコード箇所（ファイル・行、main @ 6d00a1f）

### 携行は実際に行われた

- `src/docich/hanjuku/flows/deploy.py:2635` — 選択対象は実パネル行 `row['stock'] > 0` が必須。決定ログの `card_pick` ×2（14:20:43/14:20:46）はイッテツーンの行が実際に在庫表示されていた証拠。
- `src/docich/hanjuku/flows/deploy.py:2657,2661-2666` — 各 pick で `note_card_carried` を呼び `card_pick` 決定を記録（observed_metric.stock=2）。
- `src/docich/hanjuku/strategy/bargains.py:106-116` — `note_card_carried` は `card_stock[name] = shown - taken`。イッテツーンは shown 2・taken 2 → **0**。携行分はフィールド在庫から出る（コメント内 S0b）。
- `src/docich/hanjuku/flows/deploy.py:2770-2791` — sortie_confirm 一致分岐。`want` と携行読み取り `got` が一致した場合のみ `order_context[step]` を書き `sortie_confirm … cards=carried, observed_metric=got` を記録。不一致なら `_hold_deploy`（situation_held）か `sortie_kit_mismatch` が記録される。本件は1読み目で `sortie_confirm` のみ（「計画と一致」）が記録され hold/mismatch なし → 確認画面の携行スロットもイッテツーン×2 と一致していた。
- 決定列: `card_pick`×2 → `sortie_confirm` → y_jump → `sortie_departure_requested` → `order_launched`。`card_dropped` / `sortie_kit_mismatch` なし。

### 監査が「非携行」と誤判定した経路

- `src/docich/hanjuku/month_audit.py:2238-2244` — `_positive_card_stock` は `card_stock` の正の残だけを「在庫」とする。携行済みのイッテツーンは 0 のため除外 → issue の「在庫」{ダイチスイム,フットバース,ブラッキー} は **携行後** の残り。
- `src/docich/hanjuku/month_audit.py:2257-2284` — `_sortie_carried_cards` は `rare_card_kit`/`strong_card_kit`/`dropper_card_kit` と発射記録自身の `cards`/`observed_metric` しか読まない。
- `src/docich/hanjuku/month_audit.py:2287-2318` — `_sortie_cards_event` は carried 空 かつ 正の在庫ありで `sortie_without_cards` イベント化。
- `src/docich/hanjuku/strategy/orders.py:2421-2467` — `order_launched` 記録自体はカード一覧を持たない（携行情報は deploy 側の order_context に置かれる）。
- `src/docich/hanjuku/strategy/orders.py:2358` — `RETIRED_ORDER_STATES = ('failed','done')` のみ。`'launched'` は含まれず `drop_retired_step_context` も走らないため、`order_context[step].observed_metric.cards`（= [イッテツーン,イッテツーン]、deploy.py:2785 書込）は発射観測時点で参照可能だった。監査が読んでいれば carried は非空になった。

### 計画 cards の生成経路（検証観点1）

- `src/docich/hanjuku/strategy/replan.py:826` — `INTERIM_CARDS = ('イッテツーン','イッテツーン')`。
- `src/docich/hanjuku/strategy/replan.py:862-908` — `_stock_cards`。「在庫未読は空在庫の証拠ではない」として、card_stock 未読/空なら wanted（interim ではイッテツーン×2）をそのまま返す（882-884）。計画時点で在庫が未観測でも計画へ入るのはこの既定動作。実際このケースでは pick 時点に実在庫が存在し携行できた。
- issue 記載の「計画札が在庫に無い」は監査の事後在庫（携行後残量）と見比べた誤解。計画・携行ともにイッテツーンは実在した。

### #5219 との差（検証観点2）

- #5219: 計画札が全て card_drop（選択不能）で除外され、**空キットを確認承認して出撃**した事例 → 実際に非携行、(b) 設計どおり。
- #5226: 計画札を実際に携行し、確認画面でも一致して出撃。**携行はあった**のに監査が検出できなかった誤検知。
- 差分の意味: #5219 はゲーム挙動の問題ではなく観測結果として正しい起票だったのに対し、本件は **監査の証拠読み取り欠落** が原因。結論は変わる（設計どおりではなく誤検知）。

### 現行 main での再発可能性（検証観点3）

- main の `_sortie_carried_cards` は依然旧実装（上記 2257-2284）のため、同型の誤検知は main 上で再発しうる。実際に issue #5226 へ同型の第2件（ch1 ヴィーナス, occurrence `13f3f6ffbf31f78be0bb`）も追記済み。
- 修正経路の履歴: commit `94ceb2326`（v1392 / #5118「切り札の実測携行を監査の証拠に加え非携行誤検知を修正」）が同修正を一度実装したが、そのコミットは main の祖先にない（`git merge-base --is-ancestor` = no）。v1412 ランは修正を含まない系統で実行された。同一修正をやり直すのが open PR #5258（`fix/5249-sortie-measured-kit-v1424-rebased`）で、`order_context.observed_metric.cards` / `launched_orders[step].departure_binding.cards` / 同観測tickの `sortie_confirm` 記録を携行証拠として読む。本件は order_context 経路で覆われる（sortie_confirm 記録は別tickの `records` にしか現れないため、そのフォールバックだけでは拾えない点に注意: `brains/hanjuku/bot.py:764-767` が渡す `records` は当該tickの決定のみ）。
- 真の「在庫あり・携行ゼロ」出撃（#5219 型: 計画札が全て選択不能→空キット承認）は仕様経路として残り、それを監査が起票するのは本来の目的どおり。

## 修正案

`order_context` による携行認識は open PR #5258 が既に実装・レビュー中のため、そちらのマージが正規修正。本検証で発見した補足論点（#5258 マージ後も残りうる点）は以下のみ:

- `records` フォールバックは当該tickの決定しか見えないため、sortie_confirm と order_launched が別tickでは効かない。order_context / departure_binding の2源で本件は十分だが、`world.sorties[step].evidence`（map.py `_commit_departure` が order_context を deepcopy 退避）も読めば、将来 order_context が settle 前に落とされる経路ができても耐性が上がる。
- `_sortie_carried_cards` にデバッグ用の「証拠ソース」記録（どの経路で carried を得たか）を incident へ残すと、次回同型誤検知の切り分けが速い。

いずれも #5258 のスコープを超えない監査堅牢化であり、別issueの改善提案として扱うのが適当。

## 手順上の注記

- `agent-guards/secret_scan.py`（ワークスペース直下）未配置・`DOCICH_HANDOFF_PATH` 未設定のため handoff 正本は未読（AGENTS.md §1 の許容どおり）。投稿前スキャンはリポジトリ内 `ops/vm_actions/secret_scan.py` を使用した（clean を確認後に投稿、逸脱記録付き）。
