# 全国の天気 — 気象庁予報の伝達（非配信プレビュー）

## このPRの範囲と未完了部分

全国11地点の公式予報取得、厳格な正規化、定型原稿生成、出典付き960×540画面、
読み取り専用loopbackサーバーを実装した。weather用adapterとowner stateを共通corner
catalog/rotationへ接続し、既存program slot、GameSwitchのゲーム境界待ち、runtime cleanup、
rollback、実行終了後の元のゲームへの復帰も実装した。既存の共有audio consumerへのproducer
接続も追加したが、weather catalogの`audio_enabled`は省略時も`false`であり、表示cornerとは
別にopt-inしない限り音声をqueueへ送らない。

本PRは配信運用まで完成したコーナーではない。production catalog/profile、配信encoder、
音声worker、OBSや実GameSwitch状態は変更しない。productionへの登録、現行データの全国確認、
非本番の実runtime開始・復帰確認は未実施。コードをマージしてもweather放送や読み上げは
有効化されない。
`weather-view` は合成GameSwitch runtime adapterとして実装した。
既に公開済みのsnapshotだけをloopbackで表示し、`game` / `runtime_id` / `generation` / `lease_id`の
4項目をserverへ渡して応答でも照合する。既存の960×540 presentationとGameSwitch
所有プロセスの終了処理を使い、start直前のfreshnessと起動後の同一runtime応答を検証する。
共通corner managerはこのviewを通してのみ起動する。adapter実装は登録済みだが、production
catalogにはweather行がなく、行を追加する場合も明示的なenabled設定と期間指定が必要。
weather-view用のTwitch category/title mappingは追加せず、合成view起動時にstream titleも更新しない。
復帰先の実ゲームについてはGameSwitchの既存commit hookを使う。単独では起動しない。

## 権利・出典・予報業務の境界

2026-09-29に確認した一次資料：

- 気象庁ホームページ利用規約：<https://www.jma.go.jp/jma/kishou/info/coment.html>
- 気象庁「予報業務の許可等に関するQ&A」：<https://www.jma.go.jp/jma/kishou/minkan/q_a_m.html>
- 気象データの利用案内：<https://www.data.jma.go.jp/developer/index.html>

気象庁サイトの対象コンテンツは公共データ利用規約（PDL1.0）に従って利用する。
出典と加工・編集した旨を表示し、気象庁が編集後の番組を作ったように見せない。
画面・原稿に「気象庁の発表をもとにdocichが編集」と明記する。原データのURL、
区域・地点と発表日時を保持する。第三者に権利がある素材やロゴは利用対象に含めない。

画面は自作HTML/CSSのみ。民間天気サービスの画面・文章・天気アイコン、
気象庁ロゴ、外部地図タイル、外部フォント配信は使用しない。
フォントは実行ホストのシステムフォントを参照し、フォントファイルは配布しない。
テストデータは合成データであり、実際の天気として表示・配信しない。

地図画面は Natural Earth の Admin 0 – Countries 1:10m（日本）と
Populated Places 1:10m の位置データを同梱し、画面用に簡略化したSVGとして描画する。
同データはPublic Domain。市区町村・都道府県境界や航行に使える精度を示すものではなく、
行政界を描き足さない。北海道・本州・四国・九州・沖縄を含む全国表示から、地方・代表都市へ
選択ズームし、全国表示へ戻れる。配信向け960×540ではcontain表示、縦長端末は縦スクロールの
レイアウトに切り替える。自動順送りは画面だけの操作で、読み上げ音声との同期は未接続。

- [Natural Earth Admin 0 – Countries 1:10m](https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-admin-0-countries/)
- [Natural Earth Populated Places 1:10m](https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-populated-places/)
- [Natural Earth terms of use](https://www.naturalearthdata.com/about/terms-of-use/)

気象庁が発表した予報の伝達・解説という範囲を維持する。
数値予報の独自解析、地点補間、独自の降雨時刻・確率・警報生成、
「雨の心配はない」等の入力にない判断はしない。原稿は定型でありLLMを呼ばない。
公式警報・注意報はこの版の対象外。これは個別案件の法的保証ではないため、
運用開始時と取得方法変更時には利用条件を再確認する。

## 取得とデータ契約

取得先は気象庁サイトが利用するHTTPS JSON資源に限定する。
サポートや可用性が保証された公開APIという扱いにはしない。
提供形式の変更、停止、取得不可は休止として扱い、他社サービスへ勝手に切り替えない。

対象：札幌、仙台、東京、新潟、名古屋、大阪、広島、高松、福岡、鹿児島、那覇。
天気・降水確率は各地点を含む予報区域、気温は表示地点の予報であり、
市町村単位に細分化した独自予報ではない。

- 日付はJSTで明示。`auto` は17時より前が当日、17時以降が翌日。
- 各事務所の短期予報を別々に検証し、区域コード・観測地点名を完全一致で選択する。
  最初の配列要素や似た地域へのfallbackはしない。
- 降水確率は00–06 / 06–12 / 12–18 / 18–24時の4区分。
  平均・最大を「一日の降水確率」に変換しない。`0%` と欠測を区別する。
- 温度は時刻軸を見て選ぶ。同日00時の値を最低気温と解釈しない。
  翌日の00時・09時から最低・最高を取り出す。週間予報の数値で穴埋めしない。
- 出典文字列、数値範囲、時刻軸、重複キー・重複地点、配列長を検証する。
- 現在より未来の発表時刻、発表から18時間超、取得/生成から15分以上は不受理。
  この18時間は製品側の上限であり「常に最新の発表である」ことの保証ではない。
  発表日時を画面に併記する。
- 11地点の一つでも必須データが不正なら全国snapshotを公開しない。
  発表されていない気温・時間帯は補完せず `—`。
- 取得は明示的な `fetch` だけ。各事務所を15分cacheし、有効なcacheも中身を再検証する。
  固定host/事務所allowlist、redirect拒否、1応答256KiB、socket timeout最大4秒、
  次の取得開始前の総予算確認を設ける。ただしsocket timeoutは厳密なwall-clock上限ではない。
- fetchは同一ディレクトリ内で排他し、旧公開snapshotを先に無効化する。
  更新失敗時に以前の公開snapshotを再表示しない。cacheを捏造して埋め戻さない。

## ローカルでの確認

Linux/macOS、Python 3.11以降。`fcntl` によるfetch排他を利用する。
本番stateディレクトリではなく、検証用の専用ディレクトリを指定する。

```bash
bin/docich-weather --state-dir /tmp/docich-weather-preview fetch --day auto
bin/docich-weather --state-dir /tmp/docich-weather-preview status
bin/docich-weather --state-dir /tmp/docich-weather-preview narration
bin/docich-weather --state-dir /tmp/docich-weather-preview serve --port 8803
```

`http://127.0.0.1:8803/` を通常のブラウザーで確認する。`serve` は新しい予報を取得せず、
既存snapshotを読むだけ。更新が必要なときは別プロセスで明示的に `fetch` する。
外部bind設定、ファイル一覧、任意URL proxy、mutation APIはない。
CLIの `status` / `narration` は有効なsnapshotがなければ固定理由とexit 2を返す。
`narration` は原稿のJSON出力であり、TTS queueへの送信ではない。

画面は同梱のNatural Earth日本地図と11地点の位置を表示し、地点選択・前後移動・
全国表示・画面のみの自動順送りでクローズアップする。
外部文字列をHTMLとして挿入せず、全て `textContent` で描画する。
長すぎる原文を勝手に切って意味を変えず、表示枠に収まらない場合は休止する。
描画は960×540を縦横比維持でcontainし、外側の配信枠は操作しない。
縦長端末は地図と予報パネルを縦に並べる。音声consumerが再生開始cueを公開していないため、
画面には「音声同期なし」と表示し、擬似cue時計は動かさない。

`GET /api/weather` はraw予報を毎回再検証する。有効なら200、欠損・期限切れなら503。
全応答を`no-store`とし、Host検証・CSPも設定する。
ブラウザー側は2秒poll・2秒request timeout、最長5秒のmonotonic表示leaseを持つ。
ブラウザーのJSが動いていれば、サーバー停止時にも最後の予報を無期限に残さない。
遅い旧pollが新しいpollの失敗を上書きしないよう、poll世代も確認する。

## 統合の安全条件と未完了作業

1. 共通corner catalog/adapter、owner state、既存program slotへの登録は実装済み。
   weather行の省略時は無効、enabled時は`duration_minutes` 1〜14が必須。
   `audio_enabled`も省略時falseとし、表示のopt-inから読み上げを独立させる。
   期間は上限で、snapshotの15分鮮度期限が先に来ればそこで復帰する。独立timerはない。
2. GameSwitchの`game` / `runtime_id` / `generation` / `lease_id`を使って開始と復帰をfenceする。
   開始時は旧ゲームの宣言済みラウンド境界を待ち、終了時はweather runtimeの同一identityを
   `expected_source`に指定する。operatorが別runtimeへ切替済みなら、それを停止・上書きせず
   weather ownerを中断扱いにする。960×540・既存presentation viewportとowned child cleanupを使う。
3. catalogのweather行に`audio_enabled=true`を明示した場合だけ、既存の共有comment queueへ送る。
   原稿は`weather.narration(view)`の13 literal lineをその順で使い、出典・対象日・地点別発表時刻・
   全11地点report digestをitem requestへ保持する。LLMや独自予測を使わない。
4. producerはconsumer変更PR [#558](https://github.com/azumag/soviet_now/pull/558) の統合commit
   `46d5043911645692aa765f101b9494de332ec09d` をsubmoduleでpinして
   `lib/weather_audio_consumer.py`を使う。item keyは実行UUIDとordinalから作り、
   完全requestをweather owner stateへ先に保存する。最大1項目だけqueueへ置き、再開時は先に
   durable receiptを照会する。同じitemのretryは同一payload/keyに限定し、consumerの永続冪等性に
   任せる。itemが`played`になるまで次のordinalをenqueueしない。
5. terminal receiptだけではowned player停止完了を意味しない。pinned consumerのdurable
   `player_stop_confirmed` ackが確認できるまでGameSwitch復帰を進めず、owner再開後も同じackを照会する。
   queue filenameの消失だけを停止証明に使わない。全chunkを確認した`played` receiptを全13 itemで返した場合だけ
   音声全体を`completed`と記録する。`rejected`/`interrupted`で後続itemを送らない。他cornerの音声には触れない。
6. snapshotの有効性は適格性判定、GameSwitch preflight/readiness、放送中の表示再検証で
   確認する。取得失敗は休止とし、鮮度期限が来たらGameSwitchで復帰する。
   合成adapterによる境界待ち、開始rollback、終了後復帰、operator移動のfenceをオフラインで検証した。
7. PRレビューとCIを経て正規配布する。事前の実画面・非本番の開始/終了実測は未実施。
   本番表示・開始/終了・元ゲーム復帰の受入確認は配布後に行う。
   未実施の確認を成功扱いしない。

## 検証記録

- weather/audio focused regressionは62 passed、1 skipped。skipはsandboxがowned process-group停止を拒否したため、quiescence=falseとGameSwitch restore保留を確認したケース。
- `soviet_now` consumer suiteは22 passed、2 skipped、3 subtests passed。2件のprocess-group回帰は拒否時にstop ackがfalseのままなのを確認してskipし、通常のprocess回帰はGitHub CIで確認する。
- Pinned consumerに対するisolated CLI smokeでenqueue/get/interruptを確認し、`queued` → `queued` → `rejected`を得た。queueは一時ディレクトリで、audio worker/TTSは起動していない。
- Python compileと`git diff --check`は成功。
- 現行JMA全国11地点一括取得、実VM、実OBS、実音声、実GameSwitch復帰は未実測。
- owner checkout固有のgitignored `handoff.md`、運用メモリ、VM作業中バナー・音声はこの実行環境では利用できず、確認・操作していない。


## Production rotation登録（2026-10-03）

`config/docich.soren-live.toml`の既存queue rotationへweatherを追加する。
`duration_minutes=1`, `fetch_on_start=true`, `audio_enabled=false`。
他cornerと同じrolling 24時間cooldown、共通program slot、GameSwitchのラウンド境界待ち・復帰を使う。
独立timer、他ゲームの強制終了、配信基盤の再起動は追加しない。

`fetch_on_start`はweather専用boolean、省略時false。trueならsnapshotなしでも選択候補となるが、
適格性・status照会では通信しない。選択された新executionの実行時だけ、GameSwitch要求前に
CLIと同じsingle-flight publisherを呼ぶ。固定JMA host/11 office、既存15分cache、最大45秒取得budgetを使い、
取得中は旧ゲームを維持する。全11都市を鮮度検証してからatomic publicationする。
取得・lock失敗は`interrupted / forecast-fetch-failed-before-start`としてreservationを完了し、
ゲーム切替や音声送信をせず他cornerへ進む。同requestのterminal replay、starting/active/restoring再開では再取得しない。
ゲームの境界待ち中にsnapshotが失効した場合は既存preflight/readinessが拒否し、既存rollback/reconcile経路で処理する。
取得成功は実際の開始成功を保証しない。

owned viewerは`/broadcast`を開き、初回の有効poll後、全国4秒→11地点各4秒→全国の48秒周期を自動開始する。
1分は既存catalogで選べる最短枠で、この視覚巡回一巡に足りる。手動選択・全国戻り・停止、表示失効やoverflowで巡回を停止する。
通常の`/`は手動7秒巡回を維持する。再pollは実行中の巡回をリセットしない。
実ブラウザ描画・long text fit・配信frame・開始/終了/ゲーム復帰は未実測。

音声は今回有効化しない。現行11都市取得の13項目原稿は625文字（最長73文字）。
仮に6文字/秒でも読み上げだけで約104秒となり、TTS/queue待ちを含む所要は未実測なので1分で完了を保証できない。
これは時間の推定で、音声再生証拠ではない。shared consumerには正確な開始cueがなく、画面は引き続き
「音声cue未接続」と表示する。音声を有効にする場合は所要枠とcue契約を別途検証する。

出典・編集表記は[気象庁利用規約](https://www.jma.go.jp/jma/kishou/info/coment.html)に従う既存表記を維持。
地図は[Natural Earth Public Domain](https://www.naturalearthdata.com/about/terms-of-use/)の既存同梱データを維持する。
