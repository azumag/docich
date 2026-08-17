# 引き継ぎ: 半熟英雄ペンディング → 共通部品化 (2026-08-16)

半熟英雄系の作業を**ユーザー判断で一旦ペンディング**し、次は**共通部品化**
(multi_repo_plan.md の C2 以降) を進める。この文書は、その作業を引き継ぐセッション
(または将来の自分) が、現在地・凍結状態・最初の一歩を迷わず把握するためのもの。

- 【確認済】= 一次情報 (実クローン・実コミット・実測) で裏取りした事実
- 実測日はすべて 2026-08-16
- 2026-08-17 更新: TTS 共通部品化の inventory と reference-run PoC を追加

---

## 1. 現在地スナップショット

| 項目 | 状態 |
|---|---|
| 作業ブランチ | `claude/docich-game-switching-lsom1e` = docich main (`e261c31`) + Phase 2/3 実装一式 + 字幕 drift 同期 (`2a29313`)。push 済み。**PR は未作成** (ユーザー指示があるまで作らない) |
| テスト | 字幕同期前の最終全体確認は stdlib unittest **371 件全緑**、`scripts/smoke_cli.sh` / `scripts/smoke_brain.sh` とも PASS。字幕同期後は `py_compile` と `tests/test_captions.py` **28 件全緑**。このコネクタセッションでは全体 371 件と smoke は再実行していない |
| submodule | `games/soviet_now` = `dcb2992` (本日 bump 済み) / `games/hanjuku-sfc-speedrun` = `5e98294` |
| TTS 設計 | `docs/common_parts_tts.md`: inventory + `docich say` 設計 (2026-08-17) |
| TTS PoC | `src/docich/tts.py` + CLI `say` + `tests/test_tts.py` (2026-08-17) |
| 監視 | 毎時 Routine が soviet_now main と docich main を監視 (multi_repo_plan.md §4 のプロトコル)。変化がなければ沈黙 |
| wiki | `wiki/` 原稿は最新。GitHub への発行はユーザーが `scripts/publish_wiki.sh` を実行した時点で反映 |

## 2. 半熟英雄の凍結状態 (ペンディング)

**実装・検証は完了済み**で、安全に凍結されている (`[agent] enabled = false` が既定のため、
マージ・起動しても本番には何も出ない)。

- 完了: brain 本体 (`brains/hanjuku/brain.py`)、設計書 (`docs/hanjuku_brain.md`)、
  E2E スモーク、実 LLM (claude-opus-5) での 1 サイクル動作確認 (RetroArch メニューを画像認識し
  fail-soft 判断)。watchdog / rotate / systemd 雛形 (Phase 3) も実装済み。
- 再開時にやること (すべて VM 側・ユーザー作業が起点):
  1. ROM を `games/roms/hanjuku-hero.sfc` に配置
  2. VM の `claude` CLI 認証確認 (`claude -p "ping"`)
  3. `config/games/hanjuku-hero.toml` の `[agent] enabled = true` → 実プレイ検証
- 再開の入口: `docs/hanjuku_brain.md` §6、wiki の 半熟英雄ページ。将来改善の種:
  卵落ち CSV の retrieval 注入 (`docs/hanjuku_brain.md` §3)、`ra-cmd SAVE_STATE` を絡めた復帰運用。

## 3. 共通部品化ミッション (これからやること)

方針の原典は multi_repo_plan.md §3「**移動より先に参照**」。docich からサブモジュール内の
スクリプトを参照実行する形で始め、ゲーム非依存が実証できた部品だけ docich へ昇格させる。
soviet_now への変更 (ラッパ化等) は **Codex の作業完了とユーザー合意の後** (C4)。

### 3.1 前提条件の現状 【確認済・実測】

C2 の前提「broadcast/ 系の変更が落ち着くこと」は**まだ成立していない**。部品領域ごとの
churn は大きく異なる:

| 領域 | 直近の変更 | 判定 |
|---|---|---|
| TTS (`say_enqueue.sh`, `google_tts.sh`, `coeiroink_tts.sh`) | **6週間変更なし** | 安定。次に着手 |
| オーバーレイ (`generate_*_overlay*`, `dashboard_data.py` — リポジトリ直下) | 6週間で3件 | ほぼ安定 |
| コメント応答 (`broadcast/comment*.sh`, chat 系) | **本日も変更あり** (英語コメント分類、rate-limit backoff 等) | 活発。設計のみ可 |
| ラジオ (`broadcast/radio_*`) | 直近3週間で複数 (deferred queue、caption 同期等) | 活発。設計のみ可 |
| 字幕 (soviet_now 側 `lib/closed_captions.py`) | 改善2件を docich 正典へ同期 | **完了 (`2a29313`)** |

### 3.2 完了: 字幕 drift の同期

字幕は既に docich へ昇格済みの共通部品 (`src/docich/captions.py` が正典) だが、Codex が
soviet_now 側の互換実装へ入れた以下の改善を同期した:

- `a9fcc4c0a` fix: remove closed caption chunk count cap
- `9fb403afe` fix: keep partial caption translations

反映コミット: `2a293138e914ae932b12cf0f4b3b06c3a4bde899`

- 翻訳配列が不足した場合は、順序付きの非空 prefix だけを字幕化し、後半は音声のみ継続
- 翻訳が余った場合は音声チャンク数まで切り詰め、空配列は従来どおり拒否
- 論理上の発話チャンク数から固定 32 件上限を撤廃
- FFmpeg の 32 制御スロットは維持し、`sequence % 32` で安全に再利用
- thinking、説明文、壊れた JSON、余計な schema key、非文字列、長すぎる字幕を拒否する境界は維持
- `tests/test_captions.py` へ部分翻訳・余剰翻訳・40チャンク・スロット循環の回帰テストを移植
- `docs/twitch_closed_captions.md` を新しい契約へ更新
- `soviet_now` には書き込んでいない

確認: `python3 -m py_compile` PASS、字幕単体テスト 28 件 PASS。GitHub に反映した source/test の
Blob SHA と、ローカル検証に使ったファイルの Git Blob SHA は一致。

### 3.3 次の実務タスク: TTS の共通部品化設計

1. **inventory を作る**。`games/soviet_now/say_enqueue.sh`、`google_tts.sh`、
   `coeiroink_tts.sh` を読み、ゲーム非依存度、churn、環境変数、外部コマンド、キュー、
   一時ファイル、再生経路、字幕連携、失敗時挙動を分類する。soviet_now は読むだけ。
2. **docich 側の口を設計する**。候補は `docich say <game>` または adapters と並ぶ
   `tts` モジュール。最初は `games/soviet_now/...` をサブモジュール相対パスで参照実行し、
   実装を複製・移動しない。
3. **参照実行 PoC の境界を決める**。既存の VOICEVOX / Google / COEIROINK 選択、FIFO、
   再生、caption hook のどこまでを既存スクリプトへ任せ、docich が何を正規化するかを文書化する。
4. 設計文書は `docs/common_parts_tts.md` を候補とする。実装へ進む前に、stdlib-only、
   `:98` / `docich_sink`、`stream.mode="null"` 既定、set-default-sink しない原則を再確認する。

### 3.4 その後の進め方

1. **C2 のインターフェース設計** (`docich chat <game>` / `docich radio <game>` 相当)。
   設計自体は churn と独立に進められる。**実装着手は broadcast/ の落ち着きを毎時監視
   Routine で確認してから** (目安: コメント/ラジオ系ファイルが2週間程度無変更)。
2. **C3 (オーバーレイ)** は C2 と同時期に判断 (ffmpeg drawtext フック + `generate_*` 生成物の接続)。
3. **C4 (昇格・soviet_now 側のラッパ化)** はユーザー合意ゲート。

### 3.5 完了: TTS 共通部品化 設計・参照実行 PoC (2026-08-17)

- `docs/common_parts_tts.md` に、`say_enqueue.sh` / `voicevox_tts.sh` / `google_tts.sh` /
  `coeiroink_tts.sh` / `english_tts.sh` / `bilingual_comment_tts.sh` /
  `lib/closed_captions.sh` / `lib/outbound_queue.sh` の inventory と、
  `bin/docich say <game>` を推奨口とする設計を記録。
- PoC は docich 側のみに実装:
  - `src/docich/tts.py`: ゲーム名→`games/<name>/say_enqueue.sh` の安全解決
    (repo 内パス制限・allowlist `say_enqueue.sh`・実行可能チェック)
  - CLI `say`: `-f/テキスト直指定`、`--rate`、`--pre-delay`、`--render-only -o`、
    `--wav-playlist --caption-chunks`、`--dry-run`。argv 配列のみで shell 結合しない
  - `config/games/sorengame.toml` に `submodule = "games/soviet_now"` を追加
  - 既定 env: `SAY_CONTEXT_LABEL=docich`、`DOCICH_CC_ENABLED=0`、
    `PULSE_SINK/SAY_AUDIO_DEVICE=docich_sink`、`OUTBOUND_CHAT_QUEUE_DIR=一時dir`
  - cwd はサブモジュールルート (soviet_now の相対参照をそのまま利用)
- `tests/test_tts.py` 24 件: 相対パス、submodule 欠落、不正 game/パス拒否、
  argv/env、dry-run、CLI エラー、テキスト直指定を追加。
- 検証: `python3 -m py_compile` PASS。`docich say sorengame --dry-run ...` PASS。
  内部テスト 284 件 (tts/cli/config/captions plan/actions/brains/state/supervise/
  watchdog/tmux/agent/adapters/native_captions) 全緑。
- 制約: このサンドボックスは AF_UNIX socket bind を拒否するため、
  `test_captions` の socket 3 件と `test_stream` の socket 1 件は変更前後とも
  同じ環境要因で失敗。実体は 398 件の全体テストでもその 4 件のみ。
  `scripts/smoke_cli.sh` / `scripts/smoke_brain.sh` は Xvfb/tmux 依存のため未実行。

### 3.6 追加: `docich say` テキスト直指定 (2026-08-17)

- `docich say <game> "テキスト"` をサポート。複数語はスペース結合し、一時ファイルへ
  書き出して `say_enqueue.sh` へ渡す。`-f` との併用と両方なしはエラー。
- 反映: `src/docich/tts.py` / `tests/test_tts.py` / `docs/common_parts_tts.md`。
- 検証: `tests/test_tts.py` 24 件全緑、tts+cli+config 84 件全緑、
  `docich say sorengame "こんにちは 世界" --pre-delay 0 --dry-run` PASS。

### 3.7 一旦区切り (2026-08-17) — 再開時の残課題

1. **実再生時のキュー分離を確定する**。`say_enqueue.sh` はサブモジュール内の
   `tmp/.say_queue/` を使うため、本番 soviet_now と同一ツリー上で実再生すると衝突する。
   soviet_now は読み取り専用なので、別 worktree/別 clone で参照するか、
   実行対象を dry-run / render-only に留めるかを `docs/common_parts_tts.md` に確定する。
   → **確定済み (2026-08-17)**: `DOCICH_ALLOW_REAL_PLAYBACK=1` ガードを
   `src/docich/tts.py` に追加し、実再生は本番ツリーと別 checkout でのみ許可。
   既定は `--dry-run` / `--render-only` (docs/common_parts_tts.md §4.2-8 / §5)。
2. **socket 環境で全体テストを再実行する**。AF_UNIX socket bind が使えるローカル環境か
   VM で、`python3 -m unittest` 全 398 件と `scripts/smoke_cli.sh` /
   `scripts/smoke_brain.sh` を通す。
   → **unittest 分は確定済み (2026-08-17)**: sandbox 外のローカル (socket bind 可) で
   405 件全緑を確認。smoke 2 本はローカルに `xdotool` がないため未実行のまま
   (VM / Ubuntu 環境で実行する)。実行時は「実配信中に実行しない」警告に従う。
   → **smoke 2 本も VM で PASS (2026-08-17)**: VM へ docich clone +
   `smoke_cli.sh` SMOKE PASS (obs/send/snap/stream/switch まで) +
   `smoke_brain.sh` PASS (fake brain 2回連続、retroarch 無しのため手順4は SKIP)。
   本番 soren_bridge (:99) とは別の :96 + 一時 config で実行。実配信は継続。
   → **submodule bump + ラッパー実動検証 (2026-08-17)**: docich の soviet_now を
   `a466960` (PR #110 ラッパー + #106/#107/#109 のマージ済み修正) へ bump して
   main へ反映済み。VM smoke clone (/home/ubuntu/docich) で、`voicevox_tts.sh
   --speakers` / `-o ... -f ...` / `say_enqueue.sh --render-only` (179,244 bytes WAV)
   のすべてが docich CLI 委譲で成功することを実機 VOICEVOX で確認。
3. **字幕有効化の設計**。`DOCICH_CC_ENABLED=1` は FFmpeg socket との接続確認が必要。
   PoC では既定無効のまま。
   → **設計済み (2026-08-17)**: `docich say --cc [--cc-socket PATH]` を追加。
   socket 準備を `caption_socket_ready` で検査し、準備済みのみ
   `DOCICH_CC_ENABLED=1` + `DOCICH_CC_SOCKET` を渡す。未準備は fail-open で
   stderr 警告、`--cc --render-only` はエラー。実効条件 (Linux + VOICEVOX +
   非 render-only) は say_enqueue.sh 側の判定に従う (docs/common_parts_tts.md §4.1 / §5)。
4. **C2 設計**。broadcast/ が 2 週間程度無変更になったら、同じ参照パターンで
   `docich chat <game>` / `docich radio <game>` の inventory と interface を設計する。
   → **inventory / interface 設計案を作成 (2026-08-17)**: `docs/common_parts_chat.md`。
   broadcast/ の直近変更は 2026-08-16 のため安定条件未達。実装は 2 週間無変更を
   確認してから。設計案は AI 実行なし・チャット投稿なし・`--dry-run` 検証の PoC に
   留める方針。
   → **PoC 実装済み (2026-08-17、安定条件はユーザー判断で無視)**:
   `src/docich/chat.py` + CLI `chat` / `radio`。allowlist 関数は
   `generate_comment_response` (comment.sh) と `_radio_generate_and_play`
   (radio_engine.sh)。固定ラッパ `broadcast_ref.sh` が `eloop_lib.sh` を source して
   実行。チャット投稿は `OUTBOUND_CHAT_QUEUE_DIR` で無効化、実実行は
   `DOCICH_ALLOW_REAL_COMMENT` / `DOCICH_ALLOW_REAL_RADIO` で明示許可。
   検証: tests/test_chat.py 16 件 + 全体 427 件全緑、CLI dry-run 確認済み。
   AI 実実行は未実施 (初回は別 checkout + dry-run から)。
   → **C3 オーバーレイ参照実行 PoC 実装 (2026-08-17)**: `docs/common_parts_overlay.md`
   (inventory + 設計) と `src/docich/overlay.py` + CLI `docich overlay <game> <kind>`
   (status / show_status / improve / event / notify)。`once` モードのみ公開し、
   出力先を一時ディレクトリへ向けて本番 HTML を汚さない。OBS 連動 (ensure-obs) は
   公開しない。実実行は `DOCICH_ALLOW_REAL_OVERLAY=1` で明示許可。
   検証: tests/test_overlay.py 8 件 + 全体 451 件全緑、CLI dry-run 確認済み。
   → **C3 drawtext フック実装 (2026-08-17)**: `stream.py` の `build_ffmpeg_cmd` に
   `[stream] overlay_text_file` の drawtext 合成を追加 (font/size/x/y 設定可)。
   ファイル無し・不安全パスは fail-open。字幕と併用時は 1 つの `-vf` に連結。
   検証: tests/test_stream.py の drawtext 4 件 + 全体 455 件全緑。
   → **C4 chat/radio 責務分割設計 + AI 出力ガード移植 (2026-08-17)**:
   `docs/common_parts_chat_c4.md` に責務分割 (C-S1〜C-S7) を設計。最優先の
   C-S2 (AI 出力ガード) を `src/docich/model_output_guard.py` として docich 正典へ
   移植し、CLI `docich ai-guard` を追加 (stdin → stdout 純フィルタ)。
   検証: tests/test_model_output_guard.py 9 件 + 全体 464 件全緑。
   C-S1 (AI ディスパッチ) 以降は C2 実実行の実証後に昇格判断。
   → **soviet_now 側のガード委譲ラッパを実装 (2026-08-17)**: `lib/ai_generate.sh` の
   `_ai_guard_model_output` が `DOCICH_BIN` (または PATH の docich) を使い
   `docich ai-guard` へ委譲し、無ければローカル `lib/model_output_guard.py` へ
   フォールバック (CI・探索モード互換)。ローカルコミット
   `93c26f395` (branch codex/ai-guard-docich-delegate)。docich と soviet_now の
   guard 実装は同一であることを diff で確認済み。**PR 作成・本番反映はユーザー承認待ち**。
5. **C4 昇格**。ユーザー合意後に、実証済みの TTS 部品だけ docich 正典へ移し、
   soviet_now 側を薄いラッパへ置換する。
   → **ユーザー合意済み (2026-08-17)**。責務分割設計を
   `docs/common_parts_tts_c4.md` に作成。S1 (VOICEVOX 合成) を docich 正典
   `src/docich/speech.py` として実装し、CLI `docich voicevox synth|speakers` を追加。
   キュー/再生 (S2/S3) は `say_enqueue.sh` 参照実行のまま。字幕 (S4) は既に正典。
   話者運用 (S5) は環境変数/引数で選択。
   検証: tests/test_speech.py 16 件 + 全体 443 件全緑、CLI dry-run 確認済み。
   残: soviet_now 側 `voicevox_tts.sh` の薄いラッパ化は Codex 作業完了後に別 PR
   (外部 push はユーザー承認が必要)。実機合成は VOICEVOX 起動環境で未実施。
   → **ラッパー PR #110 をマージ済み (2026-08-17)**: soviet_now main へ反映
   (`a466960`)、docich 側 submodule も bump 済み。**本番 VM (/home/ubuntu/soren) は
   まだ dcb2992 のまま**。本番へ適用する場合は、docich バイナリを VM で利用可能に
   (DOCICH_BIN 設定または PATH) した上で、ユーザー承認を得て submodule を更新する
   (サービス再起動を伴う可能性)。
   → **本番適用済み (2026-08-17)**: ユーザー承認を得て実施。
   - `voicevox_tts.sh` をラッパーへ置換 (旧実装は `.codex_deploy/backup-20260817-docich-wrapper/voicevox_tts.sh.old` に保存)
   - `.env` 末尾へ `DOCICH_BIN=/home/ubuntu/docich/bin/docich` を追記 (バックアップ `.env.bak` あり)
   - 検証: 本番ディレクトリで `voicevox_tts.sh -o ... -f ...` (180,268 bytes WAV) と
     `say_enqueue.sh --render-only` (153,132 bytes WAV) が成功。サービスは停止せず稼働継続
     (`start_all.sh --supervisor` / `soren_loop.sh` 稼働中、soren_bridge :99 維持)。
   → **本番 audio_worker 不整合を修復 (2026-08-17)**: ラッパー適用と無関係に、
   8/17 10:22 の supervisor 再起動時に audio_worker が多重起動し、pidfile と
   lock が不整合 (pid=4055251 死亡 / lock=4055251 死亡) のまま実効ワーカーが
   ゼロになり、10:20:22 以降 TTS 再生が停止。修復: 孤児 4055427 を TERM、
   stale pidfile/lock を掃除して supervisor に再起動させ、16:48:23 に
   audio_worker 2557348 が通常稼働に復帰。played.log は 16:49:54 から
   コメント再生を再開し、ラッパー経由の VOICEVOX 合成・字幕も動作確認済み。
   → **再発防止を実装・マージ・本番適用 (2026-08-17)**: soviet_now PR #111
   (audio_worker lock race fix) を main へマージ (`1350c073`) し、docich の
   submodule を bump (`18120bd`)。本番 `/home/ubuntu/soren` の audio_worker.sh を
   修正版へ置換 (バックアップ `.codex_deploy/backup-20260817-audio-worker-lock-race-fix/`)
   し、audio_worker を再起動 (18:28:20 新 PID 3552230)。再生継続を確認。
   対策内容: ロック所有者 pid が空の場合に 3 秒まで 0.2 秒間隔で再検証し、
   真に stale な場合のみ奪取する (起動競合時の worker ゼロ化を防止)。
   → **ラジオ原稿バックアップを実装・マージ・本番適用 (2026-08-17)**: soviet_now
   PR #112 を main へマージ (`18fddd37`)、docich の submodule bump (`caea793`)。
   本番 `/home/ubuntu/soren/broadcast/radio_state.sh` を修正版へ置換
   (バックアップ `.codex_deploy/backup-20260817-radio-script-backup/`)。
   再生完了時に原稿 `.txt` を `backups/radio_scripts/<YYYYMMDD>/<元名>.txt` へ
   自動コピー (`.history` / `.meta.json` も同梱)。git への自動コミットはしない
   (VM ローカルのファイル蓄積。GitHub へ残す場合は別途同期が必要)。
   動作確認: バックアップ関数を本番で直接実行して生成を確認後、テストファイルは削除済み。

### 3.5 進め方の作法

- 部品の inventory (ゲーム非依存度 × churn × 依存環境変数の分類表) を最初に作ると
  以後の判断が速い。soviet_now は読むだけ (書かない)。
- 設計文書は `docs/` に (例: `docs/common_parts_tts.md`)。実装は sonnet サブエージェントへ
  委任し、ファイル集合を分けて並行させる (今回の Phase 2/3 と同じやり方)。
- docich 本体は stdlib-only を維持。参照実行する shell 部品はサブモジュール相対パス
  `games/soviet_now/...` で呼ぶ (multi_repo_plan.md §1.1)。

## 4. 変わらない原則 (このプロジェクトの前提)

1. **役割分担**: 計画・設計・レビュー = このセッション (Fable)。実装 = sonnet サブエージェント。
   git 操作 (commit/push/merge) はセッション側で行う。
2. **soren 共存**: 本番 sorengame (soviet_now, :99) を壊さない。docich は :98 /
   `docich_sink` / `stream.mode="null"` 既定 / set-default-sink しない (architecture.md §0)。
3. **soviet_now は読み取り専用**: 改善は Codex が実施中。docich は main を監視して後追いする。
4. **ROM ポリシー**: 自己吸い出し品のみ。リポジトリにコミットしない。入手には関与しない。
5. **ブランチ規律**: `claude/docich-game-switching-lsom1e` で開発し `git push -u origin`。
   PR 作成はユーザー指示時のみ。ブランチの PR がマージ済みになったら、最新 main から
   同名ブランチを作り直して続きを積む。

## 5. 参照文書

| 文書 | 内容 |
|---|---|
| `docs/architecture.md` | 設計の一次情報 (共存原則 §0、アダプタ契約 §3、フェーズ状況 §10) |
| `docs/multi_repo_plan.md` | サブモジュール構成・C0〜C4 ロードマップ・監視プロトコル |
| `docs/hanjuku_brain.md` | 半熟英雄 brain の設計と検証状況 (再開時の正典) |
| `handoff.md` (リポジトリ直下) | Codex 側の運用引き継ぎ (Soren 本番の正体・残ゲート)。**Codex が所有** |
| `docs/twitch_closed_captions.md` | 字幕アーキテクチャ (同期済みの正典) |
| `wiki/` | 入り口・運用ハンドブック (発行は `scripts/publish_wiki.sh`) |
