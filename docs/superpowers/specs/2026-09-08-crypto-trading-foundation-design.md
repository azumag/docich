# Crypto Trading Foundation Design

## Goal

docich に、bitbank の複数現物ペアを横断して取引機会を探索し、模擬売買・資金配分・取引台帳・配信向け状態表示までを一貫して扱う暗号資産取引基盤を追加する。

初期リリースは **paper-only** とする。実 API キー、実発注、出金権限、レバレッジ取引は扱わない。

## Scope

- bitbank の現物市場を動的に発見し、単一の `BTC/JPY` に固定しない。
- 取引所接続は CCXT の bitbank adapter を薄い境界として利用する。
- CCXT は trading 機能を使う場合だけ必要な optional dependency とし、docich 本体の起動依存にしない。
- 取引所の market metadata から有効な spot market を選別する。
- 戦略は注文を直接出さず、共通の `Opportunity` を返す。
- 資金配分・注文予約・台帳は戦略から分離する。
- コーナー表示の開始・終了と trading worker の稼働を分離する。
- ゲーム配信中でも trading worker は状態管理を継続できる設計にする。

## Non-goals for the first slice

- 実資金での注文。
- API key の保存・配布・探索。
- 信用・レバレッジ・空売り。
- 三角裁定の実注文。
- LLM による直接発注や資金上限変更。

## Runtime architecture

`trading` は通常ゲームの adapter/agent lifecycle 配下に置かない。ゲーム切替で停止してはいけないため、独立した worker として扱う。

```text
bitbank public market data
        ↓
MarketGateway → MarketUniverse
        ↓
Strategy → Opportunity[]
        ↓
Allocator / RiskGate
        ↓
PaperBroker
        ↓
Ledger + public status snapshot
        ↓
docich notification / crypto corner
```

初期実装では常駐 supervisor への本番登録までは行わず、CLI から安全に paper cycle と status を実行できるところまでを完成させる。後続実装で supervisor と共通通知へ接続する。

## Market universe

市場一覧は起動時に exchange metadata から取得する。コードへ銘柄リストを固定しない。

初期フィルタは `spot == true`、`active != false`、base/quote/symbol が有効であること。bitbank 固有 metadata に売買停止フラグが存在する場合は、停止中市場を発注候補へ入れない。

JPY 建てに限定しない設計とする。ただし取引所に実在しない経路を裁定可能とみなさない。発見された市場だけが発注可能グラフを構成する。

## Opportunity model

戦略は `Opportunity` を返す。少なくとも次を保持する。

- `opportunity_id`: 一意 ID。
- `strategy_id`: 戦略名とバージョンを識別する安定 ID。
- `symbol`: 初期 slice は単一市場の spot buy/sell を対象とする。
- `side`: `buy` / `sell`。
- `score`: 候補比較用の有限な 0..1 値。
- `expected_edge_bps`: 手数料・slippage 控除前の期待 edge。
- `max_notional_fraction`: 戦略側が要求する上限。共通上限を越せない。
- `expires_at`: 古いシグナルの再実行防止。
- `reason_code`: 配信説明と検証に使う固定コード。

## Capital allocation

初期既定値は以下とする。

- `max_opportunity_fraction = 0.30`
- `max_total_deployed_fraction = 0.30`
- どちらも bot 専用 capital を reference currency へ換算した値に対する比率。
- JPY 建て以外の市場は `quote_to_reference` の明示レートで換算する。換算不能な quote asset は推測せず見送る。
- pending order の予約額も deployed として数える。
- 最小注文数量・最小 notional を満たさない場合は切り上げず見送る。
- 複数候補が同時にある場合は score の高い候補から枠を割り当てる。

30% は購入枠であって損失許容率ではない。実取引へ進む前に、別の損失上限・日次停止・slippage 上限を追加する。

## Paper execution and ledger

初期 `PaperBroker` は deterministic な価格入力を受け、即時約定として台帳へ記録する。これは約定品質を模倣するものではなく、状態遷移と資金計算の検証用である。

台帳は SQLite を使い、stdlib `sqlite3` だけで利用できるようにする。初期 schema は `paper_orders` と `paper_fills` を持ち、ID は外部から再利用可能な安定値とする。

SQLite ファイルと public status は `run/trading/` 配下へ保存し、Git 管理しない。作成時は可能な限り 0600 相当の private permission とする。

再実行時に同じ `opportunity_id` を二重約定させない。台帳に処理済み ID がある場合は idempotent に no-op とする。

## Public status

配信・Web UI が読む情報と、取引所資格情報を完全に分離する。public status JSON には以下のみを含める。

- mode (`paper`)
- worker state
- last cycle timestamp
- discovered market count
- eligible market symbols
- bot capital / deployed notional
- open paper positions
- recent paper fills
- last skip / reason codes

API key、secret、raw request headers、exchange private payload、環境変数値は出力しない。

## CLI

初期 slice では以下を追加する。

- `docich trading status`: `run/trading/status.json` を安全に表示する。未実行なら明示的な idle/absent 状態を返す。
- `docich trading discover`: CCXT bitbank public API を使い、発見・適格市場を JSON で表示する。認証情報は要求しない。
- `docich trading paper-cycle --snapshot <json>`: deterministic fixture/snapshot から機会・配分・paper fill を一巡させる開発用経路。実注文へは接続しない。

## Safety defaults

trading 設定の既定 mode は必ず `paper`。`live` は初期 schema で拒否する。

資格情報が環境に存在しても、初期 slice は private endpoint と order endpoint を呼ばない。CCXT gateway は market discovery 以外を公開しない。

## Testing and completion criteria

Initial implementation is complete when all of the following are true:

- unit tests prove market filtering rejects disabled/non-spot/invalid markets;
- unit tests prove 30% per-opportunity and 30% total deployed limits are never exceeded;
- unit tests prove too-small orders are skipped instead of rounded up;
- unit tests prove the same opportunity cannot create duplicate paper fills;
- unit tests prove public status contains no credential-like fields;
- CLI tests cover absent status and deterministic paper cycle;
- optional CCXT absence does not break ordinary docich commands;
- the full repository test suite is run and any pre-existing/environment-only failures are separated from regressions;
- no real API key is read, logged, committed, or required;
- no production VM deployment or live order occurs in this slice.

## Follow-up slices

After this foundation is green, follow-up work can add real-time market frames, multiple strategy implementations, correlation/exposure grouping, order-book-aware paper simulation, common notification/audio integration, supervisor registration, and only then a separately gated live broker.

Live trading requires a separate explicit design review and user approval. It must add loss budgets, daily stop, stale-data rejection, reconciliation, partial-fill handling, emergency pause, and credential isolation before order endpoints are enabled.
