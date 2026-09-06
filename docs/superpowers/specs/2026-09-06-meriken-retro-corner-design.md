# メリケンAI レトロゲームコーナー設計

## 目的

`docich` が所有するゲーム切替基盤の上で、1日1回、既定20:00 JSTから60分間だけCLIゲームを自動プレイし、終了時に開始前のゲームへ安全に戻す「メリケンAIのレトロゲームコーナー」を提供する。

## 境界

- スケジュール・ゲーム切替・復元は `docich` が所有する。
- `soviet_now` にVM制御・systemd timer・SSH・deployment責務を追加しない。
- 既存 `GameSwitchCoordinator` を唯一のゲーム切替経路として使う。
- 初版の対象ゲームは `robots` のみ。現在、長時間放置に耐える専用resolverが実装済みなのが `robots` だけだからである。
- 将来は `[retro_corner].games` にresolver対応済みCLIゲームを追加するだけで日替わり対象を増やせるようにする。
- 初版は毎手LLMを呼ばない。Robotsのtoken-free resolverを使い、番組名・状態・ログ上でメリケンAIコーナーとして扱う。LLM人格付きbrainは別機能として追加可能な境界を残す。

## 設定

`config/docich.toml` に次を追加する。

```toml
[retro_corner]
enabled = true
start_hour = 20
duration_minutes = 60
timezone = "Asia/Tokyo"
games = ["robots"]
```

制約:

- `enabled` はbool。
- `start_hour` は0–23の整数。
- `duration_minutes` は1–720の整数。
- `timezone` は `zoneinfo.ZoneInfo` で解決可能なIANA timezone。
- `games` は空でない安全なゲーム名リスト。
- 各対象ゲームは `adapter="cli"` かつ `agent.enabled=true` でなければならない。
- 初版の実運用設定は `robots` のみ。

## CLI

新規コマンド:

```text
docich retro-corner tick
docich retro-corner start
docich retro-corner stop
docich retro-corner status [--json]
```

- `tick`: systemd timer用。設定timezoneの現在時刻が `start_hour` で、当日未実行なら `start` 相当の1時間枠を同期実行する。条件外なら成功扱いで何もしない。
- `start`: 手動開始。既にactiveならfail-closed。開始前ゲームを保存し、対象CLIゲームへtransactional switchし、`duration_minutes` 待機した後に安全な復元を行う。
- `stop`: active枠を終了。現在ゲームがコーナー対象のままなら開始前ゲームへ復元する。途中でoperatorが別ゲームへ切り替えていた場合はその手動操作を上書きせず `interrupted` として完了する。
- `status`: 状態ファイルを読み、人間向けまたはJSONで出力する。

## ゲーム選択

`games` の選択はローカル日付に対して決定的に行う。

```text
index = date.toordinal() % len(games)
```

同じ日には必ず同じ対象を選び、再起動や再試行でゲームが変わらない。

## 状態

`<state_dir>/retro_corner.json` を0600でatomic writeする。

最低限のschema:

```json
{
  "schema_version": 1,
  "status": "idle|active|completed|interrupted|failed",
  "date": "YYYY-MM-DD",
  "game": "robots|null",
  "previous_game": "sorengame|null",
  "started_at": "ISO8601|null",
  "ends_at": "ISO8601|null",
  "completed_at": "ISO8601|null",
  "last_error": "string|null"
}
```

`<state_dir>/locks/retro-corner.lock` のflockで `tick/start/stop` を直列化する。

## 開始フロー

1. 設定と対象ゲームを検証。
2. stale active stateがあり `ends_at` を過ぎていたら、まず安全な復元を試みる。
3. canonical game switch stateから現在active gameを読む。idleなら `previous_game=null`。
4. `retro_corner.json` にactive intentを記録。
5. 既に対象ゲームならswitchしない。それ以外は `GameSwitchCoordinator.switch(target)` または idleなら `start(target)`。
6. `ends_at` まで待つ。
7. 終了フローを実行。

開始切替が失敗した場合は `failed` を記録し、開始前ゲームを無理に変更しない。

## 終了・復元フロー

1. canonical active gameを読む。
2. active gameがコーナー対象と一致する場合:
   - `previous_game` がある: transactional switchで復元。
   - `previous_game` がない: transactional stopでidleへ戻す。
3. active gameがコーナー対象と異なる場合: operatorの手動切替とみなし、復元せず `interrupted`。
4. 成功時 `completed` を記録。
5. 復元失敗時 `failed` + sanitized errorを記録し、別ゲームへの強制切替はしない。

## systemd

新規user unit:

- `docich-retro-corner.service`: `Type=oneshot`, `ExecStart=__DOCICH_ROOT__/bin/docich retro-corner tick`
- `docich-retro-corner.timer`: 毎時起動。時刻判定はPython側が設定timezoneで行うため、VMのsystem timezoneに依存しない。`Persistent=false`。

既存systemd READMEにcopy/install/enable手順を追記する。timerはrepoへのmergeだけでは自動enableせず、VMで一度 `systemctl --user enable --now docich-retro-corner.timer` を実行する。

## 失敗時の扱い

- 多重起動: lock + active stateで拒否。
- 手動ゲーム切替: operator操作を優先し、終了時に上書きしない。
- process crash: stateを残す。次回tick/startで期限切れactive stateを検出して安全復元を試みる。
- game switch failure:既存coordinatorのfail-closed/rollback契約をそのまま使う。
- state書込: `atomic_write_json` を使い0600を維持する。

## テスト

- config validation: timezone/hour/duration/games型・範囲。
- deterministic game selection。
- startでprevious game保存→robots切替。
- duration後にprevious game復元。
- idleから開始した場合は終了時idleへ戻る。
- operatorが途中で別ゲームへ切替済みなら上書きせずinterrupted。
- 同日tickは1回だけ。
- 時刻外tickはno-op。
- stale active stateの復元。
- systemd unitが `retro-corner tick` を呼ぶ。
- `robots` がCLI + enabled agent条件を満たすこと。
