# オーバーレイ共通部品化 — inventory と参照実行設計 (2026-08-17)

本稿は `docs/multi_repo_plan.md` §3 C3 の設計である。C2 (`common_parts_chat.md`) と
同じく「移動より先に参照」で、soviet_now の `generate_*_overlay` 一式を docich から
参照実行する PoC から始める。soviet_now は読み取り専用。

> **ステータス**: PoC + drawtext フック実装済み (2026-08-17)。
> `src/docich/overlay.py` + CLI `docich overlay <game> <kind>`、
> `stream.py` の drawtext フィルタ (`[stream] overlay_text_file`)。

## 1. 対象とスナップショット

| 項目 | 状態 |
|---|---|
| soviet_now submodule | `18fddd37` (PR #112 マージ後) |
| オーバーレイ一式 | 下記 6 ファイル + `lib/overlay_text.py` (約 2,900 行) |
| docich | stdlib-only、`:98` / `docich_sink` / `stream.mode="null"` が既定 |
| 本番境界 | 本番 sorengame は soviet_now (`:99`, `soren-runtime.service`) が所有。OBS は本番側 |

## 2. inventory 【確認済】

### 2.1 `generate_status_overlay.sh` (408 行)

| 項目 | 内容 |
|---|---|
| 主責務 | `status_dashboard.py` の出力を OBS HTML オーバーレイへ変換 (`tmp/state/status_overlay.html`) |
| モード | `once` / `watch [秒]` / `start [秒]` / `stop` / `ensure-obs [show|hide]` |
| 環境変数 | `STATUS_OVERLAY_HTML_FILE` / `STATUS_OVERLAY_WIDTH` / `STATUS_OVERLAY_HEIGHT` / `STATUS_OVERLAY_SOURCE` / `STATUS_OVERLAY_OBS_*` / `OBS_WEBSOCKET_*` / `OBS_DASHBOARD_SCENE` |
| 依存 | `eloop_lib.sh` source、`.env`、`python3`、`status_dashboard.py`、`lib/overlay_text.py` |
| OBS 連携 | `ensure-obs` で OBS の HTML ブラウザソースを show/hide する (WebSocket) |

### 2.2 `generate_show_status_overlay.sh` (267 行)

| 項目 | 内容 |
|---|---|
| 主責務 | `show_status.sh --once` の出力を OBS HTML オーバーレイへ変換 (`tmp/state/show_status_overlay.html`) |
| モード | `once` / `watch` / `start` / `stop` / `ensure-obs` |
| 環境変数 | `SHOW_STATUS_OVERLAY_HTML_FILE` / `SHOW_STATUS_OVERLAY_WIDTH` / `SHOW_STATUS_OVERLAY_HEIGHT` / `EVENT_OVERLAY_*` ほか |
| 依存 | `show_status.sh`、`python3`、`lib/overlay_text.py`、OBS WebSocket (ensure-obs 時) |

### 2.3 `generate_improve_overlay.sh` (205 行)

| 項目 | 内容 |
|---|---|
| 主責務 | improve 状態 (json) + AI ログ末尾を OBS HTML オーバーレイへ変換 |
| モード | `once` / `watch` |
| 環境変数 | `IMPROVE_OVERLAY_HTML_FILE` / `IMPROVE_STATE_FILE` / `IMPROVE_AI_LOG_FILE` / `IMPROVE_OVERLAY_REFRESH_SEC` / `IMPROVE_RUN_CMD_TIMEOUT_SEC` ほか |
| 依存 | `EXPLORE_MODE=1` なら即 exit (配信モード専用) |

### 2.4 `generate_event_overlay.py` (459 行)

| 項目 | 内容 |
|---|---|
| 主責務 | `overlay_events.jsonl` の toast イベント + 作業中インジケータを HTML へ変換 |
| 環境変数 | `EVENT_OVERLAY_EVENTS_FILE` / `EVENT_OVERLAY_HTML_FILE` / `EVENT_OVERLAY_KEEP_EVENTS` / `EVENT_OVERLAY_VISIBLE_SEC` / `EVENT_OVERLAY_STATE_BASE` ほか |
| 依存 | `python3` のみ。OBS 連携なし (HTML 生成のみ) |

### 2.5 `overlay_notify.sh` (65 行)

| 項目 | 内容 |
|---|---|
| 主責務 | toast イベントを `overlay_events.jsonl` へ追記し、`generate_event_overlay.py` を実行して HTML を再生成。`ensure-obs show` 相当 |
| 依存 | `core/config.sh` source、`generate_event_overlay.py`、OBS WebSocket (show 時) |

### 2.6 `generate_dashboard.sh` (880 行) + `dashboard_data.py` (691 行)

| 項目 | 内容 |
|---|---|
| 主責務 | 配信者用ダッシュボード (HTML) の生成。`status_dashboard.py` と同居 |
| 依存 | `python3`、多数の state ファイル |

## 3. 責務境界 (C3 設計)

| 責務 | docich | soviet_now |
|---|---|---|
| オーバーレイ HTML 生成の「正典」 | 参照実行のみ (将来 C3 で昇格候補) | `generate_*_overlay.sh` / `.py` |
| OBS への表示 | しない (PoC)。OBS は本番側 | 所有 (`ensure-obs` / WebSocket) |
| イベント追記 | 参照実行のみ | `overlay_notify.sh` |
| 配信への合成 (drawtext) | `stream.py` にフック追加は将来。PoC では生成物 (HTML) を一時ディレクトリへ出力するだけ | 所有 |

## 4. 推奨インターフェース (`docich overlay` 実装済み)

```
docich overlay <game> <kind> [--output PATH] [--dry-run]
kind: status | show_status | improve | event | notify
```

- `src/docich/overlay.py`: allowlist スクリプト + argv/env/cwd 構築
  - allowlist: `generate_status_overlay.sh` / `generate_show_status_overlay.sh` /
    `generate_improve_overlay.sh` / `generate_event_overlay.py` / `overlay_notify.sh`
  - cwd = サブモジュールルート (`eloop_lib.sh` の相対 source のため)
- 既定は `once` モード (HTML を 1 回生成)。`watch` は本番の OBS 運用に任せ、PoC では
  `once` + dry-run のみ
- 出力先は `--output` で指定し、指定が無ければ環境変数の既定値 (本番 `tmp/state/`)
  を使う。**PoC では `--output` を一時ディレクトリへ向けて本番 HTML を汚さない**
- 実実行ガード: `DOCICH_ALLOW_REAL_OVERLAY=1` が無ければ拒否 (OBS 連動 `ensure-obs` を
  含むため)。`--dry-run` は argv/env/cwd を表示
- セキュリティ: シェル結合なし、allowlist 外のパス/コマンドを通さない

## 5. 残余リスク・次段階

- HTML オーバーレイは OBS ブラウザソース前提。docich の FFmpeg drawtext フックへ載せる
  場合は、HTML → 画像 (またはプレーンテキスト抽出) の変換設計が必要 (次段階)
- **drawtext フック実装済み (2026-08-17)**: `stream.py` の `build_ffmpeg_cmd` が
  `[stream] overlay_text_file` (テキストファイル) を drawtext フィルタで配信に合成する。
  設定例:
  ```toml
  [stream]
  overlay_text_file = "run/status.txt"   # 空なら無効
  overlay_font = "sans"
  overlay_fontsize = 28
  overlay_x = "20"
  overlay_y = "20"
  ```
  - テキストファイルが無ければ fail-open (オーバーレイなしで配信継続)
  - パスに `:` `,` `'` `\` を含む場合は無効化 (drawtext の壊れ防止)
  - 字幕 (`docichcc`) と併用時は 1 つの `-vf` に連結する
  - `docich overlay <game> status --output run/` で生成した HTML の代わりに、
    `status_dashboard.py` 相当のテキスト出力を `run/status.txt` に置けば合成できる
- `generate_improve_overlay.sh` は `EXPLORE_MODE=1` で即 exit する。docich からの
  参照実行は `EXPLORE_MODE=0` のまま (既定) で行う
- `ensure-obs` は OBS WebSocket に触れるため、docich 側では許可しない (allowlist から
  モードを制限)。PoC の CLI は `once` のみ公開
- C4 昇格時は、HTML テンプレートと状態取得 (status_dashboard.py / show_status.sh) を
  分離して責務分割を設計する
