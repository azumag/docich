# 半熟英雄: 解除直後の game_over 再ラッチを抑止する（v642、2026-11-10）

## 症状（実測 21:4x）

`docich hanjuku-clear-terminal` で終了判定を解除しても、**次の観測で同じタイトル画面を見て
即座に再ラッチ**され、数秒でまた全入力が拒否されました（解除→再ラッチの無限ループ）。

## 原因

ゲームはタイトル画面に留まったまま、ボットの最初の入力が届くのを待っています。
その間に `observe()` が「タイトル・プレイ済み・名前入力済み」を満たして `game_over` を
再記録していました。

## 修正（v642 `hanjuku-chart-v642-game-over-grace`）

`terminal_cleared_at` から **120秒の猶予（`GAME_OVER_CLEAR_GRACE_S`）** の間は
`game_over` を再ラッチしません。猶予を過ぎれば従来どおりラッチします
（本当のゲームオーバーは従来どおり検出）。

## 確認

- `tests/test_hanjuku_game_over_grace.py`（2件: 猶予内は非ラッチ／猶予後はラッチ）。
- 端末・損失・スクリプト・診断 145件 passed。
- 本番反映後: 解除後にボットがタイトルを抜けてゲームに戻ることを実測する。

## 追加（v643）: 解除スタンプを観測で消さない

`observe()` は状態辞書を作り直すため、`terminal_cleared_at` が次の観測で消え、
猶予が効かずに**即再ラッチ**していました（実測 22:0x: `cleared_at` が None）。
`terminal_cleared_at` / `terminal_cleared_reason` を観測後も保持するよう修正しました。
