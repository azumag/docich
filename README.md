# docich — マルチゲーム AI 配信基盤

docich は、ヘッドレス Linux 上で表示・音声・配信を常駐させたまま、
ゲームと AI 操作系だけを差し替えるための基盤です。現在のアダプタは
ブラウザ、RetroArch、CLI/TUI の3種類です。

設計の一次情報は [`docs/architecture.md`](docs/architecture.md) です。
本READMEは導入・日常操作・本番Sorenとの境界をまとめています。

## 構成

```text
tmux session: docich
  display  Xvfb :98 (1280x720)
  audio    docich_sink (既存PulseAudioを再利用)
  stream   FFmpeg x11grab + Pulse -> null/file/RTMP
  game     browser / retroarch / cli adapter
  agent    observe -> external brain -> action (任意)
```

`display`、`audio`、`stream` はゲームから独立しています。
`docich switch` は `game` と `agent` だけを交換するため、配信プロセスを
維持したままゲームを切り替えられる設計です。

各windowは監督ループ配下で動き、異常終了時は指数バックオフで再起動します。
RTMPキーは状態表示と監督ログでマスクされます。

## 本番Sorenとの境界

同じVMで稼働する現在の sorengame 本番は
[`azumag/soviet_now`](https://github.com/azumag/soviet_now) が所有します。
docichをマージまたは起動しても、本番の所有権は移りません。

| リソース | Soren本番 | docich既定 |
|---|---|---|
| X display | `:99` | `:98` |
| audio sink | `soren_null` | `docich_sink` |
| stream | FFmpeg live | `stream.mode = "null"` |
| supervisor | `soren-runtime.service` | tmux `docich` |
| game AI | soviet_now戦略/改善ループ | ゲームごとの任意agent |

docichは既存PulseAudioを再利用しますが、既定sinkは変更しません。
`config/games/sorengame.toml` は `http://127.0.0.1:8080` を表示する
viewer専用定義で、Sorenの `start_all.sh` は呼びません。詳しくは
[`docs/games/sorengame.md`](docs/games/sorengame.md) を参照してください。

## クイックスタート

対象は Ubuntu 24.04 (arm64/amd64)、Python 3.11以上です。

```bash
# リポジトリを clone する (ゲーム実装はサブモジュールなので --recurse-submodules を付ける)
git clone --recurse-submodules git@github.com:azumag/docich.git
cd docich
# 既に clone 済みでサブモジュールが未取得の場合は代わりに: git submodule update --init

scripts/setup_ubuntu_arm.sh
bin/docich doctor
bin/docich up
bin/docich start nethack
bin/docich status
bin/docich switch hanjuku-hero
bin/docich down
```

安全のため、既定では配信しません。ローカル確認は
`stream.mode = "file"`、本番RTMPは `stream.mode = "rtmp"` を明示し、
キーを環境変数だけで渡します。

```bash
export DOCICH_STREAM_KEY="<stream key>"
bin/docich up
```

キーを設定ファイル、Git、Issue、Wiki、ログへ保存しないでください。

## ネイティブTwitch字幕

docichは、VOICEVOX等の日本語音声に合わせて英語のTwitchネイティブ字幕を
H.264へ埋め込む再利用可能な基盤を持ちます。標準FFmpegには含まれない
`docichcc` filterを使うため、字幕は明示的なopt-inです。

```bash
native/ffmpeg/build.sh /tmp/docich-cc-build
export DOCICH_FFMPEG_BIN=/tmp/docich-cc-build/ffmpeg-install/bin/ffmpeg
export DOCICH_CC_ENABLED=1
export DOCICH_CC_SOCKET="$XDG_RUNTIME_DIR/docich/ffmpeg-cc.sock"
bin/docich doctor
bin/docich status
```

字幕機能を要求していても、custom FFmpegが無い、`docichcc`が無い、
または`libx264 a53cc`が無い場合は、通常の映像・音声コマンドへfail-open
します。字幕の失敗で音声や配信を止めません。`status` は
`captions.requested` と `captions.active` を分けて表示します。

字幕計画とsocket操作:

```bash
bin/docich caption plan \
  --chunks-file speech.txt \
  --translations-file translations.json \
  --execution-id speech-123 \
  --output speech-123.plan.json

bin/docich caption send prepare --plan speech-123.plan.json --chunk 0 --page 0
bin/docich caption send commit  --plan speech-123.plan.json --chunk 0 --page 0
bin/docich caption send clear   --plan speech-123.plan.json
```

翻訳出力は完全一致のJSON schemaだけを受理します。thinking、Web検索や
toolの進行表示、Markdown、説明文、余分なkeyは抽出せず拒否するため、
字幕本文へ混入しません。Soren本番の読み上げ本文には、これとは別に
soviet_now側の全on-air出力guardが適用されています。

詳細:

- [`docs/twitch_closed_captions.md`](docs/twitch_closed_captions.md)
- [`native/ffmpeg/README.md`](native/ffmpeg/README.md)

## CLI

| Command | Purpose |
|---|---|
| `docich doctor` | 依存コマンド、ゲーム定義、custom FFmpeg字幕能力を点検 |
| `docich games` | ゲーム定義一覧 |
| `docich up` / `down` | 表示・音声・配信基盤を起動 / 全停止 |
| `docich start <game>` | ゲームと任意agentを起動 |
| `docich stop` | ゲームとagentだけを停止 |
| `docich switch <game>` | 配信を維持してゲームを交換 |
| `docich rotate [--dry-run]` | `[rotation]` games を順に切替 (cron/systemd timer 用) |
| `docich status` | component、game、stream、caption状態を表示 |
| `docich snap [-o path]` | スクリーンショット |
| `docich obs [game]` | brain向け観測JSON |
| `docich send <game> '<json>'` | 行動JSONを単発注入 |
| `docich ra-cmd <CMD>` | RetroArch UDP command |
| `docich webui [--dry-run]` | モデルチェーン/バックオフ管理 Web UI (Tailscale経由) |
| `docich caption plan/send ...` | 字幕計画とFFmpeg IPC |
| `docich trading status/discover/paper-cycle` | 暗号資産 paper trading 基盤（live注文なし） |
| `docich run <component>` | tmux内の監督ループ用内部command |

設定探索順は `--config`、`$DOCICH_CONFIG`、
`config/docich.toml` です。

### 暗号資産 paper trading（初期基盤）

`trading` は通常ゲームの lifecycle とは独立した paper-only の取引基盤です。
現段階では API key・private endpoint・実注文・レバレッジを扱わず、
実取引へ切り替えるオプションもありません。状態は既定で `run/trading/` に保存します。

bitbank の公開 market metadata を CCXT 経由で確認する場合だけ optional dependency を追加します。

```bash
python3 -m pip install -r requirements-trading.txt
bin/docich trading discover
bin/docich trading status
```

`paper-cycle` は allowlist された snapshot JSON だけを入力にし、複数ペアの候補を
共通の資金枠（初期既定: 1機会30%、全体30%）で比較して synthetic fill を台帳へ記録します。
同じ `opportunity_id` の再実行は二重約定になりません。JPY建て以外は
`quote_to_reference` と、そのquote assetの利用可能残高を明示できる場合だけ配分します。

```bash
bin/docich trading paper-cycle --snapshot /path/to/paper-snapshot.json
```

常駐worker、リアルタイム戦略、配信通知、実発注は後続sliceで追加します。
実発注を有効化する前には、別途の設計レビューと明示承認が必要です。

## 設定

グローバル設定は `config/docich.toml` にあります。

```toml
[display]
number = 98

[audio]
enabled = true
sink_name = "docich_sink"
set_default = false

[stream]
ffmpeg_bin = "ffmpeg"
mode = "null" # null | file | rtmp
stream_key_env = "DOCICH_STREAM_KEY"

[captions]
enabled = false
socket_path = ""

[watchdog]
enabled = false  # true で up が watchdog window (フリーズ検知+window復旧) も起動

[rotation]
games = []       # docich rotate が巡回する順序
```

字幕の環境変数上書きは `DOCICH_FFMPEG_BIN`、
`DOCICH_CC_ENABLED`、`DOCICH_CC_SOCKET` です。socketは104 byte未満の
安全な絶対Unix pathだけを受け付けます。

ゲームは `config/games/<name>.toml` に1本ずつ定義します。

- `hanjuku-hero`: RetroArch + SFC core。ROMは自己吸い出し品のみ。
  LLM brain (`brains/hanjuku/`) 同梱、既定無効 ([`docs/hanjuku_brain.md`](docs/hanjuku_brain.md))。
- `nethack`: tmux + xtermのCLI/TUI adapter。
- `robots`: `bsdgames`の軽量ターン制ゲーム。CLI/TUI adapterで元の端末サイズを保ち、配信側だけをcontain表示する例。
- `sorengame`: ローカルWebGL viewer。production controllerではない。

## Repository layout

```text
bin/docich                    CLI launcher
src/docich/                   config, adapters, agent, stream, captions, watchdog
brains/hanjuku/               半熟英雄 LLM brain (claude-cli / api / fake)
config/docich.toml            global safe defaults
config/games/*.toml           per-game definitions
games/roms/                   ROM 置き場 (gitignore。自己吸い出し品のみ)
games/soviet_now/             submodule → azumag/soviet_now (sorengame 本体, main 追跡)
games/hanjuku-sfc-speedrun/   submodule → azumag/hanjuku-sfc-speedrun (半熟英雄 RTA データ)
native/ffmpeg/                docichcc source, pinned build, PoC, stress proof
scripts/                      Ubuntu setup, smoke tests, systemd units, wiki publish
tests/                        stdlib unittest suite
wiki/                         GitHub wiki 原稿 (scripts/publish_wiki.sh で発行)
docs/architecture.md          canonical architecture
docs/multi_repo_plan.md       submodule 構成と共通部品化ロードマップ
docs/games/                   per-game contracts
docs/twitch_closed_captions.md caption architecture and production evidence
handoff.md                    current Soren/docich operational handoff
```

## Development and verification

```bash
python3 -m compileall -q src tests
python3 -m unittest discover -s tests
bash -n native/ffmpeg/*.sh
scripts/smoke_cli.sh
```

`scripts/smoke_cli.sh` は Xvfb、NetHack、FFmpeg、入力注入を使うため、
必要なLinux依存が揃った環境で実行してください。native captionのbuildと
transport proofは `native/ffmpeg/README.md` の手順を使います。

GitHub wiki のページ原稿は `wiki/` ディレクトリでバージョン管理する (入り口・運用ハンドブック。
詳細は複製せず `docs/` へリンクする)。GitHub 側への発行は、GitHub 認証のあるマシンで
`scripts/publish_wiki.sh` を実行する。

## Documentation

- [`docs/architecture.md`](docs/architecture.md): adapter・runtime・ownershipの一次情報
- [`docs/multi_repo_plan.md`](docs/multi_repo_plan.md): マルチリポジトリ構成 (submodule 化・共通部品化ロードマップ)
- [`docs/hanjuku_brain.md`](docs/hanjuku_brain.md): 半熟英雄 brain (LLM バックエンド・知識注入・検証)
- [`docs/games/hanjuku-hero.md`](docs/games/hanjuku-hero.md) / [`docs/games/nethack.md`](docs/games/nethack.md) / [`docs/games/robots.md`](docs/games/robots.md): ゲーム別セットアップ
- [`docs/games/sorengame.md`](docs/games/sorengame.md): Soren productionとの統合境界
- [`docs/oracle_arm_setup_guide.md`](docs/oracle_arm_setup_guide.md): Oracle A1 setup
- [`docs/soren_linux_migration_plan.md`](docs/soren_linux_migration_plan.md): Soren移行の完了状況と将来gate
- [`docs/twitch_closed_captions.md`](docs/twitch_closed_captions.md): native captions
