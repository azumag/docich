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

## 修正（v631 `hanjuku-chart-v631-terminal-revival`）

`observe()` で、端末判定が `game_over` のときに限り、

- エミュレータが **playing** を報告し、
- 端末判定時と**異なるフレーム**で、
- そのフレームが **title ではない**（＝ゲームプレイに戻っている）

なら、端末判定を**解除**して観測を継続します（`terminal_cleared` イベントを記録）。
`screen_stalled` / `input_stalled` は従来どおり解除しません。

## 復旧（実測）

- `hanjuku_run.json` の `terminal_reason` / `terminal_candidate` をクリア（バックアップ取得済み）。
- 直後に入力が通り、`docich: 1 件のアクションを送信しました (hanjuku-hero)` を確認。
- ゲームは召喚戦→卵バトルメニューへ進行し、ボットの意思決定も再開（18:09 の記録）。

## 確認

- `tests/test_hanjuku_terminal_revival.py` を追加（復活する／同一フレームでは解除しない）。
- 端末・損失・出撃系 96件 passed。
