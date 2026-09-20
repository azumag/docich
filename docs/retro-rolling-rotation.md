# レトロ枠: 24時間 rolling rotation

本番の `[retro_corner]` は `mode = "rotation"` で、登録ゲームを無作為な順番で回す。
毎分の timer tick は状態だけを確認し、次回予定時刻に達したときだけ1ゲームを発火する。

## 選択ルール

- 発火間隔は `24時間 ÷ [retro_corner].games の登録数`。5ゲームなら4時間48分。
- 候補は、ゲーム設定が有効で必要な実行ファイルが存在するゲームに限る。
- `selection_history` にある直近24時間以内のゲームは候補から除外する。
- 選択時刻が24時間ちょうど前になったゲームは候補へ戻る。
- 最初の5枠は、直近履歴が空なら重複なしのランダム順になる。
- 他コーナーや切替境界が占有中の場合は発火せず、次のtickで再試行する。

選択履歴と `next_due_at` は `run-soren-live/retro_corner.json` に保存する。途中で
プロセスが再起動しても履歴・次回時刻を引き継ぎ、pending選択が境界待ち中なら同じゲームを
再開する。

## 本番設定

`config/docich.soren-live.toml` の現在値は次のとおり。

```toml
[retro_corner]
mode = "rotation"
rotation_period_hours = 24.0
rotation_wait_minutes = 10
games = ["ninvaders", "nsnake", "bastet", "moon-buggy", "pacman4console"]
```

`lottery` モードは互換のため残しているが、本番の定期発火には使用しない。
