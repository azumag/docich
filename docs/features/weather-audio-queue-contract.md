# 天気原稿の共有audio queue契約（未接続）

この文書と `src/docich/weather_audio.py` は、天気音声を既存共有queueへ安全に接続する前の
**値契約**を定義する。docichの天気cornerは依然として原稿JSONを生成するだけで、音声を
queueへ送らない。別queue/spool、player、timer、production接続は追加していない。

## 既存consumerをそのまま呼べない理由

確認した `soviet_now/main` は `c72add7bd10725efb65eb4913a02ec6bf540a5b1`。

- [`lib/outbound_queue.sh`](https://github.com/azumag/soviet_now/blob/c72add7bd10725efb65eb4913a02ec6bf540a5b1/lib/outbound_queue.sh) の `enqueue_audio_text` は任意source文字列と4番目のruntime fence引数を受けるが、fenceがある場合は `source=hanjuku_commentary` を必須にし、`hanjuku_audio_fence.py` を呼ぶ。これは `hanjuku-hero` と `hanjuku_run.json` を要求するため、weatherから流用できない。
- 同関数の通常dedupは本文MD5を120秒保持する。これは別の予報項目が同じ文面だった場合に項目を区別できず、予報項目ごとの永続idempotency keyにはならない。
- [`workers/audio_worker.sh`](https://github.com/azumag/soviet_now/blob/c72add7bd10725efb65eb4913a02ec6bf540a5b1/workers/audio_worker.sh) は既存の `_play_comment_queue` を呼ぶ薄いconsumerである。[`broadcast/comment_lib.sh`](https://github.com/azumag/soviet_now/blob/c72add7bd10725efb65eb4913a02ec6bf540a5b1/broadcast/comment_lib.sh) はfence違反のitemを除去するが、item keyに結びつくplayed/rejected/interrupted receiptを返さない。[`say_enqueue.sh`](https://github.com/azumag/soviet_now/blob/c72add7bd10725efb65eb4913a02ec6bf540a5b1/say_enqueue.sh) の既存played logもdelivery key単位の永続receiptではない。

よって、`enqueue_audio_text` の成功や本文dedupを再生完了と解釈しない。Hanjuku・コメントのsource、dedup、fence、consumerの挙動も変更しない。

## docich側の値契約

`build_weather_audio_request` は明示呼出しの入力から次の値だけを構築・検証する。関数はfilesystem、shared queue、TTS、GameSwitchを呼び出さない。

- 固定 `source`: `weather_corner`。呼び出し側から任意sourceを受け取らない。
- `execution_id` と `item_index` (0–12) から作る `item_key`: `weather_corner:<execution UUID>:<index>`。本文や本文hashをkeyにしない。同じ実行項目のretryは同じkey、同じ本文の別項目は別keyとなる。同じkeyに異なるpayloadを送るconsumerはconflictとして拒否する。
- 項目本文は既存shared audio入口の1000文字上限まで受け入れ、切り詰めない。入力は既存`weather.narration(view)`の各literal lineをそのまま渡す契約で、validatorは本文の予報由来を証明したり書き換えたりしない。
- `runtime_fence`: `weather-view` 固定の `game`, `runtime_id`, `generation`, `lease_id` と、JMA snapshotの `expires_at`。runtime idのgenerationと一致することを確認し、15分を超える有効期間を認めない。
- `forecast`: 固定JMA source URL、対象日、項目に対応する発表時刻、検証済みprojected view 11地点全体のreport digest。report digestは事務所/区域/観測地点、対象日・発表時刻、天気・気温・降水確率、各地点の出典URLを正規化してSHA-256にする。都市項目はweather.narrationの地点順、冒頭/結びは同report内の最新発表時刻を使う。LLMや予測文の生成はしない。
- receipt: source、item key、正規化したrequest全体（本文・対象日・発表時刻・予報metadata・完全runtime fenceを含む）のSHA-256 digest、完全runtime fence、予報metadata、記録時刻をitemへ結び、`queued` / `played` / `rejected` / `interrupted` と限定reason codeだけを許可する。従って同一execution IDとordinalでも本文・予報identity・runtime tupleが変われば、前の成功receiptを照合できない。digestはpayload結合用であり、署名やconsumer認証ではない。

`SharedWeatherAudioPort` と `validate_weather_audio_receipt` は後続adapter向けの拡張点であり、現状このProtocolを実装するproduction adapterはない。pure validatorはreceiptのshape・request digest・完全runtime identity・時刻の整合だけを検証する。`played`値もこのvalidatorやhelperだけでは実再生済みの証明にならない。将来の実consumerがowned playerの完了を確認した場所でitem-keyごとのdurable receiptを記録し、そのconsumerが返すreceiptをdocichが照合して初めて完了証跡として扱える。

requestの現在時刻検証は既存weather契約に合わせ、JST上の今日/翌日の対象日、発表時刻が未来でないこと、発表から18時間以内、期限が現在より先かつ15分以内、発表後18時間を越えない期限を確認する。時計・期限・generation・schema versionなどの整数相当値に`bool`を受け入れず、NaN/Infinityも拒否する。

## 共有consumer接続時の最小追加範囲

本当に共有queueへ接続する段階では、別repo `soviet_now` に対し少なくとも次を別レビュー・別CIで実装し、docichはその安定APIをpinした後に明示的producerを追加する。

1. `weather_corner` 専用enqueue入口と永続item keyを追加する。既存の本文MD5 dedupを流用せず、同keyの異なるpayloadは拒否する。
2. Hanjuku helperを拡張利用せず、weather専用fence pathでcanonicalのweather-view完全identityと期限を照合する。queue受理時と再生開始時に確認する。
3. 既存 `_play_comment_queue` / `say_enqueue.sh` のowned player開始・終了点へ、期限切れ/identity不一致の `rejected`、正常終了後のみ `played`、開始後のfence loss/worker interruptionの `interrupted` receiptをitem keyごとに永続化する。
4. 旧sourceのdedup・並び順・fence・receipt挙動を変更しない回帰を、isolated shell fixture上で検証する。

receiptがなくworker crashした場合に実際にどこまで再生されたかは現在のplayer contractからは証明できない。将来consumerでその不確実性をどう扱うか（再送せず `interrupted` にするか等）は、共有queue側の具体的な設計判断として保留する。

## オフライン回帰

`tests/test_weather_audio_contract.py` のdummy queue/playerはメモリ内だけで動き、Soren shellをsourceせず、audio queueファイルを作らず、音声を鳴らさない。ここで検証するのはrequest/receipt schema、runtime/期限のvalidation、item-key retry/conflict、および将来consumerが返す3種の終端receipt形状だけである。実queue consumerや音声連携の検証には数えない。
