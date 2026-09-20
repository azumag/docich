# レトロ枠: 24時間 rolling rotation

本番の `[retro_corner]` は `mode = "rotation"` で、登録ゲームを無作為な順番で回す。
毎分の timer tick は状態だけを確認し、次回予定時刻に達したときだけ1ゲームを発火する。

## 選択ルール

- 発火間隔は `24時間 ÷ [retro_corner].games の登録数`。現在の6ゲームなら4時間。
- 候補は、ゲーム設定が有効で必要な実行ファイルが存在するゲームに限る。
- `selection_history` にある直近24時間以内のゲームは候補から除外する。
- 選択時刻が24時間ちょうど前になったゲームは候補へ戻る。
- 最初の6枠は、直近履歴が空なら重複なしのランダム順になる。
- 他コーナーや切替境界が占有中の場合は発火せず、次のtickで再試行する。
- ゲーム切替中に到着した要求は `run-soren-live/game-switch/requests/*.json` へ
  `queued` としてFIFO順に保存し、先行要求が安定phaseへ戻ってから順番に消化する。
  選択済みの `pending` は切替中・境界待ちの理由だけでは破棄しない。
- 期限切れの `draining` を新しい要求が検出した場合は、現在のboundary要求を安全に
  取り消してcanonicalを復旧できたときだけ、保持していたキュー先頭を続けて実行する。
- `[paper_corner]`、`[soren91_corner]`、専用の `[nethack_corner]` の有効な固定枠を先読みする。rotationの
  `rotation_wait_minutes + duration_minutes` の最悪終了時刻が固定枠開始の5分前までに
  収まらない場合は選択せず延期し、固定枠の実行中も開始しない。たとえば22:00の
  PAPER枠に対して21:50にdueになっても、rotationは発火せず固定枠終了後に再試行する。

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
games = ["ninvaders", "nsnake", "bastet", "moon-buggy", "pacman4console", "nethack"]
```

現在の本番設定は上記の6ゲームです。
NetHackは通常の20分slotでローテーションし、`config/games/nethack.toml` の
`persistent_run = true` と `NethackCoordinatorAdapter` により、slot終了時に通常の
NetHack save boundaryで保存して次回へ再開します。死亡・昇天まで待つ専用の長期攻略枠ではありません。

専用の `[nethack_corner]` は長期攻略用に別管理されており、rolling rotationと同時には有効化できません。
同時設定は起動時にエラーとして扱い、NetHackの二重スケジュールを防ぎます。

`lottery` モードは互換のため残しているが、本番の定期発火には使用しない。
