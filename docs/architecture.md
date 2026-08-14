# docich アーキテクチャ設計書 — マルチゲーム AI 配信基盤

docich は「VM 上で AI がゲームをプレイし、その画面と音声を ffmpeg で配信する」ための基盤である。
本書は、**ゲームを差し替え可能にする**ための中核設計を定義する。対象ゲームの第一陣:

| ゲーム | 種別 | アダプタ |
|---|---|---|
| soren game (Unity WebGL) | ブラウザゲーム | `browser` |
| 半熟英雄 (SFC) | エミュレータ (RetroArch + snes9x コア) | `retroarch` |
| NetHack | CLI (端末) | `cli` |

想定実行環境は Oracle Cloud Ampere A1 (Ubuntu 24.04 LTS arm64, ヘッドレス)。手順は `oracle_arm_setup_guide.md` を参照。

## 表記ルール

- 【確認済】: 本設計時に一次情報 (パッケージリポジトリ・実機コンテナでの動作実証) で裏取りした事実
- 【要検証】: Oracle ARM 実機での検証が必要な事項

---

## 0. 共存原則 — 稼働中の soren を壊さない

**VM では既に soren (sorengame) が本番稼働している。** docich は soren のリソースに一切触れず、名前空間を分離して同居する:

| リソース | soren (稼働中) | docich | 分離方法 |
|---|---|---|---|
| X ディスプレイ | `:99` | **`:98` (既定)** | 別番号。docich は `:99` を既定にしない |
| PulseAudio | デーモン + 既定 sink (`soren_null`) | 同一デーモンに `docich_sink` を追加 | **`set-default-sink` は絶対に実行しない**。docich 配下のプロセスは `PULSE_SINK=docich_sink` 環境変数で個別ルーティング |
| PulseAudio デーモン | 稼働中 | **既存デーモンを再利用** (`pactl info` が通れば起動しない) | 二重起動しない |
| tmux | soren のセッション | セッション名 `docich` / `docich-game` | 名前分離。他セッションに触れない |
| 配信 | soren が配信中 | `stream.mode = "null"` が既定 | 明示設定なしでは配信しない (キー競合事故の防止) |
| セットアップ | — | `setup_ubuntu_arm.sh` は **apt install と mkdir のみ** | 既存設定・サービスを変更しない |

## 1. 基本方針

1. **ディスプレイは常に生かし、ゲームだけを入れ替える。**
   Xvfb 仮想ディスプレイ (`:98`, 1280x720) と PulseAudio null sink を常駐させ、ffmpeg はその全画面+音声を配信し続ける。ゲーム切替は「ディスプレイ上のアプリの入れ替え」なので、**配信ストリームは切替中も途切れない**。
2. **OBS は使わず ffmpeg 直結。**
   soren (macOS) は OBS 構成だったが、docich は `x11grab + pulse → libx264 → RTMP` の単一 ffmpeg プロセスに簡素化する。オーバーレイが必要になったら drawtext (`textfile= + reload=1`) から始め、OBS は将来の選択肢として残す。
3. **ゲームは「アダプタ」で抽象化する。**
   アダプタは lifecycle (start/stop/alive) と AI I/O (observe/act) の 2 面の契約を実装する。配信・切替・エージェントループはアダプタの中身を知らない。
4. **AI の頭脳 (brain) は外部コマンドとして差し替え可能にする。**
   docich は「観測 JSON を stdin に渡し、行動 JSON を stdout から受け取る」境界だけを定義する。`claude` CLI・自作スクリプト・ランダムテスト器を同じ口に差せる。soren game のように**ゲーム側に既存の自動化システムがある場合は agent を無効化**し、外部システムに運転を任せる。
5. **依存は最小に。**
   オーケストレータは Python 3.11+ **標準ライブラリのみ** (tomllib/argparse/subprocess/json)。外部はコマンド (tmux/ffmpeg/Xvfb/pulseaudio/xdotool/retroarch/xterm) のみで、pip 依存ゼロ。Ubuntu 24.04 の python3 (3.12) でそのまま動く。
6. **ROM・ストリームキーはリポジトリに置かない。**
   ROM は自己吸い出し品を `games/roms/` (gitignore 済) に配置。ストリームキーは環境変数 (`DOCICH_STREAM_KEY`) のみ。

---

## 2. 全体構成

```
                        tmux セッション "docich"
  ┌─────────────────────────────────────────────────────────────┐
  │ window: display   Xvfb :98 -screen 0 1280x720x24            │
  │ window: audio     null sink 確保 (既存 pulse 再利用/新規起動) │
  │ window: stream    ffmpeg x11grab(:98) + pulse(monitor)      │
  │                     → RTMP / ファイル / null                 │
  │ window: game      アダプタが起動するゲームプロセス            │
  │                     retroarch / chromium / xterm+tmux        │
  │ window: agent     観測→brain→行動 ループ (ゲームごとに任意)   │
  └─────────────────────────────────────────────────────────────┘
         ▲ 各 window は `docich run <component>` (再起動ループ付き)

  docich CLI ──> tmux window の生成/破棄 + run/ 以下の状態管理
  brain (外部コマンド) <──stdin/stdout JSON──> agent ループ
```

- すべての常駐プロセスを tmux window に統一する。デバッグは `tmux attach -t docich` で全コンポーネントの生ログを見られる (soren の `start_all.sh` と同じ運用感)。
- 各 window は直接コマンドを実行するのではなく `docich run <component>` を実行する。これは Python 内の**監督ループ** (クラッシュ時に指数バックオフで再起動、`run/logs/<component>.log` へログ) である。
- systemd 化は Phase 3 (本書 §10)。まず tmux 常駐で成立させる。

### 2.1 リポジトリレイアウト

```
docich/
├── bin/docich                  # 起動ランチャ (PYTHONPATH=src で python3 -m docich)
├── src/docich/                 # Python パッケージ (標準ライブラリのみ)
│   ├── __main__.py / cli.py    # argparse CLI
│   ├── config.py               # TOML 読み込み + 検証
│   ├── state.py                # run/ 状態 (current_game 等)
│   ├── procs.py                # サブプロセス実行ヘルパ
│   ├── tmux.py                 # tmux 操作ラッパ
│   ├── xkit.py                 # xdotool / スクリーンショット (X11 入出力)
│   ├── stream.py               # ffmpeg コマンド構築
│   ├── supervise.py            # 再起動ループ (docich run)
│   ├── actions.py              # 行動 JSON のスキーマとパース
│   ├── adapters/               # base + retroarch / cli_game / browser
│   └── agent/                  # loop + brains (command / random)
├── config/
│   ├── docich.toml             # グローバル設定 (display/audio/stream)
│   └── games/*.toml            # ゲーム定義 (1 ゲーム 1 ファイル)
├── games/roms/                 # ROM 置き場 (gitignore。README のみコミット)
├── scripts/
│   ├── setup_ubuntu_arm.sh     # VM 初期構築 (apt インストール等)
│   └── smoke_cli.sh            # E2E スモーク (Xvfb+nethack+ffmpeg+xdotool)
├── tests/                      # unittest (pip 不要, python3 -m unittest)
└── docs/                       # 本書・ゲーム別セットアップ手順
```

### 2.2 状態ディレクトリ `run/` (gitignore)

```
run/
├── current_game            # 現在のゲーム名 (テキスト 1 行)
├── logs/<component>.log    # supervise が書くログ (ローテーションは Phase 3)
├── screenshots/            # observe 用スクリーンショット (最新のみ保持)
└── retroarch/              # 生成された retroarch.cfg / セーブ / ステート
```

---

## 3. アダプタ契約 (中核)

```python
class Adapter:                        # src/docich/adapters/base.py
    name: str                         # "retroarch" など

    # --- lifecycle (game window 内で supervise が使う) ---
    def command(self) -> list[str]:   # ゲーム本体の起動コマンド
    def prepare(self) -> None:        # 起動前フック (cfg 生成・ROM 存在確認など)
    def cleanup(self) -> None:        # 停止後フック

    # --- AI I/O (agent / CLI から使う) ---
    def observe(self) -> Observation: # 現在の画面/状態を取得
    def act(self, action: Action) -> None:  # 行動を注入
```

- ゲームプロセス自体は supervise が `command()` を実行して保持する。アダプタオブジェクトは**ステートレス**に近く、observe/act は都度 X11/tmux に問い合わせる。プロセスの生死は tmux pane の生死で判定する (pid ファイル管理をしない)。
- 切替 (`docich switch`) は「game/agent window を kill → 新しいゲームで作り直す」だけで実現される。

### 3.1 観測 (Observation) JSON

brain の stdin に渡る形式。アダプタの得意な形で `kind` が変わる:

```json
{
  "game": "nethack",
  "title": "NetHack",
  "adapter": "cli",
  "ts": 1755150000.0,
  "kind": "text",                       // "text" | "screenshot" | "both"
  "text": "80x24 の端末画面ダンプ...",    // cli アダプタ
  "screenshot": "run/screenshots/latest.png",  // retroarch / browser アダプタ
  "meta": { "interval_ms": 2000, "note": "設定の system_prompt などを将来ここに" }
}
```

- CLI ゲームは**テキストで観測**できるのが最大の利点 (LLM に画像より正確に渡る)。`tmux capture-pane` をそのまま入れる。
- 画面系は PNG のパスを渡す。brain 側 (例: `claude` CLI) がファイルを読んで解釈する。

### 3.2 行動 (Action) JSON

brain の stdout から受け取る形式。`{"actions": [...]}` の配列 (単発オブジェクトも許容):

| type | フィールド | 意味 | 対応アダプタ |
|---|---|---|---|
| `pad` | `buttons: ["a","start"]`, `hold_ms` | SFC パッドの**意味ボタン**を同時押し | retroarch |
| `key` | `keys: ["Up","x"]`, `hold_ms` | X11 キー名の生入力 (同時押し) | retroarch / browser |
| `text` | `text: "hjkl"` | 文字列をそのまま送る | cli |
| `special` | `key: "Escape"` | Enter/Escape 等の特殊キー | cli |
| `mouse` | `x, y, button` | クリック | browser |
| `wait` | `ms` | 待つ (アクション列の間合い) | 全部 |

**`pad` の意味ボタン → 物理キーの解決はアダプタの責務**。brain は「SFC の A ボタン」とだけ言えばよく、RetroArch のキーバインドを知らない。これがゲーム差し替えの要。

### 3.3 ボタンマッピング (retroarch)

内蔵の既定マップ (RetroArch のデフォルトキーバインドに一致):

| 意味ボタン | RetroArch cfg 値 | xdotool キー名 |
|---|---|---|
| a / b / x / y | x / z / s / a | x / z / s / a |
| l / r | q / w | q / w |
| start / select | enter / rshift | Return / Shift_R |
| up/down/left/right | up/down/left/right | Up/Down/Left/Right |

docich は **retroarch.cfg を毎起動時に自分で生成**し (`run/retroarch/retroarch.cfg`)、`retroarch --config` で渡す。これによりキーバインドが xdotool 側のマップと**構造的に一致し続ける** (ユーザーの `~/.config/retroarch` に依存しない・汚さない)。

---

## 4. 各アダプタの設計

### 4.1 `retroarch` (SFC 半熟英雄)

- 起動: `dbus-run-session -- retroarch --config run/retroarch/retroarch.cfg -L <core.so> <rom>`
  - 【確認済】`dbus-run-session` ラップは**必須**。Ubuntu の RetroArch 1.18 は GameMode 統合がセッション D-Bus に接続できないと **libdbus のアサートで abort する** (ヘッドレス環境で実証。`gamemode_enable=false` でも回避不可)。プライベートセッションバスで包むと正常起動する (実証済)。提供パッケージ: `dbus-daemon` (`dbus` メタパッケージで導入)
- コア解決: `core = "auto"` なら `/usr/lib/*/libretro/` から `snes9x → bsnes_mercury_performance → bsnes_mercury_balanced` の順で探索。【確認済】Ubuntu 24.04 universe に `retroarch 1.18.0` / `libretro-snes9x 1.61` / `libretro-bsnes-mercury-*` の **arm64 版が存在** (packages.ubuntu.com で確認)。snes9x は軽量で A1 (2 OCPU) に適する。実インストール先は `/usr/lib/<arch>-linux-gnu/libretro/` (コンテナで確認)。
- 生成する cfg の要点 (全文は実装参照):

  | 設定 | 値 | 理由 |
  |---|---|---|
  | `input_driver` | `"sdl2"` | 【確認済】既定の `udev` は evdev 直読みで Xvfb 上の XTEST が届かない。`"x"` も **sdl2 ビデオドライバがウィンドウを渡さないため不発** (ログ実証)。`"sdl2"` で XTEST キーが届くことをメニュー操作で実証済 |
  | `video_driver` | `"sdl2"` | 【確認済】Xvfb (GLX なし) で RGUI メニューの描画を実証。SNES コアはソフトレンダなので sdl2 で十分 |
  | `gamemode_enable` | `false` | GameMode を使わない意思表示 (ただし abort 回避には dbus-run-session が必要。上記) |
  | `audio_driver` | `"pulse"` | `PULSE_SINK=docich_sink` 環境変数で docich の sink に直接ルーティング (既定 sink は変更しない。§0) |
  | `video_fullscreen` | `true` | 1280x720 にアスペクト維持でスケール (4:3 なので左右黒帯) |
  | `pause_nonactive` | `false` | フォーカス管理に敏感にならないため |
  | `config_save_on_exit` | `false` | 生成 cfg を汚さない (決定論性) |
  | `network_cmd_enable` | `true` (port 55355) | UDP で `SAVE_STATE`/`LOAD_STATE`/`PAUSE_TOGGLE` 等を送れる。`docich ra-cmd` として公開 (配信中のステート保存に有用)。【確認済】`VERSION`/`GET_STATUS` の送受信をコンテナで実証 |
  | `input_exit_emulator` | 無効化 | 誤爆でエミュレータが落ちるのを防ぐ。停止は SIGTERM |
- observe: 画面全体のスクリーンショット (kind=screenshot)。
- act: `pad`/`key`/`wait`。実行前に `xdotool search → windowfocus --sync` で RetroArch ウィンドウへフォーカスを保証してから XTEST (`xdotool keydown/keyup`) で押下・解放 (§9-2: WM 無し Xvfb では windowactivate は不可)。同時押し (例: ↓+A) は keydown を並べて hold 後にまとめて keyup。
- 【確認済・最重要】**押下は hold 時間を挟むこと**。RetroArch はフレーム毎にキー状態をポーリングするため、`xdotool key` の瞬間 press/release は**取りこぼされる** (実証: 瞬間押しではメニューが動かず、keydown→150ms→keyup で動いた)。`tap(keys, hold_ms)` の既定 100ms を下回らない。
- ROM は `games/roms/` の自己吸い出しファイルを config で指定。**docich は ROM の取得・配布に一切関与しない**。

### 4.2 `cli` (NetHack)

- ゲーム本体は**専用 tmux セッション** `docich-game` 内で固定サイズ (80x24, `window-size manual`) で実行。セッションの **status バーは off** にする (配信画面に tmux の緑バーが映り込むため。コンテナで実証済)。
- 映像化: game window では xterm を起動し、その中で `tmux attach -r` (読み取り専用) して端末画面を Xvfb 上に表示する。フォントサイズ (既定 18pt, config 可変) で 1280x720 への埋まり方を調整。【確認済】monospace 18pt で 80 桁がちょうど 1280px 幅になり、NetHack 画面が綺麗に収まることをコンテナで実証。
- 【確認済】read-only attach のクライアントに打鍵してもtmux が破棄する (誤入力防止として好都合)。入力は必ず `send-keys` 経由。
- observe: `tmux capture-pane -p` → kind=text。**画面がそのままテキストで取れる**ため brain の精度が最も出やすい。
- act: `text` は `tmux send-keys -l` (リテラル)、`special` はキー名 (`Escape`, `Enter`, `C-c` 等) を send-keys。
- ゲームの生死 = `docich-game` セッションの生死。xterm が死んでも (映像が消えるだけで) ゲームは死なない分離構造。supervise は xterm を再起動して再アタッチする。
- NetHack は例であり、`command = "nethack"` を差し替えれば任意の CLI/TUI ゲームが動く。

### 4.3 `browser` (soren game)

- 起動: chromium (自動検出: `chromium` / `chromium-browser` / `google-chrome` / playwright の chrome) を `--kiosk --window-size=WxH --app=<url>` 相当で DISPLAY 上に起動。
- observe: スクリーンショット。act: `key` / `mouse` (xdotool)。
- **soren game の本運転は soren リポジトリの既存システム (Playwright/CDP + 戦略 AI) が担う**。docich 側は `launch_command` の差し替え (例: soren の起動スクリプトを呼ぶ) と `agent.enabled = false` で「場所と映像の提供」に徹する。docich の browser アダプタ単体は「URL を開いて映す + 汎用入力」の最小機能とする。soren 側の Linux 移植は `soren_linux_migration_plan.md` の管轄。

---

## 5. 配信 (stream)

ffmpeg 1 プロセス。コマンドは `stream.py` が設定から構築する:

```
ffmpeg -f x11grab -draw_mouse 0 -framerate 30 -video_size 1280x720 -i :98 \
       -f pulse -i docich_sink.monitor \            # audio.enabled=false なら anullsrc
       -c:v libx264 -preset veryfast -tune zerolatency \
       -b:v 4500k -maxrate 5000k -bufsize 9000k \
       -pix_fmt yuv420p -g 60 \
       -c:a aac -b:a 160k -ar 44100 \
       -f flv rtmp://live.twitch.tv/app/<DOCICH_STREAM_KEY>
```

- `-draw_mouse 0` は必須 (【確認済】省略するとマウスポインタが画面中央に映り込む)。observe 用スクリーンショットも同様。

- `stream.mode`: `null` (既定。配信しない=事故防止) / `rtmp` / `file` (flv をローカル保存。動作確認用)。
- ストリームキーは **環境変数からのみ** 読む (`stream_key_env = "DOCICH_STREAM_KEY"`)。ログ・ps 出力に混じらないよう、コマンドラインを表示するときはキーをマスクする。
- 音声無効時は `anullsrc` で無音トラックを合成する (配信先は音声トラック必須のため)。
- x264 preset は A1 (2 OCPU) では `veryfast`〜`ultrafast` を実測で選ぶ【要検証】。720p30 4.5Mbps を基準とする。
- observe 用スクリーンショットは配信とは独立に `ffmpeg -f x11grab ... -frames:v 1` の単発実行 (同一ディスプレイへの並行 x11grab は問題ない)。

---

## 6. エージェントループ (brain 境界)

```
loop:
  obs  = adapter.observe()                 # Observation JSON
  acts = brain(obs)                        # 外部コマンド: stdin=obs, stdout=actions
  for a in acts: adapter.act(a)
  sleep(残り interval)
```

- brain の種類 (game config の `[agent]`):
  - `command`: 設定した任意コマンドを**毎回起動** (ステートレス)。会話履歴・長期記憶は brain スクリプト側の責務とする (docich は関与しない)。タイムアウト (既定 120s) 超過は kill してスキップ。
  - `random`: 行動空間からランダムに 1 手 (配線テスト・デモ用)。
- 半熟英雄の本物の brain (画面認識・戦略プロンプト) は **Phase 2** で `command` brain として実装する。claude CLI をここに差す場合の認証問題は `soren_linux_migration_plan.md` §6 と共通。
- `docich obs <game>` / `docich send <game> '<json>'` で brain 開発を CLI から単発試行できる (ループ外デバッグ)。

---

## 7. CLI

```
docich doctor                 # 依存コマンド・環境の点検 (アダプタ別に OK/NG 表示)
docich games                  # config/games/ の一覧と有効アダプタ
docich up / down              # 基盤 (display/audio/stream) の起動・全停止
docich start <game>           # ゲーム起動 (+ agent.enabled なら agent も)
docich stop                   # 現在のゲーム停止 (基盤は残る)
docich switch <game>          # stop + start (配信は継続)
docich status                 # 各コンポーネントの生死・現在のゲーム
docich snap [-o out.png]      # 手動スクリーンショット
docich obs [<game>]           # 観測 JSON を出力 (brain 開発用)
docich send <game> '<json>'   # 行動を単発注入 (デバッグ用)
docich ra-cmd <CMD>           # RetroArch へ UDP コマンド (SAVE_STATE 等)
docich run <component> [...]  # (内部用) tmux window 内の supervise 実行
```

設定ファイル探索: `--config` > `$DOCICH_CONFIG` > リポジトリの `config/docich.toml`。

---

## 8. 設定スキーマ

### 8.1 `config/docich.toml`

```toml
[display]
number = 98          # DISPLAY=:98 (soren が :99 を使用中のため衝突回避。§0)
width = 1280
height = 720
color_depth = 24

[audio]
enabled = true       # pulseaudio が無い環境では false (無音配信)
sink_name = "docich_sink"
set_default = false  # true にしない限り set-default-sink はしない (soren 共存。§0)

[stream]
mode = "null"                    # null | rtmp | file
rtmp_url = "rtmp://live.twitch.tv/app"
stream_key_env = "DOCICH_STREAM_KEY"
file_path = "run/out.flv"
framerate = 30
video_bitrate = "4500k"
maxrate = "5000k"
bufsize = "9000k"
preset = "veryfast"
audio_bitrate = "160k"
gop_seconds = 2

[agent]
default_interval_ms = 2000
brain_timeout_s = 120

[paths]
state_dir = "run"                # リポジトリ相対
games_dir = "config/games"
roms_dir = "games/roms"
```

### 8.2 ゲーム定義 (例)

```toml
# config/games/hanjuku-hero.toml
[game]
name = "hanjuku-hero"
title = "半熟英雄 (SFC)"
adapter = "retroarch"

[retroarch]
rom = "games/roms/hanjuku-hero.sfc"   # 自己吸い出し ROM を配置
core = "auto"                          # or コア .so の絶対パス
# [retroarch.pad_map] で既定マップの上書きも可能

[agent]
enabled = false                        # 半熟英雄 brain は Phase 2
brain = "command"
command = ""
interval_ms = 2000
```

```toml
# config/games/nethack.toml
[game]
name = "nethack"
title = "NetHack"
adapter = "cli"

[cli]
command = "nethack"
cols = 80
rows = 24
font_size = 18

[agent]
enabled = false        # random brain の配線テストは CLI から明示実行
brain = "random"
interval_ms = 1500
```

```toml
# config/games/sorengame.toml
[game]
name = "sorengame"
title = "soren game (Unity WebGL)"
adapter = "browser"

[browser]
url = "https://example.invalid/sorengame"   # TODO: 実 URL に差し替え
kiosk = true
binary = "auto"
# launch_command = ["bash","-lc","cd ~/soren && ..."] # soren 本体で運転する場合

[agent]
enabled = false        # soren 側の自動化が運転するため docich agent は使わない
```

---

## 9. リスクと落とし穴 (実装が守るべき知見)

1. **RetroArch の入力ドライバ**: 既定 `udev` は evdev 直読みで Xvfb + xdotool では入力が届かない。`"x"` ドライバも **sdl2 ビデオドライバと組むと「Graphics driver did not initialize an input driver」となり不発** (ログ実証)。【確認済】正解は **`input_driver = "sdl2"`** (video も sdl2)。XTEST キーでの RGUI メニュー操作をコンテナで実証済。フォールバックは RetroArch Network Remote (UDP パッド)。
1b. **GameMode の D-Bus abort**: Ubuntu ビルドの RetroArch 1.18 は、セッション D-Bus が無い環境で起動すると GameMode 統合の dbus 呼び出しが **assert → abort する** (【確認済】`gamemode_enable=false` でも、`DBUS_SESSION_BUS_ADDRESS` を無効値にしても回避不可)。**`dbus-run-session --` で包んで起動する** (実証済。パッケージ `dbus-daemon`)。
2. **XTEST とフォーカス**: `xdotool key --window` (XSendEvent) は SDL/RetroArch に無視されがち。**必ずフォーカスを取ってから XTEST** (`keydown`/`keyup`、--window なし) を使う。【確認済】WM の無い Xvfb では `windowactivate` (EWMH 依存) は機能しないため **`windowfocus --sync` (XSetInputFocus 直叩き) を使う**。この経路でキー入力が X アプリに届くことをコンテナで実証済。
3. **Xvfb と GL**: `video_driver = "gl"` は Xvfb で GLX が無く失敗し得る。`sdl2` を既定とし、必要になったら mesa (llvmpipe) を検証する。【要検証】
4. **ffmpeg の CPU 負荷**: A1 2 OCPU で libx264 720p30 が回るかは preset 次第。`veryfast` で始め、溢れたら `superfast/ultrafast`・framerate 24・ビットレート減で逃がす。【要検証】
5. **PulseAudio のヘッドレス起動**: `XDG_RUNTIME_DIR` が無いと起動しない。`loginctl enable-linger` + ユーザーセッションを前提にする (ガイド §4 と共通)。pipewire-pulse でも `pactl` 互換なので同じ設定が効く。【要検証】**既にデーモンが動いている場合 (= soren 稼働中の VM) は絶対に二重起動せず、`pactl info` で検出して module-null-sink の追加のみ行う** (§0)。
6. **tmux ネスト**: cli アダプタは「docich の tmux」の window 内で xterm → その中で `tmux attach` する。`TMUX` 環境変数が伝播するとネスト拒否されるため、xterm 起動時に `TMUX` を必ず unset する。
7. **ストリームキー漏えい**: ffmpeg の引数はプロセスリストに露出する。VM はシングルユーザー前提で許容するが、docich 自身のログ/status 表示では必ずマスクする。
8. **著作権と配信ポリシー**: ROM は自己吸い出し品のみ・リポジトリ非コミット。配信プラットフォーム側のゲーム配信ガイドラインへの適合は運用者の責任範囲。

---

## 10. フェーズ計画

### Phase 1: 基盤 (本ブランチの成果物)
- 本書 + Python パッケージ一式 + アダプタ 3 種 + agent ハーネス + 設定 + セットアップ/スモークスクリプト + 単体テスト。
- 完了条件: (a) `python3 -m unittest` 緑、(b) コンテナ/実機で `smoke_cli.sh` が Xvfb+NetHack+スクリーンショット+入力注入の E2E を通す。

### Phase 2: 実機立ち上げ + 半熟英雄
- Oracle ARM で `setup_ubuntu_arm.sh` → `doctor` → RetroArch 実機検証 (§9 の 1-5)。
- RTMP 実配信 (24h 連続・CPU 実測で preset 決定)。
- 半熟英雄 brain: スクリーンショット→claude CLI→pad 操作のプロンプト設計。ステート保存 (`ra-cmd SAVE_STATE`) を絡めた復帰運用。
- soren game: soren リポジトリの Linux 移植 (`soren_linux_migration_plan.md`) と接続し、`launch_command` で統合。

### Phase 3: 運用
- systemd --user ユニット化 (tmux セッションを 1 ユニットで包む)、ログローテーション、ヘルスウォッチドッグ (フリーズ検出=スクリーンショット差分)、ゲームの時間割スケジューラ (`docich switch` を cron/Routine から叩く)、配信オーバーレイ (drawtext / OBS 再評価)、チャット連携 (soren の chat 資産の移植)。

---

## 付録: 設計上の決定記録 (ADR 要約)

| 決定 | 採用 | 不採用 | 理由 |
|---|---|---|---|
| 配信経路 | ffmpeg 直結 | OBS | ヘッドレス簡素化。ウィンドウ単位キャプチャが不要 (全画面=ゲームのみ) なら ffmpeg で足りる |
| オーケストレータ言語 | Python stdlib | bash / Node | JSON/TOML/プロセス管理/テスト容易性。pip ゼロで VM にそのまま乗る |
| SFC エミュレータ | RetroArch + libretro-snes9x (apt) | 単体 snes9x-gtk, stable-retro | arm64 の apt 供給を確認済。cfg 生成で入力を決定論化できる。stable-retro はビルド重・配信映像に別経路が必要 |
| SFC 入力注入 | xdotool XTEST + input_driver=x | RetroArch Network Remote | 汎用 (browser とも共通化)。Network Remote はプロトコルが薄文書でフォールバック扱い |
| CLI ゲーム映像化 | tmux + xterm 表示 | ttyrec→動画, pty 直描画 | 観測はテキスト (LLM 最適)、映像は「見えている端末」で兼ねる。分離構造で頑健 |
| ゲーム切替 | ディスプレイ常駐・アプリ入替 | ゲームごとに配信再起動 | 無停止切替。ffmpeg/エンコード状態を保てる |
