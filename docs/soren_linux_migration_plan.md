# soren の Linux 移植計画（Oracle ARM 24 時間ヘッドレス配信）

Soren Game AI（macOS で稼働中の Unity WebGL 自動プレイ + OBS 配信システム）を、Oracle Cloud Always Free の Ampere A1（Ubuntu 24.04 LTS ARM64, 2 OCPU / 12GB RAM, Xvfb ヘッドレス）へ段階移設するための計画書。環境構築手順は `oracle_arm_setup_guide.md` を参照。

## 表記ルール

- 【確認済】: 本計画作成時に soren リポジトリのソースを実読し、`file:line` で裏取りした事実
- 【未確認】: 推測・要実機検証の事項

---

## 1. そのまま動く部分

### ほぼ無修正で動く

| ファイル | 根拠 |
|---|---|
| `strategy.py` / `strategy_runner.py` / `analyze_board.py` / `analyze_patterns.py` / `strategy_helpers/` | Python 標準ライブラリのみ。macOS 依存なし |
| `obs_control.sh` | 【確認済】node は `command -v node` を優先（L76）。ハードコード候補（L78-84: `~/.nvm/...`、`/opt/homebrew/bin/node`、`/Volumes/satelite/...`）は macOS 用だが、**PATH に node があれば最初の候補で解決する** |
| `obs_browser_source.sh` | 【確認済】同上（L42-54）。ローカル HTML の `pathToFileURL` 変換はプラットフォーム非依存 |
| `twitch_chat.sh` / `twitch_chat_daemon.sh` / `youtube_chat.sh` / `twitch_clip.sh` / `twitch_predictions.sh` / `viewer_chat_monitor.sh` | HTTP/Twitch・YouTube API のみ。mac-ism なし（全ファイル grep 済） |
| `start_all.sh` | 【確認済】tmux スーパーバイザ（L94-112）。`stat` は既に Linux フォールバック済み（L187, L209-211） |
| `voicevox_tts.sh` / `voicevox_sing.sh` | curl の HTTP クライアント。URL を 127.0.0.1 にすれば動く |
| ステータス・オーバーレイ群（`show_status*.sh`, `generate_*overlay*.sh`, `dashboard_data.py` 等） | HTTP / OBS-WebSocket / ローカル HTML のみ（個別動作確認は Phase 1 で実施） |

### 実は修正が要る「小さな」点（探索で発見）

1. **`stat -f %m`（BSD 形式）が GNU stat でエラー**になる箇所が 6 ファイル:
   - `twitch_chat.sh:79` / `youtube_chat.sh:144` / `lib/obs_source_lock.sh:53` / `obs_capture_watchdog.sh:61` / `monitor_improve_runtime.sh:89` / `monitor_webfetch_failure.sh:76`
   - 対処: `stat -f %m ... 2>/dev/null || stat -c %Y ...` のフォールバック追加（`start_all.sh:187` と `explore.sh:115` は既に実装済みの同じパターンを踏襲）。
2. **md5**: 【確認済】`youtube_chat.sh:170-173`・`twitch_chat.sh:385`・`twitch_chat_daemon.sh:398` は既に `md5 -q` → `md5sum` のフォールバックを実装済み。追加修正不要。
3. **node パス候補**: ハードコード候補リスト（macOS パス）は無害。Linux で `node` を PATH に置けば解決。将来 `command -v node` が失敗した場合のフォールバックに `/usr/bin/node` を追加すると親切。

---

## 2. 置換・書き換えが必要な部分

### 2.1 `obs_window_capture_source.sh`（screen_capture → window_capture / xcomposite）

- 【確認済】`inputKind` は環境変数 `OBS_WINDOW_CAPTURE_INPUT_KIND` で差し替え可能（L98、既定 `screen_capture`）→ Linux では **`window_capture`（xcomposite）** に変更。
- 【確認済】mac-capture 専用の設定 `application` / `type` / `show_empty_names`（L248-254, L382-388）は Linux の xcomposite には存在しない → **`window` プロパティのみに整理**。
- 【確認済】`ensureAppAudioSource`（L290-327）が作る **`sck_audio_capture` は Linux に存在しない** → `pulseaudio_capture`（null-sink モニター）に置換するか削除。
- タイトル正規表現: 【確認済】L104 の `chromeWindowPattern = /\[Google Chrome(?: for Testing)\]/` は macOS のウィンドウタイトル形式（ブラケット表記）。Linux の Chrome タイトルは `Unity WebGL Player | soren-game - Google Chrome for Testing` 形式 → **`/Google Chrome/` 系に変更**（詳細は Linux 実機のタイトルを実測して確定）。
- 【確認済】`lib/obs_source_lock.sh`（L82-86 で使用）は macOS mac-capture の double-free 対策（ヘッダ L1-17 に理由明記）。**Linux の xcomposite には同種のバグは無いと推定**（要検証）— ロック自体は無害なので残してもよいが、`obs_capture_watchdog_check.mjs` の bounce 待ち 2.5 秒（L173）は短縮可能。

### 2.2 `obs_capture_watchdog_check.mjs`（バインド検証の X11 化）

- 【確認済】バインド検証（L149-161）: `GetInputPropertiesListPropertyItems { propertyName: 'window' }` は **Linux の window_capture にも存在**。ただし `itemValue` の形式が macOS（数値の window id）から Linux（`タイトル,ウィンドウID` 形式）に変わる → **形式を実測確認のうえ String 比較を調整**（【未確認】比較ロジックがそのまま成立するか）。
- 【確認済】`bounce`（L165-180）: `application` / `type` を含む SetInputSettings は macOS 専用 → Linux では `{ window: value }` のみに。2.5 秒待ちは Linux では不要か検証。
- 【確認済】OBS ログ既定パス（L82）: `~/Library/Application Support/obs-studio/logs` → Linux の **`~/.config/obs-studio/logs`** に変更。
- 【確認済】フリーズ検出（`GetSourceScreenshot`、L144-147, L217-235）は同一 API で **Linux でもそのまま流用可**。
- 代替: ウィンドウ列挙に **xdotool / wmctrl**（Xvfb 環境）を使う検証も可能（`xdotool search --name "soren-game"` で X11 ウィンドウ ID を確認）。

### 2.3 `obs_capture_watchdog.sh`（OBS 再起動の Linux 化）

- 【確認済】`_obs_relaunch_normal`（L98-127）: `osascript`（L110）/ `open -a`（L122）/ `pkill -f 'OBS.app/...'`（L114）が macOS 依存 → Linux では:
  ```bash
  # 案1: systemd 化した場合
  systemctl --user restart obs
  # 案2: 直接起動
  pkill -x obs; sleep 2
  DISPLAY=:99 nohup obs --disable-shutdown-check >/dev/null 2>&1 &
  ```
- 【確認済】`OBS_LOG_DIR` 既定（L41）: `~/Library/Application Support/obs-studio/logs` → `~/.config/obs-studio/logs`。
- 【確認済】`_obs_running` は `pgrep -x OBS`（L72）→ Linux のプロセス名は **`obs`** に変更。
- 【確認済】Safe Mode ダイアログ自動対処（L35-47, L78-127）: macOS 固有の機能。Linux OBS に同ダイアログがあるか【未確認】— なければ `OBS_SAFE_MODE_AUTOFIX=0` で機能停止。
- `stat -f %m`（L61）→ §1 のフォールバック化。

### 2.4 `soviet_local.mjs`（Chrome 起動まわり）

- 【確認済】`CHROME_HEADLESS` は `SOREN_CHROME_HEADLESS` 環境変数で制御（L68）。`=1` なら `launchPersistentContext({ headless: true })` に一本化（L1197, L1209）。
- 【確認済】no-focus `open -g` 起動（L393-533）は `process.platform !== 'darwin'` で**自動的に無効化**（L395）— Linux では素通し。
- 【確認済】`CFFIXED_USER_HOME`（L593-609）: macOS 用環境変数。Linux では XDG ベースになり無害。
- 【確認済】`~/Library/Caches/ms-playwright` 参照（L219-224）: `playwrightHeadlessShellExecutablePath()` は darwin 限定で Linux では `''`（L207）。メインの実行ファイル解決は `chromium.executablePath()`（L139-141）で Linux の `~/.cache/ms-playwright/` を返す → **そのまま動く**。
- 【確認済】crashpad フォールバック（L1209-1260）: macOS 固有の失敗モード（crashpad パーミッション）。Linux では発動しないと期待されるが、発動しても安全側（headless 再起動）に倒れる。
- 【確認済】BlackHole sinkId バインド（L1350-1474）: 案 A では「PulseAudio 既定出力 = null-sink」にするだけで同目的を達成。案 B（ヘッドレス）では不要。

#### 設計判断: 配信映像の取得方式（Phase 1 冒頭で A/B を検証して決定）

- **案 A（推奨・macOS の忠実移植）**: Xvfb + headed Chrome + `window_capture(X11)`。
  - `SOREN_CHROME_HEADLESS=0` + `DISPLAY=:99`。watchdog・バウンス機構・DociAI のソース構成を最大流用。
- **案 B（SOREN_CHROME_HEADLESS=1 + OBS browser_source）**: 映像は OBS の CEF 内ブラウザでゲーム URL を表示。
  - 問題: Unity WebGL のゲーム状態はクライアント側にあるため、AI が CDP で操作する Chrome インスタンスと、配信映像の OBS 内インスタンスが**別物になる（二重起動）**構造の解決が必要。AI 操作対象を OBS CEF 側に移すなら根本設計の変更になる。
  - sinkId バインド（L1350-1474）は不要になる利点がある。
- 判断基準: 「映像が AI の操作するゲームと同期しているか」「CPU/メモリが 12GB で収まるか」のスモークテスト。**既定は案 A**（差分最小・確実性優先）。

### 2.5 音声ルーティング（BlackHole + say/afplay → PulseAudio null-sink + ffplay/paplay）

`say_enqueue.sh` の現状（全て確認済）:

| 機能 | macOS 実装 | Linux 置換 |
|---|---|---|
| デバイス解決 | ffmpeg `audiotoolbox -list_devices`（L583） | `pactl list short sinks` で解決 |
| 再生 (WAV) | `afplay`（L680-686） | `paplay --device=soren_null` または `ffplay -nodisp -autoexit` |
| 再生 (MP3) | ffmpeg audiotoolbox（L688-691） | `ffplay -nodisp -autoexit` |
| Chrome 経由再生 | `chrome_audio_player.mjs`（L693-698） | 既定出力化で不要になる見込み（§2.6） |
| macOS `say` | `say -a BlackHole`（L705-712）・最終フォールバック（L1148-1176） | **Linux に `say` は存在しない** → 削除するか VOICEVOX への再帰フォールバックに置換（要設計） |
| 長さ推定 | ffprobe 主（L622-627）→ afinfo 副（L628-633） | afinfo が無いのでフォールバック消滅するだけ。**ffprobe があればそのまま機能**（L622 はコマンド有無チェック済み） |

設定手順:

```bash
# 1. null-sink 作成 (soren 専用出力)
pactl load-module module-null-sink sink_name=soren_null sink_properties=device.description=soren_null
# 2. OBS 側: pulseaudio_capture で soren_null.monitor をキャプチャ (DociAI の blackhole に相当)
# 3. 既定出力に設定
pactl set-default-sink soren_null
```

- `SAY_AUDIO_DEVICE="BlackHole 2ch"` → `"soren_null"` に変更。
- 音声合成自体（VOICEVOX / Google TTS）は HTTP API なのでプラットフォーム非依存。**再生とルーティングだけ**の置換で済む。

### 2.6 `chrome_audio_player.mjs`（要確認・置換）

- 【確認済】`connectOverCDP`（L67）で Chrome のページに `<audio>` を挿入し再生。CDP 接続先（ヘッドレス/headed）があれば動く。
- 【確認済】`setSinkId`（L122-124）はラベルから deviceId を解決して指定。Linux Chrome の PulseAudio sink への `setSinkId` は不安定な可能性（【未確認】）。
- 方針: null-sink を**既定出力**にする運用ならラベル解決が不要になり、このプレイヤー自体を廃止して ffplay/paplay 直再生に統一するのが最小構成（要確認・Phase 2）。

### 2.7 `google_tts.sh`

- 【確認済】`afplay`（L49, L226）→ `paplay`/`ffplay` に置換。gcloud は Linux（arm64 の公式 .deb）で動作。
- 【確認済】`PROJECT="gen-lang-client-0367522921"`（L9）は固定値 → VPS ではプロジェクト ID を揃えるか環境変数化。

### 2.8 `coeiroink_tts.sh`

- COEIROINK エンジンの公式コンテナは **linux/amd64 CPU ビルド** が主で、Oracle ARM では動かない可能性が高い（【未確認】Docker Hub で arm64 タグを要確認）。
- 動かなければ**未使用化（非推奨扱い）**とし、代替は VOICEVOX。使用継続する場合も `afplay -d`（L88, L123）→ `paplay` 置換が必要。

### 2.9 `youtube_chat.sh` / `twitch_chat.sh` ほか

- 【確認済】md5 → md5sum はフォールバック実装済み（§1）。
- `stat -f %m` のフォールバック化は §1 にまとめた対象 6 ファイル（`youtube_chat.sh:144`、`twitch_chat.sh:79` を含む）。

### 2.10 OBS シーン定義 `DociAI.json` の作り直し

【確認済】macOS 版（`~/Library/Application Support/obs-studio/basic/scenes/DociAI.json`）の実測ソース構成:

| ソース | 種別 (id) | Linux 版の方針 |
|---|---|---|
| `sorengame` + `wildcardParallelCand*`（計 8） | `screen_capture` | **`window_capture`（X11）に置換**（案 A）。案 B なら `browser_source` |
| `statsOverlay` / `opsOverlay` / `eventOverlay` / `improveOverlay` / `wildcardParallelOverlay` / `dashboard`（計 6+） | `browser_source` | **そのまま流用**（ローカル HTML は `obs_browser_source.sh` が `pathToFileURL` で対応済み） |
| `twica`（×2） | `browser_source`（外部 URL） | **そのまま流用** |
| `systemMsg` | `text_ft2_source` | Linux にも freetype2 プラグインが存在（要確認）。なければ `browser_source` か `image_source` で代替 |
| `blackhole` | `coreaudio_input_capture`（BlackHole 2ch） | **`pulseaudio_capture`（soren_null.monitor）に置換** |
| `soren91Audio` ほか（計 3） | `sck_audio_capture` | **`pulseaudio_capture` に置換 or 削除** |
| `mic` / `GoPro` | `coreaudio_input_capture` / `macos-avcapture` | **削除**（ヘッドレス配信では不要） |
| `clip` | （キャプチャ/録画系） | **削除** |
| フィルタ（limiter / compressor） | `*_filter` | そのまま流用可（要確認） |

移設手順: Linux OBS で新シーンコレクション `DociAI-linux` を GUI（xrdp）または JSON 直書きで作成。browser_source の URL・サイズ設定は既存 JSON の値（width/height 等）を流用する。

---

## 3. 配信先の変更（Twitch / YouTube）

- OBS は Linux 版で**プロファイルを新規作成**。設定 → 配信:
  - **Twitch**: アカウント連携、または RTMP URL（`rtmp://live.twitch.tv/app`）+ ストリームキー（Twitch ダッシュボードで**再発行**）
  - **YouTube**: RTMP URL（`rtmp://a.rtmp.youtube.com/live2`）+ ストリームキー（YouTube Studio → ライブ配信管理で発行）
- ストリームキーは `.env` ではなく **OBS プロファイル（`~/.config/obs-studio/profiles/`）** に保存される。ローカルファイルなので権限と取り扱いに注意。
- 解像度 1280x800（ゲームウィンドウ基準）、ビットレート 4–6 Mbps 目安（Oracle の無料アウトバウンド 10TB/月の範囲内）。

---

## 4. 起動・監視（tmux / systemd / Xvfb）

- 【確認済】`start_all.sh` は tmux スーパーバイザ（L94-112）— **tmux を導入すればそのまま使用可**（`sudo apt install tmux`）。
- **launchd → systemd**: 【確認済】現行は `~/Library/LaunchAgents/com.azumag.soren.stream-title-day.plist`（+ guardian は .disabled）が launchd 依存。
  - Linux では **systemd --user** ユニット（`loginctl enable-linger <user>` で非ログイン時も常駐）または **cron @reboot** に置換。
  - 例:
    ```ini
    # ~/.config/systemd/user/start-all.service
    [Unit]
    Description=soren supervisor
    [Service]
    ExecStart=/home/ubuntu/soren/start_all.sh --supervisor
    WorkingDirectory=/home/ubuntu/soren
    Environment=DISPLAY=:99
    Restart=on-failure
    [Install]
    WantedBy=default.target
    ```
- **OBS**: Xvfb の `DISPLAY=:99` 上で systemd サービスとして起動（`Restart=always`）。`obs_capture_watchdog.sh`（§2.3 の置換後）が監視ループを維持。
- **Xvfb / PulseAudio**: Xvfb は systemd 化（ガイド §4）。**【要注意】** `XDG_RUNTIME_DIR` が無いと PulseAudio が起動しない → `loginctl enable-linger ubuntu` を必ず設定し、ユーザーセッションで `pulseaudio --daemonize=yes`（または pipewire）を起動する（要検証）。
- 死活監視: UptimeRobot 等の外部監視（オプション）+ 既存の watchdog 群（capture watchdog / soviet watchdog / bridge recovery 等）が配信断を自動回復。

---

## 5. `.env` の変更点

【確認済】現行 `.env` の実測値と変更:

| 変数 | 現行値 | Linux での変更 |
|---|---|---|
| `VOICEVOX_URL_PRIMARY` | `http://192.168.11.13:50021` | **`http://127.0.0.1:50021`**（VPS 上で VOICEVOX を直接起動） |
| `VOICEVOX_URL_REMOTE` | `http://192.168.11.13:50021` | `http://127.0.0.1:50021` か未使用化 |
| `VOICEVOX_URL_FALLBACK` | `http://127.0.0.1:50021` | 変更不要 |
| `SAY_AUDIO_DEVICE` | `"BlackHole 2ch"` | `"soren_null"`（PulseAudio null-sink 名） |
| `SOREN_CHROME_AUDIO_OUTPUT_LABEL` | `'BlackHole 2ch'` | `"soren_null"` か空（既定出力化で不要） |
| `SOREN_CHROME_HEADLESS` | （未設定） | **追加: `1`**（案 B）または `0` + `DISPLAY=:99`（案 A） |
| `SOREN_CHROME_NO_FOCUS_LAUNCH` | `0` | 【確認済】darwin 限定の変数（soviet_local.mjs:395）— Linux では無意味。放置でよい |
| `WILDCARD_PARALLEL_JOBS` | `2` | **`1` に変更**（12GB では Chrome+OBS+VOICEVOX+並列で逼迫。実測後に 2 へ戻す余地）。【確認済】`.env:96` |
| `OBS_WEBSOCKET_PORT` / `OBS_WEBSOCKET_PASSWORD` | `4455` / 設定済み | 変更不要（OBS 側の設定と一致させる） |
| `MODEL_IMPROVE=minimax`（claude CLI 経由） | — | §6 の CLI 認証が VPS で成立したら有効 |
| `RADIO_AGENTS` / `COMMENT_AGENTS` 内の `opencode:...` / `qwen35e` | — | opencode CLI は Linux 可（要検証）。ollama（qwen35e）は arm64 Linux 版あり |
| `GOOGLE_TTS_*` | — | gcloud 認証状態は .env 外（gcloud 設定ディレクトリ） |

---

## 6. リスクと未確認事項（正直な列挙）

1. **strategy/ai.sh の CLI 依存（最大リスク）**
   - 【確認済】`claude` CLI を直接起動（L110-122、`claude --print -p ...`）。macOS では OAuth ログイン済みだが、**VPS ではブラウザ認証が回らない** → `claude setup-token` / `ANTHROPIC_API_KEY` によるヘッドレス認証が可能か要検証。`.env` の `OPENROUTER_API_KEY` は opencode 経由で使える想定（要検証）。
   - 【確認済】opencode CLI は XDG ベースの状態管理（L173-304）で Linux と相性が良い。`opencode auth login`（API キー方式）を VPS で要検証。
   - ollama: arm64 Linux ビルドあり（要確認）。
2. **OBS Linux arm64 ビルドの有無 → 【解決済み】**
   - 【確認済】Ubuntu 24.04 (noble) の universe に **arm64 版 obs-studio (30.0.2)** が存在（.deb 実在を確認）。noble-backports に 30.2.3 もあり。
   - 【確認済】GitHub Releases（32.2.1）は x86_64 .deb のみ、Flathub は x86_64 のみ — **本命は apt**。
   - 残課題: OBS 30.x の `window_capture`（X11）の実挙動のみ実機検証（Phase 0 で実施）。
3. **Playwright chromium の Linux arm64**
   - 【確認済】存在する（Playwright 公式が arm64 ビルドを配布）。`npx playwright install chromium` で解決。
4. **COEIROINK の ARM 非対応の可能性** → §2.8（未使用化が現実解）。
5. **その他の未確認**
   - `sharp`（package.json, ^0.34.5）: arm64 プリビルトバイナリの有無を要確認（無ければビルドに cmake が必要）。
   - OBS `window_capture` のウィンドウ列挙は Chrome 起動直後に出ないことがある → 既存 watchdog のリトライ機構がカバー（要確認）。
   - PulseAudio のヘッドレス起動（XDG_RUNTIME_DIR 周り）— §4。
   - 24h 連続配信の制限: Twitch のセッション切れ・YouTube の配信時間上限など配信先ポリシー（【未確認】日次で立て直す運用に合わせて確認）。
   - リージョン間の RTMP 遅延増（日本 → Oracle リージョン）は実害なしと想定（要実測）。

---

## 7. 推奨フェーズ分け（段階的移設スケジュール）

**原則**: macOS 現行環境を維持したまま並走。各 Phase の完了チェックをパスしたものだけ本番を移す。

### Phase 0: Oracle セットアップ（1〜2 日）
- A1 インスタンス作成・SSH・セキュリティ（ガイド §1-3）
- Xvfb / xrdp / Tailscale（ガイド §4）
- **OBS arm64 のインストール（apt で 30.x、検証は window_capture の実挙動）**、VOICEVOX arm64 起動、Playwright 導入（ガイド §5-8）
- 完了条件: `DISPLAY=:99` 上で OBS が起動し、`obs-websocket` が 4455 で応答する

### Phase 1: コア移植 — ゲームループ headless 化 + 配信（3〜5 日）
- §2.4 の A/B 検証 → 配信方式の確定（既定: 案 A）
- `soviet_local.mjs` の最小変更（環境変数で挙動制御）
- `obs_window_capture_source.sh` / `obs_capture_watchdog_check.mjs` / `obs_capture_watchdog.sh` の Linux 化（§2.1-2.3）
- `DociAI.json` の再構築（§2.10）+ シーン・プロファイルの RTMP 再設定（§3）
- `start_all.sh` を Xvfb 上で起動し、**24h 試行配信**（映像・ゲームループ・自動再起動の確認）
- 完了条件: 配信が 24 時間連続で途切れず、watchdog の自己修復が働く

### Phase 2: 音声周り（2〜3 日）
- PulseAudio null-sink 導入、`say_enqueue.sh` の ffplay/paplay 化（§2.5）
- VOICEVOX arm64 を本番起動（`VOICEVOX_URL_*` を 127.0.0.1 に）
- `google_tts.sh` / `chrome_audio_player.mjs` の置換（§2.6-2.7）、`coeiroink_tts.sh` の判定（§2.8）
- 完了条件: 全 TTS 経路（VOICEVOX / Google TTS / SOREN91）が配信に乗り、重複・欠落が無い

### Phase 3: chat / 並列 / 運用（2〜4 日）
- chat daemon（twitch / youtube）の動作確認と `stat` フォールバック適用（§1, §2.9）
- wildcard 並列の調整（`WILDCARD_PARALLEL_JOBS=1` で実測 → 2 へ増やすか判断）
- launchd → systemd / cron の置換（§4）、24h 監視の確立
- 完了条件: 無人運用 1 週間、キャプチャ・音声・chat・並列すべて自己修復が働く

**全体で 1〜2 週間**。Phase 1 完了時点で配信自体は成立するため、そこで macOS から完全切り替えし、Phase 2/3 は本番を止めずに進めることも可能。

---

## 付録: ファイル別移植チェックリスト

| ファイル | 対応 | 状態 |
|---|---|---|
| `strategy.py` / `strategy_runner.py` / `analyze_board.py` ほか AI コア | 無修正 | ✅ 確認済 |
| `obs_control.sh` / `obs_browser_source.sh` | node は PATH 解決のみ | ✅ 実質無修正 |
| `twitch_chat.sh` / `twitch_chat_daemon.sh` / `youtube_chat.sh` / `twitch_clip.sh` / `twitch_predictions.sh` | `stat -f %m` フォールバック（L79 / L144 等） | 修正小 |
| `start_all.sh` | tmux 利用（無修正） | ✅ 確認済 |
| `obs_window_capture_source.sh` | `window_capture` 化・タイトル regex・sck 削除 | 書き換え |
| `obs_capture_watchdog_check.mjs` | window 形式調整・bounce 簡略化・ログパス | 書き換え |
| `obs_capture_watchdog.sh` | 再起動コマンド・プロセス名・ログパス・stat | 書き換え |
| `lib/obs_source_lock.sh` | stat 修正のみ（ロックは維持 or 検証後に除去） | 修正小 |
| `soviet_local.mjs` | 環境変数制御（実装はほぼそのまま） | 修正小 |
| `say_enqueue.sh` | PulseAudio / ffplay / paplay 化・say フォールバック撤去 | 書き換え |
| `chrome_audio_player.mjs` | 既定出力化により廃止 or 置換 | 判定 |
| `google_tts.sh` | afplay → paplay・PROJECT 修正 | 修正小 |
| `coeiroink_tts.sh` | 未使用化（ARM 非対応の可能性） | 判定 |
| `DociAI.json` | シーン再構築 | 作り直し |
| OBS プロファイル | RTMP / ストリームキー再設定 | 作り直し |
| launchd plist | systemd --user / cron @reboot へ | 置換 |
| `.env` | §5 の一覧 | 変更 |
