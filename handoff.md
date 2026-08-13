# Soren Linux 配信・改善ループ 引き継ぎ

> 更新: 2026-08-13 18:40 JST
> 文書リポジトリ: `/Users/azumag/work/docich`（コミット管理）
> 実装リポジトリ: `azumag/soviet_now`
> この文書にストリームキー・OAuth token・秘密鍵・push target は書かない

## 1. 現在の目標（何をしようとしているのか）

1. **Issue #96 の 24 時間受入を完了する**。BGM 自動再起動修正後の soak が現在進行中で、2026-08-14 11:14 JST 頃に終了予定。合格後に残ゲート（オーバーレイ DOM 再確認、screenshot、サマリ整理、PR #97 CI/review 確認）を済ませて Issue を閉じる。
2. **ハーネス系を復活させる**。コメント応答・ラジオコーナー・改善 AI が動く状態を維持・改善する。ハーネスは codex に統一、モデルは deepseek 系（改善のみ pro、それ以外 flash）。
3. **配信オーバーレイを改善する**。改善中に improve daemon の出力が全面表示されゲームが見えなくなる問題を解消し、右サイドバーへ統合する（VM 反映・実機確認済み）。
4. **戦略改善ループを健全に回す**。改善 AI を deepseek-v4-pro に切り替え済み。停滞時は戦略自体を含むループを改善する監視も別途検討中。

## 2. VM 接続方法と設定

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

## 3. リポジトリ構成

- `azumag/soviet_now`: 実装本体（VM の `/home/ubuntu/soren` が実運用ソース）
- `azumag/docich`: 構成判断・移行計画・引き継ぎ文書のみ
- `/Users/azumag/work/docich/soren-phase1`: `soviet_now` の Git worktree（ブランチ `codex/soren-ffmpeg-direct`）。未 commit のオーバーレイ差分あり
- `/Users/azumag/work/docich/soren-harness`: 旧ローカルハーネスコピー（8/12 時点、VM と乖離注意）
- `/Users/azumag/work/docich/soren-voicevox-investigation`: 時事ニュース読み上げ調査（`HANDOFF.md` 参照）

**注意**: worktree と VM の `strategy/ai.sh` は大幅に乖離している（VM が codex ハーネス版、worktree は旧 multi-provider 版）。同期時は VM を基準にすること。

## 4. 現在の進捗状態

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

## 5. 24 時間受入（soak）の状態

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

## 6. codex CLI pro 対応（litellm）

### 経緯

codex CLI 0.147.0 と opencode.ai の `deepseek-v4-pro` は直接接続で 2 障害があった（web_search ツール reject、`OutputTextDelta` で `-o` 出力空）。litellm 変換経由で解決。

### 構成

- venv: `/home/ubuntu/litellm-venv`（litellm 1.96.0 + litellm-proxy-extras 0.4.81、fastapi 0.139.2 / uvicorn 0.52.1 / websockets 15.0.1 に固定）
- 設定: `/home/ubuntu/litellm.yaml`
  - `deepseek-v4-flash`: Responses 透過（ツール実行を維持）
  - `deepseek-v4-pro`: chat 変換（`use_chat_completions_api: true`、`-o` 出力修復）
- systemd: `soren-litellm.service`（port 4100、127.0.0.1、Restart=always、enabled）
- env: `/home/ubuntu/.config/soren-litellm.env`（`OPENCODE_GO_API_KEY` のみ、mode 600）
- codex config: `/home/ubuntu/.codex/config.toml`
  - `base_url = "http://127.0.0.1:4100/v1"`
  - `web_search = "disabled"`（pro の reject 回避）
  - `model_catalog_json = "/home/ubuntu/.codex/merged-models.json"`（pro/flash のメタデータ供給）
- モデル指定: `strategy/ai.sh` の `run_cmd` が `agent` を尊重（`codex_model="${agent:-${CODEX_MODEL:-deepseek-v4-flash}}"`）
- `.env`: `MODEL_IMPROVE=codex:deepseek-v4-pro` / `MODEL_FALLBACK_IMPROVE=codex:deepseek-v4-flash` / `RADIO_AGENTS=codex:deepseek-v4-flash` / `CODEX_MODEL=deepseek-v4-flash`

### 障害時

- `run_cmd` 先頭の health チェック（`http://127.0.0.1:4100/health`）が失敗すると rc=79 で即フォールバック（flash へ）
- litellm 停止 → `systemctl start soren-litellm`、復旧は自動（Restart=always）

## 7. strategy.py リファクタ

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

## 8. 配信オーバーレイ（現行と改善予定）

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

## 9. 既知の問題・注意点

- **VM 反映とリポジトリ同期は同時に行う（リポジトリルール、AGENTS.md 記載）**: VM（git 管理外）へ反映する変更は、同時に soviet_now の作業ブランチへコミット・push する。コミット前は「反映済み」と報告しない。乖離時は新しい側へ同期。
- **ローカル worktree / 調査ディレクトリは VM の正ではない**: `soren-harness`（soviet_now の worktree、ブランチ `codex/harness-codex`）は 2026-08-13 に **VM 基準で同期・push 済み**（commit `2adeecee6`、37 ファイル）。同期対象: 乖離 35 ファイル + 新規 `strategy_helpers/board_stats.py`・`docs/strategy_refactor_checklist.md`。`soren-voicevox-investigation` は調査用ローカル資料で VM 反映対象外。
- **注意: 同期後のテスト乖離**: `tests/test_escape_mechanisms.py`（6/2 版）はリファクタ版 `strategy.py`（8/13、hash `671db2f34b4c`）と不整合で 104 failures / 2 errors。VM 上でも同じ状態（既存乖離）。テスト更新は別途検討。
- **worktree と VM の乖離**: `strategy/ai.sh` や `strategy.py` は VM が本番最新。worktree 同期時は VM 基準
- **litellm が単一障害点**: flash 含む全 codex 呼び出しが port 4100 依存。health チェック + systemd 自動再起動でカバー
- **時事ニュース読み上げ**: 「時事ニュースコーナー」が「時事、ュースコ、です」になる問題は調査中（`soren-voicevox-investigation/HANDOFF.md`）。別スレッドで fable に相談中
- **TwiCa overlay**: 透過 iframe の合成問題はプロキシ + CSS 注入で対処済み。白画面・カード CSS 消えの再発時は `tmp/deploy-backups/` から復元
- **Oracle 無料ティア**: 現在 4 OCPU / 24 GB。トライアル終了前に PAYG 移行 or 2 OCPU / 12 GB への縮退が必要（Always Free は 2 OCPU / 12 GB）
- **`__pycache__`**: strategy_helpers 配下の pyc は helper 差分検知を汚染するため除外済み（eloop_improve.sh / sandbox.sh）。生成されたら削除してよい

## 10. 次の実行順序

1. **24h soak 完了確認**（2026-08-14 11:14 JST 頃）: `./direct_stream_soak.sh status` で passed を確認、monitor 終了、マージ overlay DOM・fresh 非付与・1280x720 screenshot 再確認
2. **Issue #96 完了処理**: summary、Twitch 720p30、BGM/SE、overlay、A/V 同期、relay 復旧 9 秒、OBS rollback 27 秒、PR #97 CI/review を整理して Issue を閉じる
3. **改善中オーバーレイのサイドバー化**: 完了（VM 反映・実機確認済み、§8）
4. **戦略改善ループ監視ジョブ**: `soren-1`（1 時間毎）が automations に無いため再作成 or 確認
5. **ハーネス系**: コメント応答・ラジオコーナー・改善 AI の稼働確認と継続改善

## 11. 参照

- [Issue #96](https://github.com/azumag/soviet_now/issues/96)
- [soviet_now #97](https://github.com/azumag/soviet_now/pull/97)（マージ済み・実配信ゲートは未完了のまま）
- [Oracle Free Tier](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm)
- `docs/oracle_arm_setup_guide.md`
- `docs/soren_linux_migration_plan.md`
- `soren-voicevox-investigation/HANDOFF.md`
- VM 側: `docs/strategy_refactor_checklist.md`
