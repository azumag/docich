# 半熟英雄: game_over 判定後の復活で入力を再開する（v631、2026-11-10）

## 症状（実測 17:40〜18:05）

エミュレータが落ちた際に `hanjuku_run.json` へ **`terminal_reason: game_over`** が記録され、
ゲームを再起動・セーブ復帰した後も

```
docich: エラー: Hanjuku terminal evidence holds input
```

で**すべての入力が拒否**され続けました（約2時間、画面は復帰済みなのにボットが動かない）。

## 原因

`hanjuku_run.observe()` は端末判定（`TERMINAL_REASONS`）が入ると**以後その run を終了扱い**にし、
新しいフレームが来ても判定を解きません。ランタイムが復活する経路がありませんでした。

## 自動解除は行わない（設計判断）

`game_over` は「タイトルへ戻った」＝試合終了の証拠で、**別フレームが来ても解除しない**のが
既存の契約です（`tests/test_hanjuku_script.py::test_title_return_requires_a_started_game_and_multiple_timed_observations`:
"Terminal evidence is latched even if a later screen is different"）。

今回の事象は、**エミュレータが落ちて復旧した**ことが原因で、ゲーム内容のゲームオーバーでは
ありません。自動解除は「終了した試合を再開する」危険があるため行わず、
**運用手順（下記）で解除**します。

## 復旧（実測）

- `hanjuku_run.json` の `terminal_reason` / `terminal_candidate` をクリア（バックアップ取得済み）。
- 直後に入力が通り、`docich: 1 件のアクションを送信しました (hanjuku-hero)` を確認。
- ゲームは召喚戦→卵バトルメニューへ進行し、ボットの意思決定も再開（18:09 の記録）。

## 復旧手順（今回の実施内容）

1. `run-soren-live/runtimes/<rt>/hanjuku_run.json` をバックアップ。
2. `terminal_reason` と `terminal_candidate` を `null` に更新。
3. `docich send - '{"actions":[...]}'` で入力が通ることを確認。
4. 画面が進むことを確認（召喚戦 → 卵バトルメニュー）。

## フォローアップ（恒久策の候補）

- エミュレータ停止→復帰を検知して**端末判定を明示的に解除する運用手順**を CLI に用意する
  （終了した試合を誤って再開しないよう、オーナー操作を必須にする）。
- `docich switch` の `round_boundary` タイムアウト時に canonical 状態が `failed` へ落ち、
  `recover` も `quiesce` で失敗する経路の改善（今回は `game_switch.json` のバックアップから復元）。
