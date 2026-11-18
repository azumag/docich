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

## 安全停止

止める理由は6つ定義してある。コードが自動で止めるのは、未知の注文(`record_order_observation`)、手数料の未取得と約定の矛盾(`record_fill`)、書き込み権の喪失(`lease_lost`)、データ異常(`ingest.anomaly`)の4つ。`balance_mismatch` と `enable_missing` は旧行の互換のために列挙だけ残しており、**新しくは誰も書かない**(残高の不一致は停止ではなく警告。オーナー決定 2026-11-18)。止まっている間は、新規の買いの送信だけを拒否する。売り(手じまい)は送れる(オーナー決定 2026-11-16)。二重売りは、同じ銘柄・同じ向きの未完了の注文を1件に制限する台帳の索引が防ぐ(結果不明の売りが残っていれば、2つ目の売りは作れない)。結果不明の買いは売りを止めない(約定していても保有が増えるだけ)。照合と監視は止まっていても続ける。オン/オフの制御ファイルはこれとは別で、OFF は買いも売りも止めるが、安全停止ではないので trip も解除操作も要らない。**安全停止の解除は人だけ**。コードから解除を呼ぶ処理は無く、テストで確かめている。

## L2a: 取引所の型と模擬取引所

- `exchange.py`: 取引所の型(注文・約定・残高・板)、例外の分類(`ExchangeAmbiguous`=届いたか不明、`ExchangeRejected`=明示的な拒否=作られていない確定、`OrderNotFound`、`ExchangeDataError`)、2つの Protocol。**読み取り(`ExchangeReader`)と送信(`OrderSender`)を分けてある**。L3 で実接続を作るときは Reader だけを実装する。金額は Decimal(float は拒否)。
- `mock_exchange.py`: 決定論的な模擬取引所。送信(`OrderSender`)を実装する唯一のクラス(テストで固定)。板・約定・部分約定・取消・拒否・手数料(買いはbase建ても可、maker は負も可)・資金の拘束を模し、故障を意図して注入できる(リクエスト消失/応答消失・拒否・可視性の遅れ・手数料の欠落と後出し・約定の遅れ/重複/矛盾・ページの打ち切り・残高の遅れ・時刻のずれ)。`state_path` を渡すと、変更のたびに(応答を返す・例外を上げる前に)ファイルへ書く。SIGKILL 試験用。
- ネットワーク・ccxt・APIキーは使わない。bitbank にクライアント注文 ID があるかは未確認で、Capabilities の2通りを模擬で試せる形にしてある(どちらを使うかは L3 で確定)。


## L2b: 実行器・再起動時の照合・残高照合(まだどこからも呼ばれない)

- `executor.py`(`OrderExecutor`): `plan`(意図を台帳へ)→ `submit`(`mark_submitting` のコミットのあとで送る)→ 結果を記録 → `step`(照会の1周)。送信は、取引所が**明示的に拒否**したときだけ REJECTED。それ以外の失敗(タイムアウト・切断・分類されない例外)は UNKNOWN_SUBMISSION で、**同じ意図は二度と送らない**。送信できる `sender` は許可リスト(`SENDER_KINDS_ALLOWED = {"mock"}`)にあるものだけ。フェーズ E でこの1行をオーナーのレビュー付きで変える。
- `reconcile.py`: 起動時の照合(PLANNED は未送信が確定しているので ABANDONED、SUBMITTING は UNKNOWN にしてから解決、ACKED/PARTIALLY_FILLED は取引所に照会して続行)。UNKNOWN は、見つかれば(クライアント注文IDの照会、または注文一覧・約定履歴との突き合わせ)ACKED にする。見つかる前に「作られていない」と決めるのは、(1) settle 時間(既定 120 秒)が経ち、(2) 取引所の答えが完全で該当なし(クライアントIDの方式は、settle 秒あけて2回の空の答え)、(3) 残高に説明できない変化が無い、の**全部**を満たしたときだけ(証拠は台帳に JSON で残す)。売りの証明は base 通貨の残高だけで足りる(JPY 側の不一致で手じまいが止まらない)。時間だけでは何も解除しない。
- 未知の注文の掃除(`sweep`): 台帳に無い注文(オーナーの手動注文など)を見つけたら UNKNOWN_ORDER を trip(買い停止)。履歴の読み取り開始位置は**銘柄ごと**で、その銘柄を完全に読めたときだけ進む(読めなかった・飛ばした銘柄は、前回の位置から読み直す)。約定履歴の読み取り開始位置は、意図の送信時刻から固定(時計とともに動かさない)。クライアントIDが無い取引所で、注文一覧と約定履歴の突き合わせで見つけた1件を意図に結ぶのは、両方が**完全**なときだけ。
- 終端の状態になった意図のポジションが空なら閉じる処理は、起動時と各 `step` でも冪等に行う(終端遷移とクローズの間で止まっても直る)。残高照合は、2回の残高読み取りの間に未完了の注文を照会して台帳を最新にし、その照会が失敗したら判定しない。
- `balance.py`: 台帳から計算した期待残高と取引所の残高を比べる。許容は割合ではなく丸めの単位×(1+約定数)。**手数料が不明な約定**は、base と quote の両方に `max_fee_rate × 約定の大きさ` だけ許容を広げる(手数料が base 建てでも、負でも通る)。静止しているとき(未解決の注文が無く、残高の前後読みが一致)だけ判定し、不一致が **30 秒以上あけて2回** 続いたら通貨を `warning_currencies` に残す。**買いを止めず、安全停止も trip しない**(オーナー決定 2026-11-18)。期首の残高は**ワーカーが起動時に自動で記録**する(source `auto_opening`。取引所の total が正で、台帳にその通貨の履歴が無く、2回の残高読みが一致した通貨だけ)。
- `fence.py`: 書き込み権の検査を送信の直前・取消の直前・`step` の最初に行う。失ったら LEASE_LOST を trip して例外で止まる。
- `ingest.py`: 取引所の観測を検証してから台帳へ(注文の矛盾・約定の食い違い・約定の遅れ・手数料の後出しなど)。**自動で DATA_ANOMALY を trip する場合**(解除は人だけ): 注文の整合が取れない/増えるはずの数量が減る/終わった注文が変わる、1つの意図に2つ目の注文、応答が意図と食い違う、約定が注文と合わない、同じ約定が別の中身で再配信、約定が `fill_lag_polls` 回待っても揃わない、ACK 済みの注文が取引所から消えた、残高や注文の取得結果が壊れている。
- 手数料が後から分かった再配信は台帳に書かない(L1 は変わった約定を矛盾と扱う)。残高照合の許容が吸収する。

### L2b の既知の限界(フェーズ E の前に解消する)

- 手じまいが詰まる場合: 売りの送信結果が分からなくなった(UNKNOWN)とき、その銘柄の base の残高が証明できない(すでに残高の不一致がある、`max_fee_rate` が不明、ACK を記録できない)と、UNKNOWN のまま残り、同じ銘柄の次の売りは作れない(未完了の注文は銘柄・向きごとに1件)。人が証拠付きで解決する道具は、まだ無い(テストで固定している)。
- クライアントIDが無い取引所で、同じ銘柄・向き・数量・価格の手動注文が**1件だけ**あると、送信が失われた意図の注文として結んでしまう(両方の一覧が完全でも区別できない)。bitbank にクライアント注文IDがあるかを L3 で確定し、無ければ運用で避ける(LIVE の銘柄では手動で同じ条件の注文を出さない)。
- 手数料が不明な約定の許容は、台帳の期間にわたって積み上がる。L3 で、生の応答から手数料を復元する。
- bitbank の `remaining_amount` の意味(取消後の残量など)を L3 で確かめるまで、「約定数量+残量が注文数量と合わない」応答は ACK を記録しない(DATA_ANOMALY)。

## L3a: bitbank の読み取り層(ccxt なし・ネットワークなし)

`src/docich/trading/exchanges/bitbank_live/`(`live/` の外。`live/` はこのパッケージを import しない)。**読み取りだけ**で、送信・取消の経路は存在しない(AST のテストで固定)。

- `credentials.py`: 読み取り専用キーの読み込み。置き場は `<state>/trading-live/owner/bitbank_readonly_key.json`(0600、owner ディレクトリは 0700、symlink 不可)。環境変数は `source="env"` と明示したときだけ使い、読んだら環境から消す。キーが無い・検査に通らない場合は fail-closed(理由コードだけを例外にし、値・パスは出さない)。`Credentials` は repr/str/pickle で何も出さない。
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
- `python -m docich.trading.live_owner --state-dir <state> cashflow add --kind {deposit,withdrawal,adjustment} --currency CUR --amount DEC --occurred-at ISO8601 --operator NAME --note TEXT [--confirm TOKEN] [--allow-repeat]`: 入出金・調整の記録。**期首残高のためにオーナーが実行する必要は無い**(ワーカーが `auto_opening` として自動記録する)。**二段確認**: `--confirm` 無しは正規化した内容と token を出すだけで、何も書かず・LIVE ディレクトリも作らない。`--confirm TOKEN` で同じ引数から token を再計算し、一致したときだけ writer lease を取って記録する。preview は、その confirm が台帳を作るのか既存の台帳に書くのかも `ledger: will be created` / `ledger: exists` の1行で示す。token は内容と台帳 ID のハッシュなので、台帳ができた後は preview を取り直す。同じ id は `already recorded`、同じ (kind, 通貨, 金額) は `--allow-repeat` が無ければ拒否。
- `python -m docich.trading.live_owner --state-dir <state> activate --snapshot PATH --experiment-digest HEX64 --operator NAME --note TEXT [--confirm TOKEN]`: 実験スナップショットから11銘柄の方針セットを作り、そのオーナー有効化を台帳へ記録する。制御ファイルも git の revision も**必要ない**。**二段確認**: `--confirm` 無しは `ledger` / `policy_set` / `experiment` / `mode`(制御ファイルの参考表示。無ければ `none`)/ `operator` / token を出すだけで何も書かない。`--confirm TOKEN` で token を再計算して一致したときだけ writer lease を取り、`record_owner_activation` を呼ぶ(方針セットの digest だけを記録し、`enable_digest` は None)。終了コードは上と同じ(0/1/2/5)。**src の中でオーナー有効化を記録する唯一の場所**。
- `python -m docich.trading.live_owner --state-dir <state> control status|on|off [--mode shadow|trade] [--yes-trade] --operator NAME`: オン/オフ制御ファイルの読み書き。`status` は読み取り専用。`on` は `--mode` を省くと既存の `mode`(読めなければ `shadow`)を保ち、`--mode trade` は `--yes-trade` が無ければ拒否する。`off` は `mode` を保ったまま `state` を `off` にする。無い `trading-live` / `owner` は 0700 で作り、ファイルは 0600 で原子的に置き換える。writer lease は要らない。
- `python -m docich.trading.live_owner --state-dir <state> unknown list`: **読み取り専用**(取引所にも触れない)。UNKNOWN_SUBMISSION の意図を1行ずつ、**完全な intent id**・銘柄・売買・注文種別・経過秒数で出す(そのまま `show`/`resolve --intent` に渡せる)。金額と価格は TTY のときだけ(それ以外は `amount: hidden` / `price: hidden`)。無ければ `unknown_submissions: none`。
- `python -m docich.trading.live_owner --state-dir <state> unknown show --intent ID`: **読み取り専用**(writer lease を取らない)。解決に使う時間窓(`submitted_at` ± `skew_margin_s`)、未約定一覧と**約定履歴**が完全か、候補(台帳が意図に結び付けていない注文。**未約定一覧と、窓の開始以降の約定履歴の両方**から集める。約定履歴にしか無い注文は id で照会する)と一致数(`matching`)、台帳が既に注文を持っているか(`ledger_order`)、`settle_s` を過ぎたかを出す。取引所の注文 ID は TTY のときだけ。
- `python -m docich.trading.live_owner --state-dir <state> unknown resolve --intent ID (--found-order EID | --not-created) --evidence TEXT --operator NAME [--override-incomplete-proof] [--confirm TOKEN]`: **人が証拠つきで UNKNOWN_SUBMISSION を解決する**。`--found-order` は取引所の注文が意図と一致(`reconcile.matches_intent`)し、かつ他の意図のものではないときだけ `found_by_owner` として ACKED へ繋ぐ(一致しなければ何も記録せず拒否)。`--found-order` の拒否は、取引所がその ID を知らない・台帳がこの意図に別の注文を既に持っている・取引所の注文がこの意図に一致しないか整合しない・その ID が別の意図のもの、のいずれの場合も書き込みの前に起きる。`--found-order` は有効な安全停止があっても止められない(取引所に実在する注文を結ぶ経路で、機械自身の解決も同じ規則)。`--not-created` はまず、① 台帳がこの意図の注文を既に記録していないこと、② 未約定一覧**と約定履歴**のどちらにも一致する注文が無いこと、③ `settle_s` 経過、を確かめ、次に**機械自身の証明**(`reconcile.try_resolve`)を試す。機械が `acked`/`not_created` を出せばそれで終わり(オーナーの文面は証拠として記録しない)。**既存の注文・`anomaly`・機械が記録した data anomaly は `--override-incomplete-proof` では越えられない**(拒否)。機械自身の証明の途中で記録された安全停止、または UNKNOWN_ORDER / DATA_ANOMALY / FEE_MISSING / BALANCE_MISMATCH のいずれかが有効な停止として残っている場合も、`--override-incomplete-proof` があっても `--not-created` を拒否し、停止は `trip list` で確認して `trip clear`(preview が出す token を `--confirm` で返す。writer lease が必要)で解除する。LEASE_LOST と ENABLE_MISSING(旧行)はこの4つに含まれず、`--not-created` の override を妨げない。ENABLE_MISSING は旧行の互換のための理由で、他の停止と同じ人の解除経路(`trip clear`)で解除する。制御ファイルや方針セットは必要ない。LEASE_LOST もオーナーの証拠だけで解除する。`--override-incomplete-proof` があるときは有効な停止が preview を拒否して token を出さず、無いときは preview が `safety_stops_active: <reasons>` を出したうえで token も出すが、confirm は停止が有効な間は拒否し、安全状態を読めないときは preview と confirm の両方を拒否する。機械が何も記録せず `waiting` のときだけ、このフラグで `owner_not_created` として NOT_CREATED にし、証拠 JSON を残す。証拠を書く直前に取引所を読み直し、そのとき一致する注文が見えた場合は、override を拒否して何も書かない。二段確認で、`--confirm` 無しは何も書かず token を出すだけ。確認時は writer lease を取り、**lease の下で全チェックをやり直す**ので、**先に live worker を止めておく**こと(終了コード `5`)。証拠 JSON は `{"method":"owner_manual","mode":... ,"operator":...,"evidence":...,"checked_at":...,"machine":{"result":...,"open_list_complete":...,"fills_complete":...,"candidates":...,"override_incomplete_proof":...}}`(found のときだけ `found_order_id` を追加。金額・注文 ID は not_created 側には入れない)。
- `python -m docich.trading.live_owner --state-dir <state> trip list`: **読み取り専用**(writer lease を取らない)。有効な停止を1行ずつ `trip_id=<id> reason=<reason> tripped_at=<ISO 8601 UTC>` で出し、続けて `detail=<detail>` を出す(**TTY のときだけ**。それ以外は `detail=hidden`)。停止が無ければ `no active safety stops`。台帳が無ければ `ledger: not created` の1行だけで、何も作らない。安全状態を読めないときは `refused: the safety state cannot be read` で終了コード `1`。
- `python -m docich.trading.live_owner --state-dir <state> trip clear --reason {unknown_order,balance_mismatch,data_anomaly,fee_missing,enable_missing,lease_lost} --operator NAME --note TEXT --evidence TEXT [--confirm TOKEN]`: **人が安全停止を解除する経路**。`--operator` と `--note` は前後の空白を除いて 1〜200 文字、`--evidence` は `unknown resolve` と同じ 20〜400 文字。**二段確認**で、`--confirm` 無しは `trip_id` / `reason` / `tripped_at` / `detail`(TTY のみ)/ token を出すだけで何も書かず、lease も取らない。`--confirm TOKEN` が一致したときだけ writer lease を取り、lease の下で安全状態を読み直し、同じ理由の停止の `trip_id` が preview と変わっていれば `refused: the safety stop changed since the preview; run the preview again` で拒否する(再 trip された停止は古い token を無効にする)。`enable_missing` は旧行の互換のための理由で、有効化ファイルの確認は無い。他の停止と同じく、preview の token と人が渡した証拠だけで解除する。成功すると `cleared: <reason> (trip_id=<id>)`。live worker が lease を保持していれば終了コード `5`。
- 終了コード: `0` 成功 / `1` 拒否(refused) / `2` 引数の誤り(usage) / `5` writer lease を live worker が保持(停止してから再実行)。
- 金額は `Decimal`(浮動小数は拒否、8桁まで)。`--occurred-at` はタイムゾーン付きの ISO 8601 のみ(naive と未来は拒否)。`--operator` と `--note` は前後の空白を除いて 1〜200 文字。

## L4b: 常駐 worker の資本・期首残高・残高の警告

- 資本 C は**取引所の JPY 残高**(free と locked の合計)に、ボット自身の保有ポジションの取得原価(`views.deployed_cost`、約定から再計算)を足したもの(equity at cost)。固定値でも cashflow の合計でもなく、毎サイクル取り直す。deployed はポジション原価+未完了の買い注文の notional なので、headroom = C − deployed = JPY 残高(total) − 未完了買いの notional。JPY 残高に含まれる拘束分を二重計上しない。
- 使用可能な買い資金 F は取引所の free JPY(0 未満にしない)。口座全体の JPY を使ってよい。取引所への入金は台帳に書かなくても次のサイクルの C を増やす。
- 期首残高: worker が起動時に、監視通貨(銘柄の base/quote)ごとに、台帳履歴(cashflow・約定・手数料通貨・取引所に届いた可能性のある intent)が無く、取引所の total が正で、2回の残高読みが一致する場合に `auto_opening:<CCY>` の deposit を1件記録する(source `auto_opening`)。dust も同じ。読みが食い違えば次の機会に再試行する。オーナーは期首残高のために `cashflow add` を実行しない。後からの入出金は自動記録せず、残高の警告としてだけ現れる。
- 残高の不一致は**停止しない**。2回の安定した照合で確認できた不一致の通貨数を `balance_warnings` として status に出す(0以上の整数)。金額・残高そのものは status に書かない。不一致でも買い・売りは止まらない。
- 起動時の残高取得や自動記録が失敗しても inert にならず、終了もしない。周期ループで再試行する。inert になるのは方針セットが無い場合だけ(`no_active_policy_set`)。

## L1 でやらないこと

APIキー・取引所への接続・ワーカー・通知・昇格の適用。いずれも後続の PR で、PR ごとにオーナーの確認を取る。実注文(E)と常駐(F)は、オーナーの明示承認と、取引所の API キーの発行(出金権限なし)が前提。
