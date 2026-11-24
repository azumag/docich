# VERIFY-5391 — [半熟英雄] 月内出撃ゼロ: ch4 出撃開始後に発射されない

- 対象: docich#5391 / fingerprint `f097be01bf69d359` / occurrence `d0a0b4627df75158981e`
- ラン: `g1342-1fe0734c` / generation 1342 / `hanjuku-chart-v1459-merchant-listed-unknown`
- 月: ch4 6-12 / 485 observations / 752.2s / max gap 5.3s
- 集計: confirmed 0 / departures 0 / `order_start=2` / `order_launched=0`
- 検証範囲: issue JSONの再読とコード照合。ゲーム操作・本番変更なし。

## 結論: (d) 判定不能 — 月内ゼロは確かだが、発射に至らなかった原因は特定できない

月内に敵向け出撃が確定・発射されなかったこと、`order_start_without_launch` が立つこと自体は正しい。一方、以前の記録にあった「2件の order_start は読み取り専用 probe が最有力」「すべて owner 承認済み経路」という結論は、issue本文の実データと照合できていなかったため撤回する。

今回 GitHub issue API から取得できた集計は次のとおり。

| 決定 | 件数 |
|---|---:|
| `order_start` | 2 |
| `order_dispatch_starved` | 2 |
| `battle_start` | 8 |
| `battle_result` | 8 |
| `battle_melee` | 19 |
| `roster_probe_complete` | 0（countsに存在せず） |

`roster_probe_complete` が集計に無く、2件の `order_start` の行自体も `decisions.before` の30行窓に含まれない。したがって2件をprobeと断定できない。`order_dispatch_starved=2` はpending注文がdispatch上限を越えて待った証拠であり、単にprobeが2回動いたという説明では足りない。

## 月末スナップショット

- 所有: ミヒラギアン、アルカ、ビクトリア、マラコビア
- 失城: ネクスドリア
- 将軍所在: アルマムーンに どうし・ヴィーナス・セミヨン、ミヒラギアンに ヘスティア・アルファルファ、ビクトリアに ミルフィーユ、他の保有城に各1名
- `world.sorties`: ヘスティアのネクスドリア奪還が `launched_unconfirmed`（age 192 ticks）、アテナのフヌイヌム攻撃が `overdue`（age 552 ticks）
- `world.launched_orders` にも上記2件が残る
- 採用済みchart planには、ミルフィーユをアルマムーンからネクスドリアへ送る pending 行が1件ある。ただし実測garrisonではミルフィーユはビクトリアにおり、目的も null。現行の `_source_spare_missing` は名前付き将軍が出撃元にいない非ボス注文を止めるため、この行は開始可能な計画とは確認できない。

8件の戦闘開始、2件のdispatch starvation、2件の未決着sortie、失城奪還待ちが同時に存在する。これらは長い戦闘・再確認・行軍待ちと整合するが、どの状態が2件のdispatchを止めたかは集計だけでは特定できない。月末の未決着行軍が翌月に解消したかも、このoccurrenceの本文からは分からない。

## コード照合

- `order_start` はdispatch開始の記録であり、読み取り専用probeだけを意味しない。
- `order_launched` は実際の出撃確定後に記録されるため、両者の差は「dispatch後に未確定」だったことを示す。
- `order_dispatch_starved` はpending注文が60観測超待ったことを示す。これは調査対象であり、単独では設計どおりとも欠陥とも決められない。
- pending planの将軍・出撃元不一致を含むが、当該2件の `order_start` と対応するstepを結ぶ完全な行が無いため、今回それを根因とは断定しない。

## 次に必要な証拠

1. 翌月7-1以降の同一runで、上記2 sortiesが `arrived` / `failed` / `lost` のどれに確定したか。
2. 次回incidentまたはdecision exportで、2件の `order_start` の `chart_step`、`roster_probe`、`purpose`、source/targetを取得する。
3. `order_dispatch_starved` の `observed_metric.pending` と、その時点のplan order statusを照合する。
4. pendingのミルフィーユ注文が再ソースされたか、破棄されたか、同じまま残ったかを確認する。

安全に変更できる実装欠陥はまだ確定していないため、ゲームの出撃・防衛ガードは変更しない。#5391は次月の決着証拠が得られるまでOpenを維持する。
