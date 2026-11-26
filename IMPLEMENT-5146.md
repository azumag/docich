# IMPLEMENT-5146 — [半熟英雄] 出撃失敗: ch8 66-5 シェーブル

対象: docich#5146 / 検証 `~/work/docich-5146/VERIFY-5146.md`（結論 (a) 実欠陥、§6 の修正方針案が正本）
実装 worktree: `~/work/docich-5146-impl`（base: main `97a6deba2` / v1457-empty-kit-cancel 相当、PR #5380 マージ済み）

## 変更概要

検証 §6 の主案＋副案＋案3（任意）をすべて実装。

### 主案: 出撃側への遮蔽許容の適用

- `src/docich/hanjuku/os_layer/readers.py` `_sortie_inventory(screen, *, occluded=0)`
  - `occluded` キーワード引数を新設。`_empty_sortie_inventory` と同じクラスの許容:
    期待セルが UNKNOWN / 消失でも合計 `occluded` 個まで不一致としない。
    読めるセルが期待と違う・期待外位置に既知文字がある・カード名スパンが
    UNKNOWN で分割される場合は従来どおり不可読（None）。携行札の名前は
    「期待固定セル」ではないため、名前が欠けたままでは推測しない。
  - `occluded > 0` のとき末尾のオプション名一致は「読めた名前が携行札の
    部分集合」へ緩和（遮蔽でオプション span が落ちるため）。既知外の
    カード名が出る場合は従来どおり不可読。
  - `occluded=0`（既定）の厳格レシートは完全に同一の判定（`matches` は
    `observed == expected` に短絡）。repair 側・既存の全 strict 呼び出しは不変。
- `src/docich/hanjuku/flows/deploy.py` `deploy_step` の `sortie_confirm` 分岐
  - `from .house import SORTIE_PANEL_OCCLUDED`（=2、#3920/v655 の修理派遣で実測済みの
    同一障害クラスの上限）を遅延 import し、
    `_empty_sortie_inventory(screen, occluded=SORTIE_PANEL_OCCLUDED)` と
    `_sortie_inventory(screen, occluded=SORTIE_PANEL_OCCLUDED)` を呼ぶ。
    これにより ながはま型（城スプライトが右パネルに被さる）の確認画面で
    `carried=None` 固定化が解消される。

### 副案: 中断越しの盲承認フラグ再武装

- `deploy.py` `deploy_step` の `order is not None` 直後:
  `kind != 'sortie_confirm'` かつ注文が `launched_unconfirmed` でないとき、
  `sortie_confirm_miss` / `sortie_confirm_blind` の当該 step をリセット。
  確認画面を離れた画面種別（メニュー再遷移・戦闘割り込み後の復帰）は
  「その連続未読ラウンドの終了」を意味し、再突入した確認には盲承認の
  1 回の実施機会を新たに与える。盲Aが中断に飲まれた場合に
  「2巡目だから取消」へ直行する誤判定（VERIFY §3 の隙間）を防ぐ。
  `launched_unconfirmed` は map.py order_start と同じく再武装しない
  （名寄せ読みは前回出撃の盲A再許可ではない）。

### 案3（監査証拠、実施）: order_failed に盲承認有無を記録

- `deploy.py` の「出撃確認の携行札を読めない」`_finish_order('failed')` の
  `observed_metric` に `blind_approved` を追加。直近30行窓に第1巡が
  落ちても JSON だけで盲承認の発火有無を検証できる（VERIFY §6.3）。

## バージョン

- `src/docich/hanjuku/version.py`: `hanjuku-chart-v1567-sortie-panel-occluded`
- `ops/vm_actions/collect_diagnostics.py` allowlist: 同値を登録（末尾カンマあり）。
- 採番根拠: 2026-11-27 の main は v1566、v1459 は別機能へ使用済みのため、
  rebase 時に次の未使用版 v1567 へ再採番した。

## 対象ファイル

- `src/docich/hanjuku/os_layer/readers.py`（`_sortie_inventory` に occluded 許容）
- `src/docich/hanjuku/flows/deploy.py`（occluded 渡し・中断後リセット・blind_approved 記録）
- `src/docich/hanjuku/version.py`（v1567）
- `ops/vm_actions/collect_diagnostics.py`（allowlist 登録）
- `tests/test_hanjuku_sortie_panel_occlusion.py`（新規・回帰テスト）
- `tests/test_hanjuku_sortie_evidence.py`（意図した挙動変更に追随:
  期待セル1個の UNKNOWN/消失は strict では不可読だが deploy は承認する
  ケースへ分割。可読な置換文字・承認項目が読めないケースは引き続き保留）

## テスト

- 追加: `tests/test_hanjuku_sortie_panel_occlusion.py`
  - 主案: 実測スプライトフィクスチャ上の空札承認・固定セル遮蔽での札レシート・
    上限超過/置換文字/札名欠損/既知外文字は依然不可読
  - 副案: 確認離脱で盲承認が再武装されること・`launched_unconfirmed` では
    再武装しないこと
- **ローカル実行は未実施**: 本セッションでは shell 実行が拒否され
  pytest を起動できなかった。関連テスト（`test_hanjuku_sortie_panel_occlusion`・
  `test_hanjuku_sortie_evidence`・`test_hanjuku_sortie_retry_budget`・
  `test_hanjuku_sortie_confirm_cancel`・`test_hanjuku_repair_confirm_sprite_occlusion`）の
  結果は PR の CI に委ねる（AGENTS.md §13 の方針どおり）。

## コミット

- 同上の理由で `git add` / `git commit` も未実施。変更は worktree の
  作業ツリーに残してある。push・PR 作成・本番 SSH は依頼どおり実施していない。

## 残件・既知の不確定点

- OPEN PR のバージョン重複は `gh` 非実行のため未確認（v1458 衝突時は再採番）。
- 不可読の直接パターン（城スプライト遮蔽かスロット配置の未校正か）は
  `hanjuku_frames/` の実測が無いと最終確定できない（VERIFY §7）。
- `docs/operations/hanjuku-confirm-blind-approve-20261109.md` 等の
  「盲承認は1回だけ」記述は「連続未読ラウンドごとに1回」へ意味が変わる。
  文書更新は今回の範囲外（コード挙動の変更に追随したい場合は follow-up）。
