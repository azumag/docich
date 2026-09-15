# 株デイトレ・常駐FXペーパートレード

2026-09-16のユーザー依頼で #132 の保留を **ペーパートレード部分だけ再開**する。
この変更はPRまで。mainへのマージ、VM反映、サービス登録、口座接続、実売買の開始はしていない。

## 番組と取引の契約

| 市場 | 番組（Asia/Tokyo） | 売買・集計 |
| --- | --- | --- |
| 日本株 | 09:00–10:00「中華AIのデイトレ」 | 開場日だけ、表示がactiveかつheartbeatが20秒以内の場合に新規売買。09:59:45以降は決済のみ。遅延開始でも10時を延長しない。 |
| FX | 03:00–03:30「FXで大儲け結果発表」 | 表示とは独立したworkerが開場中に常駐。前日03:00から当日03:00の損益を固定して発表する。番組終了でworkerやポジションを停止しない。 |

株の開始が試合境界待ちで遅れた場合は、その日の取引時間を短縮する。期限後の遡及実行はしない。
株の終了・表示停止時は新規を止め、保有の決済処理はworkerに残す。欠損・古い価格・売買停止・板数量不足で
決済できないものは「未決済／暫定」として記録し、架空の10時約定を作らない。
FXの週末ゲートはAmerica/New_Yorkで夏冬時間を扱い、提供元のtradeable判定を併用する。

## 再利用と分離

`ProgramViewAdapter`のChromium/contain表示、`GameSwitchCoordinator`の試合境界と復帰、
`program_slot`の共通排他、既存のoverlay/audioキューとイベントID重複排除、
`trading.ai_text.generate_text`のAIディスパッチを再利用する。配信エンコーダは再起動しない。

台帳は `<state_dir>/market-paper/{stocks,fx}/paper.sqlite3` に分離し、暗号資産の
`<state_dir>/trading/`には書かない。ゲーム・配信停止とworkerの寿命も分離する。
SQLiteのtransaction内でaccount/tick/fill/metricを更新する。約定と結果・改善jobは保存し、
raw tickは概ね2万件、metricsは概ね5万件に毎時整理する。削除済みページは再利用する。
実験ごとの台帳は証跡として残るため、運用前に実験アーカイブの容量上限・保管方針を決める。

## 導入設定（未実行）

入口は `bin/docich-market-paper --config <profile> --market stocks|fx <command>`。
commandは `status/tick/worker/corner/improve/dashboard`。
独自設定は `--markets-config <path>`。通常は `config/market-paper.toml`。
各市場の `enabled=false` が初期値で、`mode`はpaper以外を拒否する。

初期の仮想資金は各1,000万円（実資金やユーザーの投資予算ではない）。同時投入上限30%、
1ポジション10%、日次損失停止2%、最大3ポジション。株100株、FX1,000通貨単位を設定例とする。
手数料・スリッページ・FX保有コストは**推定モデル**であり、実ブローカー条件の再現を保証しない。
資金・制限はAIから変更できず、既存台帳で変更すると明示的な移行が必要として停止する。
株は現物ロングのみで、同日の同銘柄再エントリーを禁じ、売却代金の反復利用を保守的に制限する。
FXはJPY建てペアのロング／ショートのみ。証拠金レバレッジや非JPY損益換算は未対応。

### 市場データ

株はkabuステーションAPIの **GET boardだけ**。認証tokenの発行・日次更新・Windows側稼働は別途必要。
`DOCICH_KABU_TOKEN`を安全な環境から渡す。既定はloopback:18080。VMから使う場合は利用者が承認した
HTTPSプロキシを `DOCICH_KABU_DATA_URL` で設定する。リダイレクトは拒否し、tokenを他ホストへ転送しない。
kabuのBidPrice=売気配、AskPrice=買気配を通常のbid/askへ変換する。特別気配・古い時刻は売買しない。

FXはOANDA practiceの **GET pricingだけ**。`DOCICH_OANDA_ACCOUNT_ID` / `DOCICH_OANDA_TOKEN` が必要。
practice価格を入力し、約定はdocich内のローカル台帳にのみ生成する。OANDA側にpaper注文も送らない。
口座の利用資格・API利用・公開配信でのデータ表示権限は未確認。認証情報をソース、Issue、ログへ書かない。

どちらも既存の承認済み価格収集器から `feed="file"` を選べる。atomic replaceで次の形式を配置する。
時刻はUnix秒。以下の数値はスキーマ例であり、本番価格として使わない。

```json
{"market":"fx","realtime":true,"quotes":[{"symbol":"USD_JPY","ts":1789510000,
"bid":"150.00","ask":"150.01","bid_size":"100000","ask_size":"100000",
"tradeable":true,"source":"approved-feed","currency":"JPY"}]}
```

株カレンダーは `<state_dir>/market-data/jpx-calendar.json` に有効範囲と**実際の開場日のみ**を列挙する。
カレンダー未設定・範囲外・休場日は売買しない。自動取得や祝日を推測するフォールバックは設けない。

```json
{"valid_from":"2026-09-01","valid_through":"2026-09-30","sessions":["2026-09-16"]}
```

これは9月16日だけを許可する例であり、完全な月間営業日カレンダーではない。

### systemdと出力

`docich-market-worker@{stocks,fx}.service` が5秒周期、`docich-market-corner@.timer` が毎分番組を判定する。
`docich-market-improve@.timer` は5分周期でpending jobを処理する。定時起動の取りこぼしを後から再生しない。
テンプレートの`__DOCICH_ROOT__`置換とowner-only正規デプロイが必要。**このPRはunitを設置・起動しない。**
既存のcrypto worker、既存timer、本番profile、VM gatewayの権限は変更しない。
`market-paper.env`のAI許可は `DOCICH_ALLOW_REAL_AI=1` を明示する場合のみ。秘密値はEnvironmentFileで管理する。

ダッシュボードはloopback:8801（株）、8802（FX）。損益・保有・直近約定・資産推移を表示し、
古いデータを最新として見せない。結果の発話は費用込み損益を必ず含み、「大儲け」は番組名であって成績を偽らない。

## AI改善と昇格候補

結果固定時に一意なjobを作る。`news.rss_urls`へ承認済みHTTPS RSSを設定し、見出し・公開時刻・取得時刻を記録する。
ニュース欠損／未来時刻／AI未設定では改善しない。エラーは最大3回のbackoff再試行で、全文や資格情報はログへ出さない。
`ai.agents`は既存のAIディスパッチ識別子。空の場合は選択profileの`paper_corner.improve_agents`を参照する。
「中華AI」として運用する前に、この経路を実際に利用可能な中国系モデルへ設定・実測すること。
本変更はモデル契約や既存の配信モデルを勝手に変更しない。

改善対象はmomentum/reversion、lookback、entry/stop/takeの閾値、最大保有秒数の厳格JSONのみ。
任意Pythonコード、シェル、資金上限、feed、口座、権限は変更できない。記事の指示は実行しない。
現行・候補を同じ**提案後の未観測価格**で独立したpaper口座に流す。最低24時間かつ各10決済を待ち、
候補が費用込みで正、現行より良い、DD悪化が0.5ポイント以内、比較時点の価格検証成功の場合に
main paper口座がflatになってから採用する。有意差は必須にしないが、これを実運用の収益保証にしない。
実験不良は主workerを停止させない。採否と旧policyを残す。

将来の実取引用にはstrategy versionごとの20取引日・100決済・純益・DDの審査材料を出す。
`broker_costs_and_execution_verified=false` と `owner_approved=false` を維持し、
`live_enabled=false`からは変化しない。**実口座／実発注／承認によるlive化そのものは未実装**。
将来は独立した監査・正確な費用/約定/証拠金検証・明示承認・少額上限・緊急停止を別PRで実装する。

## 検証と有効化前の残件

ローカルでは34件のネットワーク不要テスト、compileall、launcher構文を検証する。
GitHub CIは新規unit suiteに加え、既存corner/coordinator/cryptoの退行も確認する。
独立レビュー、実際の価格認証、実AI、Chromium/tmuxでの配信PID維持・音声・1時間/30分通し試験は未実施。
市場ごとの `health.json` / `experiment-status.json` を出すが、owner-only diagnostics collectorの
runtime/queue registryへの統合は未完了。現在はopt-inの新規unitを有効化する前の阻害事項として扱う。
本番有効化前にはデータ公開権限、対象銘柄、カレンダー更新、ニュースと中国系AI経路、ディスク保管上限、
診断registry、kill/restart/二重起動・境界待ちの実機試験を確認する。
`handoff.md`は調査したmainには存在しないため、運用中の引き継ぎを捏造せずPR本文へ検証状況を残す。

## 参照した一次情報（2026-09-16確認）

- JPX売買立会時間: https://www.jpx.co.jp/english/equities/trading/domestic/01.html
- kabu API board仕様: https://kabucom.github.io/kabusapi/reference/index.html
- OANDA pricing定義: https://developer.oanda.com/rest-live-v20/pricing-df/

公開仕様だけでは当該口座での利用権限・市場データ再配布可否を証明しない。
