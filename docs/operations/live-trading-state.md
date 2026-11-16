# LIVE の状態・方針・有効化ゲート(L1、Issue #3623)

L1 は、本番(LIVE)の売買の**土台だけ**で、動作しない。どの実行経路からも import されず、ネットワークを使わず、注文を送らない。PAPER のワーカーにはつながっていない。LIVE のワーカー(L4)・取引所への接続(L3)・昇格の適用(L6)はまだ無い。

## 配置と権限

```
<state_dir>/trading-live/            0700   PAPER の <state_dir>/trading とは別
  live.sqlite3 (+ -wal, -shm)        0600   LIVE の台帳
  live-writer.lock                   0600   単一の書き込み権(flock)
  owner/                             0700   オーナーが作る。コードは作らない
    live_enable.json                 0600   有効化ファイル。コードは作らない
    policy/<experiment_id>.json      0600   オーナーが置く方針スナップショット
```

- 台帳は `src/docich/trading/live/ledger.py`。意図(intent)は送信の**前**に保存し、保存が済むまで送信してはならない。再起動後に「送信中」だった意図は、取引所へ照会して確かめるまで、自動では再送しない。
- 台帳の既定の置き場は PAPER と同じ `state_dir`。`git clean -x` などの手作業で消さないこと。

## 有効化ファイル

オーナーだけが作る。コードは作らず、読むだけ。次のどれかに当てはまれば無効(送信しない)。

- ファイルが無い、権限が 0600 でない、所有者が違う、symlink、4096 バイト超、JSON が壊れている。
- 知らないキーがある(円建ての上限のキーも置けない)。
- 期限切れ、まだ有効でない、タイムゾーンの無い時刻。
- 中の `policy_set_digest` が、使う方針の組の digest と違う。`code_revision` が実行中のコードと違う。

```json
{"schema_version":1,"kind":"docich-live-enable","mode":"shadow",
 "policy_set_digest":"<64 hex>","code_revision":"<40 hex>",
 "created_at":"2026-11-20T09:00:00+09:00","expires_at":"2026-12-20T09:00:00+09:00"}
```

`mode` が `shadow` なら、注文は送らない(L5 のシャドー用)。`trade` だけが注文を許す。作るときは `umask 077; install -d -m 700 <state_dir>/trading-live/owner` で作り、ファイルは 0600 にする。ファイルを消すと、一度有効だった後なら `enable_missing` で止まる(一度も有効になっていない状態では何も書かない)。ただし L1 には、この確認を周期的に呼ぶワーカーがまだ無い。

## 方針スナップショット

- 最初の戦略は `snapback_harvest_v25`(OAS・OMG・XYM・CYBER・MONA・ATOM・KLAY・LPT・AXS・IMX・BOBA の11銘柄)。定義は、本番の `paper_strategy_evaluation_history.json` の `experiment` にあるもの(最大20件で古い順に消えるので、取り出して保管する)。
- digest は、`activated_at` を除いて正準化した JSON の sha256。ファイルが入力どおりに往復できない場合(知らないキー、float の閾値など)は読み込まない。
- 確かめるには `python -m docich.trading.live.policy digest <file>`(digest を表示するだけ)。
- 保有中のポジションは、入ったときの方針の売りルールを保つ(台帳のトリガーが、別の方針での売りを拒否する)。

## 安全停止

止める理由は6つ定義してある。L1 のコードが自動で止めるのは、未知の注文(`record_order_observation`)、手数料の未取得と約定の矛盾(`record_fill`)、有効化ファイルが一度開いた後に無効になったとき(`note_enable_result`)の4つ。残高の不一致(`balance_mismatch`)と書き込み権の喪失(`lease_lost`)は、それを検知するコードが L2・L4 で入るまで、定義だけで自動では止まらない。止まっている間は、新規の買いも売りの送信も拒否する(照合と監視は続ける)。**解除は人だけ**。コードから解除を呼ぶ処理は無く、テストで確かめている。

## L1 でやらないこと

APIキー・取引所への接続・ワーカー・通知・昇格の適用。いずれも後続の PR で、PR ごとにオーナーの確認を取る。実注文(E)と常駐(F)は、オーナーの明示承認と、取引所の API キーの発行(出金権限なし)が前提。
