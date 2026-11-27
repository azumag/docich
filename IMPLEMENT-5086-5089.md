# IMPLEMENT-5086-5089 — [半熟英雄] キャンプ帰還未確認 (ch3 アルマムーン) / 出撃失敗 (ch4 ココット)

ブランチ: `fix/hanjuku-5086-5089-recall-sortie`（`origin/main` `9b1904e3b` へ rebase 済み）
BOT_VERSION: `hanjuku-chart-v1573-recall-preempt`

## 再ベース後の対象範囲

- 現行 main には PR #5441（2026-11-24 マージ）で #5089 の出撃失敗ゲートが入っている。
  そのため、この PR からは `map.py` の `_release_departure_request` / `_commit_departure`、
  `state/regions.py` の `departure_sent_miss` 宣言、#5089 側の重複回帰テストを drop した。
  rebase 中の #5089 専用フォローアップ（sortie guard と stale/overdue fixture のテスト）も
  main 側の実装・テストと重複するため取り込んでいない。
- 保持するのは main に無い #5086 の recall preempt 部分:
  `_dispatch_owner_foreign`、`dispatch_preempted`、`camp_recall_preempted`。
- `card_census.py` の `departure_sent_miss` busy key も #5089 側の補助変更なので、この PR では保持しない。

## #5086 — キャンプ帰還の確認読み取りが他手順の画面で終わる誤記録を防ぐ

**`src/docich/hanjuku/flows/recall.py`**

- `_dispatch_owner_foreign(mem)` を新設。フィールドフレームの所有者が出撃系手順かを判定する:
  `_flow['departure_request']` が存在するか、未中断の `sortie_attempt` の
  `confirmation_tick`/`tick` が `mem['tick']` から 300tick 以内か。
- `_recall_dispatch_step` の終端で、`interrupted`/`input_sent` でないのに待機が終わり、
  `_dispatch_owner_foreign` が真なら `status='dispatch_preempted'` とする。
  - `recall_interrupted` に現在 tick を記録し、場面切断と同じ短期リトライ経路へ乗せる。
  - `_arm_recall_skip` では fort/interrupted/scene_covered/mode_lost と同じ短期保留にする。
  - `camp_recall_preempted` を `_record` し、`camp_recall_unconfirmed` には記録しない。
- `input_sent` 済み（`arrival_unconfirmed`/`dispatched`）は従来どおり。送信済みの帰還は
  preemption に再分類しない。
- 現行 main の stale dispatch 構造に合わせ、終端メトリクスは `terminal_metric` を使う。

根拠（g1190 2026-11-21 ch3）: 出撃フローの survey フレームが await_dispatch の待機に
カウントされ、帰還Aは一度もジャーナルされず、終端が `camp_recall_unconfirmed` に誤記録された。
画面 kind だけでは帰還自身の宛先ピッカーと区別できないため、フレーム所有者で分離する。

## 監査・診断側

- `ops/vm_actions/collect_diagnostics.py`
  - `HANJUKU_CAMP_DECISIONS` に `camp_recall_preempted` を追加。
  - hot-load バージョン allowlist に `hanjuku-chart-v1573-recall-preempt` を追加。
- `ops/vm_actions/tests/test_collect_diagnostics.py`
  - fixed-values リストへ同じバージョンを追加。

## バージョン採番

- 現行 main: `hanjuku-chart-v1572-sortie-picker-resume`（PR #5150 マージ後）
- open PR 最大: `hanjuku-chart-v1570-launch-priority-start`（PR #5692）
- この PR: `hanjuku-chart-v1573-recall-preempt`

## 回帰テスト

`tests/test_hanjuku_recall_preempt_sortie_gate.py`（6テスト）では以下を確認する。

- departure_request 存在下・新鮮な sortie_attempt 下での `dispatch_preempted` 化
  （`camp_recall_preempted` 記録、`recall_interrupted` 記録）。
- 古い/中断済み attempt や外部所有なしでは `dispatch_unconfirmed` を維持する。
- input_sent 済みは `arrival_unconfirmed` を維持し、preemption に再分類しない。

## テスト結果（2026-11-27 rebase 後）

- `tests/test_hanjuku_recall_preempt_sortie_gate.py`: **6 passed**
- recall/camp 関連 8ファイル: **84 passed**
- `ops/vm_actions/tests/test_collect_diagnostics.py -k 'hotload or version or camp'`: **5 passed**

## 残件・未確認事項

- 本番 VM の実機フレームでの確認は未実施。
- `dispatch_preempted` の camp_recheck 側の扱いは既存の再観測経路に委ねる。
