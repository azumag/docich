# 第6話ボス行軍が上限で「迷子」にされる（v401、2026-11-05）

## 症状（実測）

第6話で通常11城を制圧したあと、`interim_candidates` がボス隊を編成し、
オケアナから2隊（ヘルメス、リースリング）をボスへ出撃させた。

- 出撃: tick 39917 / 39943（`sortie_departure_requested` → `order_launched`）
- Yジャンプは目標セルで確定（`world_cursor` 165.5, 70.5 / residual -0.2, 0.0）
- tick 40736（819観測後）に `boss_sortie_overdue` が発火:
  `{"general": "ヘルメス", "marching": 819, "relaunches": 0, "source": "オケアナ"}`
- 同時刻のもう1隊（リースリング）は `en_route` のまま

## 原因

`_arrival_limit` は「チャート距離 × 章の実測レート」で待ち時間を出すが、
第6話の下限は `BOSS_ARRIVAL_LIMIT`（800観測 = 約20分）で、
オケアナ→ボスはチャート距離 203 セル × 2.4 = 487 → 下限 800 が採用されていた。

同じ第6話の実測（`docs/operations/hanjuku-ch6-march-arrival-20261105.md`）では、
603セルの行軍に23〜32分（1380〜1920観測）、740セルに34分（2040観測）かかっている。
ボスは森の中にあり実路は直線より長いため、800観測は行軍の途中で切っていた。

## 修正

- `_arrival_limit`: 目標がボス城（`chart.is_boss_castle`）のときは
  `MARCH_ARRIVAL_CAP`（= `LONG_MARCH_TICKS` 1600観測・約35分）まで待つ。
- これは `_long_march` が戦闘を指示に結び付けられる上限と同じで、
  到着しても紐付かない待ちにはしない。
- 通常城への行軍は従来どおり距離依存。実測レートのない章は従来どおり固定上限。
- 出し直しの予算（`BOSS_RELAUNCH_LIMIT`）と `BOSS_STALE_COOLDOWN` は変更しない。
  セルが誤っている場合の無限ループは従来どおり上限で止まる。

## 検証

- `tests/test_hanjuku_ch6_boss_march_wait.py`（新規 5 件）:
  ボス行軍は `MARCH_ARRIVAL_CAP`、通常城は距離依存、
  819観測では取り消さない、上限後に出し直す、実測レートのない章は固定上限。
- 修正前のコードではこのうち 2 件が失敗（2 failed）、修正後は 5 passed。
- 関連: `test_hanjuku_boss_sortie_overdue.py` / `test_hanjuku_boss_wave_double.py` /
  `test_hanjuku_long_march_bind.py` / `test_hanjuku_recall_arrival_status.py` と併せて 97 passed。

## 未確認・残件

- 実機でボス隊が上限内に入城するかは未確認（v401 稼働後に `attack_observed` /
  ボス入城メッセージを確認する）。
- ボスセル (818,184) 自体は未検証。入城しなければセルが誤っている可能性が高く、
  その場合は次ラウンドで Y ビューの実測からセルを修正する。
