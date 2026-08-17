# TTS 共通部品化 — inventory と参照実行設計 (2026-08-17)

本稿は、docich が soviet_now の TTS/say 経路を「移動より先に参照」する
(`docs/multi_repo_plan.md` §3 / `docs/handoff_common_parts.md` §3.3) ための inventory と
設計案である。

- 【確認済】= このセッションで `games/soviet_now` = `dcb2992` の実コードと実コマンドを確認した事実
- soviet_now は読み取り専用。本稿の PoC は docich 側のみに実装する

---

## 1. 対象とスナップショット

| 項目 | 状態 |
|---|---|
| soviet_now submodule | `dcb2992` (docich 側ポインタ) |
| TTS 安定性 | `say_enqueue.sh` / `google_tts.sh` / `coeiroink_tts.sh` は直近 6 週間変更なし。`voicevox_tts.sh` `english_tts.sh` `lib/closed_captions.sh` は今回も未変更 (読み取りのみ) |
| docich | stdlib-only、`:98` / `docich_sink` / `stream.mode="null"` が既定 |
| 本番境界 | 本番 sorengame は soviet_now (`:99`, `soren-runtime.service`) が所有。docich は本番キュー/サービスに触れない |

## 2. TTS inventory

### 2.1 `games/soviet_now/say_enqueue.sh` 【確認済】

| 項目 | 内容 |
|---|---|
| 主責務 | 発話コンテンツ (テキストファイル) を受け取り、TTS 合成・FIFO 順次再生・字幕連携・リトライまでを行う排他キュー |
| ゲーム固有依存 | なし。`SAY_CONTEXT_LABEL` で呼び出し元ラベル (comment/radio/soren91 等) だけを切り替える。ラジオ固有の `.voice` sidecar と `tmp/voicevox_*.txt` の話者指定は `say_enqueue.sh` 側の運用機能 |
| 環境変数 | `.env`、`EXPLORE_MODE`、`SOREN_OBS_PLATFORM`、`SAY_AUDIO_DEVICE`、`PULSE_SINK`、`VOICEVOX_URL(*)` / `VOICEVOX_SPEAKER` / `VOICEVOX_TIMEOUT` / `VOICEVOX_MAX_CHARS` / `VOICEVOX_PITCH` / `VOICEVOX_TEMPO` / `VOICEVOX_INTONATION` / `SAY_VOICEVOX_SPEAKER_OVERRIDE` / `SAY_CHUNK_GAP_SEC` / `SAY_RETRY_*` / `SAY_TRUNCATE_*` / `SAY_HANG_EXTRA_SEC` / `SAY_PRESERVE_PRERENDERED_CHUNKS` / `SAY_CONTEXT_LABEL` / `SAY_CC_TEXT` / `SAY_DISABLE_COMMENT_YIELD` / `DOCICH_CC_*` / `OUTBOUND_CHAT_*` / `TMP_DEBUG_DIR` 等 |
| 外部コマンド | bash, curl, python3, ffmpeg, paplay/ffplay (Linux), afplay/say (macOS), node (`chrome_audio_player.mjs` は macOS 経路), pactl |
| 入出力ファイル | 入力: `.txt` コンテンツ (必須)。出力: `tmp/.say_queue/` 以下 (Content コピー, pid, played.log, debug.log, ロック), `tmp/.say_queue/stream_*` (ストリーミング WAV), `tmp/state/radio_talk_played` |
| 一時ディレクトリ | `tmp/.say_queue/`、`tmp/.say_queue/stream_*`、`/tmp/gtts_chunk_*` / `/tmp/google_tts_*` / `/tmp/voicevox_chunk_*` (下位 TTS スクリプト側) |
| キュー・ロック方式 | `tmp/.say_queue/.lock` の mkdir 原子ロック + owner_pid + heartbeat (stale 180s)。VOICEVOX 合成は別の `.voicevox_synth_lock` + 優先度待ち |
| 再生経路 | Linux: `paplay --device` 優先、なければ `ffplay`。macOS: `afplay` / `say` / `ffmpeg` / `node chrome_audio_player.mjs` |
| PulseAudio 依存 | あり (Linux)。`SAY_AUDIO_DEVICE` を sink 名として `pactl` で解決し `paplay --device=<sink>`。`ffplay` は既定 sink へ流れる |
| VOICEVOX 依存 | 主経路。`tmp/voicevox_voice.txt` / `.voice` sidecar / `SAY_VOICEVOX_SPEAKER_OVERRIDE` で話者選択し、`voicevox_tts.sh` を exec |
| Google TTS 依存 | フォールバック。`google_tts.sh` を exec (gcloud 認証必須) |
| COEIROINK 依存 | フォールバック。`tmp/coeiroink_voice.txt` により ON になり `coeiroink_tts.sh` を exec |
| 字幕連携 | `lib/closed_captions.sh` を source し、VOICEVOX 音声境界に合わせて plan / prepare / commit / clear を実行。失敗時は音声を止めない (fail-open)。`--wav-playlist --caption-chunks` で外部バンドル再生・字幕同期 |
| rate・delay | `rate` = 第2引数 (既定 120)、`pre_delay_sec` = 第3引数 (既定 60) |
| 終了コード | 0 = 再生完了 (一部 fallback 含む)、1 = 再生失敗・render-only 失敗、2 = 引数/形式エラー、75 = render 保留 (優先音声待ち)、97~99 は内部 rc (say 起動失敗/切断) |
| retry・timeout | 外側 TIMEOUT_CMD は現状無効化中。VOICEVOX 合成/再生の retry (既定 6 回, backoff 2→20s)、再生ハング監視、played.log による連続失敗監視 (audio_worker) |
| fail-open / fail-closed | TTS 合成失敗はリトライ後にフォールバック → 最終失敗は rc 1 で fail-closed。字幕 (CC) は fail-open。音声レベルで停止させる設計は本番でも同様 |
| 並行実行時の挙動 | FIFO を保証する mkdir ロック。ロック取得待ち、前の say PID 待ち、ラジオ→コメント yield、VOICEVOX 合成の同時 1 リクエスト制限 |
| セキュリティ上の注意 | 任意のテキストファイルパス・任意の WAV playlist を受理する。docich 側は書式を検証し、`games/soviet_now/...` 外のパスや shell メタ文字を渡さない |
| docich から参照実行できるか | 可 (相対パス `games/soviet_now/say_enqueue.sh`、cwd をサブモジュールルートにして `bash` 経由)。★ 大量の相対ファイル参照 (`lib/closed_captions.sh`, `voicevox_tts.sh`, `config/`, `tmp/`) があるため cwd が必須 |
| 将来共通部品へ昇格可能か | 可 (C4)。ただし 2,572 行で TTS + キュー + 字幕 + 話者運用が一体。昇格時は責務分割を伴う |
| 今は触らない理由 | 6 週間安定していて本番に広く使われる。docich から参照して契約 (argv/env/cwd/rc) を固定するのが先 |

### 2.2 `games/soviet_now/voicevox_tts.sh` 【確認済】

| 項目 | 内容 |
|---|---|
| 主責務 | VOICEVOX HTTP API への音声合成ラッパ。チャンク分割、話者、ピッチ/テンポ/抑揚、URL フェイルオーバー、WAV 結合 |
| ゲーム固有依存 | なし |
| 環境変数 | `VOICEVOX_URL` / `VOICEVOX_URL_PRIMARY` / `VOICEVOX_URL_FALLBACK` / `VOICEVOX_URL_LOCAL` / `VOICEVOX_ACTIVE_URL` / `VOICEVOX_SPEAKER` / `VOICEVOX_TIMEOUT` / `VOICEVOX_HEALTH_TIMEOUT` / `VOICEVOX_MAX_CHARS` / `VOICEVOX_PITCH` / `VOICEVOX_TEMPO` / `VOICEVOX_INTONATION` / `VOICEVOX_NY_PAUSE_FIX` / `VOICEVOX_NY_PAUSE_LEN` |
| 外部コマンド | bash, curl, python3, avconv なし (Python wave で結合), ffmpeg なし |
| 入出力ファイル | `config/voicevox_word_replace.txt` を読み込み。出力 WAV は引数で指定 |
| 一時ディレクトリ | `/tmp/voicevox_chunk_${$}_*.wav` |
| キュー・ロック方式 | なし (被呼び出し側)。呼び出し側 `say_enqueue.sh` が合成ロックを持つ |
| 再生経路 | `--test` 時のみ `afplay`。`-o` 付きでは再生しない |
| PulseAudio 依存 | なし |
| VOICEVOX 依存 | 必須 (HTTP API) |
| Google TTS 依存 | なし |
| COEIROINK 依存 | なし |
| 字幕連携 | なし (チャンク配列を返さない。字幕のチャンク境界は呼び出し側が保持) |
| rate・delay | `VOICEVOX_TEMPO` で速度変更。引数 rate/delay なし |
| 終了コード | 0/1 (curl/Python 失敗時) |
| retry・timeout | curl `--max-time` (既定 30s)、URL fallback ループ、`_synthesize_one` のチャンク単位再試行はなし |
| fail-open / fail-closed | fail-closed (失敗は rc 1、WAV 削除) |
| 並行実行時の挙動 | 同時合成を抑制しない。呼び出し側の mkdir ロックが直列化する |
| セキュリティ上の注意 | テキストは curl `--data-urlencode` 経由でエスケープ。`--` 不可のテキスト開始文字は見かけ上許容されるが argv1 として安全に渡す。docich PoC では直接呼ばず `say_enqueue.sh` 経由にする |
| docich から参照実行できるか | 可。ただし `say_enqueue.sh` 経由が本番と同じ経路で、字幕・レート・pre-delay も一貫する |
| 将来共通部品へ昇格可能か | 可。HTTP クライアント部分のみなら `src/docich/` 側の urllib 実装も候補だが、本稿では移動しない |
| 今は触らない理由 | `say_enqueue.sh` からの呼び出しと test 群 (`tests/test_say_streaming.py` 等) に密結合で安定。呼び出し契約の確立が先 |

### 2.3 `games/soviet_now/google_tts.sh` 【確認済】

| 項目 | 内容 |
|---|---|
| 主責務 | Google Cloud Text-to-Speech (gcloud 認証) の MP3 合成ラッパ。テキスト分割・結合・任意再生なし |
| ゲーム固有依存 | なし |
| 環境変数 | `GOOGLE_TTS_VOICE` / `GOOGLE_TTS_RATE` / `GOOGLE_TTS_PITCH` / `GOOGLE_TTS_MAX_CHARS` / `SOREN_OBS_PLATFORM` / `SAY_AUDIO_DEVICE` |
| 外部コマンド | bash, gcloud, curl, python3, ffmpeg (結合時), paplay/ffplay (Linux 再生), afplay (macOS 再生) |
| 入出力ファイル | `-o out.mp3` / 既定 `/tmp/tts.mp3`。`-f` で入力テキストファイル |
| 一時ディレクトリ | `/tmp/gtts_chunk_${$}_*.mp3`, `/tmp/gtts_concat_$$.txt` |
| キュー・ロック方式 | なし |
| 再生経路 | `-o` なし時: Linux `paplay`/`ffplay`、macOS `afplay`。`say_enqueue.sh` からは常に `-o` 付きで呼ばれる |
| PulseAudio 依存 | 再生のみ。macOS では不要 |
| VOICEVOX 依存 | なし |
| Google TTS 依存 | 必須 (gcloud auth + API) |
| COEIROINK 依存 | なし |
| 字幕連携 | なし |
| rate・delay | `--rate` / `--pitch` / `--volume` / `GOOGLE_TTS_RATE` |
| 終了コード | 0/1 (gcloud/curl/合成失敗) |
| retry・timeout | curl `--max-time` なし。リトライなし |
| fail-open / fail-closed | fail-closed |
| 並行実行時の挙動 | なし |
| セキュリティ上の注意 | API 認証トークンをコマンドラインのシェル変数で扱う。docich 側から認証情報を作らない。テキストは JSON エスケープ済み |
| docich から参照実行できるか | 可。`say_enqueue.sh` のフォールバックとして既に使用 |
| 将来共通部品へ昇格可能か | 条件付き可 (API トークン管理と chunk 結合を移動する価値が低い。docich 側 HTTP も候補だが優先度低) |
| 今は触らない理由 | `say_enqueue.sh` のフォールバックで、直接呼ぶ用途は開発/デモ用。認証トークンは本番では保持しない方針 |

### 2.4 `games/soviet_now/coeiroink_tts.sh` 【確認済】

| 項目 | 内容 |
|---|---|
| 主責務 | COEIROINK v2 HTTP API の WAV 合成ラッパ。話者一覧・テスト音声 |
| ゲーム固有依存 | なし |
| 環境変数 | `COEIROINK_URL` / `COEIROINK_DIR` / `SPEAKER_UUID` / `STYLE_ID` / `COEIROINK_TIMEOUT` / `SAY_AUDIO_DEVICE` |
| 外部コマンド | bash, curl, python3, afplay (テスト時), ffmpeg なし |
| 入出力ファイル | `-o out.wav`、既定 `/tmp/coeiroink_$$.wav` |
| 一時ディレクトリ | `/tmp` (既定出力) |
| キュー・ロック方式 | なし |
| 再生経路 | `--test` のみ `afplay -d "$SAY_AUDIO_DEVICE"` (`-d` は afplay のデバッグオプションでありデバイス指定ではない)。`say_enqueue.sh` からは `-o` 付きで呼ばれる |
| PulseAudio 依存 | なし (Linux では `say_enqueue.sh` 側が再生) |
| VOICEVOX 依存 | なし |
| Google TTS 依存 | なし |
| COEIROINK 依存 | 必須 (HTTP API) |
| 字幕連携 | なし |
| rate・delay | `speedScale` は固定 1.0。引数 rate/delay なし |
| 終了コード | 0/1 |
| retry・timeout | curl `--max-time` (既定 30s) |
| fail-open / fail-closed | fail-closed |
| 並行実行時の挙動 | なし |
| セキュリティ上の注意 | テキストは JSON エスケープ済み。`COEIROINK_URL` を検証してから渡す (docich 側) |
| docich から参照実行できるか | 可。`say_enqueue.sh` のフォールバックとして既に使用 |
| 将来共通部品へ昇格可能か | 条件付き可 (HTTP クライアントの共通化候補だが優先度低) |
| 今は触らない理由 | 上記 Google と同じ理由。呼び出し契約の確立が先 |

### 2.5 `games/soviet_now/english_tts.sh` / `bilingual_comment_tts.sh` 【確認済】

| 項目 | 内容 |
|---|---|
| 主責務 | Flite による英語 WAV 合成 (`english_tts.sh`) と、英語+日本語を 1 WAV へ正規化結合するバイリンガル経路 |
| ゲーム固有依存 | なし |
| 環境変数 | `ENGLISH_TTS_VOICE` / `ENGLISH_TTS_FLITE_BIN` / `ENGLISH_TTS_FFMPEG_BIN` / `ENGLISH_TTS_SAMPLE_RATE`、`BILINGUAL_TTS_*` |
| 外部コマンド | bash, flite, ffmpeg, python3 (`lib/comment_bilingual.py`), `say_enqueue.sh` |
| 入出力ファイル | 入力 JSON サイドカー (`comment_bilingual.py emit-segments`)。出力 WAV |
| 一時ディレクトリ | `mktemp -d "${TMPDIR:-/tmp}/soren-english-tts.*"` / `soren-bilingual-tts.*` |
| キュー・ロック方式 | なし (バイリンガルは render-only の `say_enqueue.sh` を内部で呼ぶ) |
| 再生経路 | なし (出力 WAV)。呼び出し側が `say_enqueue.sh --wav` で再生 |
| PulseAudio 依存 | なし |
| VOICEVOX 依存 | バイリンガルの日本語側は `say_enqueue.sh --render-only` 経由で必要 |
| Google TTS 依存 | なし |
| COEIROINK 依存 | バイリンガルの日本語側は VOICEVOX 優先。COEIROINK は使わない |
| 字幕連携 | 日本語 chunk の字幕計画は `say_enqueue.sh --render-only` 側と二重管理しない。バイリンガル結合 WAV は字幕なしで再生する |
| rate・delay | バイリンガルは第2引数 `RATE` を日本語 `say_enqueue.sh` へ渡す |
| 終了コード | 0/1/2/127 (Flite/ffmpeg 不在 127) |
| retry・timeout | なし (内部 say_enqueue のリトライに従う) |
| fail-open / fail-closed | fail-closed |
| 並行実行時の挙動 | 日本語合成ロックは内部 `say_enqueue.sh` が持つ |
| セキュリティ上の注意 | FFmpeg concat の playlist は自前生成。docich 側でバイナリを直接選ばない |
| docich から参照実行できるか | 可。ただしコメント応答 (C2) の一部であり、今回の PoC 対象外 |
| 将来共通部品へ昇格可能か | 可 (C2/C4 でコメント応答と一緒に判断) |
| 今は触らない理由 | broadcast/ 系が活発に変更されており、本稿は `say_enqueue.sh` の閉じた入口を固定する段階 |

### 2.6 `games/soviet_now/lib/closed_captions.sh` 【確認済】

| 項目 | 内容 |
|---|---|
| 主責務 | 字幕 fail-open オーケストレーション。`say_enqueue.sh` から source され、翻訳計画の開始/待機/FFmpeg へ prepare/commit/clear |
| ゲーム固有依存 | なし |
| 環境変数 | `DOCICH_CC_ENABLED` / `DOCICH_CC_SOCKET` / `DOCICH_CC_CONTROLLER` / `DOCICH_CC_PYTHON` / `DOCICH_CC_TRANSLATIONS_FILE` / `DOCICH_CC_SOCKET_TIMEOUT_SEC` / `XDG_RUNTIME_DIR` / `DEBUG_LOG_FILE` |
| 外部コマンド | `python3` (`lib/closed_captions.py plan/send`) |
| 入出力ファイル | `<content>.txt_cc_plan.json`, `<content>.txt_cc_chunks.txt`, `lib/closed_captions.py` |
| 一時ディレクトリ | なし (コンテンツ隣接) |
| キュー・ロック方式 | なし。`say_enqueue.sh` の再生ロック内で実行 |
| 再生経路 | なし (FFmpeg socket 制御のみ) |
| PulseAudio 依存 | なし |
| VOICEVOX 依存 | `docich_cc_is_enabled` が `USE_VOICEVOX=1` を要求 (WAV/プリレンダー以外は VOICEVOX 前提) |
| Google TTS / COEIROINK 依存 | なし |
| 字幕連携 | 本功能そのもの。失敗は音声継続 |
| rate・delay | なし |
| 終了コード | 関数の戻り値のみ (0/1)。単体では exit しない |
| retry・timeout | socket timeout 3s、翻訳 plan は非同期で失敗時待機解除 |
| fail-open / fail-closed | fail-open (字幕が無くても音声は継続) |
| 並行実行時の挙動 | FFmpeg 側の 32 スロット循環 (`sequence % 32`) は docich `src/docich/captions.py` と同期済み |
| セキュリティ上の注意 | `DOCICH_CC_CONTROLLER` の相対パスはサブモジュールルート基準。docich 側から絶対パスを注入せず、cwd 固定で相対のまま使う |
| docich から参照実行できるか | 可 (`say_enqueue.sh` 経由。直接 source する用途は今回設けない) |
| 将来共通部品へ昇格可能か | 可。`src/docich/captions.py` が正典。shell 側は薄いブリッジに整理する余地あり |
| 今は触らない理由 | 字幕同期は完了済み。shell 側の契約を動かすと本番音声経路へ影響するため、PoC では既存のまま参照 |

### 2.7 `games/soviet_now/lib/outbound_queue.sh` 【確認済】

| 項目 | 内容 |
|---|---|
| 主責務 | Twitch 投稿を直接せず、pending → processing → sent のファイルキューへ積むチャット送信キュー |
| ゲーム固有依存 | なし (ただし `.env` の認証情報と `twitch_chat.sh` / `youtube_chat.sh` を参照) |
| 環境変数 | `OUTBOUND_CHAT_QUEUE_DIR` / `OUTBOUND_CHAT_*` / `TMP_DEBUG_DIR` / `.env` 由来の Twitch/YouTube OAuth |
| 外部コマンド | bash, md5/md5sum, stat, `./twitch_chat.sh`, `./youtube_chat.sh` |
| 入出力ファイル | `tmp/.outbound_chat_queue/{pending,processing,sent,dedup}` |
| 一時ディレクトリ | `tmp/.outbound_chat_queue/.twitch_send_err.XXXX` |
| キュー・ロック方式 | ファイル rename + dedup マーカー (mkdir) |
| 再生経路 | なし |
| PulseAudio / VOICEVOX / Google TTS / COEIROINK 依存 | なし |
| 字幕連携 | なし |
| rate・delay | なし |
| 終了コード | 関数の戻り値のみ (0/1) |
| retry・timeout | 送信失敗は pending へ戻し、認証失敗は backoff ファイル |
| fail-open / fail-closed | fail-open (送信失敗を音声失敗にしない) |
| 並行実行時の挙動 | 複数 enqueue は rename 原子性。単一 consumer 前提ではないが backoff dedup で保護 |
| セキュリティ上の注意 | API トークンを env 経由で参照。PoC では source しない (チャット投稿を行わない) |
| docich から参照実行できるか | 可だが、`say_enqueue.sh` が内部で source する。docich はキューへ直接投稿しない |
| 将来共通部品へ昇格可能か | 可 (C2 のチャット部品として)。TTS には含めない |
| 今は触らない理由 | コメント応答 (C2) 側。今回の TTS PoC で source されても副作用 (Twitch チャット) を起こさないよう、無効化環境で動かす |

## 3. docich / soviet_now の責務境界

| 責務 | docich | soviet_now |
|---|---|---|
| ゲーム切替・配信・display/audio 基盤 | 所有 | 本番 sorengame は別所有 (C1) |
| TTS 実行の「正典」 | 今回の設計では参照実行のみ。将来 C4 で昇格候補 | `say_enqueue.sh` / `voicevox_tts.sh` / `google_tts.sh` / `coeiroink_tts.sh` |
| 字幕計画 (翻訳・FFmpeg 制御) | `src/docich/captions.py` が正典 | `lib/closed_captions.py` + `lib/closed_captions.sh` を参照実行 |
| 音声キュー所有権 | **docich は追加でキューを作らない**。参照実行する `say_enqueue.sh` がサブモジュール内の `tmp/.say_queue/` を持つ | 所有 |
| 一時ファイル所有権 | PoC では入力テキストだけ docich が一時作成し、`say_enqueue.sh` が copy 後に削除 | 所有 |
| チャット投稿 | しない (PoC 既定)。`enqueue_chat_message` が source されるため、`OUTBOUND_CHAT_QUEUE_DIR` を無害な一時ディレクトリへ向ける | 内部でのみ |
| 本番配信 | `stream.mode="null"` 既定。PoC は音声再生・配信へ影響しない | 本番は `soren-runtime.service` が所有 |

## 4. 推奨インターフェース (`docich say`)

候補比較:

| 候補 | 評価 |
|---|---|
| `bin/docich say <game> ...` | ★ 推奨。既存 CLI の自然な拡張で、`--config`・ユーザー向けエラーハンドリングを再利用できる |
| `bin/docich tts <game> ...` | 可 (エイリアス)。ただし `say` は内部コマンド名に近く、ユーザーは発話/キュー再生の意味で捉えやすい |
| docich 内部 tts モジュール + CLI ラッパ | 実装方向それ自体。CLI を薄くし、検証済み argv/env 構築を `src/docich/tts.py` に置く |
| adapter として扱う | 不採用。TTS はゲームライフサイクル (observe/act) と無関係で、adapter の抽象を足しても価値が薄い |

### 4.1 `docich say` の契約 (PoC 実装方針)

```
docich say <game> -f <text_file> [options]
docich say <game> "読み上げるテキスト" [options]
```

実装:

- `src/docich/tts.py` に `TtsSpec`/`resolve` 相当の配列ベース argv/環境構築と、
  ゲーム名→`games/<name>/say_enqueue.sh` の安全解決を置く
- CLI は `say` を追加し、実装は `src/docich/tts.py` へ委譲する
- ゲーム名は `load_game` 経由で解決し、`games/` 内の実行スクリプトは **allowlist 固定**
  (`say_enqueue.sh` のみ。将来 `english_tts.sh` 等は明示追加)
- 入力テキストは `-f <path>` と `<text>` 直指定の両方を許し、`-f` はファイルを
  **docich の一時ディレクトリへコピー**、`<text>` は一時ファイルへ書き出してから
  渡す (元ファイル破壊防止・argv 肥大化回避)。両方の併用はエラー
- 相対パス解決:
  - サブモジュールルート = `repo_root/games/<name>`。`resolve()` で実在確認
  - スクリプトは `repo_root/games/<name>/say_enqueue.sh` の 1 つだけ
  - `cwd` はサブモジュールルート (soviet_now が `.env`・`lib/`・`tmp/.say_queue/` を相対参照するため)
- 環境:
  - `EXPLORE_MODE` を 1 にしない (音声合成・再生を止める用途は PoC では使わない)
  - `SAY_CONTEXT_LABEL="docich"` を既定
  - `SAY_AUDIO_DEVICE` / `PULSE_SINK` は docich の `audio.sink_name` に揃える (既定 `docich_sink`)
  - `DOCICH_CC_ENABLED=0` を既定 (字幕 socket 未作成なら false。明示有効化は将来)
  - `.env` 由来の VOICEVOX/Google 設定は `say_enqueue.sh` が自身で読み込む
- 再生なし・合成のみ: `--render-only` を渡す
- 実再生ガード: `--render-only` も `--dry-run` も指定せず実行する場合は
  `DOCICH_ALLOW_REAL_PLAYBACK=1` を要求する。無ければ TtsError で拒否
  (`src/docich/tts.py` の `run_tts` が検査)。理由は §4.2-8 / §5 のキュー分離
- 生成物: `-o <wav>` を指定すると render 結果をコピーし、`--wav-playlist` 時は
  bundle (playlist + captions) の扱いを文書化
- `--dry-run`: 実行せず argv/env/cwd を表示
- タイムアウト: 既定なし (say_enqueue 側の retry/backoff に任せる)。`--timeout` は将来
  `procs.run(timeout=...)` に対応可能だが、現実装は argv に渡さない
- キャンセル: Ctrl+C (SIGINT) で `procs.run` 経由の子プロセスへ送る。ロックは
  `say_enqueue.sh` の EXIT trap が解放
- 終了コード: 子プロセスの戻り値をそのまま返す (0=再生完了, 1=失敗, 2=引数エラー等)
- 既定動作: `stream.mode="null"` に依存せず、`--dry-run` も再生しない。ただし
  `-f`/テキスト指定がない場合はエラー、`--render-only` と `--play` の混在はエラー
- 本番配信へ影響しない: `PA_SINK` を `docich_sink` にするだけで既定 sink は変更しない
- セキュリティ: `shlex`/shell 結合を使わず argv 配列のみ。任意パス・任意コマンドを
  allowlist 外へ通さない

### 4.2 安全条件 (参照実行時)

1. `games/soviet_now` が submodule として実在し、`say_enqueue.sh` が実ファイルであること
2. `games/soviet_now` 内の相対参照はすべて cwd がサブモジュールルートのままであること
3. `say_enqueue.sh` に渡すテキストファイルは `-f` で指定し、docich が一時コピーする
4. `--wav-playlist` の playlist と captions は `games/soviet_now` 側が生成したものだけを対象にする
5. 本番チャット投稿を防ぐため、`OUTBOUND_CHAT_QUEUE_DIR` を無害な一時ディレクトリへ向ける
   (PoC 既定)。将来 `docich say --chat` を実装する場合は C2 と一緒に設計する
6. `DOCICH_CC_ENABLED=0` 既定。実再生する場合も broadcast/radio の queue や
   `soren-runtime.service` には触れない
7. 実 TTS や音声を鳴らさない検証は、`--dry-run` + unit tests で行う
8. **キュー分離 (2026-08-17 確定)**: `say_enqueue.sh` の `QUEUE_DIR="tmp/.say_queue"`
   は cwd (サブモジュールルート) 基準の固定パスで、`SAY_QUEUE_DIR` 等の上書き環境変数は
   存在しない (dcb2992 で確認)。このため docich からの**実再生は、本番 soviet_now
   ツリー (VM `/home/ubuntu/soren` / `soren-runtime.service`) とは別 checkout の
   サブモジュールでのみ許可**する。docich-integration の `games/soviet_now` は VM と
   物理的に別なので対象だが、同じツリー上で実再生して本番キューと衝突させる構成は禁止。
   実行側は `DOCICH_ALLOW_REAL_PLAYBACK=1` の明示で合意を示し、既定は `--dry-run` /
   `--render-only` に留める

## 5. 残余リスク・次段階

- `say_enqueue.sh` は `tmp/.say_queue/` をサブモジュールルートに作る (固定パス、上書き不可)。
  **キュー分離は §4.2-8 で確定済み**: 実再生は `DOCICH_ALLOW_REAL_PLAYBACK=1` +
  本番ツリーと別 checkout の 2 条件で許可し、既定は `--dry-run` / `--render-only`。
  実再生の初回検証 (VOICEVOX 合成 → 再生) は、本番を止めずに docich-integration の
  サブモジュール checkout 上で行う。
- `say_enqueue.sh` 内部の `lib/outbound_queue.sh` source は、`OUTBOUND_CHAT_QUEUE_DIR` を
  docich の一時ディレクトリへ向けることで無効化できる (チャット投稿なし)。
- 字幕を docich 側から有効化する場合は、`DOCICH_CC_SOCKET` を docich の FFmpeg socket に
  向ける設計を将来追加する。今回の PoC では無効のまま。
- C4 昇格時に `say_enqueue.sh` 本体を docich へ移す場合は、本稿 inventory を起点に
  責務分割 (合成 / キュー / 再生 / 字幕 / 話者運用) を先に設計する。
