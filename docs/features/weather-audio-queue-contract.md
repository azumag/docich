# 天気原稿の共有audio queue契約（default-off producer）

この文書と `src/docich/weather_audio.py` は、天気音声の**値契約**を定義する。
`audio_enabled`のcatalog defaultはfalseで、明示opt-inがない限りproducerは共有queueを呼ばない。
接続時も既存comment queue/worker/owned playerだけを使い、別queue/spool、player、timerは追加しない。

## 使用する既存consumer API

このPRはconsumer変更PR [#558](https://github.com/azumag/soviet_now/pull/558) のhead
`b5ffca79243bf52c35602b1d1b0b61d280864524` をsubmoduleでpinし、weather専用の
`lib/weather_audio_consumer.py` がある。旧`enqueue_audio_text`/本文MD5 dedup/Hanjuku-only fenceは流用しない。
producerは`enqueue`, `get`, `interrupt`, `quiescence` CLIを呼び、consumerのweather-specific durable receiptと既存
`_play_comment_queue` / `say_enqueue.sh` owned player boundaryを使う。Hanjuku・他コメントの動作は変更しない。

## docich側の値契約

`build_weather_audio_request` は明示呼出しの入力から次の値だけを構築・検証する。関数はfilesystem、shared queue、TTS、GameSwitchを呼び出さない。

- 固定 `source`: `weather_corner`。呼び出し側から任意sourceを受け取らない。
- `execution_id` と `item_index` (0–12) から作る `item_key`: `weather_corner:<execution UUID>:<index>`。本文や本文hashをkeyにしない。同じ実行項目のretryは同じkey、同じ本文の別項目は別keyとなる。同じkeyに異なるpayloadを送るconsumerはconflictとして拒否する。
- 項目本文は既存shared audio入口の1000文字上限まで受け入れ、切り詰めない。入力は既存`weather.narration(view)`の各literal lineをそのまま渡す契約で、validatorは本文の予報由来を証明したり書き換えたりしない。
- `runtime_fence`: `weather-view` 固定の `game`, `runtime_id`, `generation`, `lease_id` と、JMA snapshotの `expires_at`。runtime idのgenerationと一致することを確認し、15分を超える有効期間を認めない。
- `forecast`: 固定JMA source URL、対象日、項目に対応する発表時刻、検証済みprojected view 11地点全体のreport digest。report digestは事務所/区域/観測地点、対象日・発表時刻、天気・気温・降水確率、各地点の出典URLを正規化してSHA-256にする。都市項目はweather.narrationの地点順、冒頭/結びは同report内の最新発表時刻を使う。LLMや予測文の生成はしない。
- receipt: source、item key、正規化したrequest全体（本文・対象日・発表時刻・予報metadata・完全runtime fenceを含む）のSHA-256 digest、完全runtime fence、予報metadata、記録時刻をitemへ結び、`queued` / `played` / `rejected` / `interrupted` と限定reason codeだけを許可する。従って同一execution IDとordinalでも本文・予報identity・runtime tupleが変われば、前の成功receiptを照合できない。digestはpayload結合用であり、署名やconsumer認証ではない。

`SharedWeatherAudioPort`はこのproducer adapterの最小境界である。pure validatorはreceiptのshape・request digest・完全runtime identity・時刻の整合だけを検証する。`played`値そのものが汎用的に実再生を証明するわけではない。このpinned consumerがplanned chunk全部の完了を確認した後に返すdurable receiptだけをdocichが照合し、全13 itemのreceiptが`played`なら音声全体を完了扱いする。

requestの現在時刻検証は既存weather契約に合わせ、JST上の今日/翌日の対象日、発表時刻が未来でないこと、発表から18時間以内、期限が現在より先かつ15分以内、発表後18時間を越えない期限を確認する。時計・期限・generation・schema versionなどの整数相当値に`bool`を受け入れず、NaN/Infinityも拒否する。

## Producer lifecycle

1. weather catalog rowの`audio_enabled`は省略時false。audio-off cornerは既存表示/GameSwitch動作のみ実行し、consumer processも呼ばない。
2. audio-on後、GameSwitch start receiptで確定した同一weather runtime identityと新鮮なsnapshotから13 requestを作り、完全payloadをowner stateへ保存してからitem 00をenqueueする。文面は`weather.narration(view)`のliteral lineそのもの。
3. 各poll/restartで保存済みitemの`get`を先に呼ぶ。receiptがあればenqueueを繰り返さず検証する。見つからずforecastがまだ有効な場合だけ同じkey・完全に同じpayloadでretryする。consumer側のper-key durable idempotencyが二重publishを防ぐ。
4. `played`後にだけ次ordinalへ進み、`rejected`/`interrupted`後はそこで止める。manual stop、snapshot expiry、GameSwitch transition時は1 pending itemのみ既存`interrupt`で終端化する。terminal receiptとは別に、pinned consumerのdurable `player_stop_confirmed` ackを照会し、ackがない間は再開後もGameSwitch restoreを進めない。queue filename消失だけをplayer停止の証拠にしない。
5. 受理済み`queued`は再生完了を意味しない。13 receiptすべてがconsumerから`played`になって初めてdelivery stateが`completed`となる。全itemがcornerのduration/forecast expiry前に終わらない場合は残りを送らずstopped/incompleteのまま終える。

一度に存在するweather queue itemは最大1件。cancel-by-key CLIのないconsumer版に合わせ、adapterはexecution UUID/ordinalに一致するconsumerの規定filenameだけを特定して既存`interrupt` commandへ渡す。helper自身もfilename、sidecar、request digestを再検証するため、別sourceや別itemには操作しない。

## オフライン回帰

`tests/test_weather_audio_contract.py` はrequest/receipt値契約を、weather corner testsはdefault-off/逐次配送/receipt restart/cancel/全13-item completionをfake portで確認する。shared consumer自身はpinned source repoのtemporary queue + temporary GameSwitch + dummy player testsで確認し、TTS/audio worker/production queueは起動しない。これらは実配信音声や実VM GameSwitchの確認とは区別する。
