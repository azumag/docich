# メリケンAI レトロゲームコーナー設計

## 目的

`docich` が所有するゲーム切替基盤を使い、1日1回、20:00 JSTから60分間だけCLIゲームをSoren本番画面へ重ね、終了時に安全にSorenへ戻す「メリケンAIのレトロゲームコーナー」を提供する。

## 本番境界

- VM・deployment・systemd・ゲーム切替は `docich` が所有する。
- `soviet_now` に新しいVM制御・timer・SSH責務を追加しない。
- Soren本番の `soren-runtime.service` は従来どおりXvfb `:99`、`soren_null`、FFmpeg、workerを所有し続ける。
- 既存 `config/docich.soren-live.toml` を使い、`display.managed=false` / `stream.mode="null"` / `audio.enabled=false` のまま、960x540 viewportへdocichゲームwindowだけを載せる。
- Soren本体は背景で動き続ける。docich live canonicalがidleから始まる通常ケースでは、終了時にRobotsをstopすると背景のSorenが自然に再露出する。
- 既存 `GameSwitchCoordinator` を唯一のゲーム切替経路として使う。

## 初版ゲーム

初版は `robots` のみ。長時間無人運転を前提にしたtoken-free resolverが既に実装済みだからである。毎手LLMは呼ばない。

将来はresolver等で安全な無人運転が確認されたCLIゲームを `[retro_corner].games` に追加する。ゲーム選択はローカル日付に対して決定的に行う。

```text
index = date.toordinal() % len(games)
```

## 設定

通常の `config/docich.toml` では自動枠を無効にする。

```toml
[retro_corner]
enabled = false
start_hour = 20
duration_minutes = 60
timezone = "Asia/Tokyo"
games = ["robots"]
```

本番の `config/docich.soren-live.toml` だけ `enabled = true` にする。

制約:

- `enabled`: bool
- `start_hour`: 0–23の整数
- `duration_minutes`: 1–720の整数
- `timezone`: `zoneinfo.ZoneInfo` で解決可能なIANA timezone
- `games`: 空でない安全なゲーム名リスト
- 各対象ゲーム: `adapter="cli"` かつ `agent.enabled=true`

`retro_corner.py` がこの専用tableを検証する。既存 `GlobalConfig` に番組固有設定を混ぜない。

## CLI

```text
docich --config config/docich.soren-live.toml retro-corner tick
docich --config config/docich.soren-live.toml retro-corner start
docich --config config/docich.soren-live.toml retro-corner stop
docich --config config/docich.soren-live.toml retro-corner status [--json]
```

`python -m docich` の入口が `retro-corner` だけ専用moduleへrouteし、既存巨大CLIの変更範囲を増やさない。

- `tick`: systemd用。設定timezoneの現在hourが `start_hour` で、当日未実行なら開始する。時刻外は完全no-op。
- `start`: 手動開始。対象ゲーム検証→必要時だけ既存 `docich up` 契約でlive runtime準備→transactional start/switch→duration待機→復元。
- `stop`: active枠を早期終了。operatorが別ゲームへ切替済みなら上書きせず `interrupted`。
- `status`: private stateを表示するだけでruntimeへ触らない。

## runtime準備

毎時timerが時刻外にもtmuxへ触らないことを必須とする。`docich up` 相当は以下の場合だけ実行する。

1. 実際にコーナーを開始する直前。
2. 期限切れactive stateを復旧する直前。
3. active枠を手動stopする直前。

`docich.soren-live.toml` では `up` は共有tmuxの準備と外部`:99`の到達確認だけを行い、Xvfb・audio・FFmpegを起動/停止しない。

## 状態と排他

`<state_dir>/retro_corner.json` を0600でatomic writeする。

```json
{
  "schema_version": 1,
  "status": "idle|active|completed|interrupted|failed",
  "date": "YYYY-MM-DD|null",
  "game": "robots|null",
  "previous_game": "string|null",
  "started_at": "ISO8601|null",
  "ends_at": "ISO8601|null",
  "completed_at": "ISO8601|null",
  "last_error": "string|null"
}
```

`<state_dir>/locks/retro-corner.lock` を0600で使う。ただし60分のsleep中はlockを保持しない。これによりoperatorの `stop` や別ゲームへの手動切替を妨げない。

## 開始

1. stale activeが期限切れなら安全復旧する。
2. 同日terminal stateならscheduled tickはno-op。
3. 対象ゲーム定義を検証する。
4. 必要なlive runtimeを準備する。
5. canonical active gameを読む。live本番の通常状態はidleなので `previous_game=null`。
6. active intentをatomic保存する。
7. idleなら `coordinator.start(target)`、別docich gameがactiveなら `coordinator.switch(target)`。
8. `ends_at` までsleepする。この間corner lockは解放する。
9. 再lockして終了処理する。

## 終了・復元

- 現在activeがcorner targetなら:
  - `previous_game` がある: transactional switchで復元。
  - `previous_game` がnull: transactional stopでdocich live canonicalをidleへ戻し、背景Sorenを再露出。
- 現在activeがtargetと異なる: operatorの手動操作を優先し `interrupted`。自動復元しない。
- 復元失敗: `failed` + sanitized error。別ゲームへ強制切替しない。

## crash recovery

長時間oneshotが落ちてもstateは残す。毎時tickは時刻外でも期限切れactive stateだけを検査し、必要ならruntimeを準備して安全復旧する。未期限のactive stateには触れない。

## systemd

- `docich-retro-corner.service`: `Type=oneshot`, live configを明示して `retro-corner tick`。`TimeoutStartSec=15h`。
- `docich-retro-corner.timer`: `OnCalendar=hourly`, `Persistent=false`。

毎時起動はVM timezone依存を避けるためで、時刻判定はPython側が `Asia/Tokyo` で行う。時刻外tickはruntime準備を行わない。

## テスト

- config validationとdefault/live profileのenable境界。
- deterministic game selection。
- previous game保存→Robots→復元。
- live通常形のidle→Robots→idle。
- 60分待機中の手動stop。
- operator別ゲーム切替を上書きしない。
- 同日scheduled tickは1回だけ。
- 時刻外tickはruntime準備を呼ばない。
- stale activeの復旧。
- state 0600。
- `docich --config ... retro-corner status --json` routing。
- systemdがlive configを明示し、hourly/Persistent=falseであること。
