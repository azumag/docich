# IMPLEMENT-5118 — [半熟英雄] 切り札在庫があるのに非携行で出撃: ch10 ヘーゼル

ブランチ: `fix/5118-sortie-carried-cards`（`origin/main` `dbb5d9dcc` = v1386 から作成、commit/push なし）
BOT_VERSION: `hanjuku-chart-v1394-sortie-measured-kit`（main は v1386 まで進行済み。
PR 作成時点で main の最新バージョンを再確認し、重なっていたら付け替えること）
検証結論: `~/work/docich-5118-5119/VERIFY-5118-5119.md` #5118 節 —
**誤検知（監査の証拠収集欠陥）**。`_sortie_carried_cards` が参照するのは
rare/strong/dropper 補助キットと発射レコードの `cards` だけだが、
4種の発射 decision（`order_launched` / `sortie_departed_observed` /
`sortie_launch_confirmed` / `sortie_launch_unconfirmed_kept`）はいずれも
`cards` を持たない。`cards` を載せるのは `order_start`（計画値）と
`sortie_confirm`（実測携行）だけで、どちらも `LAUNCH_DECISIONS` に含まれない。
したがって予定札のみ携行した通常出撃は構造上必ず「非携行」と判定されていた。

## 変更内容

### 1. `_sortie_carried_cards` に実測kit経路を追加（VERIFY-5118 §8-1）

**`src/docich/hanjuku/month_audit.py`**

- `_card_names(value)` ヘルパーを新設し、`cards` / `card` / `cards_selected`
  3キーの str/list 展開を共通化（既存の record / observed_metric 走査も同ヘルパーへ整理）。
- 既存経路（`rare_card_kit` / `strong_card_kit` / `dropper_card_kit` と
  発射 record の `cards` / `card` / `cards_selected`・`observed_metric`）は維持。
- 追加した実測経路（いずれも `mem['plan']` / `mem['world']` 領域からの読み取りのみ、
  監査は state を変更しない）:
  - `mem['plan']['order_context'][step]['observed_metric']['cards']`
    — 出撃確認で読んだ実測kit（deploy.py:2758。`sortie_confirm` 承認時に保存）。
  - `mem['world']['launched_orders'][step]['departure_binding']['cards']`
    — `confirm_departure_order` が保存する確定出撃の実測携行
    （execution.py:83-90、`_commit_departure` で `launched_orders` へ格納 map.py:2804）。
    binding 付き snapshot のトップレベル `cards` も同じ実測値なので併せて読む。
  - 同一 batch の `records` 内で `decision=='sortie_confirm'` かつ
    `chart_step==step` の decision の `cards`（後方参照）。
- 計画値を証拠にしないガード: `departure_snapshot` の未確認フォールバック
  （execution.py:131-134）は `launched_orders[step]['cards']` に**計画札**を
  写すため、`departure_binding` を持たない snapshot のトップレベル `cards` は
  証拠にしない。これにより blind approve（`sortie_confirm_blind_approve`）等の
  「実測kit 未読」の出撃は引き続き起票される。
- シグネチャ変更: `_sortie_carried_cards(mem, step, record, records=None)`、
  `_sortie_cards_event(record, mem, records=None)`。`_track_sortie_cards` から
  batch の `records` をそのまま引き渡す。`records` 既定値 None で後方互換。

### 2. ドキュメント更新（VERIFY-5118 §8-2）

**`docs/operations/hanjuku-month-audit.md`**（「切り札在庫があるのに非携行で出撃」節）

- 「携行ありは補助キット＋record の cards で判定」「計画だけあってkitが無ければissue」
  の記述を、実測kitの定義へ更新:
  - 補助キット＋発射 record に加え、`order_context[step].observed_metric.cards`、
    `world.launched_orders[step]` の `departure_binding.cards`（binding付き
    snapshot の `cards` も同じ実測値）、同一 step の `sortie_confirm` decision の
    `cards` を実携行の証拠と明記（v1394/#5118）。
  - `departure_binding` を持たない snapshot の `cards` は計画値であり証拠にしない。
    実測kit が空または未読（blind approve 等）の出撃だけを issue にする旨を明記。

### 3. 退行テスト（VERIFY-5118 §8-3）

**`tests/test_hanjuku_month_audit.py`**（`sortie_without_cards` テスト群に 5 件追加）

- `test_sortie_with_measured_kit_in_order_context_is_not_recorded`
  — 予定札のみの出撃で `order_context` の実測kit があれば起票しない（本issueの再現形）。
- `test_sortie_with_bound_kit_in_launched_orders_is_not_recorded`
  — `departure_binding.cards` のある確定出撃は起票しない。
- `test_sortie_with_sortie_confirm_cards_is_not_recorded`
  — 同一 batch の `sortie_confirm` decision の `cards` で起票しない。
- `test_sortie_with_empty_measured_kit_is_recorded`
  — 実測kit が空の出撃は起票される（真陽性の維持）。
- `test_sortie_with_planned_snapshot_cards_is_recorded`
  — `departure_binding` なし snapshot の `cards`（計画値）だけでは起票される
  （実測kit 未読の出撃を誤って握り潰さない）。

### 4. バージョン・allowlist

- `src/docich/hanjuku/version.py`: `hanjuku-chart-v1394-sortie-measured-kit`
- `ops/vm_actions/collect_diagnostics.py`: allowlist に同文字列を追加（末尾カンマあり）
- `ops/vm_actions/tests/test_collect_diagnostics.py`: fixed-values リストに同文字列を追加
  （IMPLEMENT-5077 と同じ先例。`versions[0]` が `BOT_VERSION` のため動的には既に
  カバーされるが、リテラルも積む）

## 検証結果

実行コマンド（worktree `~/work/docich-5118`）:

| 対象 | 結果 |
|---|---|
| `python3 -m pytest -q tests/test_hanjuku_month_audit.py` | **未実行** |
| `python3 -m pytest -q ops/vm_actions/tests/test_collect_diagnostics.py` | **未実行** |

本環境ではオペレーターが python/pytest の実行を拒否したためローカル検証は未実施。
`pytest tests/test_hanjuku_month_audit.py` と
`pytest ops/vm_actions/tests/test_collect_diagnostics.py` を検品ゲートまたは
レビュー環境で実行されたい（AGENTS.md §13: ローカルは変更関連テストのみ、
フルスイートは CI）。

## 制約の内訳・注意点

- 監査は evidence のみ。`mem['plan']` / `mem['world']` から読むだけで
  `migrate_legacy_state` を呼ばず state を変更しない
  （`order_context`・`launched_orders` はともに `migrated: True` の領域キー）。
- `sortie_confirm` の records 後方参照は同一観測 batch 内のみを見る補助経路。
  本番では `sortie_confirm` と発射 decision は別 decide のため、主経路は
  `order_context` / `launched_orders` の2経路（どちらも発射時点で mem に残存。
  `order_context` は `_commit_departure` で `departure_order` のみ pop され
  `observed_metric` は残る）。
- `sortie_kit_corridor_approve`（第9話回廊）は `sortie_confirm`・`order_context`
  を残さない既存の別経路で、本修正以前から監査は実測kitを取得できなかった
  （corridor 注文は `order['cards']` が空）。本変更はその挙動を変えない。
- commit・push・PR 作成は実施していない（検品ゲートが別途実施）。
- handoff 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読・未更新。
