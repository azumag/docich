# CC 翻訳アダプタ（docich#2894 / #2900）

字幕翻訳の loopback 受け口（`127.0.0.1:4100`、OpenAI 互換 `POST /v1/chat/completions`）を、既存の direct-chat 経路へ中継する常駐プロセス。
`docich.cc_translation_adapter` が担当し、`captions.py` のクライアントは無変更。

## 設定

| 変数 | 必須 | 既定 | 内容 |
|---|---|---|---|
| `DOCICH_CC_ADAPTER_UPSTREAM` | はい | なし | `<direct-chat provider>:<model>`。`vercel-api` / `openrouter-api` / `cloudflare-api` のいずれか |
| `DOCICH_CC_ADAPTER_PORT` | いいえ | `4100` | 127.0.0.1 の待受ポート（`.env` の `DOCICH_CC_TRANSLATION_URL` と一致させる） |
| `DOCICH_CC_ADAPTER_TIMEOUT_SEC` | いいえ | `25` | upstream 1 回の上限（0.1〜45）。クライアント側 timeout より短く保つ |
| `DOCICH_CHAT_VERCEL_UPSTREAM` 等 | upstream による | — | 既存 direct-chat の前提 env。`vercel-api` は単一の明示 upstream が要る |
| `AI_GATEWAY_API_KEY` 等 | upstream による | — | 既存の secret。値は env ファイルか `*_FILE` でのみ渡し、ファイル・log・Actions 出力に書かない |

## 起動・確認

- unit: `ops/caption_adapter/docich-cc-translation-adapter.service`（`User=ubuntu`、`Restart=always`、`WantedBy=multi-user.target`）。
- 健康確認（値は出ない）: `curl -s http://127.0.0.1:4100/healthz` → `{"status": "ok", "upstream": "<provider>:<model>"}`。
- 失敗は HTTP 502 と kind のみ（`rate_limit` / `provider_failed` など）。log は `[CC_TRANSLATION_ADAPTER] failed kind=...`。

## 配置前の未確定事項（#2900）

以下は VM 側の read-only 確認で埋めてから配置 PR に固定する。推測では固定しない。

1. `EnvironmentFile` の実パス（unit の `/home/ubuntu/soren/.env` は仮置き）。
2. docich src の配置先（unit の `PYTHONPATH=/home/ubuntu/work/docich/src` は仮置き）。
3. 既存 direct-chat 経路（`vercel-api`）が本番 VM で実キーにより応答するか、レイテンシ（目安 <10s）と品質の実測。
4. 使う upstream の規約確認を一文で `#2900` に記録（AGENTS §11 と同じ基準）。

## 配置

AGENTS §9 のとおり、本番反映は owner 専用の control plane（`ops/vm_actions/` + `.github/workflows/vm-operations.yml`）経由のみ。VM 上の tracked file を直接編集しない。
pre-switch gate（上記 3・4）を通過してから整備時間帯に配置し、`systemctl enable --now` の結果と `healthz` を記録する。
