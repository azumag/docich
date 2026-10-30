# CC 翻訳アダプタ（docich#2894 / #2900）

字幕翻訳の loopback 受け口（`127.0.0.1:4100`、OpenAI 互換 `POST /v1/chat/completions`）を、既存の direct-chat 経路へ中継する常駐プロセス。
`docich.cc_translation_adapter` が担当し、`captions.py` のクライアントは無変更。

## 設定

| 変数 | 必須 | 既定 | 内容 |
|---|---|---|---|
| `DOCICH_CC_ADAPTER_UPSTREAM` | はい | なし | `<direct-chat provider>:<model>`。`vercel-api` / `openrouter-api` / `cloudflare-api` / `sarashina-api` のいずれか |
| `DOCICH_CC_ADAPTER_PORT` | いいえ | `4100` | 127.0.0.1 の待受ポート（`.env` の `DOCICH_CC_TRANSLATION_URL` と一致させる） |
| `DOCICH_CC_ADAPTER_TIMEOUT_SEC` | いいえ | `25` | upstream 1 回の上限（0.1〜45）。クライアント側 timeout より短く保つ |
| `DOCICH_CHAT_VERCEL_UPSTREAM` 等 | upstream による | — | 既存 direct-chat の前提 env。`vercel-api` は単一の明示 upstream が要る |
| `AI_GATEWAY_API_KEY` 等 | upstream による | — | 既存の secret。値は env ファイルか `*_FILE` でのみ渡し、ファイル・log・Actions 出力に書かない |
| `DOCICH_CHAT_SARASHINA_UPSTREAM` | `sarashina-api` のとき必須 | — | 単一の endpoint。https のみ（loopback http だけ例外）。userinfo・query・fragment 不可。起動時に pin し、redirect は追従しない |
| `SARASHINA_RELAY_TOKEN`（`_FILE` 可） | `sarashina-api` のとき必須 | — | adapter→リレーの bearer credential のみ。起動時に存在だけ確認し、値は表示しない。ファイル・log・Actions 出力に書かない。Sarashina の実 API キーは VM に置かない |

### sarashina-api（#2900）

- 登録モデル: `sarashina3-nano`（第一候補）、`sarashina3-mini`（fallback）。`sarashina3-translate` は登録しない。
- adapter が要求するのは chat/completions の形だけ。実際にどの Sarashina API を叩くかは endpoint 側の責任。
- 利用条件の受入（§11）は owner が明示するまで配置しない。
- 信頼境界: VM（adapter）が持つのはリレー発行の bearer credential（`SARASHINA_RELAY_TOKEN`）のみ。Sarashina の実 API キーはリレー側だけが保持し、VM の env・リポジトリ・log には現れない。rotation もリレー側で完結する。

## 起動・確認

- unit: `ops/caption_adapter/docich-cc-translation-adapter.service`（`User=ubuntu`、`Restart=always`、`WantedBy=multi-user.target`）。
- 健康確認（値は出ない）: `curl -s http://127.0.0.1:4100/healthz` → `{"status": "ok", "upstream": "<provider>:<model>"}`。
- 失敗は HTTP 502 と kind のみ（`rate_limit` / `provider_failed` など）。log は `[CC_TRANSLATION_ADAPTER] failed kind=...`。

## 配置時の固定値（#2900、VM 上で read-only 確認済み）

unit・PR 本文・本 README は以下の同じ値で揃えている。

1. `EnvironmentFile`: `/home/ubuntu/soren/.env`（存在する env ファイル。値は読まない・表示しない）。
2. docich src の配置先: `/home/ubuntu/docich/src`（unit の `PYTHONPATH`）。

以下は未確認。推測で固定しない。pre-switch gate として #2900 に記録してから配置する。

3. 既存 direct-chat 経路（`vercel-api`）が本番 VM で実キーにより応答するか、レイテンシ（目安 <10s）と品質の実測。
4. 使う upstream の規約確認を一文で `#2900` に記録（AGENTS §11 と同じ基準）。
5. VM 側の env に `DOCICH_CC_ADAPTER_UPSTREAM`（と必要なら `DOCICH_CC_ADAPTER_PORT`）が未設定の場合は、配置時に owner 管理の env へ追加する。`DOCICH_CHAT_VERCEL_UPSTREAM` の存在も同じ read-only 確認で見る（値は出さない）。

## 状態確認（docich status / diagnostics）

- `docich status` の `captions.adapter` 行: 127.0.0.1 の `/healthz` を read-only で 1 回だけ probe し、`ok (upstream=…)` / `unreachable` / `invalid response` を表示する。配信未設定（`stream.mode=null`）では表示しない扱い。
- `collect_diagnostics` の `cc_translation_adapter` 節: probe の state・port・upstream 名のみ。prompt・key・応答本文は含めない。
- AGENTS §11 の確認: 本機能は registry（既存 unit 一覧）・queue（処理キューを持たない）に影響しない。health は上記 probe、telemetry はこの節のみ。

## 配置

AGENTS §9 のとおり、本番反映は owner 専用の control plane（`ops/vm_actions/` + `.github/workflows/vm-operations.yml`）経由のみ。VM 上の tracked file を直接編集しない。
pre-switch gate（上記 3・4）を通過してから整備時間帯に配置し、`systemctl enable --now` の結果と `healthz` を記録する。
