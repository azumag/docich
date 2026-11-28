# 暗号資産BOT：約定の現実化・流動性ゲート・昇格根拠の作り直し（設計、2026-11-28）

対象：暗号資産 PAPER（共通スロット・銘柄別スロット・組み込み戦略）と LIVE（銘柄別方針・自由戦略ミラー）。
自由戦略の PAPER（`free_strategy/broker.py`）は既に板の bid/ask で約定しているので対象外。

## 1. 背景（2026-11-28 の観測。公開データのみ）

- PAPER の約定価格は直近の 5 分足終値（`worker.py`：`prices = {symbol: frame.last_price}`）を基準にし、
  板は「最良気配から先のすべり」にだけ使う（`paper.resolve_slippage_bps`）。**終値と最良 ask/bid の差（スプレッド）を払っていない。**
- LIVE の 11 銘柄（OAS, KLAY, OMG, LPT, BOBA, XYM, MONA, AXS, IMX, ATOM, CYBER）は JPY 建て 43 銘柄で最もスプレッドが広い群：
  OAS 377bps、KLAY 321、OMG 288、LPT 232（43 銘柄の中央値 23bps、BTC/ETH/XRP/SOL は 0〜0.1bps）。5 分足の 67〜96% は取引なし。
- OAS の 5 分足（11/25〜27）：値動きの中央値 364bps ≒ スプレッド、値動きの 84%（110/131）が直後にほぼ同じ幅で反転（lag-1 自己相関 −0.20）。
  終値が bid と ask の間を往復しているだけ（bid-ask バウンス）。「下げたら買い、戻ったら売る」戦略は PAPER では利益、実取引では毎回スプレッド＋手数料の損。
- これが PAPER の純損益の約 86% が OAS だった事実（Issue #3623 2026-11-16）と、PAPER→LIVE の成績差の主因と判断する。
- bitbank の手数料（公開 API `spot/pairs`、上記全銘柄）：maker −0.02%、taker 0.12%。

## 2. 目的と非目的

- 目的：PAPER の成績を「実際にテイカーで売買したらこうなる」に一致させる。流動性の無い銘柄で新規に買わない。
  汚れた PAPER の成績を根拠に LIVE へ昇格させない。
- 非目的（別 PR）：LIVE の maker（post-only）発注、バックテスト基盤、時間足・特徴量の変更、診断への成績の収載。

## 3. 設計

### D1. PAPER の約定を板の実行価格にする（`paper-cost-v3`）

- `PaperBroker` の約定の基準価格を、決定の数量で板を歩いた VWAP にする：買いは asks、売りは bids。
  その上に手数料を掛ける（今の `paper_execution_price` の手数料部分）。**板の VWAP を使った約定では、すべり（slippage_bps）を追加で掛けない**（二重計上の防止）。
- 板が無い・形式不正・数量に対して深さが足りない場合は **約定しない**（終値へのフォールバックは廃止）。
  その決定はスキップとして扱い、理由コードは `book_unavailable` / `depth_insufficient`。売りも同じ（次のサイクルで再判定）。
- `cost_context` を `model = "paper-cost-v3"` にし、次を記録する：`price_source = "book_vwap"`、`reference_close`（判断に使った終値）、
  `top_bid`、`top_ask`、`spread_bps`、`fee_rate`、`fee_source`。数値は文字列。
- ワーカーの板取得 `fetch_top_books(limit=5)` は深さ不足が増えるので `limit=20` にする。
- 割当（数量の計算）は今どおり終値で行い、約定価格だけを板にする。資金の上限（`max_notional_fraction` 等）を約定価格が超える場合は、
  数量を板価格で計算し直して切り下げる（最小数量未満ならスキップ `below_min_amount`）。
- `cell_metrics` は model 番号 ≥2 を「手数料込み」とみなしている（`FEE_AWARE_MIN_MODEL_VERSION = 2`）。v3 もそのまま手数料込みとして扱われることをテストで固定する。
- 出口の判断（`net_pnl_bps` の推定）は今どおり終値ベース（既知の限界として docs に記す）。約定だけが bid になる。

### D2. 流動性ゲート（PAPER と LIVE の新規の買いだけ）

- 新モジュール `src/docich/trading/liquidity.py`（純関数、ネットワーク・ファイルなし）：
  - `spread_bps(book) -> Decimal | None`：最良 bid/ask から `(ask − bid) / mid × 10000`。板が使えなければ `None`。
  - `active_bar_fraction(frame, bars=24) -> Decimal`：直近 `bars` 本のうち出来高 > 0 の割合。
  - `entry_block(book, frame) -> str | None`：`spread_bps` が `None` → `book_unavailable`、`> MAX_ENTRY_SPREAD_BPS` → `spread_too_wide`、
    `active_bar_fraction < MIN_ACTIVE_BAR_FRACTION` → `illiquid`、それ以外 `None`。
  - 定数：`MAX_ENTRY_SPREAD_BPS = 40`、`MIN_ACTIVE_BAR_FRACTION = 0.5`、`ACTIVITY_BARS = 24`。
- **売り（出口）には掛けない。** 保有の手じまいは流動性が悪くても止めない。
- PAPER（`worker.py`）：買いの約定直前に `entry_block` を判定し、該当すればその買いを約定せずスキップ（理由コードを skipped に入れる）。
- LIVE（`live/decide.py`）：板を取った後、エントリーだけの銘柄に `entry_block` を適用し、該当する買いの機会を落としてスキップを記録する
  （`_drop_symbol` と同じ記録経路。出口がある銘柄は出口を落とさない＝買いの機会だけ落とす）。
- LIVE 自由戦略（`live/free_mirror.py`）：買いの機会を作る前に同じゲートを当てる（板が無ければ買わない）。
- LIVE ワーカーの市場データは 5 分足 25 本で `volumes` を持つので、そのまま使える。

### D3. 昇格の根拠を作り直す（ルール v4）

- `cell_metrics` に、v3 の約定だけから作った日ブロック `return_blocks_v3` をセルごとに足す。
  対象は「その売りが v3 で、かつ売った在庫の取得が全部 v3 の買い」の決済だけ。
  プールごとに `v3_only` を持ち、v3 以外の買いが入ったら False、プールが空になったら True に戻す。
- `promotion_shadow` を **ルール v4**：e-process・定数（δ=0.002、c=0.02、しきい値 5、最小 5 ブロック）は v3 と同じ。
  使うブロックを `return_blocks_v3` に替え、`FIRST_BLOCK_DAY` の日付フィルタは外す（v3 の約定は D1 の反映後にしか無いので）。
  `RULE_VERSION = "v4"`。docstring に理由（1 章）を書く。
- LIVE の自動昇格（`live/promotion.py`）は `promotion_shadow` の関数を使うので自動で v4 になる。
  **D1 の反映後、v3 の約定で 5 日分のブロックが溜まるまで昇格は起きない**（意図した挙動。テストで固定）。
- 自由戦略の昇格（`free-v1`）は既に実行価格なので変えない。

## 4. テスト（新規・変更）

- `paper`：買いは asks の VWAP×(1+手数料)、売りは bids の VWAP×(1−手数料)。slippage を追加で掛けない。
  板なし・深さ不足は約定しない（`record_fill` が呼ばれない）。`cost_context` の v3 フィールド。
- `worker`：板なしの買いがスキップされる。スプレッド 41bps の買いは `spread_too_wide`、出来高の無い足が多いと `illiquid`、売りはゲートされない。
- `liquidity`：各関数の境界値（40bps ちょうどは通す、0.5 ちょうどは通す）。
- LIVE `decide`：スプレッドの広い銘柄の買いが落ち、同じサイクルの出口は残る。自由戦略ミラーの買いも落ちる。
- `cell_metrics`：v2 の買いを含むプールからの売りは `return_blocks_v3` に入らない、空になった後の v3 往復は入る。
- `promotion_shadow` v4 / `live/promotion`：v2 のブロックだけでは昇格 0、v3 のブロック 5 日分で昇格。
- 既存のテストで「終値で約定」を前提にした期待値は、v3 の意味に合わせて直す（テストを消して緑にしない）。

## 5. 反映・運用

- PAPER の成績は大きく下がる見込み（それが実力）。コーナーの発表もそれを反映する。
- 反映後しばらく、昇格は起きない（v3 のデータ待ち）。オーナーの停止スイッチ `promotion.disabled` は不要になるが、置いてあっても問題ない。
- runtime checklist：新しい worker・queue なし。理由コード `book_unavailable` / `depth_insufficient` / `spread_too_wide` / `illiquid` が skipped に増える。

## 6. 次の段階（別 PR、設計は後で）

- P1：LIVE の maker（post-only）発注と未約定時の扱い。
- P2：公開ローソク足＋スプレッド込み費用のバックテスト、流動性の高い銘柄・長い時間足への移行、入れ替え基準の長期化。
- P3：診断への成績の収載（今は corners の出力が上限超過で省略されている）。

## 7. 設計レビューへの対応（2026-11-28）

- 板は最良気配1段しか返らない → d423710 で 20 段の深さを銘柄ごとに取得（解決済み）。
- 数量の切り下げ：上限は割当の `reference_notional`。数量を減らすと買いの VWAP は下がるので 1 回の再計算で上限内に収まる。amount / quote_notional / reference_notional を比例して更新。約定できないときは `PaperFillSkipped`（`reason_code`）で、worker が skipped に記録する。
- 自由戦略ミラー：ゲートは板の取得後、早期 return はゲートの前なので、ゲートで全部落ちてもスキップは記録される（問題なし）。
- `liquidity` の入力：`DepthBook`（`.price` / `.amount`）、`TopOfBook`（`.bid` / `.ask`）、LIVE `OrderBook`（`(price, amount)`）。空・非正値・ask < bid は None（`book_unavailable`）。足が 24 本未満ならある本数で判定し、0 本は 0（`illiquid`）。
- 売りの `cost_context` が無い・壊れている → v3 ではない。`promotion_shadow` の `first_block_day` キーは削除（参照箇所なし）。
- 出口判定の推定は従来どおり終値＋`paper_execution_price`（slippage 5bps）、約定だけが板の VWAP＋手数料（slippage なし）。
- R5（dust で昇格対象外が固定される）と R6（活動度ゲートを割当の前へ）をこの追補で修正。
- 実測（2026-11-28 06:29 UTC の板 1 回）：bitbank JPY 47 ペア中、スプレッド 40bps 以下は 26 ペア（BTC, ETH, XRP, SOL, DOGE, ADA, BNB, SUI など。LIVE の 11 銘柄では CYBER のみ）。
