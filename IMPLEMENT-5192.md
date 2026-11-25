# Issue #5192 — 切り札を残して退却: ch2 ヘラ（監査 metric エクスポート欠落）

## 対象

Issue #5192（監査自動起票、fingerprint `9c90f103e9f83199`、ラン `g1190-ch1v9`）。
VERIFY-5192.md の結論 (a) 実欠陥に基づく修正。欠陥の所在はボットの退却動作ではなく、
退却系決定レコードの `observed_metric` が `cards_unclassified` をエクスポートしない
監査側の穴。#5158/#5181 の `cards_unconfirmed_only` medium 降格は敗北経路
（`battle_result`）にしか効かず、退却経路では同じ帳簿構造でも降格不能で high 起票
される非対称が残っていた。

ボットの退却・切り札フロー自体は owner 方針（v1360「たまごや切り札を使い切ってから
退却」）に適合しており、本修正は監査証拠の伝搬のみ。ボット動作は変更しない。

## 変更内容

- `src/docich/hanjuku/flows/battle.py`
  - `battle_retreat_preempted_card`（`_hero_retreat_open`、preempt 直前に
    `_card_use_unclassified` で pending 札が unclassified へ積算済み）の
    `observed_metric` に `'cards_unclassified': cur.get('cards_unclassified', [])`
    を追加。
  - `battle_hero_retreat_open` の `observed_metric` にも同フィールドを追加
    （監査の起票対象レコードではないが、帳簿の整合のため）。
- `battle_hero_retreat_select` への追加は**不要**: v1414（`e91428f7e`、退却一律禁止）
  で emitter が削除済み。VERIFY-5192.md は v1408 系を基盤に記載されており、現行
  main では対象コードが存在しない。監査側は旧ランの `battle_hero_retreat_select`
  レコードを引き続き受理するが、当該 emitter がこのフィールドを出力した履歴はなく、
  旧レコードへの遡及適用は原理上できない。
- `ops/vm_actions/collect_diagnostics.py`: `BOT_VERSION` 許可リストへ
  `hanjuku-chart-v1460-retreat-unclassified-export` を追加（末尾カンマあり）。
- `src/docich/hanjuku/version.py`: `BOT_VERSION` を
  `hanjuku-chart-v1460-retreat-unclassified-export` へ bump。
  （開始時の現行 main は ee1b4fb95 / v1458。`gh` がこの環境では実行不可のため
  OPEN PR の採番は再確認できず、依頼記載の候補 v1460 を使用した。v1460 が先に
  取られていた場合はレビュー時に再採番する。）

## 修正後の動作

- `battle_retreat_preempted_card` の `card` フィールド（= preempt された pending 札）
  が `cards_remaining` に入るのは従来どおりだが、同札は preempt 直前に
  `_card_use_unclassified` で `cards_unclassified` に積算され、今回の export で
  metric へ届く。残存が unclassified のみの場合
  `cards_unconfirmed_only = True` → `confidence = 'medium'`
  （`confidence_reasons = ['cards_unconfirmed_signature']`）→ filer の既定
  `min_confidence='high'` で起票対象外。敗北経路と同じ降格が退却経路にも効く。
- 残存に使用未確認でない通常札が混ざる退却は従来どおり high で起票する
  （回帰テストで対照として固定）。

## 回帰テスト（tests/test_hanjuku_month_audit.py）

- `test_unclassified_only_card_retreat_is_not_filed`: #5192 と同形の帳簿
  （`cards_selected=[N,N]` / `cards_used=[N]` / `cards_unclassified=[N]` /
  `card=ノリウツール`、ally_hp 12）の `battle_retreat_preempted_card` レコードが
  `battle_retreat_with_resources` + `confidence='medium'` + `confidence_reasons=
  ['cards_unconfirmed_signature']` になり、既定閾値の `file_issue` が
  `'no_incident'` を返すこと（不起票）を表明。
- `test_plain_card_retreat_stays_high_and_filed`: 対照。unclassified でない残り札の
  退却は `confidence='high'` のまま `file_issue` が `'created'` になること
  （過剰抑制でないことのガード）。
- `tests/test_hanjuku_survival.py`
  `test_critical_hp_preempts_unconfirmed_card_for_every_general`: preempt レコードの
  `observed_metric` 期待値に `cards_unclassified` を追加（emit の回帰ガード）。

## #5194 への影響

VERIFY-5202.md §5 が #5192/#5194/#5198 を同一機構（退却系レコードの
`cards_unclassified` 監査欠落 → `cards_unconfirmed_only` が発火せず high 起票）
と整理している。本修正で `battle_retreat_preempted_card` が当該フィールドを
emit するようになるため、**今後発行される同型の退却レコード（#5194 の系列を含む）
は medium 降格が発火し、既定閾値では起票されない**。

制約: `battle_hero_retreat_select` は v1414 で emitter 削除済みのため、旧ラン由来の
同レコードがこのフィールドを持つことはなく、過去記録への効果はない。#5198 について
も同じ穴の系統であり、preempt 経路の新規レコードは同様にカバーされるが、select 経路
の旧記録は対象外。

## 検証

- ローカル pytest を実施済み（Hermes が 2026-11-24 に実行）:
  `tests/test_hanjuku_month_audit.py`・`tests/test_hanjuku_survival.py`・
  `tests/test_hanjuku_issue_4999.py` = 264 passed。
  allowlist 整合（`test_hanjuku_hotload_versions_are_fixed_values_not_an_arbitrary_prefix`
  ほか）= 2 passed。
  `ops/vm_actions/tests/test_collect_diagnostics.py` は 231 passed / 2 failed だが、
  `test_an_inaccessible_docich_root_is_treated_as_absent` の 2 件は
  `os.chmod(locked, 0)` を root が迂回するため変更を stash した状態でも同じく失敗する
  環境依存の既知事象で、本修正とは無関係。
- `git add` / `git commit` は受入条件（検品ゲート後にメイン Muse が実施）に従い、
  変更はブランチ上の未コミットの作業ツリー変更として残す。push・PR 作成も行わない。

## 未コミットの既存変更について

本 worktree の作業ツリーには、着手時点で `flows/battle.py` の 2 箇所への
`cards_unclassified` export と `test_hanjuku_survival.py` /
`test_hanjuku_month_audit.py` の回帰テストが既に（未コミットで）適用されていた。
内容は VERIFY-5192.md §6 の想定修正と一致するためそのまま採用し、version bump・
diagnostics allowlist・本ドキュメントを追加分として補完した。
