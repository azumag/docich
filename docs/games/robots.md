# Robots

Robots は `bsdgames` に含まれる軽量なターン制端末ゲームです。ROMや外部データは不要で、
既存の `cli` アダプタを使って `tmux` 内で動作します。

## インストール

Ubuntu 24.04では次のパッケージを使用します。

```bash
sudo apt install -y --no-upgrade bsdgames
```

`scripts/setup_ubuntu_arm.sh`にも含まれています。

## 操作

```bash
bin/docich start robots
bin/docich obs robots
bin/docich send robots '{"type":"key","keys":["h"]}'
bin/docich stop
```

移動はviキー（`h`/`j`/`k`/`l`、斜めは`y`/`u`/`b`/`n`）を使います。`w`は安全な間
待機し、`.`は1ターン待機、`q`は終了です。

ゲーム開始時に案内画面が表示された場合は、`Enter`または任意キーを送って開始します。

## AI運用

本番設定では `[agent] enabled = true`, `brain = "resolver"` です。resolverは盤面を
ローカルに解析して決定論的にキー入力を返すため、1手ごとのLLM呼び出しはありません。
`run/resolver/robots_strategy.json`（live profileでは `run-soren-live/resolver/...`）を
hot reloadでき、改善loopが戦略パラメータを更新してもagent再起動は不要です。

Robotsは現在、長時間の無人運転を前提にしたCLIゲームの基準実装です。このため
「メリケンAI レトロゲームコーナー」の初版対象もRobotsだけに限定しています。

## Soren本番画面への切替

既存FFmpeg接続を維持したまま`:99`へ載せる場合は、外部所有display用設定を明示します。
docichはXvfb、音声bus、FFmpegを起動・停止しません。

```bash
bin/docich --config config/docich.soren-live.toml up
bin/docich --config config/docich.soren-live.toml start robots
```

`config/docich.soren-live.toml` は `managed=false` でSorenの`:99`へ接続し、960x540の
viewportにRobotsを載せます。終了時にdocich側をstopすると、背景で動き続けていたSorenが
再び見えるため、FFmpeg接続を張り直す必要はありません。

## メリケンAI レトロゲームコーナー

本番profileでは毎日20:00 JSTから60分、Robotsを前面へ載せます。

```bash
bin/docich --config config/docich.soren-live.toml retro-corner status --json
bin/docich --config config/docich.soren-live.toml retro-corner start   # 手動試験
bin/docich --config config/docich.soren-live.toml retro-corner stop    # 早期終了
```

定期起動は `docich-retro-corner.timer` が毎時 `tick` し、Python側が `Asia/Tokyo` の
開始時刻と「当日実行済み」を判定します。60分待機中はコーナーlockを保持しないため、
operatorの早期stopや別ゲームへの手動切替を妨げません。途中で別ゲームへ切り替えられた
場合、終了処理はoperator操作を上書きせず `interrupted` として記録します。
