# News PAPER worker / status / dashboard（N07）

親Issue #769 / 実装単位 #784。**PAPER研究専用**で、実注文・broker credential・live経路を持たない。
`mode` は常に `paper`、`live_eligible` は常に `false`。

実装は Python 3.11+（設定読取に標準ライブラリ `tomllib` を使用）を前提とする。CI は
ubuntu-24.04 の python3 で実行する。

この文書は N07 の worker・health・queue・status・dashboard の契約を定義する。実装は
`src/docich/trading/news_paper/{worker,status,dashboard}.py`。N05/N06 の専用台帳・執行は
main にマージ済みのもの（`ledger.py` / `manager.py` / `risk.py` / `execution.py`）を統合する。

## 1. default-off 境界

- `config/news-paper.toml` の `[worker] enabled` が **false または欠落**なら、import・CLI・
  status のいずれもディレクトリ/DB/socket/子processを作らず、既存設定を変更しない。
- `enabled = true` では **全値の明示指定が必須**。未承認の運用数値（周期・予算・risk・lot・
  fee・latency等）に既定値は無く、欠落・型不正・未知keyは起動拒否（fail-closed）となる。
- `mode != "paper"` は `LIVE_ELIGIBILITY_FORBIDDEN`、`clock_quality` が untrusted なら
  `CLOCK_UNTRUSTED` で起動しない。

## 2. 起動

```sh
bin/docich-news-paper worker --config config/news-paper.toml [--once]
bin/docich-news-paper status --config ...   # 実際は status 用引数（--state-dir 等）
bin/docich-news-paper dashboard --config config/news-paper.toml
```

- `--once` は1周期だけ実行して終了する（検証・保守用）。
- SIGTERM/SIGINT で graceful stop（最終 heartbeat `stopping`、lock解放）。
- unit案は `scripts/systemd/docich-news-paper-worker.service`（**静的な定義のみ**。install/
  enable/restart は別承認。N09で隔離を実測）。

## 3. 周期とキュー

1周期: 観測queue drain → `entries_disabled` 時のpending買取消 → pending注文の約定処理
（N05 `PaperManager.process_observations` → N06 `execution`）→ 候補submit（N03 seam）→
評価記録（valuation）→ heartbeat書込。

- 観測は **N02 seam**（`ObservationSource.poll`）から投入する。既定は未接続（通信なし）。
- 判断候補は **N03 seam**（`CandidateSource.candidates`）から投入される。worker自身は
  LLM/HTTPを呼ばない（risk loopに同期I/Oを持ち込まない）。
- queueは **サイズ・期限・重複・優先度**を持つ:
  - `queue_size` 超過は破棄（`queue_dropped_full`）
  - 期限超過はdrain時に破棄（`queue_dropped_expired`）— 期限後応答はrisk loopへ届かない
  - 同一quote identity（source/asset/market_event_at/quote_digest）は重複として破棄
  - priority降順、同一priorityはFIFO
- **単一writer**: `worker.lock`（flock）＋ `NewsLedger` の `<db>.lock`（flock）。競合は
  `STORE_UNAVAILABLE` で fail-closed（二重起動しない）。

## 4. entries_disabled（障害・停止時）

- 新規ニュース判断と **risk増加pending買** を停止（pending買は `FEATURE_DISABLED` で取消）。
- **継続する**: 保有評価（valuation）、protective exit（売り）、会計、heartbeat。
- quote欠落時は valuation を `stale=1` とし、未決済/pendingを status に残す。全停止が必要な
  場合は監視停止と未決済を明示して承認済みの管理責任へ引き継ぐ（親#769 I6）。

## 5. health.json（heartbeat）

`<state_dir>/news-paper/health.json` を tmp + `os.replace` で原子的に書く（0600、dir 0700）。

| key | 意味 |
|---|---|
| `schema_version` | 1 |
| `worker_id` / `run_id` / `scenario_id` / `account_id` | 実行同一性 |
| `mode` / `live_eligible` | `"paper"` / `false` 固定 |
| `status` | `idle` / `starting` / `running` / `stopping` / `failed` |
| `heartbeat_at` | 最後に周期を完了したwall clock（UTC RFC3339 マイクロ秒） |
| `started_at` / `cycle_seq` | 起動時刻・周期番号 |
| `entries_disabled` | 現在の新規停止状態 |
| `queue_depth` / `queue_dropped_{full,expired,duplicate}` | queue状態 |
| `last_market_available_at` / `last_fill_at` | 直近の観測利用可能時刻・約定時刻 |
| `last_news_available_at` / `last_classification_at` | N02/N03未接続のため現状 `null` |
| `last_error_code` | 固定error codeのみ（raw本文・例外文字列は保存しない） |

**鮮度**: statusは `heartbeat_at` 等のdurable時刻から age を計算し、ファイルmtimeでfreshと
判定しない。ageが `null`（未接続・未実行）を fresh として扱わない。運用閾値は N09 で承認する。

## 6. status（read-only）

`build_status(state_dir, run_id, scenario_id, account_id)` は専用 `paper.sqlite3` を
**`mode=ro`** で開く。DB/accountが無ければ `state="empty"`、パス逸脱は `blocked`、破損等は
`unavailable` を返し、**ディレクトリ/DB/WALを作成しない**。返す値はallowlistのみ:

- `mode` / `live_eligible` / `run` / `scenario` / `account` / `phase` / `entries_disabled`
- `account`（cash等）・`positions`・`fills`・`valuation`（`stale`含む）
- `receipts`（accepted/held/rejected件数・理由別集計・accepted uncommitted）
- `orders`（status別件数・pending買/売・最古期限）
- `pending_work.total`（未確定receipt + pending買 + pending売）
- `ages`: heartbeat / market / news / classifier（未接続は `null` + `not_connected`）
- `worker`: health由来のstatus/queue/last_error_code

既存口座・crypto PAPER・free_strategy の資産と合算しない。raw本文・prompt・モデル出力・
危険URL・秘密は返さない。

## 7. dashboard（read-only GET）

- 固定route: `GET /`（escape済みHTML）、`GET /api/news-paper/status`（allowlist JSON）。
  `HEAD` 可。その他メソッドは **405**、未知routeは **404**。
- 応答ヘッダ: `Cache-Control: no-store`、`X-Content-Type-Options: nosniff`、
  `Content-Security-Policy`（default-src 'none' 等）、`Referrer-Policy: no-referrer`。
- bindは **loopbackのみ**（`127.0.0.1` / `::1` / `localhost`、他は設定拒否）。HTMLは
  全値をescapeし、script・外部リンク・操作ボタンを持たない。
- 既存配信の音声・映像への自動掲載は行わない（配信連携は別承認・権利審査後）。

## 8. runtime registry / diagnostics

- `ops/vm_actions/runtime_registry.py` は **soviet_now側のworker/queue lane/pid規約**を
  記述するもので、docich trading workerは登録されていない（market-paperも同様）。
  本workerはdocich-localな研究ユニットであり、ここへ無理に登録しない。
- 正規の運用診断は owner-only VM gateway の read-only `diagnostics`（AGENTS.md §10）。
  本workerは **固定code・allowlist projection以外を診断へ出さない**。health/statusの
  契約は本文書を正本とし、VM配備時のhealth/queue/diagnostics統合は N09 で審査する
  （未統合をruntime readyと呼ばない）。
- `docs/operations/runtime-diagnostics.md` の変更checklist（runtime registry / worker
  health / queue registry / telemetry / diagnostics coverage / regression tests /
  secret-redaction / deploy影響）は本書と実装で対応を記録する。

## 9. 障害時の固定code

- 致命的（loop終了・`status=failed`）: `STORE_UNAVAILABLE` / `SCHEMA_VERSION_UNSUPPORTED` /
  `STATE_PATH_FORBIDDEN` / `CLOCK_UNTRUSTED`
- 継続（新規停止・held・skip）: `FEATURE_DISABLED` / `SOURCE_TIMEOUT` / `QUOTE_STALE` /
  `LIQUIDITY_INSUFFICIENT` / `RISK_CHANGED_BEFORE_FILL` 等（N00 D10の固定enumのみ）
- 例外文字列・raw記事・HTTPヘッダ・LLM応答をhealth/status/receiptへ保存しない。

## 10. 検証

```sh
PYTHONPATH=src python3 -m pytest -q tests/test_news_paper_worker.py \
  tests/test_news_paper_status.py tests/test_news_paper_dashboard.py
```

offlineのみ（fake transport/LLM、一時state、外部HTTP 0）。実VM・実workerの起動・unit
install/enable/restart・PR/merge/配備は本ユニットの非対象（親#769 H）。
