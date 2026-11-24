# 半熟英雄: 自城に塞がれた行軍は出し直さず指示を閉じる（v571、2026-11-09）

## 症状（実測）

g1163 第7話 2026-11-09 15:30〜17:08 に `sortie_march_overdue` が5件。
すべてアルマムーン発で、入城も経由も観測されないまま上限超過:

| 時刻 | 将軍 | 行先 | 同行軍の後半 |
|---|---|---|---|
| 15:30:13 | ピオーネ | ロキュータ | 802 ticks |
| 16:32:20 | ジェラート | ロキュータ | 815 ticks（再送） |
| 16:48:13 | ガルバンゾー | フィロメラ | 1135 ticks |
| 17:08:02 | ナストリーニ | リデンブロック | 933 ticks |
| 17:08:02 | タピオカ | ハシオクラム | 901 ticks |

`chart.march_blocked` で確認すると、**アルマムーン→ロキュータはレオナール**、
**アルマムーン→フィロメラはベンゴディ**（いずれも自城）に直線行路を塞がれています。
直進した部隊は塞いだ自城の位置で止まり、敵城へは決して届きません。

## 原因

`_relaunch_overdue_march` は上限超過の出撃を**同じ出撃元のまま pending に戻して
出し直す**だけで、行路が自城に塞がれているかを見ていませんでした。そのため
レオナール取得後も同じ塞がれた行路を繰り返し送っていました。

## 修正（v571 `hanjuku-chart-v571-march-blocked-settle`）

`_relaunch_overdue_march` で、行先が敵城かつ未取得、そして
`chart.march_blocked(chapter, source, target, owned)` が真なら、
出し直さず **オーダーを failed に閉じ**、`sortie_march_blocked_settled` を記録します。

- 以後の計画は塞いでいる自城（`_interim_source` の blocker 経路）からの出撃を選びます。
- 道路章（`routes.is_road`）とボス行軍は従来どおり（ボスは専用の再送規則）。
- 塞がれていない行軍（リデンブロック/ハシオクラムなど）は従来どおり再送。

## 確認

- `tests/test_hanjuku_march_blocked_settle.py`（新規2件）: 塞がれた行軍は failed＋
  `sortie_march_blocked_settled`、塞がれていない行軍は従来どおり pending へ。
- 行軍・計画系 141件 passed。
- 本番反映後: `sortie_march_blocked_settled` が出て、同じ塞がれた行路の再送が消えることを
  実測する。
