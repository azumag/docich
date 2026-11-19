# LIVE の状態・方針・オン/オフ制御(L1、Issue #3623)

L1 は、本番(LIVE)の売買の**土台だけ**で、動作しない。どの実行経路からも import されず、ネットワークを使わず、注文を送らない。PAPER のワーカーにはつながっていない。LIVE のワーカー(L4)・取引所への接続(L3)・昇格の適用(L6)はまだ無い。

## 配置と権限

```
<state_dir>/trading-live/            0700   PAPER の <state_dir>/trading とは別
  live.sqlite3 (+ -wal, -shm)        0600   LIVE の台帳
  live-writer.lock                   0600   単一の書き込み権(flock)
  owner/                             0700   制御ファイルの置き場(無ければ control が作る)
    live_control.json                0600   オン/オフ制御ファイル(WebUI とオーナー CLI が書く)
    policy/<experiment_id>.json      0600   オーナーが置く方針スナップショット
```

- 台帳は `src/docich/trading/live/ledger.py`。意図(intent)は送信の**前**に保存し、保存が済むまで送信してはならない。再起動後に「送信中」だった意図は、取引所へ照会して確かめるまで、自動では再送しない。
- 台帳の既定の置き場は PAPER と同じ `state_dir`。`git clean -x` などの手作業で消さないこと。

## オン/オフ制御ファイル

オーナーの普通のスイッチ。WebUI とオーナー CLI(`control on|off`)の両方が同じファイルを書く。
コードが読むだけで、勝手に作ることはない(`control` の書き込み時だけ、無い `trading-live` と
`owner` を 0700 で作る)。

```json
{"schema_version":1,"kind":"docich-live-control","state":"on","mode":"trade",
 "updated_at":"2026-11-20T09:00:00+00:00","updated_by":"owner"}
```

- キーはこの6つだけ。知らないキー・型違い・schema 違い・タイムゾーンの無い `updated_at` は
  無効として OFF に読む。
- `state` は `on` / `off`。`off` は買いも売りも止める(送信直前に `gate_closed` で放棄する)。
  ただし TTL の取消(`ttl.cancel_expired` / `executor.cancel`)は止めない。リスクを減らす
  操作は止めないため。
- `mode` は `on` のときだけ意味を持つ。`shadow` は注文を送らず、`trade` だけが注文を許す。
  `off` でも `mode` は保持する(次に `on` にしたときの既定)。
- 無い・0600 でない・所有者が違う・symlink・4096 バイト超・JSON が壊れている・不正な
  schema は、すべて OFF として読む(理由コード付き)。**安全停止は trip しない**。
  OFF から ON に戻すのに、人の安全停止解除も、再起動も要らない。
- 期限・方針 digest・コード revision には結び付かない。どのコミットで動いていても、
  同じファイルの `on`/`off` がそのまま効く。

`python -m docich.trading.live_owner --state-dir <state> control on --mode trade --yes-trade
--operator NAME` で書く。`--mode` を省くと、読める既存ファイルの `mode` を保ち、無ければ
`shadow`。`--mode trade` は `--yes-trade` が無ければ拒否する。`control off --operator NAME`
は `mode` を保ったまま `state` を `off` にする。`control status` は読み取りだけで、
`state` / `mode` / `updated_at` / `updated_by` / 理由コードを出す。

## 方針スナップショット

- 最初の戦略は `snapback_harvest_v25`(OAS・OMG・XYM・CYBER・MONA・ATOM・KLAY・LPT・AXS・IMX・BOBA の11銘柄)。定義は、本番の `paper_strategy_evaluation_history.json` の `experiment` にあるもの(最大20件で古い順に消えるので、取り出して保管する)。
- digest は、`activated_at` を除いて正準化した JSON の sha256。ファイルが入力どおりに往復できない場合(知らないキー、float の閾値など)は読み込まない。
- 確かめるには `python -m docich.trading.live.policy digest <file>`(digest を表示するだけ)。
- 保有中のポジションは、入ったときの方針の売りルールを保つ(台帳のトリガーが、別の方針での売りを拒否する)。

## 初期方針のセットアップ (WebUI、取引の有効化とは別)

LIVE タブの「初期方針のセットアップ」は、PAPER の `paper_strategy_evaluation_history.json` の
`entries[].experiment` からオーナー承認済みの初期方針(`snapback_harvest_v25`)を読み、
`INITIAL_SYMBOLS` の11銘柄の policy set として台帳へ `owner_initial` で記録する。LIVE の
ON/OFF、`trade`、control ファイル、worker は変更しない。

- `GET /api/live/initial-policy/export`: 読み取り専用。履歴の候補を厳密に検証し、
  `activated_at` を除く canonical snapshot JSON と full 64-hex `experiment_digest` を返す。
  ディレクトリは設定済み `state_dir/trading` だけを使い、パスは受け取らない。UI の
  ダウンロード名には full digest が入る。
- `POST /api/live/initial-policy/preview`: 読み取り専用。operator/note を文字列として検証し、
  候補を再読込して、現在の有効 policy・台帳 ID に結び付いた確認 token を返す。ledger も
  control も作らない。
- `POST /api/live/initial-policy/activate`: プレビュー token（10 文字の小文字 hex のみ）を
  検証し、writer lease の下で候補と有効 policy を**再読込**してから `owner_initial` を記録する。
  候補が変わった場合・別の有効 policy がある場合は拒否する。同じ policy set が既に有効なら、
  operator/note と token を検証したうえで何も書かず `already_active` を返す。初回成功が
  台帳を作成した場合でも、同じ preview の再送（応答喪失・二重クリック）は `already_active`
  として扱う。別の contender が先に有効化した場合は、writer lease と再読込により上書きしない。
  初回 preview の後に空の台帳ができていた場合（失敗した試行や別プロセス）は token が台帳 ID に
  結び付かないため、書き込まず `stale_preview` として再プレビューを求める。
- 履歴が 20 件から消えている場合は、以前にエクスポートした canonical JSON と、それとは
  独立に保管した full digest を UI に貼り付ける。同じ厳密パーサ・64 KiB 上限・
  experiment ID 検査を通る。**履歴に候補がある場合は full canonical digest が一致するときだけ**
  受け付け、別の `snapback_harvest_v25` を ID だけで受け入れない。履歴が壊れている・候補が
  曖昧な場合はフォールバックせず拒否する。
- 既存の operator 認証・viewer/read-only 拒否・CSRF・Origin/Host 検査を通る。
  GET は状態を変更しない。UI は送信中に確認ボタンを無効化し、エラーは固定コードだけを
  表示する。パス・台帳の生値・キー・token は表示しない。

## 安全停止

止める理由は6つ定義してある。コードが自動で止めるのは、未知の注文(`record_order_observation`)、手数料の未取得と約定の矛盾(`record_fill`)、書き込み権の喪失(`lease_lost`)、データ異常(`ingest.anomaly`)の4つ。`balance_mismatch` と `enable_missing` は旧行の互換のために列挙だけ残しており、**新しくは誰も書かない**(残高の不一致は通常のサイクルでは判定も警告もせず、停止もしない。オーナー決定 2026-11-18)。止まっている間は、新規の買いの送信だけを拒否する。売り(手じまい)は送れる(オーナー決定 2026-11-16)。二重売りは、同じ銘柄・同じ向きの未完了の注文を1件に制限する台帳の索引が防ぐ(結果不明の売りが残っていれば、2つ目の売りは作れない)。結果不明の買いは売りを止めない(約定していても保有が増えるだけ)。照合と監視は止まっていても続ける。オン/オフの制御ファイルはこれとは別で、OFF は買いも売りも止めるが、安全停止ではないので trip も解除操作も要らない。**安全停止の解除は人だけ**。コードから解除を呼ぶ処理は無く、テストで確かめている。

## 書き込み権(writer lease)が必須の範囲(Issue #4588)

台帳の書き込みトランザクションは2種類だけ。`_tx(lease)` は `BEGIN IMMEDIATE` の中で lease を検証し、`lease=None` を `ValueError("a writer lease is required")` で拒否する。`_tx_admin` は lease を検査しない管理用で、使えるのは次の4箇所だけ:

- 台帳の作成(`_migrate` の初期行)
- lease 行そのものの確保(`claim_lease_row`。lease がまだ存在しないので当然)
- 安全停止の記録(`trip_safety`。lease を失った直後でも止められなければならない)
- 人だけが呼ぶ停止の解除(`clear_safety`。owner CLI は先に lease を取るが、ledger 層は lease 無しでも受け付ける)

それ以外の公開 writer(方針の保存・有効化、意図の作成・遷移、ポジションの終了・端数化、live イベント・注文観測・約定・cashflow・有効化ファイル観測の記録、lease 行の解放)は、引数の検査より先に `lease is None` を拒否する。引き渡された lease が失効・陳腐化(epoch/holder のずれ・`released_at` あり・ロックファイルの差替)していれば、トランザクション内の `verify_in_tx` が `LeaseLost` で止め、変更は commit されない。テストは `tests/test_trading_live_lease_scope.py`(全 writer × lease 無し/陳腐/解放済み/横取り後で DB 全体が不変であること)。

## 同一銘柄の複数建玉(Issue #4588 の許容条件・互換性記録)

開いているポジションの一意制約は `(symbol, entry_policy_digest)` なので、同じ銘柄でも方針が交代すれば digest 違いの別ポジションを持てる。台帳スキーマ(schema v1)と既存行はそのままで、移行・統合は行わない。層ごとの扱い:

- **台帳層(許可)**: digest の異なる2つ目の open ポジションは有効な行として残る。売りは要求の `policy_digest` で建てたポジションにだけ結び付く(別 digest の売りは `no open position under that policy` で拒否)。数量・原価・端数判定は `position_id` ごと。(symbol, side) の未完了意図は1件なので、どのポジション宛てでも2件目の売り・買いは作れない(重複注文の抑止)。
- **ワーカー層(自動売買が許す範囲)**: open ポジションが1つで digest が現行と違うものは「legacy」で、入口 digest のまま売れるが買い増しはしない(= ワーカー自身は2つ目を作らない)。同じ銘柄に open が2つ以上あれば「ambiguous」で、その銘柄には買いも売りも計画しない(数量を併合したり、所有戦略を推測して売りを選んだりしない)。実行器の `exceeds_holding` は自分の `position_id` の net を超える売りを捨てるので、銘柄の合計で足りても他ポジションの分は切らない。
- **回復**: 端数ポジションを `residual_inventory` にする・ポジションを閉じれば、open が1つに戻り ambiguous は自然に解消する。既存台帳の複数建玉は再オープン後もそのまま読め、イベント連鎖は検証できる(ワーカーは引き続き ambiguous として扱う)。テストは `tests/test_trading_live_multi_position.py`。

## L2a: 取引所の型と模擬取引所

- `exchange.py`: 取引所の型(注文・約定・残高・板)、例外の分類(`ExchangeAmbiguous`=届いたか不明、`ExchangeRejected`=明示的な拒否=作られていない確定、`OrderNotFound`、`ExchangeDataError`)、2つの Protocol。**読み取り(`ExchangeReader`)と送信(`OrderSender`)を分けてある**。L3 で実接続を作るときは Reader だけを実装する。金額は Decimal(float は拒否)。
- `mock_exchange.py`: 決定論的な模擬取引所。送信(`OrderSender`)を実装する唯一のクラス(テストで固定)。板・約定・部分約定・取消・拒否・手数料(買いはbase建ても可、maker は負も可)・資金の拘束を模し、故障を意図して注入できる(リクエスト消失/応答消失・拒否・可視性の遅れ・手数料の欠落と後出し・約定の遅れ/重複/矛盾・ページの打ち切り・残高の遅れ・時刻のずれ)。`state_path` を渡すと、変更のたびに(応答を返す・例外を上げる前に)ファイルへ書く。SIGKILL 試験用。
- ネットワーク・ccxt・APIキーは使わない。bitbank にクライアント注文 ID があるかは未確認で、Capabilities の2通りを模擬で試せる形にしてある(どちらを使うかは L3 で確定)。


## L2b: 実行器・再起動時の照合・残高照合(まだどこからも呼ばれない)

- `executor.py`(`OrderExecutor`): `plan`(意図を台帳へ)→ `submit`(`mark_submitting` のコミットのあとで送る)→ 結果を記録 → `step`(照会の1周)。送信は、取引所が**明示的に拒否**したときだけ REJECTED。それ以外の失敗(タイムアウト・切断・分類されない例外)は UNKNOWN_SUBMISSION で、**同じ意図は二度と送らない**。送信できる `sender` は許可リスト(`SENDER_KINDS_ALLOWED = {"mock", "bitbank_spot"}`)にあるものだけ。`bitbank_spot` は明示的な paired session が渡したときだけ通り、既存の `live_control` ゲートはそのまま適用される。
- `reconcile.py`: 起動時の照合(PLANNED は未送信が確定しているので ABANDONED、SUBMITTING は UNKNOWN にしてから解決、ACKED/PARTIALLY_FILLED は取引所に照会して続行)。UNKNOWN は、見つかれば(クライアント注文IDの照会、または注文一覧・約定履歴との突き合わせ)ACKED にする。見つかる前に「作られていない」と決めるのは、(1) settle 時間(既定 120 秒)が経ち、(2) 取引所の答えが完全で該当なし(クライアントIDの方式は、settle 秒あけて2回の空の答え)、(3) 残高に説明できない変化が無い、の**全部**を満たしたときだけ(証拠は台帳に JSON で残す)。売りの証明は base 通貨の残高だけで足りる(JPY 側の不一致で手じまいが止まらない)。時間だけでは何も解除しない。
- 未知の注文の掃除(`sweep`): 台帳に無い注文(オーナーの手動注文など)を見つけたら UNKNOWN_ORDER を trip(買い停止)。履歴の読み取り開始位置は**銘柄ごと**で、その銘柄を完全に読めたときだけ進む(読めなかった・飛ばした銘柄は、前回の位置から読み直す)。約定履歴の読み取り開始位置は、意図の送信時刻から固定(時計とともに動かさない)。クライアントIDが無い取引所で、注文一覧と約定履歴の突き合わせで見つけた1件を意図に結ぶのは、両方が**完全**なときだけ。
- 終端の状態になった意図のポジションが空なら閉じる処理は、起動時と各 `step` でも冪等に行う(終端遷移とクローズの間で止まっても直る)。UNKNOWN_SUBMISSION の NOT_CREATED 証明に使う残高照合(`stable_check`)は、2回の残高読み取りの間に、その結果不明の送信を照合するための注文の照会(クライアント注文IDの照会、または未約定一覧・約定履歴の読み取り)だけを挟む。稼働中の未完了注文をすべて更新するものではなく、その照会が失敗したら判定しない。
- `balance.py`: 台帳から計算した期待残高と取引所の残高を比べる。許容は割合ではなく丸めの単位×(1+約定数)。**手数料が不明な約定**は、base と quote の両方に `max_fee_rate × 約定の大きさ` だけ許容を広げる(手数料が base 建てでも、負でも通る)。この比較は UNKNOWN_SUBMISSION を NOT_CREATED と結論する証明にだけ使い、対象通貨がすべて `ok` で、残高の前後読みが一致し、間の照合読み取りが失敗していないときだけ証明になる。定期的な残高照合や警告は行わず(残高警告の廃止 2026-11-19)、通常のサイクルでは残高を二重に読まない。残高の不一致それ自体は買い・売りを止めず、安全停止も trip しない(オーナー決定 2026-11-18)。期首の残高は**ワーカーが起動時に自動で記録**する(source `exchange_history`、id は `auto_opening:<CCY>`。取引所の total が正で、台帳にその通貨の履歴が無く、2回の残高読みが一致した通貨だけ)。
- `fence.py`: 書き込み権の検査を送信の直前・取消の直前・`step` の最初に行う。失ったら LEASE_LOST を trip して例外で止まる。
- `ingest.py`: 取引所の観測を検証してから台帳へ(注文の矛盾・約定の食い違い・約定の遅れ・手数料の後出しなど)。**自動で DATA_ANOMALY を trip する場合**(解除は人だけ): 注文の整合が取れない/増えるはずの数量が減る/終わった注文が変わる、1つの意図に2つ目の注文、応答が意図と食い違う、約定が注文と合わない、同じ約定が別の中身で再配信、約定が `fill_lag_polls` 回待っても揃わない、ACK 済みの注文が取引所から消えた、注文の取得結果が壊れている。残高の取得結果が壊れている場合に DATA_ANOMALY を trip するのは UNKNOWN_SUBMISSION の照合経路だけで、通常のサイクルの残高取得は失敗しても判定せず fail closed する。
- 手数料が後から分かった再配信は台帳に書かない(L1 は変わった約定を矛盾と扱う)。残高照合の許容が吸収する。

### L2b の既知の限界(フェーズ E の前に解消する)

- 手じまいが詰まる場合: 売りの送信結果が分からなくなった(UNKNOWN)とき、その銘柄の base の残高が証明できない(すでに残高の不一致がある、`max_fee_rate` が不明、ACK を記録できない)と、UNKNOWN のまま残り、同じ銘柄の次の売りは作れない(未完了の注文は銘柄・向きごとに1件)。人が証拠付きで解決する道具は、まだ無い(テストで固定している)。
- クライアントIDが無い取引所で、同じ銘柄・向き・数量・価格の手動注文が**1件だけ**あると、送信が失われた意図の注文として結んでしまう(両方の一覧が完全でも区別できない)。bitbank にクライアント注文IDがあるかを L3 で確定し、無ければ運用で避ける(LIVE の銘柄では手動で同じ条件の注文を出さない)。
- 手数料が不明な約定の許容は、台帳の期間にわたって積み上がる。L3 で、生の応答から手数料を復元する。
- bitbank の `remaining_amount` の意味(取消後の残量など)を L3 で確かめるまで、「約定数量+残量が注文数量と合わない」応答は ACK を記録しない(DATA_ANOMALY)。

## L3a: bitbank の読み取り層(ccxt なし・ネットワークなし)

`src/docich/trading/exchanges/bitbank_live/`(`live/` の外。`live/` はこのパッケージを import しない)。**読み取りだけ**で、送信・取消の経路は存在しない(AST のテストで固定)。

- `credentials.py`: 読み取り専用キーの読み込み。置き場は `<state>/trading-live/owner/bitbank_readonly_key.json`(0600、owner ディレクトリは 0700、symlink 不可)。環境変数は `source="env"` と明示したときだけ使い、読んだら環境から消す。キーが無い・検査に通らない場合は fail-closed(理由コードだけを例外にし、値・パスは出さない)。`Credentials` は repr/str/pickle で何も出さない。同じ検査を共有する file-only の `load_spot_order_credentials` が、明示的な発注プロセス用の固定ファイル `bitbank_spot_order_key.json`(`kind: docich-bitbank-spot-order-key`)を読む。こちらは環境変数を使わない。
- `parse.py`: bitbank の生の JSON → L2 の型。**文字列から Decimal**(float は拒否)、時刻は13桁のミリ秒だけ(秒・マイクロ秒は拒否)、注文の状態は bitbank の語彙そのまま、`remaining_amount` は送られた値のまま(正規化しない)。手数料は `fee_amount_base`/`fee_amount_quote` の生の文字列から決め、不明瞭なら UNKNOWN(両方非0、`fee_occurred_amount_quote` との不一致、符号が料率と逆、など)。`max_fee_rate` は4つの料率の絶対値の最大(1つでも読めなければ None)。
- `errors.py`: 失敗の分類。読み取りが上げてよいのは `ExchangeAmbiguous`(既定)と `OrderNotFound`(注文の照会に対する 50009 だけ)で、`ExchangeRejected` は上げない。60002 を資金拘束のエラーとして扱わない。メッセージにはエンドポイント・種類・HTTP・コード・例外クラス名だけ。
- `reader.py`: `BitbankReader`(`ExchangeReader` だけを実装)。**bitbank にクライアント注文 ID は無い**(`Capabilities(client_order_id=False, fetch_by_client_id=False, fills_by_order=True)`)ので、送信が失われた注文は一覧との突き合わせでしか見つけられない。**一覧の完全性は保守的**: 未約定の注文は件数が `count` 未満のときだけ完全、約定履歴は件数が1ページ未満か、最古の約定がすでに開始時刻より前のときだけ完全。`since` は取引所に送らず(単位の記述が文書間で食い違うため)、手元で絞る。市場ルール・料率は作るときに読み、`market_rules` はネットワークに出ない。作れなければ `ReaderUnavailable`。
- 取引所の接続(ccxt を使う `transport`)と、オーナーが実キーで行う確認(`check`)は L3b。

**公式ドキュメント(github.com/bitbankinc/bitbank-api-docs)で確認済み**: 注文作成にクライアント注文 ID は無い / `remaining_amount` は「未約定の数量」/ 時間窓の規則(`request_time < server + 1000` かつ `server − request_time ≤ window`)/ 日本語版は約定履歴などの `since`・`end` を**ミリ秒**と明記(英語版は単位の記載なし。ccxt は秒で送る)/ 3ヶ月より前の注文の照会は 50009 / 公開 API の `depth.timestamp` は「published at」。

**未確認(実キーで確かめる = フェーズ B)**: `since` の単位 / 取消後・一部約定後の `remaining_amount` の意味 / maker のリベートの符号 / 手数料の丸め / 参照のみの権限で読める範囲 / 応答の形(`data.pairs`・`data.assets`・`data.orders`・`data.trades`)が文書のとおりか。合成データでの試験は、仮定との整合しか示さない。

## L3b: ccxt の transport・起動ファクトリ・オーナー用の確認(まだどこからも呼ばれない)

- `transport.py`(`CcxtReadTransport`): ccxt を使うのはここだけ(関数の中で遅延 import)。ccxt の統一メソッド(`fetch_order` など。`fee_amount_base` を捨て、数値を float にする)は使わず、固定の GET の暗黙メソッド6つ(`ENDPOINTS`)を呼び、生の JSON を `parse_float=str` で受ける(float が一度も生まれない)。**読み取り専用を3層で担保**: ① エンドポイント名は固定の表から引く ② `exchange.fetch` を包み、GET 以外・本文つき・表に無い URL は、送信の前に `ReadOnlyViolation`(`BaseException`。誰も握りつぶせない)③ この層にも読み取り層にも送信の型(`submit_order`・`cancel_order`・`kind`)が無い。レート制限は最小間隔 0.2 秒(bitbank の上限 10回/秒の半分)、429 は1回だけ待って再試行、他の失敗は再試行しない(次の周で L2b が再び見る)。ccxt のログ・`verbose`・直近の要求ヘッダーは封じ、呼び出しのたびに消す。例外は L2 の例外にだけ変換し、ccxt の例外の連鎖・本文・キーは残さない。
- `factory.py`(`open_bitbank_reader`): キーを読む → transport → 市場ルールと料率を読む → **時計を、応答の `Date` ヘッダーで確かめる**(ローカルが bitbank より 0.8 秒を超えて進んでいる、または 7 秒を超えて遅れていれば作らない。bitbank は未来側に1秒以上ずれた要求を、時間窓より古い要求を拒否する)。bitbank に時刻を返す API は無く、**`depth.timestamp` は板の公開時刻で「今」ではない**(同期した端末で 0.7〜2.4 秒遅れて見えた)ので使わない。`Date` は秒単位に切り捨てられるので、1回の呼び出しは「ずれの区間」(送信時刻−Date−1 秒 〜 受信時刻−Date)しか与えない。0.2 秒おきの8回の区間を**重ねて**絞り、**進みは区間の上限、遅れは区間の下限**で判定する(安全側。通信が遅いと区間が広がり、判定は厳しくなるが誤って通すことはない)。区間どうしが食い違えば(測定中に時計が動いた)作らない。署名つきの呼び出しが時間窓のエラー(公式の errors.md で時刻に関わるのは 20034・20035・20037 だけ。20004・20033・20036・20038・20039 は要求ヘッダーの欠落・誤りで、`request` に分類)で失敗すれば、それも時計の失敗として分類される。どれかが失敗すれば `ReaderUnavailable`(理由だけ)。
- `check.py`(オーナーが実キーで行う確認 = フェーズ B。注文は一切送らない): 件数・形・単位だけを出す(キー・秘密・残高の額・価格・注文の中身は出さない。出力の前に、すべての行をキーと秘密に照合し、含まれていれば何も出さず終了コード 2)。内容: 時計の推定(`Date` ヘッダー)と銘柄ごとの板の公開時刻の古さ(参考)/ 11銘柄の市場ルール(公開情報)/ 各 GET エンドポイントの到達(assets・active_orders・trade_history・deposit_history・spot/status)/ 存在しない注文を照会したときのエラー(50009 か)/ 残高の形(`onhand == free + locked`)/ 銘柄ごとの未約定注文数と約定数 / 手数料の形(base・quote・負)・maker/taker・時刻の桁数 / 履歴にある注文を照会し `開始数量 == 約定+残量` かの分布 / 約定履歴の `since` の単位の実験(なし・ミリ秒・秒で件数を比べる)/ 読み取り層の全メソッドの煙試験。

**フェーズ B の手順(オーナー)**: ① bitbank の画面で API キーを発行(権限は**参照のみ**。取引・出金は付けない)② `umask 077; install -d -m 700 <state>/trading-live/owner`、キーを `<state>/trading-live/owner/bitbank_readonly_key.json`(0600、`{"schema_version":1,"kind":"docich-bitbank-readonly-key","api_key":"…","api_secret":"…"}`)に置く ③ `python -m docich.trading.exchanges.bitbank_live.check --state-dir <state>`(ccxt が入った環境。`pip install -r requirements-trading.txt`)④ 出力を Issue に貼る。結果で、`since` の単位・`remaining_amount` の意味・maker のリベートの符号・参照のみの権限で読める範囲が確定する。**キーは会話・リポジトリ・Issue に書かない。**

## L4a オーナー用 CLI(status / cashflow)

`src/docich/trading/live_owner/`(`live/` を読むだけで、`exchanges/`・ccxt・ネットワークは使わない。テストで固定)。状態ディレクトリを `--state-dir` に**絶対パス**で渡す(存在しないディレクトリは作らずに終了コード `2` で拒否する)。

- `python -m docich.trading.live_owner --state-dir <state> status`: **読み取り専用**。台帳が無ければ `ledger: not created` の1行だけで、ディレクトリもファイルも作らない。あれば有効な方針ダイジェストの先頭12桁(無ければ `none`)、状態ごとの意図数(0件の状態は出さない)、未決済・`residual_inventory` のポジション数、安全停止の理由(無ければ `none`)、UNKNOWN_SUBMISSION の数、cashflow 数、event 数を出す。金額と注文 ID は出さない。
- `python -m docich.trading.live_owner --state-dir <state> cashflow list`: **読み取り専用**。1行1件で id・kind・通貨・時刻(ISO)・source を出し、金額は**端末(TTY)のときだけ**表示する。それ以外は `amount: hidden`。台帳が無ければ status と同じ1行。
- `python -m docich.trading.live_owner --state-dir <state> cashflow add --kind {deposit,withdrawal,adjustment} --currency CUR --amount DEC --occurred-at ISO8601 --operator NAME --note TEXT [--confirm TOKEN] [--allow-repeat]`: 入出金・調整の記録。**期首残高のためにオーナーが実行する必要は無い**(ワーカーが `auto_opening:<CCY>` として自動記録する)。**二段確認**: `--confirm` 無しは正規化した内容と token を出すだけで、何も書かず・LIVE ディレクトリも作らない。`--confirm TOKEN` で同じ引数から token を再計算し、一致したときだけ writer lease を取って記録する。preview は、その confirm が台帳を作るのか既存の台帳に書くのかも `ledger: will be created` / `ledger: exists` の1行で示す。token は内容と台帳 ID のハッシュなので、台帳ができた後は preview を取り直す。同じ id は `already recorded`、同じ (kind, 通貨, 金額) は `--allow-repeat` が無ければ拒否。
- `python -m docich.trading.live_owner --state-dir <state> activate --snapshot PATH --experiment-digest HEX64 --operator NAME --note TEXT [--confirm TOKEN]`: 実験スナップショットから11銘柄の方針セットを作り、そのオーナー有効化を台帳へ記録する。制御ファイルも git の revision も**必要ない**。**二段確認**: `--confirm` 無しは `ledger` / `policy_set` / `experiment` / `mode`(制御ファイルの参考表示。無ければ `none`)/ `operator` / token を出すだけで何も書かない。`--confirm TOKEN` で token を再計算して一致したときだけ writer lease を取り、`record_owner_activation` を呼ぶ(方針セットの digest だけを記録し、`enable_digest` は None)。終了コードは上と同じ(0/1/2/5)。この CLI は従来どおり任意のスナップショットを `owner_initial` / `owner_manual` として記録できる。
  WebUI の初期セットアップは別経路で、`owner_initial` のみを扱い、別の有効方針を上書きしない。
- `python -m docich.trading.live_owner --state-dir <state> control status|on|off [--mode shadow|trade] [--yes-trade] --operator NAME`: オン/オフ制御ファイルの読み書き。`status` は読み取り専用。`on` は `--mode` を省くと既存の `mode`(読めなければ `shadow`)を保ち、`--mode trade` は `--yes-trade` が無ければ拒否する。`off` は `mode` を保ったまま `state` を `off` にする。無い `trading-live` / `owner` は 0700 で作り、ファイルは 0600 で原子的に置き換える。writer lease は要らない。
- `python -m docich.trading.live_owner --state-dir <state> unknown list`: **読み取り専用**(取引所にも触れない)。UNKNOWN_SUBMISSION の意図を1行ずつ、**完全な intent id**・銘柄・売買・注文種別・経過秒数で出す(そのまま `show`/`resolve --intent` に渡せる)。金額と価格は TTY のときだけ(それ以外は `amount: hidden` / `price: hidden`)。無ければ `unknown_submissions: none`。
- `python -m docich.trading.live_owner --state-dir <state> unknown show --intent ID`: **読み取り専用**(writer lease を取らない)。解決に使う時間窓(`submitted_at` ± `skew_margin_s`)、未約定一覧と**約定履歴**が完全か、候補(台帳が意図に結び付けていない注文。**未約定一覧と、窓の開始以降の約定履歴の両方**から集める。約定履歴にしか無い注文は id で照会する)と一致数(`matching`)、台帳が既に注文を持っているか(`ledger_order`)、`settle_s` を過ぎたかを出す。取引所の注文 ID は TTY のときだけ。
- `python -m docich.trading.live_owner --state-dir <state> unknown resolve --intent ID (--found-order EID | --not-created) --evidence TEXT --operator NAME [--override-incomplete-proof] [--confirm TOKEN]`: **人が証拠つきで UNKNOWN_SUBMISSION を解決する**。`--found-order` は取引所の注文が意図と一致(`reconcile.matches_intent`)し、かつ他の意図のものではないときだけ `found_by_owner` として ACKED へ繋ぐ(一致しなければ何も記録せず拒否)。`--found-order` の拒否は、取引所がその ID を知らない・台帳がこの意図に別の注文を既に持っている・取引所の注文がこの意図に一致しないか整合しない・その ID が別の意図のもの、のいずれの場合も書き込みの前に起きる。`--found-order` は有効な安全停止があっても止められない(取引所に実在する注文を結ぶ経路で、機械自身の解決も同じ規則)。`--not-created` はまず、① 台帳がこの意図の注文を既に記録していないこと、② 未約定一覧**と約定履歴**のどちらにも一致する注文が無いこと、③ `settle_s` 経過、を確かめ、次に**機械自身の証明**(`reconcile.try_resolve`)を試す。機械が `acked`/`not_created` を出せばそれで終わり(オーナーの文面は証拠として記録しない)。**既存の注文・`anomaly`・機械が記録した data anomaly は `--override-incomplete-proof` では越えられない**(拒否)。機械自身の証明の途中で記録された安全停止、または UNKNOWN_ORDER / DATA_ANOMALY / FEE_MISSING / BALANCE_MISMATCH のいずれかが有効な停止として残っている場合も、`--override-incomplete-proof` があっても `--not-created` を拒否し、停止は `trip list` で確認して `trip clear`(preview が出す token を `--confirm` で返す。writer lease が必要)で解除する。LEASE_LOST と ENABLE_MISSING(旧行)はこの4つに含まれず、`--not-created` の override を妨げない。ENABLE_MISSING は旧行の互換のための理由で、他の停止と同じ人の解除経路(`trip clear`)で解除する。制御ファイルや方針セットは必要ない。LEASE_LOST もオーナーの証拠だけで解除する。`--override-incomplete-proof` があるときは有効な停止が preview を拒否して token を出さず、無いときは preview が `safety_stops_active: <reasons>` を出したうえで token も出すが、confirm は停止が有効な間は拒否し、安全状態を読めないときは preview と confirm の両方を拒否する。機械が何も記録せず `waiting` のときだけ、このフラグで `owner_not_created` として NOT_CREATED にし、証拠 JSON を残す。証拠を書く直前に取引所を読み直し、そのとき一致する注文が見えた場合は、override を拒否して何も書かない。二段確認で、`--confirm` 無しは何も書かず token を出すだけ。確認時は writer lease を取り、**lease の下で全チェックをやり直す**ので、**先に live worker を止めておく**こと(終了コード `5`)。証拠 JSON は `{"method":"owner_manual","mode":... ,"operator":...,"evidence":...,"checked_at":...,"machine":{"result":...,"open_list_complete":...,"fills_complete":...,"candidates":...,"override_incomplete_proof":...}}`(found のときだけ `found_order_id` を追加。金額・注文 ID は not_created 側には入れない)。
- `python -m docich.trading.live_owner --state-dir <state> trip list`: **読み取り専用**(writer lease を取らない)。有効な停止を1行ずつ `trip_id=<id> reason=<reason> tripped_at=<ISO 8601 UTC>` で出し、続けて `detail=<detail>` を出す(**TTY のときだけ**。それ以外は `detail=hidden`)。停止が無ければ `no active safety stops`。台帳が無ければ `ledger: not created` の1行だけで、何も作らない。安全状態を読めないときは `refused: the safety state cannot be read` で終了コード `1`。
- `python -m docich.trading.live_owner --state-dir <state> trip clear --reason {unknown_order,balance_mismatch,data_anomaly,fee_missing,enable_missing,lease_lost} --operator NAME --note TEXT --evidence TEXT [--confirm TOKEN]`: **人が安全停止を解除する経路**。`--operator` と `--note` は前後の空白を除いて 1〜200 文字、`--evidence` は `unknown resolve` と同じ 20〜400 文字。**二段確認**で、`--confirm` 無しは `trip_id` / `reason` / `tripped_at` / `detail`(TTY のみ)/ token を出すだけで何も書かず、lease も取らない。`--confirm TOKEN` が一致したときだけ writer lease を取り、lease の下で安全状態を読み直し、同じ理由の停止の `trip_id` が preview と変わっていれば `refused: the safety stop changed since the preview; run the preview again` で拒否する(再 trip された停止は古い token を無効にする)。`enable_missing` は旧行の互換のための理由で、有効化ファイルの確認は無い。他の停止と同じく、preview の token と人が渡した証拠だけで解除する。成功すると `cleared: <reason> (trip_id=<id>)`。live worker が lease を保持していれば終了コード `5`。
- 終了コード: `0` 成功 / `1` 拒否(refused) / `2` 引数の誤り(usage) / `5` writer lease を live worker が保持(停止してから再実行)。
- 金額は `Decimal`(浮動小数は拒否、8桁まで)。`--occurred-at` はタイムゾーン付きの ISO 8601 のみ(naive と未来は拒否)。`--operator` と `--note` は前後の空白を除いて 1〜200 文字。

## L4b: 常駐 worker の資本・期首残高

- 資本 C は**取引所の JPY 残高**(free と locked の合計)に、ボット自身の保有ポジションの取得原価(`views.deployed_cost`、約定から再計算)を足したもの(equity at cost)。固定値でも cashflow の合計でもなく、毎サイクル取り直す。deployed はポジション原価+未完了の買い注文の notional なので、headroom = C − deployed = JPY 残高(total) − 未完了買いの notional。JPY 残高に含まれる拘束分を二重計上しない。
- 使用可能な買い資金 F は取引所の free JPY(0 未満にしない)。口座全体の JPY を使ってよい。取引所への入金は台帳に書かなくても次のサイクルの C を増やす。
- 期首残高: worker が起動時に、監視通貨(銘柄の base/quote)ごとに、台帳履歴(cashflow・約定・手数料通貨・取引所に届いた可能性のある intent)が無く、取引所の total が正で、2回の残高読みが一致する場合に `auto_opening:<CCY>` の deposit を1件記録する(source `exchange_history`)。dust も同じ。読みが食い違えば次の機会に再試行する。オーナーは期首残高のために `cashflow add` を実行しない。後からの入金は取引所の入金履歴から `deposits.run` が台帳へ自動記録する(#4587)。
- 定期的な残高照合・残高警告は行わない(残高警告の廃止 2026-11-19)。通常のサイクルは残高を1回だけ読み、不一致の通貨数などのフィールドを status に出さない。金額・残高そのものも status に書かない。残高の比較は UNKNOWN_SUBMISSION の NOT_CREATED 証明にだけ残り、比較自体は安全停止を trip せず、売買を止めない。
- status(`live_status.json`)は実際の周期を `interval_ms`(30,000〜3,600,000 の整数)として残す。CLI の `--interval` は30〜3600秒で、ミリ秒に丸めることで 600.5 秒のような端数も失わない。WebUI は running status の鮮度を **3×`interval_ms`** とし、連続失敗時は `next_wait` の再試行待ち(interval 以上、interval が300秒以下なら300秒で頭打ち)を加える。`interval_ms` が無い旧 status や範囲外・非整数の値は、従来の60秒 / 180秒へフォールバックする。running status は cycle の終了時に書き、**lease を失った後は running も exited も書かない**。
- 起動時の残高取得や自動記録が失敗しても inert にならず、終了もしない。周期ループで再試行する。inert になるのは方針セットが無い場合だけ(`no_active_policy_set`)。
- 常駐化(systemd user unit)のテンプレートと終了コードの扱いは [live-worker-systemd.md](live-worker-systemd.md)。ユニットのインストール・enable はオーナーの作業。既定は `sender=None` で発注経路が無い。`run --spot-order-sender` を明示した別プロセスだけが、同じ1口座の固定キーから reader と spot sender の対を作る(下記 L4b-3)。

## L4b-3: 明示的な spot session(将来の F、既定では使わない)

- `run --spot-order-sender` は1回のプロセス起動時のサービス役割選択で、実行中の売買スイッチではない。付けた場合だけ、`bitbank_spot_order_key.json`(0600、owner 0700、symlink 不可、環境変数なし、`kind: docich-bitbank-spot-order-key`)を読み、同じ `Credentials` から GET 専用 `BitbankReader` と `BitbankSpotSender` を作る。reader と sender に別口座を混ぜない。
- キーの権限は残高・注文・約定の参照と現物の注文作成・取消に限り、**出金権限は付けない**。ファイル名は発注可能なプロセス用であることだけを示し、取引所側スコープの証明ではない。
- 既定の起動はキーが置かれていても reader だけを使い、sender を作らない。新規注文の実行時スイッチは従来どおり `live_control.json` の `on` + `trade` だけ。オプション付きセッションの起動失敗は config/auth/clock の既存の終了コードで fail-closed になり、reader 専用へフォールバックしない。
- これは将来の F のプロセス選択肢。実注文 E はオーナーの別ハンドオフで、1注文ごとの明示承認を含む E の手順はここでは扱わない。ユニットテンプレート・デプロイ・自動化にこのフラグを設定しない。


## L4c: shadow の模擬保有・資金・約定(Issue #3623)

`src/docich/trading/live/shadow.py` は **shadow モード専用**の模擬執行である。ワーカーの通常の
`act.decide_and_act` フックから呼ばれ、`trade` / `monitor` の経路と実 state には触れない。

- 模擬状態は既存のハッシュ連鎖 LIVE イベントの `shadow_fill` だけから `ShadowBook` として
  投影する。実際の intent / order / fill / position 行、および実ポジション・実 decision key は
  模擬ポジションや模擬 decision key の種にしない。`shadow_fill` は実注文の `fills` テーブルにも
  実ポジションにも書かない。実 dust を `residual_inventory` にすることもない。
- 資金の基準は毎サイクル取り直す取引所の現在の資本(JPY total + 実オープンポジション原価)で、
  これに ShadowBook の模擬 quote キャッシュ差分と模擬ポジション原価を足す。使用可能 quote は
  実際の free JPY に模擬キャッシュ差分を足したもの。買いの代金・手数料はここから一度だけ引き、
  売却代金・実現損益はここに戻す。現物の free を超えて買わず、模擬在庫を超えて売らない。
- 発注計画は通常の純粋な scan / size 経路(同じ policy、market rules、`DecisionSet.markets` の
  検査済み板)で作る。約定は板の価格制限内の VWAP で、買いは limit 以下の ask、売りは limit 以上の
  bid を深さの分だけ消費する。数量は実際の amount step に切り下げ、可視 depth・模擬在庫・
  模擬 quote のどれかが尽きたら partial で止める。`levels_used` は切り下げ後の数量が実際に
  触れた level 数だけを数え、VWAP と fee はその数量の notional から計算する。板がない/薄い場合は
  fill を作らず skip を記録する。手数料は market rules の `max_fee_rate` を quote 建てで使う
  (不明なら fill しない)。同じ signal の partial 売りが同じ板・同じ bar の表示 depth を
  二度消費しないよう、fill の `book_ts_ms`/`bar_ts_ms` を durable な印として replay 後も保持する。
  板または bar が更新されれば新しい attempt として扱う。
- `shadow_fill` の payload は文字列と整数だけである。full policy digest、entry position id、
  symbol/side、計画数量・価格、模擬数量・notional・VWAP・fee、fee rate/currency、decision key、
  opportunity/signal、experiment digest、bar の時刻と timeframe、板の時刻、levels used を持つ。
  decision key は `shadow1:` 名前空間で、実 `live1:` と衝突しない。fill id は decision key から
  決定的に作る。position id は `position_id[:8]` を使う売り sizing のため、先頭 8 文字が
  ハッシュ由来の値になる。
- 再起動時はイベントを event id の小さい順に bounded page で全件 replay する。プロセス内では
  `CycleState.shadow_book` に投影を保持し、最後に見た event id 以降だけを読む。別 seed 行はなく、
  取引所資本は毎サイクル読み直す。モードを trade に変えても shadow イベントは消さず、shadow に
  戻すと続きから投影する。同じ fill id の再送は同一内容なら無視し、異なる内容なら fail-closed。
- 書き込みは event append が先、メモリ上の ShadowBook への適用が後である。append 後・適用前に
  落ちても、次回の replay がその 1 件を適用する。append が失敗した場合は event も book も変えず、
  次のサイクルで再試行する。`LeaseLost` はそのまま伝播する。
- 読み取り専用の比較は
  `python -m docich.trading.live_owner --state-dir <state> shadow summary`(別名 `report` /
  `status`)で行う。`shadow_report` が実 PAPER schema(`paper_fills` の `signal_context` TEXT)を
  `path.as_uri()` + `mode=ro` で読み、shadow の plan/fill/position 数、planned と fill の
  突き合わせ、PAPER との symbol/side/opportunity/strategy/bar の一致、
  `experiment_digest` 同士の一致、欠けている証拠(`shadow_fill` / `shadow_plan` /
  `paper_ledger` / `paper_table` / `paper_read` / `paper_fill` / `paper_digest` /
  `shadow_digest`)を出す。LIVE の per-symbol `policy_digest` は PAPER との比較に使わない。
  PAPER の missing / no table / read error と valid empty は区別し、identity・strategy・
  observation window が曖昧または不一致の候補は verified にしない。verified のときは
  amount/price/fee の差を出し、fee 証拠が無ければ `unavailable` とする。trade モードの
  decision は比較から除く。shadow の plan/fill が無いときは「0 件の一致」ではなく
  `shadow: no evidence` と表示する。`live_owner status` にも shadow の有無と件数だけを出す
  (金額・ID は出さない)。

## L4b-2: 公開市場データの欠損検知(Issue #4586)

- 周の手順 8 の銘柄ごとの stale を `CycleState.market_gap` が周期をまたいで追跡する。`stale_since` は各銘柄の連続欠損が始まった時刻を保持し、新鮮なフレームが取れた時点で消える(復帰)。`all_stale_since` / `all_stale_cycles` は全銘柄が連続して欠損している期間と周数を数える。フレーム取得に到らなかった周(stop・lease 喪失・step 失敗・cycle error)は欠損の継続とも復帰ともみなさず、追跡を変えない。
- status には `market_gap_scope`(`none`/`partial`/`all`)、`market_stale_count`、`market_gap_since`/`market_gap_seconds`(最古の継続欠損の開始時刻と秒数)、`market_all_stale_since`/`market_all_stale_seconds`/`market_all_stale_cycles` を出す。`stale_symbols` が欠損中の銘柄一覧。
- exit 8 と再試行の条件(決定): **一部欠損は永遠に再試行し、終了しない**。全銘柄の連続欠損が `CycleConfig.market_gap_exit_s`(既定 1800 秒、仮の値・オーナー確認待ち)を超えたら `EXIT_MARKET_DATA` を返し、runner が終了コード 8(再起動可)で終わる。`None` で終了しない観測専用にできる。判定はモードに依らない(monitor でもデータ源が死んでいれば同じ条件)。reader 不健康の周は従来どおり `reader_unhealthy` の失敗周で、gap 終了とは別扱い。
- 未決: 閾値の実値、取引所側障害で再起動が繰り返されるときの上限(systemd の StartLimit 任せでよいか)、単一銘柄の恒常的欠損(上場廃止等)を別途扱うか。

## L1 でやらないこと

APIキー・取引所への接続・ワーカー・通知・昇格の適用。いずれも後続の PR で、PR ごとにオーナーの確認を取る。実注文(E)と常駐(F)は、オーナーの明示承認と、取引所の API キーの発行(出金権限なし)が前提。
