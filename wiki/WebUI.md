# Web UI (モデルチェーン / バックオフ管理)

soviet_now の「用途別モデルチェーン (AI_COMMON_AGENTS / RADIO_AGENTS など)」と
「モデル別バックオフ設定 (AI_BACKOFF_SEC_ITEMS / AI_AGENT_BACKOFF_SEC)」「ピーク時間帯
(PEAK_HOURS_*)」をブラウザから操作する管理 UI。`docich webui` コマンドで起動し、
Tailscale (tailnet) 経由で安全に公開する想定の機能である。

実装は `src/docich/webui.py` (Python 標準ライブラリのみ。`pip` 依存なし)。
フロントエンドは http.server + vanilla JS の単一ページ (SPA)。

## できること

- **Chains**: チェーン 6 種の表示・編集 (先頭が最優先、失敗時に次へフォールバック)
  - `AI_COMMON_AGENTS` (共通原典) / `MODEL_IMPROVE_LIST` / `RADIO_AGENTS` /
    `RADIO_PREPASS_AGENTS` / `COMMENT_AGENTS` / `COMMENT_TRANSLATION_AGENTS`
- **Backoff**: モデル別バックオフ設定 (`AI_BACKOFF_SEC_ITEMS`, `AI_AGENT_BACKOFF_SEC`,
  `AI_BACKOFF_FAILURE_SEC`) の編集と、稼働中 backoff ファイル
  (`tmp/state/ai_backoff/<sanitized>`) の残り時間表示・個別クリア・全クリア
  - `AI_BACKOFF_FAILURE_SEC` は PR #125 (backoff type split) で新設された「一過性の
    プロバイダ/CLI 失敗 (rc≠0) 用の短いバックオフ」(既定 300 秒)。レート制限/429 のみ
    モデル別の長バックオフ (`AI_BACKOFF_SEC_ITEMS`) に留め、無料枠が 1 日級に
    パークされないようにする
- **Peak**: ピーク時間帯設定 (`PEAK_HOURS_WINDOWS` / `TZ` / `PRIORITY_AGENT` /
  `AGENT_PREFERENCE` / `AGENT_SWAP_ENABLED` / `QUEUE_GATE_ENABLED`)
- **Stats**: `tmp/state/ai_stats/<YYYYMMDD>.jsonl` の試行/成功/失敗を直近 N 日分グラフ表示
- **Health**: 稼働中の worker (radio_worker / chat_worker) へ reload 信号 (SIGUSR1) を送信

## 起動方法

```bash
bin/docich webui                          # 127.0.0.1:8787 で起動 (soren_root は自動検出)
bin/docich webui --dry-run                # 起動せず設定解決結果だけ表示
bin/docich webui --soren-root /path/to/soren   # Soren ルートを明示
bin/docich webui --read-only              # 閲覧専用 (設定変更・backoff 操作・reload 不可)
bin/docich webui --port 9000              # ポート変更
```

systemd --user で常駐させる場合 (雛形 `scripts/systemd/docich-webui.service`):

```bash
sed "s|__DOCICH_ROOT__|$(pwd)|g" scripts/systemd/docich-webui.service \
  > ~/.config/systemd/user/docich-webui.service
systemctl --user daemon-reload
systemctl --user enable --now docich-webui.service
```

**重要**: 本番 VM で docich のサブモジュール (games/soviet_now) とは別に
`/home/ubuntu/soren` を運用している場合、`--soren-root /home/ubuntu/soren` を必ず明示する
こと。省略すると auto-discover が docich 側のサブモジュールを指してしまう。

## Tailscale での公開

```bash
sudo tailscale serve --bg --https=443 http://127.0.0.1:8787
```

- 公開 URL: `https://<hostname>.<tailnet>.ts.net/` (tailnet 内のみ)
- **tailnet 側で serve が未承認だと拒否される**: 管理画面
  `https://login.tailscale.com/f/serve?node=...` で一度有効化が必要。
- serve 設定の変更には root 権限が必要 (`sudo tailscale serve`)。回避するなら
  `sudo tailscale set --operator=$USER` を一度実行する。
- 停止: `sudo tailscale serve --https=443 off`

## 設定 (`config/docich.toml`)

```toml
[webui]
bind = "127.0.0.1"        # Tailscale serve で公開する想定 (非loopback+writable+token未設定は起動時error)
port = 8787
soren_root = ""           # 空なら games/soviet_now を自動検出
token = ""                # 空なら Tailscale ACL のみ (設定時 8文字以上)
token_env = "DOCICH_WEBUI_TOKEN"   # token の上書き環境変数名
allow_cors = false        # 別オリジン API 呼び出しを許可 (開発用)
read_only = false         # true で閲覧専用
```

## 認証・セキュリティ

- 主防御は **Tailscale ACL** (tailnet 外からは到達不能)。`serve` は HTTPS + 自動証明書。
- 任意の二層目として Bearer token (設定 or `token_env` 環境変数)。SPA はトップページの
  ログインフォームに入力した token を sessionStorage へ保存し、`Authorization: Bearer`
  ヘッダで付与する。URL query (`?token=`) は受理/生成しない (history/referrer への
  漏洩を避けるため。issue #41)。
- **fail closed (issue #41)**: bind が loopback (127.0.0.1 等) 以外、かつ
  `read_only=false` (writable)、かつ token 未設定 (config値・`token_env` 環境変数の
  どちらも空) という組み合わせは `docich webui` 起動時に error で拒否する
  (`--bind`/`--read-only` の CLI 上書き後の実効値も検証対象)。既定設定
  (`bind=127.0.0.1`) はこの条件に該当しないため通常運用には影響しない。
- token 比較は `hmac.compare_digest` による timing-safe 比較。
- ログ (`tmp/debug/webui.log`) にはクエリを除いたパスのみ記録される。万一 `?token=`
  付きでアクセスされても値は `REDACTED` に伏せてから記録する (token 非漏洩)。
- 書き込み対象はホワイトリスト (`WEBUI_ALLOWLIST`) のみ。シークレットキー
  (API_KEY / TOKEN / SECRET / STREAM_KEY / PASSWORD) は書けない。
- **`.env` は worker が bash で source する (`set -a; . ./.env`)**。このため全キーを
  厳格に検証している:
  - `PEAK_HOURS_TZ` は IANA 名のみ (`$(...)` 等のシェル構文は 400 で拒否)
  - 空白を含む値 (`AI_BACKOFF_SEC_ITEMS`) はダブルクォートで書き出し (source 時に壊れない)
  - ブール値は `0`/`1` のみ (ランタイムが `"1"` 判定のため)
- `.env` 更新は原子書き込み: バックアップ (`.env.bak.<ns>`) → 一時ファイル →
  `os.replace` → `chmod 600`。同時編集は mtime 比較で 409 (conflict) を返す。

## API エンドポイント

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/` | SPA (HTML) |
| GET | `/api/health` | 稼働状態・soren_root・uptime・最終 reload 結果 |
| GET | `/api/config` | 全 allowlist キーの値/実効値/既定値/in_env/masked |
| PUT | `/api/config` | 設定更新 (`{"values": {...}, "expected_mtime": N}`)。成功時 worker へ USR1 |
| GET | `/api/backoffs` | 稼働中 backoff の残り時間一覧 |
| DELETE | `/api/backoffs/<sanitized>` | 特定エージェントの backoff をクリア |
| POST | `/api/backoffs/clear` | 全 backoff をクリア |
| GET | `/api/stats?days=N` | 直近 N 日 (1..30) の AI 統計 |
| POST | `/api/reload` | radio/chat worker へ SIGUSR1 送信 |

`read_only` モードでは書き込み系 (PUT / DELETE / POST) が 403 になる。

## 本番 VM での導入状況 (2026-08-20)

- systemd --user `docich-webui.service` (`ExecStart` に `--soren-root /home/ubuntu/soren`)
- `sudo tailscale serve --bg --https=443 http://127.0.0.1:8787`
- 公開 URL: `https://<hostname>.<tailnet>.ts.net/`
- 検証実績: 設定の読み書き / worker reload / backoff 表示・クリア / 統計表示を実測確認済み

詳細は `src/docich/webui.py` の docstring と `handoff.md` のセクション 23/24 を参照。