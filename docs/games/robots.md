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
bin/docich stop robots
```

移動はviキー（`h`/`j`/`k`/`l`、斜めは`y`/`u`/`b`/`n`）を使います。`w`は安全な間
待機し、`.`は1ターン待機、`q`は終了です。

ゲーム開始時に案内画面が表示された場合は、`Enter`または任意キーを送って開始します。

## AI運用

初期設定では`[agent] enabled = false`です。Robotsはターン制なので、観測結果を基に1手ずつ
判断する`direct-play`の最初の検証対象に適しています。自動操作を有効化する前に、手動で
起動・観測・入力・停止が成立することを確認してください。

## Soren本番画面への切替

既存FFmpeg接続を維持したまま`:99`へ載せる場合は、外部所有display用設定を明示します。
docichはXvfb、音声bus、FFmpegを起動・停止しません。

```bash
bin/docich --config config/docich.soren-live.toml up
bin/docich --config config/docich.soren-live.toml start robots
```
