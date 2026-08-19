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

## 0. 共存原則 — 稼働中の Soren を壊さない

**VM では既に soviet_now の Soren (sorengame) が本番稼働している。**
本番は system `soren-runtime.service` が所有し、FFmpeg直接配信とネイティブ
Twitch字幕を運用している。docich の merge/up/start は所有権移管ではない。
docich は名前空間を分離して同居する:

| リソース | soren (稼働中) | docich | 分離方法 |
|---|---|---|---|
| X ディスプレイ | `:99` | **`:98` (既定)** | 別番号。docich は `:99` を既定にしない |
| PulseAudio | デーモン + 既定 sink (`soren_null`) | 同一デーモンに `docich_sink` を追加 | 共存運転では `set_default=false` を維持する。docich 配下のプロセスは `PULSE_SINK=docich_sink` 環境変数で個別ルーティング |
| PulseAudio デーモン | 稼働中 | **既存デーモンを再利用** (`pactl info` が通れば起動しない) | 二重起動しない |
| tmux | soren のセッション | セッション名 `docich` / `docich-game` | 名前分離。他セッションに触れない |
| 配信 | custom FFmpegで本番配信中 | `stream.mode = "null"` が既定 | 明示設定なしでは配信しない (キー競合事故の防止) |
| 字幕 | `/run/user/1001/docich/ffmpeg-cc.sock` を本番FFmpegが所有 | `$XDG_RUNTIME_DIR/docich/ffmpeg-cc.sock`、既定無効 | display/audio/streamと同様にproduction所有者を重複させない |
| セットアップ | — | `setup_ubuntu_arm.sh` は **apt install と mkdir のみ** | 既存設定・サービスを変更しない |

## 1. 基本方針

1. **ディスプレイは常に生かし、ゲームだけを入れ替える。**
   Xvfb 仮想ディスプレイ (`:98`, 1280x720) と PulseAudio null sink を常駐させ、ffmpeg はその全画面+音声を配信し続ける。ゲーム切替は「ディスプレイ上のアプリの入れ替え」なので、**配信ストリームは切替中も途切れない**。
2. **OBS は使わず ffmpeg 直結。**
   docich は `x11grab + pulse → libx264 → RTMP` の単一 ffmpeg プロセスを使う。Soren本番もLinux移行後はFFmpeg directを使用し、OBSはrollbackとして保持する。ネイティブ字幕を要求した場合だけ `docichcc + libx264 a53cc` を追加する。
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
  │                     + optional docichcc Unix socket         │
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
│   ├── captions.py             # bilingual plan + Unix socket client
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
├── native/ffmpeg/              # docichcc source, pinned build, proofs
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
- **現在の本運転は soviet_now の既存システム (Playwright/CDP + 戦略AI + worker + 配信) が担う**。docichの `sorengame` 定義は `http://127.0.0.1:8080` を `:98` に開くviewerで、`agent.enabled = false` とする。
- `start_all.sh` を `launch_command` に設定してはならない。これはgameだけでなくdisplay/audio/stream/supervisorを所有するため、docichと二重起動になる。
- 将来の統合には、呼出側の `DISPLAY` / `PULSE_*` / state directoryを尊重し、game/browser/bridgeだけを起動・SIGTERM停止するSorenの **game-only entry point** が必要。契約は `docs/games/sorengame.md` を一次情報とする。

---

## 5. 配信 (stream)

ffmpeg 1 プロセス。コマンドは `stream.py` が設定から構築する。通常構成:

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

### 5.1 Native Twitch closed captions

字幕を明示的に要求し、custom FFmpegが `docichcc` filterと
`libx264 a53cc` optionを持つ場合、映像経路へ次を追加する:

```text
-vf docichcc=socket=/run/user/<uid>/docich/ffmpeg-cc.sock
-c:v libx264 -a53cc 1
```

`captions.py` は日本語音声chunkと英訳を揃えたprivate planを作り、
`prepare → commit → clear` をacknowledgement付きUnix IPCで送る。
`executionId`により古い音声のlate clearが新しい字幕を消すことを防ぐ。

字幕は補助経路であり、custom binary不在、能力不足、翻訳失敗、socket失敗の
どれでも映像・音声は継続する。`resolve_runtime()`はcaptionless commandへ
fail-openし、`doctor` / `status` は requested/active/detailを分けて表示する。
stream起動時はsocket親directoryを`0700`で作成し、symlink・他user所有・
group/other accessを拒否する。この準備に失敗した場合も字幕だけを外す。

翻訳は完全一致JSON schemaだけを受理する。reasoning、tool trace、Web検索の
進行、Markdown、説明文、余分なkeyを部分抽出しない。CEA-608向けASCII正規化、
32 columns × 2 linesのhard bound、4 KiB IPC上限を適用する。詳しくは
`docs/twitch_closed_captions.md` と `native/ffmpeg/README.md` を参照。

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
- 半熟英雄の本物の brain (画面認識・戦略プロンプト) は `brains/hanjuku/brain.py` に**実装済み** (設計: `docs/hanjuku_brain.md`。LLM バックエンド3系統: claude-cli 既定 / anthropic SDK / fake)。【確認済】claude-cli 経路は実 LLM (claude-opus-5) で RetroArch メニューを認識し fail-soft 判断まで一連動作。制約: スクリーンショットがリポジトリ内 (`run/`、既定) にないと claude CLI の Read が権限拒否になる。claude CLI の認証問題は `soren_linux_migration_plan.md` §6 と共通。
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
docich rotate [--dry-run]     # [rotation] games を順に切替 (cron/systemd timer から叩く)
docich status                 # 各コンポーネントの生死・現在のゲーム
docich snap [-o out.png]      # 手動スクリーンショット
docich obs [<game>]           # 観測 JSON を出力 (brain 開発用)
docich send <game> '<json>'   # 行動を単発注入 (デバッグ用)
docich ra-cmd <CMD>           # RetroArch へ UDP コマンド (SAVE_STATE 等)
docich caption plan ...       # 日本語chunkと英訳からprivate字幕計画を作る
docich caption send ...       # prepare/commit/clear/resetをFFmpegへ送る
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
ffmpeg_bin = "ffmpeg"              # custom buildはDOCICH_FFMPEG_BINでも指定
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

[captions]
enabled = false                    # DOCICH_CC_ENABLED=1でも上書き可能
socket_path = ""                  # 空なら$XDG_RUNTIME_DIR/docich/ffmpeg-cc.sock

[agent]
default_interval_ms = 2000
brain_timeout_s = 120

[watchdog]
enabled = false       # true で `docich up` が watchdog window も起動 (Phase 3)
interval_s = 60       # 点検周期 (5以上)
freeze_cycles = 5     # 連続同一スクリーンショットでフリーズ判定 (2以上)
recover_windows = true # display/audio/stream window 消失時に冪等な up で再生成

[rotation]
games = []            # `docich rotate` が巡回する順序 (例: ["nethack", "hanjuku-hero"])

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
enabled = false                        # 有効化はユーザー判断 (認証・ROM・コスト確認後)
# brain の詳細設計・知識注入 (games/hanjuku-sfc-speedrun submodule) は docs/hanjuku_brain.md 参照
brain = "command"
command = ["python3", "brains/hanjuku/brain.py"]
interval_ms = 7000                     # LLM レイテンシ (3〜6秒) を織り込んだ周期
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
url = "http://127.0.0.1:8080"     # 同一VMのlocal WebGL viewer
kiosk = true
binary = "auto"

[agent]
enabled = false        # viewer専用。productionはsoviet_nowが運転
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
9. **字幕をstream readinessと混同しない**: caption能力が無くてもA/Vは起動する。逆に`captions.requested=yes`だけでTwitch表示済みと判断せず、`captions.active`、socket、SEI、decoder、実playerを段階的に確認する。
10. **生成出力を字幕へ直結しない**: model応答からJSONらしいsubstringを抽出しない。完全schema不一致はcaption failureとして破棄し、audioを継続する。

---

## 10. フェーズ計画

### Phase 1: Generic repository foundation (implemented)
- Python package、adapter 3種、agent boundary、safe defaults、setup/smoke、unit tests。
- Native caption planner/filter/build/proofsとgeneric fail-open stream integration。
- stdlib unit suiteは全件通過 (Phase 2/3 実装後の現在 371 件)。`smoke_cli.sh`を使うXvfb/NetHack/入力注入の
  Linux実機確認は、対象環境ごとのrelease gateとして残る。

### Phase 2: Game bring-up
- **半熟英雄 brain: 実装済み** (`brains/hanjuku/` + `docs/hanjuku_brain.md`)。コンテナで fake/claude-cli 両経路の E2E 済み (`scripts/smoke_brain.sh` + 実 LLM 1サイクル)。残り: VM で ROM 実プレイ (`[agent] enabled = true` 化) — **ユーザー判断で一旦ペンディング中** (2026-08-16。引き継ぎ: `docs/handoff_common_parts.md`)。
- Oracle ARM で `setup_ubuntu_arm.sh` → `doctor` → RetroArch 実機検証 (§9 の 1-5)。
- RTMP 実配信 (24h 連続・CPU 実測で preset 決定)。ステート保存 (`ra-cmd SAVE_STATE`) を絡めた復帰運用。
- sorengame: viewer rehearsalは可能。本番所有権移管はgame-only entry pointと別cutoverが揃うまで行わない。

### Phase 3: 運用
- **実装済み**: ヘルスウォッチドッグ (`src/docich/watchdog.py`。フリーズ検出=スクリーンショット sha256 の連続一致、agent 稼働中のみ判定。window 消失は冪等な `up` で復旧)、ゲームの時間割ローテーション (`docich rotate` + `[rotation]`)、systemd --user ユニット雛形 (`scripts/systemd/`。既定では何も enable しない)。
- 残り: ログローテーション、配信オーバーレイ (drawtext / OBS 再評価)、チャット連携 (multi_repo_plan.md C2/C3 として soviet_now の落ち着きを待つ)。

---

## 付録: 設計上の決定記録 (ADR 要約)

| 決定 | 採用 | 不採用 | 理由 |
|---|---|---|---|
| 配信経路 | ffmpeg 直結 | OBS | ヘッドレス簡素化。ウィンドウ単位キャプチャが不要 (全画面=ゲームのみ) なら ffmpeg で足りる |
| オーケストレータ言語 | Python stdlib | bash / Node | JSON/TOML/プロセス管理/テスト容易性。pip ゼロで VM にそのまま乗る |
| SFC エミュレータ | RetroArch + libretro-snes9x (apt) | 単体 snes9x-gtk, stable-retro | arm64 の apt 供給を確認済。cfg 生成で入力を決定論化できる。stable-retro はビルド重・配信映像に別経路が必要 |
| SFC 入力注入 | xdotool XTEST + input_driver=sdl2 | RetroArch Network Remote | 汎用 (browser とも共通化)。Network Remote はプロトコルが薄文書でフォールバック扱い |
| Native captions | pinned FFmpeg `docichcc` + libx264 A/53 | OBS caption plugin / overlay-only text | Twitch playerで選択可能なCC、音声同期、generic fail-openを同一direct streamで実現 |
| CLI ゲーム映像化 | tmux + xterm 表示 | ttyrec→動画, pty 直描画 | 観測はテキスト (LLM 最適)、映像は「見えている端末」で兼ねる。分離構造で頑健 |
| ゲーム切替 | ディスプレイ常駐・アプリ入替 | ゲームごとに配信再起動 | 無停止切替。ffmpeg/エンコード状態を保てる |
