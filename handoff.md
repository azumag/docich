# Soren / docich operational handoff

> Updated: 2026-08-15 JST
> Runtime source: [`azumag/soviet_now`](https://github.com/azumag/soviet_now)
> Multi-game and caption source: [`azumag/docich`](https://github.com/azumag/docich)
> Never place stream keys, tokens, API keys, prompts, generated speech, or raw
> model/improvement logs in this document.

## Current outcome

The live Soren broadcast is running on the Oracle VM with FFmpeg direct
streaming, Japanese VOICEVOX audio, and English native Twitch captions. The
game continues after GAME OVER and also continues while improvement work is in
progress. Radio/search/tool reasoning is filtered from on-air output. The say
path has bounded retry, foreground priority, and deferred radio recovery.

docich now aligns that Soren work with the reusable multi-game foundation:

- generic display/audio/stream lifecycle remains isolated on `:98`;
- sorengame remains owned by soviet_now production on `:99`;
- docich contains the canonical reusable caption planner, Unix IPC client,
  native FFmpeg filter, pinned build, proof scripts, configuration, and tests;
- the generic stream fails open to normal audio/video if caption capability is
  unavailable;
- `start_all.sh` is explicitly excluded from the browser-adapter contract.

## Production identity

| Item | Value |
|---|---|
| SSH identity | `ubuntu@129.146.54.105` (`soren-prod-vnic`) |
| Runtime directory | `/home/ubuntu/soren` |
| Service | system `soren-runtime.service` |
| Display | `:99` |
| Audio bus | `soren_null.monitor` |
| Caption socket | `/run/user/1001/docich/ffmpeg-cc.sock` |
| Custom FFmpeg | `/home/ubuntu/build/docich-cc-c13837ddf/bin/ffmpeg` |
| Caption opt-in | `DOCICH_CC_ENABLED=1` |

The external RTMP push target is root-managed outside the repository. Do not
read or reproduce it during routine verification.

## Merged implementation

| PR | Main result |
|---|---|
| [soviet_now #98](https://github.com/azumag/soviet_now/pull/98) | output guard, synchronized VOICEVOX/English CC, fail-open audio, MiniMax JSON/reasoning controls, say retry/fairness |
| [soviet_now #99](https://github.com/azumag/soviet_now/pull/99) | current GitHub/local/VM integration and production retry fixes |
| [soviet_now #100](https://github.com/azumag/soviet_now/pull/100) | improve daemon supervisor ownership and GNU/BSD status fixes |
| [soviet_now #101](https://github.com/azumag/soviet_now/pull/101) | durable failed-improvement batch snapshot and retry-lock restoration |
| [soviet_now #102](https://github.com/azumag/soviet_now/pull/102) | native-caption response protocol version validation |
| [soviet_now #103](https://github.com/azumag/soviet_now/pull/103) | exact plan schema and hard 32-column x 2-line validation |

Current soviet_now `main` after #103:
`b647179983bca7520ad3a53a83d218fa7a8aa36e`.

The local `main` at `/Volumes/satelite/work_satelite/soren` is fast-forwarded to
that commit. Its unrelated untracked files are intentionally preserved.

## Live evidence from this rollout

- Twitch captions required explicit player/broadcaster enablement and a stream
  restart. After that, captions displayed, changed, and cleared in the live
  stream while audio continued.
- FFmpeg ran at about 29.94 fps and the A/V sync probe passed after deployment.
- The service had one supervised improve daemon. The daemon was a direct child
  of the service main process; duplicate detached daemons were removed.
- The game counter advanced through multiple GAME OVER cycles, disproving the
  earlier stopped-at-GAME-OVER state.
- A real 100-game improvement attempt ended `failed_no_apply`. Gameplay kept
  advancing, but the retry lock/backoff were absent and the batch was lost.
  PR #101 was merged and hot-deployed before the next threshold. It preserves a
  separate batch snapshot and restores only a current-hash eligible retry.

The production cycle is intentionally `MIN_GAMES_BEFORE_IMPROVE=100`; the two
fresh-objective early-trigger flags observed during this rollout are disabled.
Do not lower the threshold simply to make a test fire. Verify the next natural
threshold with structured state.

## Safe verification

Use only aggregate/structured fields for routine monitoring:

- service active state and MainPID;
- daemon PID, parent PID, and cgroup;
- game count and `game_state.json.state`;
- accumulated count, current strategy-hash prefix, Russia/Soviet counts, and
  best type;
- improve status/PID/phase/progress/timestamps;
- presence and age of improve lock, retry snapshot, and backoff;
- worker count, FFmpeg fps, A/V probe, caption socket readiness, and audio queue
  health.

Do not dump `.env`, process arguments containing a stream key, raw model output,
raw improve logs, private radio text, or full production history.

## Deployment rule

The VM runtime is not a normal Git worktree. A production change is complete
only when both sides are synchronized:

1. compare target VM files with the last known repository baseline;
2. stop if the VM has a newer or unexplained difference;
3. commit, push, review, and merge the source change;
4. stage only the named files on the VM;
5. verify checksum and syntax before replacement;
6. keep a timestamped/exact backup and rollback trap;
7. restart only when necessary and authorized;
8. verify live service, game, stream, audio, and captions separately.

Local tests do not prove VM deployment, and VM deployment without a pushed
source commit is not a completed change.

## Test status and known debt

- The focused runtime/config/continuous-gameplay/retry suites for PR #101 pass
  (48 tests).
- docich's complete stdlib suite passes after this integration (244 tests),
  including Unix-socket protocol and tampered-plan rejection coverage.
- The large legacy soviet_now `tests.test_escape_mechanisms` suite is not a
  clean release gate for the current runtime: the last full run executed 383
  tests with 105 failures and 3 errors, mostly stale static fixtures and a
  missing `sorengame/build/index.html`. Focused new tests pass, but the legacy
  suite still needs a separate fixture-alignment effort.

## Remaining gates

1. Observe the next natural improvement threshold. Confirm retry snapshot,
   running PID/state, continued game advancement, and either a strategy apply or
   preserved lock/backoff after failure.
2. Keep the docich native filter and the soviet_now compatibility copy in sync
   until a versioned artifact dependency replaces the copy.
3. Keep docich Issue #1 linked to the merged implementation, README, Wiki, and
   production evidence so the administrative record matches the deployed work.
4. Treat moving sorengame production ownership into docich as a separate
   cutover requiring a game-only Soren entry point and rollback proof.

## Documentation

- [`docs/architecture.md`](docs/architecture.md)
- [`docs/games/sorengame.md`](docs/games/sorengame.md)
- [`docs/twitch_closed_captions.md`](docs/twitch_closed_captions.md)
- [`docs/soren_linux_migration_plan.md`](docs/soren_linux_migration_plan.md)
- [`native/ffmpeg/README.md`](native/ffmpeg/README.md)

## Soren strategy.py / improve-loop 運用ログ（並行ブランチより統合, 2026-08-11〜08-19 JST）

> 以下は同リポジトリの別ブランチ（`codex/soren-repo-handoff`）で並行更新されていた日本語運用ログを、
> main 追従（`git merge origin/main`）の際にそのまま統合したセクションです。上記の英語セクション
> （docich 側の多ゲーム／字幕統合の観点）とは別に、VM 上の soviet_now `strategy.py` 改善ループの
> 日次運用・rollback 判定・パラメータ調整を時系列で記録しています。統合前の元文書はこのセクション
> のトップレベル見出しでした（`# 1.` 〜 `# 14.`）。原文ママ、内容の要約・削除は行っていません。


> 更新: 2026-08-19 05:45 JST（クリップ修正rollback判明 + HIGH_TYPE_COVER_AVOID軸投入、末尾 §12 参照）
> 追記: 2026-08-19 16:50 JST（main追従 + RADIO_AGENTS/COMMENT_AGENTSへのOpenRouter free/AMD Token Factory追加、§14 参照）
> 文書リポジトリ: `/Users/azumag/work/docich`（コミット管理）
> 実装リポジトリ: `azumag/soviet_now`
> この文書にストリームキー・OAuth token・秘密鍵・push target は書かない

### 1. 現在の目標（何をしようとしているのか）

1. **Issue #96 の 24 時間受入を完了する**。BGM 自動再起動修正後の soak が現在進行中で、2026-08-14 11:14 JST 頃に終了予定。合格後に残ゲート（オーバーレイ DOM 再確認、screenshot、サマリ整理、PR #97 CI/review 確認）を済ませて Issue を閉じる。
2. **ハーネス系を復活させる**。コメント応答・ラジオコーナー・改善 AI が動く状態を維持・改善する。ハーネスは codex に統一、モデルは deepseek 系（改善のみ pro、それ以外 flash）。
3. **配信オーバーレイを改善する**。改善中に improve daemon の出力が全面表示されゲームが見えなくなる問題を解消し、右サイドバーへ統合する（VM 反映・実機確認済み）。
4. **戦略改善ループを健全に回す**。改善 AI を deepseek-v4-pro に切り替え済み。停滞時は戦略自体を含むループを改善する監視も別途検討中。

### 2. VM 接続方法と設定

```bash
ssh -i /Users/azumag/.ssh/id_rsa ubuntu@129.146.54.105
```

- 作業ディレクトリ: `/home/ubuntu/soren`（**git 管理外**。直接編集 + バックアップ運用）
- 配信: FFmpeg 直接配信（`SOREN_STREAM_BACKEND=ffmpeg`）、Xvfb `:99`、PulseAudio `soren_null`
- ゲーム: Chrome（kiosk）内 Unity WebGL、内部描画 576x324、CSS 表示 960x540、出力 1280x720
- 外部 RTMP 宛先: `/etc/soren-rtmp/push.conf`（root 管理・内容を読まない・記載しない）

### サービス

| サービス | 状態 | 説明 |
|---|---|---|
| `soren-runtime` | active | supervisor（start_all.sh）、OBS/FFmpeg 選択 |
| `soren-rtmp` | inactive | 旧 relay サービス（直接配信では nginx を別管理） |
| `soren-litellm` | active | codex CLI 用 litellm プロキシ（port 4100） |

### 主要プロセス

- `start_all.sh --supervisor` / `soren_loop.sh`（ゲームループ）
- `improve_daemon.sh`（改善ループ、ポーリング 30 秒）
- `workers/radio_worker.sh`（ラジオ生成・読み上げ）
- `node soviet_local.mjs`（tmux `soren_bridge` 内、ゲーム橋渡し・オーバーレイ配信）
- `lib/direct_stream.py run`（FFmpeg 配信）
- `litellm`（/home/ubuntu/litellm-venv）

### 3. リポジトリ構成

- `azumag/soviet_now`: 実装本体（VM の `/home/ubuntu/soren` が実運用ソース）
- `azumag/docich`: 構成判断・移行計画・引き継ぎ文書のみ
- `/Users/azumag/work/docich/soren-phase1`: `soviet_now` の Git worktree（ブランチ `codex/soren-ffmpeg-direct`）。未 commit のオーバーレイ差分あり
- `/Users/azumag/work/docich/soren-harness`: 旧ローカルハーネスコピー（8/12 時点、VM と乖離注意）
- `/Users/azumag/work/docich/soren-voicevox-investigation`: 時事ニュース読み上げ調査（`HANDOFF.md` 参照）

**注意**: worktree と VM の `strategy/ai.sh` は大幅に乖離している（VM が codex ハーネス版、worktree は旧 multi-provider 版）。同期時は VM を基準にすること。

### 4. 現在の進捗状態

### 完了済み

- PR #95（Linux OBS portability）と PR #97（FFmpeg 直接配信）は main へマージ済み
- 直接配信経路（x11grab・Pulse・libx264・relay・A/V probe・BGM/SE・overlay 3 出力）実機確認済み
- 24 時間 soak 以外の短時間受入・オーバーレイ（マージ方式・カラフル化）・音声（東北イタコ・多重読み上げ解消・FIFO キュー）は合格
- **codex CLI の pro 対応**（litellm 導入、後述 §6）
- **strategy.py リファクタ**（コメント圧縮 + board_stats 抽出、後述 §7）
- **`__pycache__` 除外**（helper 差分検知の汚染防止）
- **BGM 自動再起動・ヘルスチェック・クラッシュログ退避**（`external_game_audio.mjs` / `soviet_watchdog.sh` / `lib/bridge_recovery.sh`、テスト 8 件追加、VM 反映済み・実機 kill -9 → 2 秒自動復帰を確認）

### 進行中

- **24 時間 soak**: run_id `20260813-021426`、2026-08-14 11:14 JST 頃完了予定（§5）
- **改善ループ**: improve_daemon 稼働中、現状 idle。active_branch head = `671db2f34b4c`（リファクタ版）
- **soren-issue-96 heartbeat automation**: ACTIVE（10 分毎、24h soak 監視）

### 未着手・要確認

- **改善中オーバーレイのサイドバー化**: VM 反映・実機確認済み（2026-08-13、§8）
- **戦略改善ループ監視ジョブ（soren-1・1 時間毎）**: 作成を試みたが `~/.codex/automations/` に存在しない。再作成 or 確認が必要
- **Issue #96 の完了処理**: 24h soak 合格後のサマリ・ゲート確認（§10）

### 5. 24 時間受入（soak）の状態

### 前回 run（`20260811-222340`）は不合格

- 音声比率 96.9%（基準 99%）、drop 連続成長 32 回で不合格
- 原因: soviet_local（ゲームブリッジ）が 24 時間で 8 回クラッシュ。tmux kill-session が BGM（ffplay）も巻き添え kill。さらに旧設計は BGM を「警告ログのみで再起動しない」ため、15:35 のクラッシュ後 22 分間の完全無音が発生
- クラッシュ原因自体は OOM なし・ログ上書きで痕跡消失のため未特定（→ 修正でログ退避を追加）

### 修正（VM 反映済み・バックアップあり）

- `external_game_audio.mjs`: BGM 予期せぬ終了時に 2 秒後自動再起動（backoff 1→16 倍）+ 30 秒ごとのヘルスチェック。mute/停止/shutdown 中は再起動しない。`SOREN_GAME_BGM_RESTART_DELAY_MS` / `SOREN_GAME_BGM_HEALTH_INTERVAL_MS` で調整可
- `soviet_watchdog.sh` / `lib/bridge_recovery.sh`: relaunch 前に直前ログを `tmp/debug/soviet_local.log.<時刻>` へ退避（30 世代で prune）
- バックアップ: `external_game_audio.mjs.bak.bgm.*` / `soviet_watchdog.sh.bak.bgm.*` / `lib/bridge_recovery.sh.bak.bgm.*`

### 現在の run（`20260813-021426`）

- `./direct_stream_soak.sh start --duration 86400 --interval 60`、開始: 2026-08-13 11:14 JST / 完了予定: **2026-08-14 11:14 JST**
- 直近サンプル（14:29 JST・elapsed 3h15m）: ffmpeg fps 30.0 / speed 1.0 / drop 6515 / dup 6480（増分 約1/分・連続成長なし、最大連続 5 回）、ゲーム measuredFps 30.0 / canvas 576x324 / visible、音声 ok 100%（180 サンプル）、publisher=1、relay active、OBS inactive
- soviet_local: 3h17m 継続稼働（クラッシュなし）。BGM ffplay: 3h16m 継続再生。run 開始直後に BGM 自動再起動が 1 回発動し 2 秒で復帰（修正の実証）
- 監視コマンド: `./direct_stream_soak.sh status` / `./direct_stream_status.sh`
- 合格条件: 出力平均 29.5fps 以上、ゲーム平均 29.5fps 以上、speed p05 0.98 以上、drop/dup 各 1% 未満、音声 probe/非無音 99% 以上、publisher/relay 各 1、OBS inactive

### 6. codex CLI pro 対応（litellm）

### 経緯

codex CLI 0.147.0 と opencode.ai の `deepseek-v4-pro` は直接接続で 2 障害があった（web_search ツール reject、`OutputTextDelta` で `-o` 出力空）。litellm 変換経由で解決。

### 構成

- venv: `/home/ubuntu/litellm-venv`（litellm 1.96.0 + litellm-proxy-extras 0.4.81、fastapi 0.139.2 / uvicorn 0.52.1 / websockets 15.0.1 に固定）
- 設定: `/home/ubuntu/litellm.yaml`
  - `deepseek-v4-flash`: Responses 透過（ツール実行を維持）
  - `deepseek-v4-pro`: chat 変換（`use_chat_completions_api: true`、`-o` 出力修復）
- systemd: `soren-litellm.service`（port 4100、127.0.0.1、Restart=always、enabled）
- env: `/home/ubuntu/.config/soren-litellm.env`（`OPENCODE_GO_API_KEY` / `MINIMAX_API_KEY`、mode 600）
- codex config: `/home/ubuntu/.codex/config.toml`
  - `base_url = "http://127.0.0.1:4100/v1"`
  - `web_search = "disabled"`（pro の reject 回避）
  - `model_catalog_json = "/home/ubuntu/.codex/merged-models.json"`（pro/flash のメタデータ供給）
- モデル指定: `strategy/ai.sh` の `run_cmd` が `agent` を尊重（`codex_model="${agent:-${CODEX_MODEL:-deepseek-v4-flash}}"`）
- `.env`: `MODEL_IMPROVE=codex:deepseek-v4-pro` / `MODEL_FALLBACK_IMPROVE=codex:deepseek-v4-flash` / `RADIO_AGENTS=codex:deepseek-v4-flash` / `CODEX_MODEL=deepseek-v4-flash`

### MiniMax M3（公式 API）追加（2026-08-13）

- litellm に `minimax-m3` を追加: `model: openai/MiniMax-M3` / `api_base: https://api.minimax.io/v1` / `api_key: os.environ/MINIMAX_API_KEY` / `use_chat_completions_api: true`。litellm 1.96 の minimax ネイティブ provider は M2.1 まで対応のため、OpenAI 互換パススルーで追加
- キー: `.env` の既存 `MINIMAX_API_KEY`（公式 API で有効確認済み）を `/home/ubuntu/.config/soren-litellm.env` へ追加
- codex カタログ: `/home/ubuntu/.codex/merged-models.json` に `minimax-m3` を追加
- 検証: `curl http://127.0.0.1:4100/health` で 3 モデル healthy / unhealthy 0、`codex exec --model minimax-m3 "1+1=?"` が正常応答
- 既定の `MODEL_IMPROVE` は変更していない。改善に MiniMax 公式を使う場合は `MODEL_IMPROVE=codex:minimax-m3` へ変更
- バックアップ: `litellm.yaml.bak.20260813_220326` / `soren-litellm.env.bak.20260813_220326` / `merged-models.json.bak.20260813_220326`

### 障害時

- `run_cmd` 先頭の health チェック（`http://127.0.0.1:4100/health`）が失敗すると rc=79 で即フォールバック（flash へ）
- litellm 停止 → `systemctl start soren-litellm`、復旧は自動（Restart=always）

### 7. strategy.py リファクタ

### 変更内容（VM 本番反映済み）

- 2582 行 → 1973 行（コメント 1680 → 868 行）
- Phase 1: Change History 104 エントリを直近 5 件のタイトルへ圧縮、decide 内 vNNN 履歴詳細・refs を削減。安全不変条件・評価軸マップ（axis 1〜9.7）・phase 閾値は全て残置
- Phase 2: `strategy_helpers/board_stats.py` を新設（機械的集計 5 関数のみ）。評価軸・係数は decide() 内にフラット維持（hash 追跡・wildcard 摂動のため）
- decide hash: `182f9ad3954d` → `671db2f34b4c`
- 検証: 実ゲーム履歴 60 スナップショットで旧 decide と出力完全一致、`validate_strategy_with_helpers` パス

### 重要: active_branch pin との整合

decide hash を変える手動リファクタは `eloop.sh` の `repair_strategy_to_active_branch_head_if_needed()` が旧 head へ自動巻き戻す。**必ず**:

1. `strategy_versions/by_hash/<NEW_HASH>.py` へアーカイブ登録
2. `tmp/state/active_branch.json` の `head_hash` と `lineage` を更新
3. `repair_strategy_to_active_branch_head_if_needed` が no-op になることを確認

手順は `docs/strategy_refactor_checklist.md`（VM 側と worktree 側に配置）に明文化済み。

### 8. 配信オーバーレイ（現行と改善予定）

### 現行（VM 反映済み）

- 3 面構成: sidebar（右 320px）= SHOW-STATUS-G 32 行 + SHOW-STATUS 43 行のマージ表示、top（960x90）= サマリ 4 枚、bottom（960x90）= トースト 3 枚
- 改善中は `SOREN_DIRECT_IMPROVE_OVERLAY_ENABLED=1` により **improve 全画面オーバーレイがゲームを覆う**（これが課題）
- TwiCa overlay はローカルプロキシ経由（`SOREN_DIRECT_TWICA_OVERLAY_URL`、外部 X-Frame-Options 回避）
- 既存 show-status/show-status-g/overlay_notify と各 generator は read-only ソースとして不変（方針）

### 改善中オーバーレイのサイドバー化（VM 反映・実機確認済み 2026-08-13）

worktree `codex/soren-ffmpeg-direct` に未 commit 差分 5 ファイル:

- `lib/direct_overlay.mjs` / `lib/direct_broadcast_overlay.mjs` / `overlays/direct_broadcast_overlay.html` / テスト 2 本
- 内容: 改善中に全画面 improve surface を外し、improve_state + 改善ログ末尾をサイドバーの IMPROVE パネル（GAME を一時非表示、OPS+IMPROVE 表示）へ統合。wildcard ゲート・ANSI 除去・idle クリア・色分け対応
- テスト 22 件通過、deepseek v4 pro レビュー BLOCKER/HIGH/MEDIUM なし
- VM 反映済み: scp 3 ファイル + bridge 再起動（ユーザー承認済み）。現行版は `tmp/deploy-backups/474fe91ac/` に退避
- 実機確認: 改善中はサイドバーが IMPROVE + OPS に切替（GAME 非表示・改善ログ 24 行・状態テキスト表示）。ゲーム画面 960x540 は全面表示のまま、全画面 improve surface なし。idle 時は従来どおり G + STATUS

### 9. 既知の問題・注意点

- **VM 反映とリポジトリ同期は同時に行う（リポジトリルール、AGENTS.md 記載）**: VM（git 管理外）へ反映する変更は、同時に soviet_now の作業ブランチへコミット・push する。コミット前は「反映済み」と報告しない。乖離時は新しい側へ同期。
- **ローカル worktree / 調査ディレクトリは VM の正ではない**: `soren-harness`（soviet_now の worktree、ブランチ `codex/harness-codex`）は 2026-08-13 に **VM 基準で同期・push 済み**（commit `2adeecee6`、37 ファイル）。同期対象: 乖離 35 ファイル + 新規 `strategy_helpers/board_stats.py`・`docs/strategy_refactor_checklist.md`。`soren-voicevox-investigation` は調査用ローカル資料で VM 反映対象外。
- **注意: 同期後のテスト乖離**: `tests/test_escape_mechanisms.py`（6/2 版）はリファクタ版 `strategy.py`（8/13、hash `671db2f34b4c`）と不整合で 104 failures / 2 errors。VM 上でも同じ状態（既存乖離）。テスト更新は別途検討。
- **worktree と VM の乖離**: `strategy/ai.sh` や `strategy.py` は VM が本番最新。worktree 同期時は VM 基準
- **litellm が単一障害点**: flash 含む全 codex 呼び出しが port 4100 依存。health チェック + systemd 自動再起動でカバー
- **時事ニュース読み上げ**: 「時事ニュースコーナー」が「時事、ュースコ、です」になる問題は調査中（`soren-voicevox-investigation/HANDOFF.md`）。別スレッドで fable に相談中
- **TwiCa overlay**: 透過 iframe の合成問題はプロキシ + CSS 注入で対処済み。白画面・カード CSS 消えの再発時は `tmp/deploy-backups/` から復元
- **Oracle 無料ティア**: 現在 4 OCPU / 24 GB。トライアル終了前に PAYG 移行 or 2 OCPU / 12 GB への縮退が必要（Always Free は 2 OCPU / 12 GB）
- **`__pycache__`**: strategy_helpers 配下の pyc は helper 差分検知を汚染するため除外済み（eloop_improve.sh / sandbox.sh）。生成されたら削除してよい

### 10. 次の実行順序

1. **24h soak 完了確認**（2026-08-14 11:14 JST 頃）: `./direct_stream_soak.sh status` で passed を確認、monitor 終了、マージ overlay DOM・fresh 非付与・1280x720 screenshot 再確認
2. **Issue #96 完了処理**: summary、Twitch 720p30、BGM/SE、overlay、A/V 同期、relay 復旧 9 秒、OBS rollback 27 秒、PR #97 CI/review を整理して Issue を閉じる
3. **改善中オーバーレイのサイドバー化**: 完了（VM 反映・実機確認済み、§8）
4. **戦略改善ループ監視ジョブ**: `soren-1`（1 時間毎）が automations に無いため再作成 or 確認
5. **ハーネス系**: コメント応答・ラジオコーナー・改善 AI の稼働確認と継続改善

### 12. strategy.py リファクタ + 性能改善検討（2026-08-18）

### 依頼と進め方
「VM の strategy.py を修正して性能を上げる、適切にリファクタリングする」という依頼。
opus サブエージェントに設計を委任し、fable サブエージェントにも独立に相談（ユーザー指定）。
両者は独立に同じ2つの結論（Change History圧縮によるリファクタ／単独T13→2個目T13,T14誘導の
新axis追加）に収束した。実装後、別の opus サブエージェントに最終差分の自己レビューを依頼。

### 完了（VM本番反映済み・soviet_now main へ push 済み、commit `933c6d6`）
- **Patch A（リファクタ）のみ反映**。decide() の AST は完全不変
  （`extract_decide_hash.py` 出力は前後とも `83f5995eda15`）。
  - module-level Change History ブロック（191行）+ decide() 内の v番号付き履歴・
    rollback failure mode コメントを削除、docstring を実挙動（phase 到達不能バグ・
    height_mult 床値による平坦化）に合わせて書き換え。2025行→1375行
  - 削除したコメント（rollback failure mode の知見含む）は
    `docs/strategy_decide_history_archive_20260818.txt` に全文アーカイブ（VM・repo両方に配置）
  - 検証: ランダム合成入力1300件超で新旧 decide() 出力 完全一致（0 mismatch）、
    `validate_strategy_with_helpers` 通過、VM本番反映後に実ゲーム turn 30-43 の
    連続正常動作をライブ確認（Monitor tool でtail -f）
  - 反映先: VM `/home/ubuntu/soren/strategy.py`（バックアップ:
    `tmp/deploy-backups/patchA_20260818/strategy.py.bak`）+ `azumag/soviet_now` main
    直接コミット（PRなし、ユーザー指定）

### 見送り（NO-GO・重要な教訓）
- **性能改善案（`T13_FRONTIER_LANE_GUIDANCE`: 単独T13以上→2個目生成レーンへの
  誘導axis）は本番反映していない**。今朝11:09の実rollback postmortem
  （`単独T13が盤面にある局面で2個目のT13/T14を作る経路を優先する`という具体的指示）を
  根拠に、opus設計→自分で1300件超の検証→別opusによる最終レビュー、の3段階を経たが、
  **最終レビューが実ゲームログ20試合の実測データを使って設計の中核前提の破綻を発見**:
  - 幾何ヘルパー`contact_lane_xs`の`reach_mult*半径`が実際のピース半径
    （T11≈1.65, T12≈1.33）に対して盤面幅の約半分に達し、意図と逆に
    「anchorから遠ざかる」誘導が64.1%で発生
  - 30.2%で単独T13の真上（守るべき対象）へ誘導
  - 狙った行動（feederピースを接触点へ落とす）は`next_type`の実分布と
    `merge_grade=="NO"`ゲートの組み合わせで構造的にほぼ発火しえない
    （実測 A/B で決定が変わった33件中 0件が意図通り）
  - 整合するよう修正しても実ゲーム20試合中で意味のある発火はわずか2ターン
  - **教訓**: 合成データでの検証（fired=0/changed=0等のゲート確認）だけでは
    「発火した結果どこへ誘導されるか」の欠陥は検出できない。実データでの
    方向・頻度・大きさの検証が必須
  - 再設計は `analysis["results"]` の `merges`/`merge_result_*`
    （併合結果位置の直接情報）を使う方向で別セッションが着手中
    （`/Users/azumag/work/docich` 側の scratchpad にのみ存在、VM/repo未反映）

### 追加完了（2026-08-19・VM本番反映済み・soviet_now main へ push 済み、commit `4e664ce`）

前夜の性能改善検討（NO-GO）の副産物として見つかった「出力クリップ破損」を、別セッションが
実データ検証（`analyze_board.py`でanalysisを実ログから再構成し`decide()`を直接実行するリプレイ
基盤を構築）の末に発見。セッション制限で一度「failed」表示になったが実際には作業完了しており、
`PATCH_B_V2_PLAN.md`（579行）にまとまっていた内容を引き継いで対応した。

- **`decide()`末尾の出力クリップ修正（1行）**:
  `best_x = max(-0.991, min(4.362, best_x))` → `max(-3.0, min(3.0, best_x))`。
  コメント自身が「clip to drop range [-3.0, +3.0]」と明記し、`analyze_board.py`の
  `DROP_X_MIN/MAX`とも一致する値への復元（新規閾値の発明ではない）
- 実測根拠（2日分・独立した集計で再現）: 全決定の約11%がちょうど`x=-0.991`に丸められ、
  その位置で併合を宣言した決定の成功率が有意に低い（80.5% vs 他位置95.4%、Fisher検定p≈0.003）。
  盤面左1/3が事実上選択不能だった
- 検証: `extract_decide_hash.py`（`80e1c297a82a`→`77134db06ae2`）、`validate_strategy_with_helpers`、
  合成入力2000件（契約違反0件）、実ログ由来analysisでのdecide()直接実行（契約違反 17.4%→0%に解消、
  reason文字列は全て不変=スコアリング軸自体は無変更）。opus 3連続529エラーのため sonnet
  サブエージェントで独立レビューしGO判定（統計検定込みで自前実装により再現）
- VM反映手順: `docs/strategy_refactor_checklist.md`通り by_hash登録・`active_branch.json`更新
  （2026-08-19に同ファイルが初めて出現し repair 機構が有効化されたため、今回から手順が必須に
  なった）・`repair_strategy_to_active_branch_head_if_needed`のno-op確認まで実施
- 当初依頼の性能改善軸（単独T13→2個目T13/T14誘導）はNO-GO確定（`next`にT12が来ないため
  構造的に発火し得ない、実測: 直近20試合のT13生成16件は全て連鎖の副産物）。なお同時期に
  **VM自律改善ループ自身がロシア(type15)建国に初到達**（best_max_type=15, russia_count=1）
  しており、当該課題は自律ループの通常進化で前進していた

### 作業ファイルの所在（このセッションのローカルscratchpad、次回参照用）
`/private/tmp/claude-501/-Users-azumag-work-docich/314ad3cc-f42c-48c3-a727-60c8af94e279/scratchpad/vm-pull/`
に `OPUS_PLAN.md` / `FABLE_PLAN.md` / `SELF_REVIEW.md` / `PATCH_B_V2_PLAN.md`（579行、実リプレイ
検証基盤`v2work/`付き）/ `CLIP_FIX_REVIEW.md`（sonnetによる独立レビュー全文）。
セッション終了後は失われる可能性がある一時ディレクトリのため、再利用する場合は早めに永続化すること。

### 副産物として見つかった構造的所見（今回は未対応、単独サイクル推奨）
- phase判定のしきい値順序バグ（HIGH分岐が構造的に到達不能）。docstringにのみ明記、コードは温存
  （wildcard摂動の探索余地として残す方が安全、という2モデル共通の判断）
- axis 9.3 (AVOID_BLOCK_REACTIVE_PAIR): 長さガード条件が実データと合わず実質no-op
- `board_stats.has_reactive_for_type`/`has_near_for_type` が常にFalseを返し axis 9.6 が不発火
- FALLBACK_ALL_SUPPRESSED経路の同種クリップ破損（line 1403 `max(-1.612, min(0.862, x))`）。
  実測でdead code確認済み（piece_count最大51、候補数最小31）だが次サイクルでの修正推奨
- 詳細・実測値は上記 SELF_REVIEW.md / OPUS_PLAN.md / PATCH_B_V2_PLAN.md 付録参照

### 重要な訂正: クリップ修正はVM自律ループにより rollback された（2026-08-19 03:58/04:11）

前セクションで「反映済み」と報告した出力クリップ修正（hash `77134db06ae2`）は、
VMの自律改善ループ自身の実ゲーム評価で **`objective_regression+lost_ukraine_gate`**
（ウクライナ T13 到達率の後退）を理由に rollback された（n=14試合、comp自体は
ほぼ同等 9150.6 vs 9102.1 だったが建国ゲートが後退と判定）。13分後、rollback先
（`80e1c297a82a`、これもクリップ修正なし）も別のcomp系rollbackで、さらに古い
anchor `95b4310bee23` まで戻った。**現在の本番はクリップ修正を含んでいない。**

**教訓**: 「稼働確認できた」（クラッシュなし・契約違反なし・意図通りの挙動）ことと
「実ゲーム成績で成功する」ことは別物。事前にコード・合成データ・実ログ複数段階で
検証しても、rollback機構による実ゲーム評価でしか分からない回帰がありうる。
コストは1サイクル分（約14試合）に収まった＝rollback機構は設計通り機能した。
クリップ修正自体は再挑戦していない（ユーザー判断で見送り）。

### 追加完了（2026-08-19・VM本番反映済み・soviet_now main へ push 済み、commit `d8d015e`）

前サイクルで opus/fable 双方が「条件付きGO」と判定していた `HIGH_TYPE_COVER_AVOID` 軸
（高type併合レーンの被覆抑止）を、クリップ修正rollback後の現行系統(`95b4310bee23`)に
移植して投入した。

- **機能**: 非併合ドロップに限り、上が空いている(併合レーンとして生きている) type>=10
  ピースの真上への着地を-200で抑止。next_typeと同type・その1つ下のtype(N-1)は除外
  （`improve_strategy.md`「typeNの上にtypeN-1をのせるのはいい」に準拠）。
  ロシア(type15)在盤時は実測データ不足のため不発火（v701と同じガード）
- 根拠: 現行系統の実ログ9試合756ターンで、露出高typeの10ターン以内併合率37.5%、
  被覆時15.1%。被覆原因の89.7%が戦略自身のドロップ。DIRECT/NEAR/DEADLINE_GUARDの
  選択は完全不変、新規デッドライン超過0、契約違反0
- 検証プロセス: opus/fableが独立に設計・実データ検証（2案が収束）→現行系統への
  移植→opusサブエージェントによる最終レビュー（GO with fixes、3点修正: コード内
  バージョン番号削除・ロシアフェーズガード追加・二重減点コメント是正）
- hash: `95b4310bee23` → `1888e7d7ed65`
- **正直な限界**: 得点・comp・建国率の改善そのものは実証していない。決定変化率が
  maxtype10-11局面で30.5%と全体平均(12.6%)より高く、**前回rollbackの原因
  （ウクライナT13ゲート）に隣接する領域**。この実装（N-1除外あり）はopus版・
  fable版のどちらとも異なる第三の変種で、この組み合わせでのA/Bは今回が初めて。
  rollback機構による12試合窓での判定に委ねる（ユーザー承認済み・係数-200のまま投入）
- **リポジトリ同期の訂正**: 前回コミット(`4e664ce`)はVM側で既にrollback済みの
  クリップ修正版を反映したままだったため、今回VMの実際の現状（95b4310bee23系統）
  を正としてリポジトリを同期し直した

### 追記（2026-08-19 13:17 JST）: HIGH_TYPE_COVER_AVOID は12試合窓を通過し anchor 昇格

デプロイから約7時間後、`active_branch.json` の `anchor_hash` が `1888e7d7ed65`
（本軸を含む版）に更新されているのを確認。`logs/change_log.txt` に本ハッシュへの
rollback記録は無い。**クリップ修正（最初の評価窓で即rollback）とは対照的に、
実ゲーム評価を通過して新しい最良戦略として採用された**（comp=9277.51, n=12,
best_max_type=15, russia_count=1）。現在は `e5b671c8d352` という新しい探索
ブランチ（depth=1）がこのanchorを土台に自律ループの通常サイクルを継続中。
得点/comp面での「改善」を統計的に主張できるほどのサンプル数ではないが、
少なくとも「悪化ではない」ことをrollback機構自身が判定した。

### 13. 改善ループ評価窓の整合（ROLLING_SCORE_KEEP, 2026-08-19）

ユーザー指摘: 「改善ループが100試合分（`MIN_GAMES_BEFORE_IMPROVE=100`）なのに、
戦略スコア評価が20試合になっているのはおかしい。改善ループ分に合わせるべきでは」。

**調査結果（実測）**: `strategy/regression.sh`の`update_rolling_scores()`が
`tmp/state/rolling_scores.json`の各hashの`scores`配列を毎回`ROLLING_SCORE_KEEP`
（デフォルト20、`.env`に未設定＝常にデフォルト適用）で切り詰めていた。この配列から
`comp = 0.55*p50 + 0.30*p25 + 0.15*lcb`（rollback判定・anchor昇格ランキングの
実質的な唯一の評価指標）を算出しているため、`MIN_GAMES_BEFORE_IMPROVE=100`で
100試合分辛抱して蓄積しても、評価は直近20試合分しか見ていなかった。
さらに調査で、`strategy/improve.sh:2309`に**別変数**`CURRENT_RUN_SCORE_KEEP`
（同じくデフォルト20）があり、`check_regression`が優先的に参照する「現戦略側」
metrics(`tmp/state/current_strategy_run.json`)はこちらに支配されることが判明。
片方だけ変更するとn非対称でlcb項にバイアスが生じるため、両方同時変更が必要。

**設計根拠**（opusサブエージェント、本番スコアの実データでブートストラップ
シミュレーション実施）: rollback閾値`REGRESSION_MIN_COMP_GAP=1000`は、n=20では
comp差の標準誤差538に対し1.3σでしかなく、同一実力の戦略同士でも約18.2%の頻度で
誤って閾値超過が観測される計算。n=100なら誤判定率0.2%まで低下し、閾値が
意図通りの「有意差フィルタ」として機能する。

**適用した変更**: VM `.env` に2行追加（コード変更なし、`strategy.py`/decide()の
hashには一切影響しない。`strategy.py`はos.environ/getenvを1つも参照しないことを
実測確認済み）:
```
ROLLING_SCORE_KEEP=100
CURRENT_RUN_SCORE_KEEP=100
```
`.env`は毎試合`soren_loop.sh`・`improve_daemon.sh`双方から再読込される実装のため
再起動不要（実測確認: デプロイ直後の次ゲームからrolling/current_run双方の
`scores`配列長がn=20→21→22→23→24とロックステップで伸長、非対称なし）。

**変更不要と判断したもの**（ユーザーも早期粛清はOKと明言）: `MIN_GAMES_BEFORE_REGRESSION=12`,
`EARLY_OBJECTIVE_REGRESSION_MIN_GAMES=4`等の早期粛清フロア値、`HOT_STREAK_ROLLING_KEEP=200`
（コード内`max(normal_keep, hot_keep)`ガードにより無害と実コードで確認）。

**残存リスク（現状は無害、将来のtoggle再有効化時に要対処と記録）**:
1. `best_strategy_anchor.json`のanchor側comp/p50/p25/lcb/nは**昇格時点のスナップショットで
   固定**（現在n=12のまま）。current側のnが100に伸びるにつれ、lcb項の差で
   comp換算約85点current側に有利なバイアスが新規に生じる（rollbackしにくくなる方向）。
   `REGRESSION_MIN_BREACH_COUNT=2`（comp単独では発火しない）により実害は限定的。
   次にanchorが更新されれば対称性は自然回復するため、今すぐの対処は不要。
2. `_recent_archives`の保持上限がハードコード（rolling側25件・current_run側50件、
   さらに`infra/cleanup.sh`がgame_history実体を直近13試合しか残さない）で、
   score窓を100に広げても連動していない。窓が広がるほど`nation_progress()`が
   "no-archive"の0埋めを返す比率が増える。ただし影響を受けるはずの4トグル
   （`STAGE_ACHIEVEMENT_REGRESSION_ENABLED`, `EARLY_OBJECTIVE_REGRESSION_ENABLED`,
   `CURRENT_RUN_FRESH_OBJECTIVE_REGRESSION_ENABLED`, `EARLY_COMP_TOP_GAP_ENABLED`）は
   全て`.env`で`=0`（無効）であることを実測確認済みのため、**現状は不発**。
   これらのトグルを将来再有効化する前に、必ずprogress窓とscore窓の整合を
   先に直すこと（`strategy/improve.sh:2277`の`seed_scores = scores[-20:]`リテラルも
   同じ課題としてセットで対応。単独修正すると`recent_archives`上限との不整合で
   かえって悪化するため、今回は据え置き）。

検証はopus 2段階（設計レビュー→適用後の自己レビュー）＋自分でのVM実測（n実測、
strategy.pyのenv非依存確認、無効トグル4件の実測確認）で行った。

### 14. main追従 + AIハーネスのフォールバックチェーン拡張（2026-08-19、Claude Codeセッション）

#### main追従（このリポジトリ docich 自体）
`codex/soren-repo-handoff` ブランチが `origin/main` から分岐後に大きく乖離（main側は
`src/docich/` 本体・ゲームsubmodule・docsを多数追加、本ブランチは本 handoff.md の
Soren運用ログのみ拡張）していたため、`git merge origin/main` で追従した。
`handoff.md` のみ衝突（main側が2026-08-15に英語のdocich多ゲーム/字幕向けhandoffへ
全面書き換えしていたため）。ユーザー指示によりmain版をベースとして採用し、本ブランチの
日本語Soren運用ログ（この節を含む全体）は「Soren strategy.py / improve-loop 運用ログ」
という節としてそのまま追記保持した（内容の要約・削除なし）。他ファイルは無衝突。
マージコミット後、**まだ push していない**。

#### OpenRouter free / AMD Token Factory を RADIO_AGENTS・COMMENT_AGENTS に追加
VM `soren-litellm`（port 4100）の litellm.yaml には `openrouter/free` モデルが
以前から定義済みだったが `OPENROUTER_API_KEY` 未設定で常に失敗していた
（`/health` で `Missing credentials` を実測確認）。AMD Token Factory は未定義だった。

- **OpenRouter**: ユーザー提供の新規キーを VM `/home/ubuntu/.config/soren-litellm.env` の
  `OPENROUTER_API_KEY` に設定。モデルIDは `openrouter/free`（OpenRouter公式の
  「無料モデルをランダムに選ぶルーター」、`GET /api/v1/models` で実在確認済み。
  廃止/新規モデルへの追従が自動な代わりに、どの実モデルが応答するかは毎回変わる）
- **AMD Token Factory**: ローカル codex-router (`~/.codex/codex-router/amd-token-factory-api-key.secret`)
  のキーをそのまま再利用（ユーザー指示）。VM `soren-litellm.env` に `AMD_API_KEY` として追加、
  litellm.yaml に `amd-token-factory-deepseek-v4-flash`
  （`model: openai/DeepSeek-V4-Flash`, `api_base: https://developer.amd.com.cn/radeon/api/v1`）
  を新規追加
- `soren/.env` の `RADIO_AGENTS` と `COMMENT_AGENTS` 両方に
  `codex:amd-token-factory-deepseek-v4-flash` を `codex:openrouter/free` の直後に挿入
  （`MODEL_IMPROVE`/`MODEL_FALLBACK_IMPROVE` は意図的な `disabled` のため変更していない）
- `soren-litellm` を再起動し `curl :4100/health` で実測: `openrouter/free` と
  AMD の `DeepSeek-V4-Flash` はともに healthy（実際の completion 呼び出しで確認）
- バックアップ: VM `~/tmp/deploy-backups/fallback_chain_20260819_164749/`
  （`litellm.yaml.bak` / `soren-litellm.env.bak` / `soren.env.bak`）
- **未確認・要注意**: ユーザーは当初「AMD Token Factoryは本日分のクォータを使い切った」と
  申告していたが、投入直後の `/health` 実測では AMD (`DeepSeek-V4-Flash`) は healthy
  （実際に応答した）だった。ローカル codex-router 側の `rate-limits.json` に記録されていた
  cooldown（`retryAt` 2026-08-19T06:01 UTC）は本確認時点で既に経過していたため、日次上限と
  この一時的レート制限は別物である可能性が高い。今回の投入順序では `openrouter/free` が
  AMD より先に試行されるため、通常運用でAMDまで到達するのは openrouter/free 失敗時のみ。
  実際にAMDへ到達して課金/消費が発生するかは今後の実運用ログで要確認
- 秘密鍵の値そのものはこの文書はもちろんVM外にも一切記録していない

#### 追記: `.env` は自動リロードされない。反映には reload signal が必須（実測で判明）
`.env` 更新直後、`radio_worker.sh`/`chat_worker.sh` は**自動では反映されなかった**
（本節冒頭の「§6 `.env` は毎試合再読込される」という記載は `soren_loop.sh`/`improve_daemon.sh`
（試合ごとにファイルを再読込する設計）についての記述であり、`radio_worker.sh`/`chat_worker.sh`
には当てはまらない。両者は起動時に一度 `.env` を `set -a; . ./.env; set +a` するだけで、
以後は親プロセスの環境変数がそのまま子プロセス（コーナー生成のたび fork される）に
継承され続ける）。

- 実測: `.env` 編集後、`RADIO_AGENTS` の peak-hours ログ（`peak hours → agents=...`）は
  複数回（17:00:20〜17:01:54）にわたり**旧リスト（amd無し）のまま**だった
- 両ワーカーとも `SIGHUP`/`SIGUSR1`/`SIGUSR2` で `.env` 再読込する reload ハンドラを持つ
  （`_request_reload` → `_reload_runtime`、ログに `reload requested`/`reload complete` が出る）。
  **ただし signal は worker の最上位プロセス（`start_all.sh --supervisor` の直接の子、
  例: 今回は radio_worker.sh の PID 3802950）に送る必要がある**。長時間稼働 VM では PID が
  一巡している場合があり、`pgrep -f ... | sort -n | head -1`（最小PID）は誤ったプロセス
  （実は新しく fork された子プロセス）を拾うことがあるため、`ps -o pid,ppid,lstart,cmd` で
  `ppid` が supervisor（`start_all.sh --supervisor` の PID）であることを確認してから送ること
- `chat_worker.sh`（COMMENT_AGENTS）は reload signal を送らなくても次のコメント生成時点で
  新しいリストを使っていた（13:32→17:03 の呼び出しで自然反映、reload ログなし）。
  仕組みは未調査だが、radio 側と挙動が異なる点として記録しておく
- radio 側は `kill -USR1 3802950`（正しい最上位PID）で `reload requested`/`reload complete`
  をログで確認。その後 17:11:13 に `[RADIO:theme] codex:deepseek-v4-flash OK` という実際の
  生成成功を確認したが、`radio_worker.log` は**勝者のプロバイダ名しかログに出さず**
  （`peak hours →` 行は候補順序が実際に入れ替わった時だけ、かつ現在 UTC 06:00-10:00 の
  peak帯であるにも関わらずこの呼び出し前には出ていない＝base順序のまま試行された可能性）、
  amd-token-factory が候補として実際に試行されたか（4番目の候補として失敗したのか、
  そもそも到達していないのか）は本セッション内では**直接確認できなかった**。
  reload完了ログと COMMENT_AGENTS 側の同一メカニズムでの実証を踏まえれば有効になっている
  可能性は高いが、次に触るときは候補ごとの試行ログ（あれば）か、意図的に上位候補を一時的に
  無効化してAMDまで到達させる等で直接確認すること
- **今後の教訓**: soren の `.env` 変更は「ファイルを書き換えた」＝「反映された」ではない。
  対象プロセスの env 読み込み方式（毎回再読込 / 起動時一度のみ）を確認し、後者なら
  最上位PIDへ reload signal を送るか再起動するまでは変更は effectiveでない

#### リポジトリルール: handoff.md の運用を明文化
ユーザー指示により、本リポジトリ直下に `AGENTS.md`（+ `CLAUDE.md` からの参照）を新設し、
「作業再開時は handoff.md を読む」「一段落したら handoff.md を更新する」を明文化した
（`/handoff` スキルを使う運用、詳細は当該ファイル参照）。

### 15. 参照

- [Issue #96](https://github.com/azumag/soviet_now/issues/96)
- [soviet_now #97](https://github.com/azumag/soviet_now/pull/97)（マージ済み・実配信ゲートは未完了のまま）
- [Oracle Free Tier](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm)
- `docs/oracle_arm_setup_guide.md`
- `docs/soren_linux_migration_plan.md`
- `soren-voicevox-investigation/HANDOFF.md`
- VM 側: `docs/strategy_refactor_checklist.md`
