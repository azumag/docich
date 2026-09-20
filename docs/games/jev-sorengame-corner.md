# JEV 本編 sorengame コーナー

Issue #771 の第一段階は、既存の `sorengame` を停止・複製せず、ゲーム境界でプレイヤーだけを切り替える手動コーナーである。

既定の `[jev_corner].enabled` は `false`。自動スケジューラは持たず、明示したコマンドだけが実行対象になる。

```sh
bin/docich-jev-corner status --json
bin/docich-jev-corner diagnose
bin/docich-jev-corner start
# JEV の1試合終了と supervisor の one-game park を確認した後
bin/docich-jev-corner finish
```

本番VMでの実行は任意コマンド経路を使わず、owner-onlyの
`.github/workflows/jev-corner-operator.yml` から `start` / `finish` / `status` /
`recover` の固定操作だけをdispatchする。workflowはproductionが現在のprotected
main SHAと一致することを確認してから `/home/ubuntu/docich/bin/docich-jev-corner`
を呼び出す。`diagnose` は固定されたpreflightカテゴリだけを返し、必要な場合の
`refresh-bridge` も現試合を境界まで継続してからゲームbridgeだけを再起動するため、
共通配信・音声・encoderは再起動しない。

`start` は次の順で固定される。

1. Soren bridge の `player_policy_v1` capability を確認する。
2. lifecycle broker に `player_change(target_policy=jev)` を登録する。
3. 現在の試合を `GAMEOVER` まで継続し、`prepared` を待つ。待機中に入力を止めない。
4. `player_generation` のCASを再確認して `player-commit` する。

`finish` は同じ契約で `jev → existing` を行う。通常の `GameSwitchCoordinator` のゲーム切替、Soren91、通常の改善・回帰・promotion はこの経路から呼ばない。共通配信、音声、overlay、encoderも所有しない。

JEV試合の終了時は Soren側に `jev_one_game.json` を記録し、supervisor が同じJEV試合を自動再起動しない。`finish` は loop が既にparkしていても `GAMEOVER` 境界をbrokerへ再確認してから `existing` をcommitする。

JEVの候補選択・API境界・drop結果・専用証跡は `games/soviet_now` 側が所有する。request budget/decision/http timeout は Issue #771 の固定値として runtime 側でも強制し、設定値を契約ハッシュに束ねる。APIキーやネットワークは有効化しない。実API、VM、本番配信、画面スクリーンショットを使った受入確認は別段階である。
