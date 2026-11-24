# IMPLEMENT-5125 — [半熟英雄] 月内出撃ゼロ: ch10 出撃開始後に発射されない

ブランチ: `fix/5125-dispatch-starve-measure`（main `966b12a75`＝v1387 から作成、push なし）
BOT_VERSION: `hanjuku-chart-v1392-dispatch-starve-measure`

検証結論: `~/work/docich-5125/VERIFY-5125.md` — (c) 未カバーの実欠陥。
完全観測の通常月（g1314 ch10 94-9）で `order_start=0`・`departures=0`。
PR #5095 の `order_dispatch_starved` は `boss_only_phase` 限定で本件の争奪月には
届かず、main 時点でも通常月向けの dispatch 保証は存在しない。

採用方針: VERIFY §6 案1「計測の一般化」のみ（エージェント推奨で決定済み）。
案2〜4（譲り拡張・滞留行軍の決着圧・割り込み後の再開）は本件では実装しない。

## 変更内容

### 1. dispatch 待ち計測の一般化（記録のみ・譲りはボス限定のまま）

**`src/docich/hanjuku/strategy/orders.py`**（`order_dispatch_starved`）

- 先頭の `if not boss_only_phase(mem): return False` ゲートを撤去し、
  pending 注文の待ち計測（`order_dispatch_since` のシード・待ち判定・
  pending 消滅時の解除）をフェーズによらず行うようにした。
- 待ちが `ORDER_DISPATCH_STARVE_TICKS`（60観測・約90秒）を超えた時点で
  `boss_only_phase` を評価する:
  - **ボスフェーズ**: 従来どおり `order_dispatch_starved` を1エピソード1回
    `_record` して True を返す（前景ワークへの譲りは不変）。
    dedup key は既存の `order_dispatch_starved_at`。
  - **ボスフェーズ外（通常月・争奪月）**: 同名 `order_dispatch_starved` を
    1エピソード1回 `_record` するが、`observed_metric.boss_phase=False`
    を付け、理由も「譲りは発火しない」旨に変えて False を返す。
    dedup key は新規 `order_dispatch_noted_at`。
- dedup key を分けたのは、同一 `since` エピソードがフェーズ境界をまたぐ場合に
  両側の記録を残すため（通常月で記録→ボス突入で譲り＋記録）。
- 返り値 True（＝呼び出し側の前景ワーク譲り）はボスフェーズ限定のまま。
  `map.py` L2402（select_focus）/ L2443（world_map_open）/
  `recall.py` L1124（新規 camp recall）の各譲り箇所はコード変更なしで
  通常月では一切譲らない。
- `map.py` の dispatch ブロックでの `order_dispatch_since` 計測開始・解除
  （L2468/L2472/L2531）は月の種別によらず動くことを確認済み。map.py 自体の
  変更はなし。

**`src/docich/hanjuku/state/regions.py`**

- `order_dispatch_noted_at` を PLAN/chapter キーとして宣言
  （`order_dispatch_starved_at` と同じ扱い）。

### 2. 監査側

`month_audit.py` の変更なし（指示どおり camp_recall_failed 等他 detector も
不変）。`order_dispatch_starved` は `_decision_counts` で汎用に集計されるため、
記録が出れば月の `decisions.counts`・`before` 行にそのまま現れる。
`observed_metric.boss_phase` フラグと月の `kind` / `_boss_phase` 判定で
「譲りが発火したボス枯渇」と「記録だけの通常月枯渇」を読み分ける。

### 3. バージョン・allowlist

- `src/docich/hanjuku/version.py`: `hanjuku-chart-v1392-dispatch-starve-measure`
- `ops/vm_actions/collect_diagnostics.py`: allowlist に同文字列を追加（trailing comma あり）
- `ops/vm_actions/tests/test_collect_diagnostics.py`: fixed-values リストに同文字列を追加

バージョン選定の根拠（この環境では `gh`・リモート参照が使えず、ローカル
worktree の実測のみ）:

- main の HEAD（`966b12a75`）は v1387。
- 依頼文の情報: PR #5103 = v1388、PR #5133 = v1389 を使用中。
- ローカル worktree 実測: v1388 を docich-5120（#5120 付け替え予定）・
  docich-5118・docich-5127・docich-5086-5089 が、v1389 を docich-5105-5108 が
  それぞれ使用中。v139x を使う worktree は存在しない。
- 上記から **v1390** を採用。#5128 の付け替え先は未確認のため、push/PR 前に
  open PR の使用番号を再確認すること。

### 4. テスト

`tests/test_hanjuku_boss_dispatch_starved.py`（10 → 14 件）:

- `test_outside_the_boss_phase_nothing_yields` を
  `test_outside_the_boss_phase_the_starvation_is_recorded_not_yielded` へ更新:
  通常月で待ち超過時に False（譲りなし）だが `order_dispatch_starved` が
  1エピソード1回・`boss_phase=False` 付きで記録されることを検証。
- 追加: フェーズ境界をまたぐエピソードで両側の記録が残ること、
  通常月でも starved 時に world_map 調査・select リフォーカス・
  新規 camp recall が譲らないこと（記録は出る）。
- 既存のボスフェーズ側テスト（譲り・1エピソード1回・解除条件）は全て不変で
  通る想定（ボス側の記録形状・返り値は変更していない）。

## 検証結果

実行コマンド（予定）: `python3 -m pytest -q tests/test_hanjuku_boss_dispatch_starved.py
tests/test_hanjuku_state_regions.py tests/test_hanjuku_month_audit.py`

この作業環境では exec 経由のテスト実行が受理されなかったため、ローカル実行は
**未実施**。変更は記録追加のみでゲーム入力に影響しない設計（譲りは
`boss_only_phase` 限定のまま、返り値の契約は不変）だが、push/PR 前に上記
ファイルの pytest 実行と、CI の hanjuku スイートでの確認が必要。
`test_collect_diagnostics.py` は環境起因の既知失敗（root 実行で
`test_an_inaccessible_docich_root_is_treated_as_absent` ×2）を IMPLEMENT-5077
と同じく抱え得る。

## 制約の内訳

- 譲りの発火（world_map_open / select_focus / 新規 camp recall の1フレーム譲り）
  は `boss_only_phase` 限定のまま。通常月の前景ワーク挙動・ゲーム入力は不変。
- 変更は観測点の追加のみで、発射・dispatch ロジックの動作は変えない。
- `order_dispatch_since` の新しい副作用: ボスフェーズ外でも譲り判定の呼び出しで
  pending 注文があると `since` がシードされる（pending 消滅・開始不能・
  `order_start` で解除される従来のライフサイクルは不変）。
- 記録が残らないケース: 譲り判定自体へ届かない月（戦闘等で非マップ画面が占有）、
  pending 注文が無い月、全 pending が開始不能で待ちが解除され続ける月。
  記録の欠如は「dispatch 健全」の証明にはならない（VERIFY §7 の限界は残る）。
- push・PR・本番 SSH は実施していない。
