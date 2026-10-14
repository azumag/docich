# Self Repair Implementation Plan

**Goal:** VM先行復旧と既存の毎時PRレビューを配備台帳で接続する。
**Architecture:** gatewayが唯一のlive writerとなり、台帳をcurrent stateと同時に保存する。修正workerは隔離候補を生成し、固定policy検証を通した変更のみgatewayへ依頼する。
**Tech Stack:** Python標準ライブラリ、Git、既存shell dispatcher、GitHub CLI。
**Spec:** docs/superpowers/specs/2026-09-07-self-repair.md

## Global Constraints
CIとowner認可を保持。レビュー待ちにlockを保持しない。strategy・資格情報・配信再起動を自動修正対象にしない。

## Task 1: gateway台帳
- [x] ops/vm_actions/tests/test_pending_repairs.pyに実Git fixtureで保持/正式化/競合/drift/rollbackを追加し赤を確認。
- [x] ops/vm_actions/gateway.pyに先行修正stageとprojection reconciliationを追加。current JSONへpending_repairsを保存しdeploy rollback時に既存live内容を戻す。
- [x] python3 -m unittest discover -s ops/vm_actions/tests -q を実行。

## Task 2: 修正候補workerと既存受付接続
- [x] root-owned policyに基づく候補生成/テスト/live検証/PR引渡しと拒否ケースをtestsへ追加。
- [x] 固定policy・隔離workspace・秘密情報を含まないreportを使うworkerを追加し、既存の無効化済みlegacy dispatcherとは分離する。
- [x] 機能テストで偽agent/偽GitHubを使って再開と失敗を確認。

## Task 3: 運用統合
- [ ] 独立レビューを受け、必須指摘を修正して再テストする。
- [ ] 作業ブランチへpush・PR作成。既存レビュー周期への引渡し手順をPR本文に明記する。
- [ ] 許可済み範囲のVM導入を行い、既存CIとの同一lock/state利用・非干渉を検証。未検証の自然発火を明記する。

## 実装時の限定
初期policyはevent_overlay_python_syntaxだけ。音声・字幕・任意の表示異常の汎用修復は未対応。既存OpenCode text-only JSONとLinux sandboxは実測。容量上限時は停止・状態記録のみで、ChatGPTへの新規通知連携は未実装。
