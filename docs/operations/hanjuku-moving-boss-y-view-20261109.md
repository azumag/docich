# 半熟英雄: 第9話の移動ボスをYビューのドクロから実測して攻撃を解禁する（v536、2026-11-09）

## 症状（ゲームクリアの最大ブロッカー）

`_boss_target_unlocked` は第9話のボスを常に False にしていました。
`chart.castles(9)` にボスのセルが無い（移動するため静的座標を持たせない設計）ので、
第9話は章末でボスへ出撃できず、クリアできません。

## 実測・根拠

- 第3話のボスのドクロはYビューで **(101, 63)** に描かれ、対応するセルは
  `(306, 141)`（tests/fixtures の実測フレーム）。
- 同じドクロ sprite が第4話の4地点にも使われており、この関係で計算したセルは
  本番登録済みの `SEASON_SITE_CELLS` と**完全一致**します
  （(146,125) / (882,125) / (130,893) / (898,909)）。
- したがって、移動ボスもYビューにドクロとして描かれていれば、その位置から
  現在セルを実測できます。

## 変更（v536 `hanjuku-chart-v536-moving-boss-y-view`）

1. `os_layer/readers.py`: `y_view_skulls(frame)`（灰色画素を起点にした36点テンプレート照合）と
   `skull_cell_at(pos, chapter)`（Y ビューのオフセットでセルへ変換）。
2. `os_layer/world._apply_moving_boss`: 第9話のYビューでドクロが**1つだけ**見えるとき、
   **2回連続で同じ位置**なら `moving_boss = {cell, tick, readings}` を記録
   （`moving_boss_seen` を判断ログへ）。見えないフレームは部分一致を破棄。
3. `hanjuku_chart`: `MOVING_BOSS_CHAPTER=9` / `DYNAMIC_BOSS_CELLS` / `set_moving_boss_cell` /
   `moving_boss_cell`。`castles(9)` は動的セルがあるときだけ `ボス` を返す。
4. `orchestrator.decide`: 毎判断で `moving_boss` を検査し、`MOVING_BOSS_TICKS`
   （1200観測）以内の新しい実測だけを登録。古い実測は自動で無効化。
5. `strategy/orders._boss_target_unlocked`: 第9話は**動的セルがあるときだけ**ボス注文を許可。
6. `campaign` の章リセットに `moving_boss` / `moving_boss_streak` を追加。

## 安全性

- 実測が無ければ従来どおりロック（挙動不変）。実測が古ければ無効。
- ドクロが0個/2個以上のフレーム、範囲外セルは登録しない。
- 動的セルは第9話だけ。第5話などのボスセルは不変。

## 確認

- `tests/test_hanjuku_moving_boss.py`（新規15件）: 第3話ドクロ→(306,141)、
  第4話4地点→登録セル一致、動的セルの登録/解除/不正値、ボス注文の解禁、
  2回一致の記録、位置変化でやり直し、他章では走らない、`decide` の鮮度同期。
- チャート・ボスセル・層 431件 passed。
- 実機の第9話到達時に `moving_boss_seen` が出るか、出ない場合にドクロ以外の
  描画かを実測する。テストの成功を実機の成立とは扱わない。
