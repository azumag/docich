# レトロ枠: 24時間 rolling rotation

> production設定は[全corner共通rotation](corner-rotation.md)へ移行した。
> 以下は`corner_rotation.enabled`を有効にしていない旧profileの互換仕様。
> 統一モードではPAPER/メリケンも同列で、間隔の分母は実効有効数Nとなる。

`corner_rotation.enabled=false` の旧profileでは、`[retro_corner]` の
`mode = "rotation"` が登録ゲームを無作為な順番で回す。現行productionの共通catalogでは
レトロゲーム・PAPER・メリケンを同列に扱うため、選択規則は
[全corner共通rotation](corner-rotation.md)を参照する。
旧profileの毎分timer tickは状態だけを確認し、次回予定時刻に達したときだけ1ゲームを発火する。

## 選択ルール

- 発火間隔は `24時間 ÷ [retro_corner].games の登録数`。7件登録なら約3時間26分。
  無効な登録も分母に含む。半熟英雄の登録前の6件では4時間だった。
- 候補は、ゲーム設定が有効で必要な実行ファイルが存在するゲームに限る。
- ゲーム側 `[retro_corner].enabled = false` は自動コーナーの適格性検査で除外する。
  省略時は `true`。boolean以外は不正設定として除外する。これはagent起動の許可ではない。
- rotation/lottery設定の即時 `retro-corner start` も適格なゲームだけから選ぶ。
  適格なゲームがなければ `no-eligible-game` で開始しない。
- `selection_history` にある直近24時間以内のゲームは候補から除外する。
- 選択時刻が24時間ちょうど前になったゲームは候補へ戻る。
- 適格なゲームは、直近履歴が空なら重複なしのランダム順になる。
  全ゲームがクールダウン中なら開始せず、候補が戻るまで次のtickで再試行する。
- 他コーナーや切替境界が占有中の場合は発火せず、次のtickで再試行する。
- ゲーム切替中に到着した要求は `run-soren-live/game-switch/requests/*.json` へ
  `queued` としてFIFO順に保存し、先行要求が安定phaseへ戻ってから順番に消化する。
  選択済みの `pending` は切替中・境界待ちの理由だけでは破棄しない。
- 期限切れの `draining` を新しい要求が検出した場合は、現在のboundary要求を安全に
  取り消してcanonicalを復旧できたときだけ、保持していたキュー先頭を続けて実行する。
- `[soren91_corner]`、専用の `[nethack_corner]` の有効な固定枠を先読みする。rotationの
  `rotation_wait_minutes + duration_minutes` の最悪終了時刻が固定枠開始の5分前までに
  収まらない場合は選択せず延期し、固定枠の実行中も開始しない。たとえば18:00の
  soren91枠に対して17:50にdueになっても、rotationは発火せず固定枠終了後に再試行する。
  固定時間を持たない `[paper_corner]` は静的な予約枠を持たず、program slot の実行時排他で
  PAPER枠の開始を待たせる（rotationが先に走ってもPAPERはprogram lock待ちになる）。

選択履歴と `next_due_at` は `run-soren-live/retro_corner.json` に保存する。途中で
プロセスが再起動しても履歴・次回時刻を引き継ぎ、pending選択が境界待ち中なら同じゲームを
再開する。

## 本番設定

旧profileで使う `config/docich.soren-live.toml` 相当の設定例は次のとおり
（現行productionのcatalogとは別）。

```toml
[retro_corner]
mode = "rotation"
rotation_period_hours = 24.0
rotation_wait_minutes = 10
games = ["ninvaders", "nsnake", "bastet", "moon-buggy", "pacman4console", "nethack", "hanjuku-hero"]
```

7件のうち半熟英雄は登録のみで、ゲーム側 `retro_corner.enabled = false` により除外される。
既存6ゲームも必要な実行ファイルがない環境では除外される。
半熟英雄の実行資格は [ゲーム資料](games/hanjuku-hero.md#8-レトロコーナー登録と実行資格) を参照。
登録数変更で次回予定が再計算されるため、配備後の最初のtickは即時dueになり得る。
NetHackは通常の20分slotでローテーションし、`config/games/nethack.toml` の
`persistent_run = true` と `NethackCoordinatorAdapter` により、slot終了時に通常の
NetHack save boundaryで保存して次回へ再開します。死亡・昇天まで待つ専用の長期攻略枠ではありません。

専用の `[nethack_corner]` は長期攻略用に別管理されており、rolling rotationと同時には有効化できません。
同時設定は起動時にエラーとして扱い、NetHackの二重スケジュールを防ぎます。

`lottery` モードは互換のため残しているが、本番の定期発火には使用しない。
