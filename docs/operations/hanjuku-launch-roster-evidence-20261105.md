# 出撃の確認を全体マップの▲ではなく出撃元の将軍一覧で行う（v390、2026-11-05）

## 症状

第6話の攻略中、出撃を確定した直後の全体マップ（Y）確認で部隊▲が見えず、
出撃が未発射に巻き戻される（`sortie_launch_unverified` → `order_reopened`）ことが
繰り返された。同じ指示の再試行が上限に達すると `sortie_launch_failed` で
指示そのものが失敗になる。

g1133（第6話、2026-11-05 00:10〜00:21 JST）の実測:

| 時刻 (JST) | 出来事 |
| --- | --- |
| 00:14:13 | ロックフォールをトウトウへ出撃（`order_launched`） |
| 00:14:53 | `sortie_launch_unverified`（markers は `[[107,135,113,140]]` のみ、misses=1） |
| 00:15:29 | 出撃元アルマムーンの将軍一覧に **ロックフォールがいない** |
| 00:20:39 | ピスタチオをユージアへ出撃（`order_launched`） |
| 00:21:15 | 将軍一覧に **ピスタチオがいない** |

70分間で `order_launched` 15 に対し `sortie_launch_unverified` 6、
`sortie_launch_failed` 4。出撃は実際に成立しているのに巻き戻していた。

## 原因

`world_markers` は `WORLD_MARKER = (255,24,0)` の三角だけを部隊▲として拾う。
第6話の Y 画面では行軍中の部隊が **顔グラフィック**（赤白の帽子の肖像）で描かれ、
(255,24,0) の三角は存在しない。検出できていたのは各章の「あたしの家」の赤い
ハート（第6話は `(107,136)-(113,141)`）だけで、これは動かない置物である。

そのため `_probe_launch_markers` の baseline と現在の markers が毎回同じになり、
「新しい▲が無い」= 出撃失敗 と判定して、成功した出撃を巻き戻していた。
巻き戻しは将軍を在城に戻し、指示を pending に戻し、再試行を2回で failed にする。

## 修正（v390）

- `_probe_launch_markers` は、3回の観測で新しい▲が無くても即座に巻き戻さない。
  `blind` を立てて `sortie_launch_unverified`（`resulting_event=roster_pending`）を
  1回だけ記録し、出撃元の将軍一覧の実測を待つ。
- `settle_launch_probes_from_roster`（新設）は、出撃元の城の将軍一覧を最後まで
  読み切った時（`wrapped` / `stalled`）に、出撃した将軍の有無で判定する。
  - 一覧に**いない** → 出撃成立（`sortie_launch_confirmed`）。巻き戻さない。
  - 一覧に**いる** → 本当に出撃していない（`sortie_launch_unverified` / 上限で
    `sortie_launch_failed`）。従来どおり巻き戻す。
  - `blind` になる前でも判定する（実測では一覧の方が先に届く: 00:38:04 の
    観測に対し 00:38:40 の一覧）。
- 将軍一覧が読めないまま `LAUNCH_PROBE_ROSTER_TICKS`（200観測、約5分）を過ぎた
  場合だけ、従来の巻き戻しへフォールバックする。
- `flows/deploy.py` の `_observe_garrison` から、読み切った一覧で判定を呼ぶ。
- `flows/sortie_retry_guard.py` の巻き戻しラッパーに `reason` を通す。

## 確認の仕方

- `bot_version` が v390 になったあと、`sortie_launch_unverified` の
  `resulting_event` が `roster_pending` になり、直後の
  `sortie_launch_confirmed`（理由「出撃元の将軍一覧に出撃した将軍がいないため出撃成立を確認」）
  が続くこと。同じ指示の `sortie_launch_failed` が減ること。
- `tests/test_hanjuku_departure_request.py` が、▲が無いだけでは巻き戻さず、
  将軍一覧にいれば巻き戻し、いなければ成立、待ち時間超過で従来の巻き戻しに
  戻ることを確認する。
- `tests/test_hanjuku_general_list_scan.py` が、読み切った将軍一覧から
  blind な probe を確定／巻き戻しすることを確認する。
- 実プレイでの成立は配備後の実観測で確認する。テストの成功を実機の成立とは
  扱わない。

## 未確認

- 第1〜5話では (255,24,0) の▲が実際に見える場面があり（g1107 で
  `new_markers=[[155,147,158,150]]` の成立2件）、本変更後もその経路は
  そのまま速い確定として残る。章ごとに部隊の描画が違う理由は未解明。
- 顔グラフィックを部隊として検出する実装は行っていない。Y 画面の部隊表示は
  「▲」を前提にしない。


## 実機確認（2026-11-05 00:38 JST、g1133）

v390 がホットロードされた直後の出撃で、期待どおりに動いた。

| 時刻 | 出来事 |
| --- | --- |
| 00:38:00 | マンゴスチンをアルブラーカへ出撃（`order_launched`） |
| 00:38:04 | `sortie_launch_unverified`（`resulting_event=roster_pending`、巻き戻さない） |
| 00:38:40 | `general_list_scanned` → `sortie_launch_confirmed`（将軍一覧にいない） |

巻き戻しは起きず、出撃はそのまま継続した。第6話の章は継続中で、
章クリアそのものは未達（この修正はクリアへの時間短縮であってクリアではない）。

