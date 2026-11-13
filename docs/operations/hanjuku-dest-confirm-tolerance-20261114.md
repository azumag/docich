# 半熟英雄: 帰還先ピッカーの確定許容をスプライト幅に合わせる（v809、2026-11-14）

## 症状（実測 g1163 2026-11-14 03:42）

野営のメニューから帰還先の選択（dest）に入り、最初の観測でマーカーと自軍城の
屋根のずれが **6px** でした。`RECALL_CONFIRM_PX=4` では確定できず移動を
返しましたが、次の観測は月替わりの遷移に入り、A が送られないまま場面で
閉じられました。

```
03:42:53 きかん（here）
03:45:29 camp_recall_interrupted {"stage": "dest", "steps": 1, ...}
```

## 原因

ピッカーのマーカーと城ルーフのアンカーはスプライト幅のぶん（数px）ずれて
見えます。v798 で野営の occluded enter を8pxに広げたのと同じ理由で、dest の
確定判定も4pxでは厳しすぎました。

## 修正（v809 `hanjuku-chart-v809-dest-confirm-tolerance`）

`RECALL_CONFIRM_PX` を 4 → **8** に変更します。マーカーが最寄りの自軍城
ルーフの8px以内なら、その場でAを送って確定します（遷移に入る前に）。

## 検証

- 新規 `test_a_marker_within_the_sprite_confirms_the_destination`:
  マーカーと屋根が6pxずれでもAを送り `camp_recall_requested` が記録される。
- 野営・帰還・月場面・field recall 関連の既存テストを実行。
- 修正前の `recall.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- dest の最初の観測でAが送られ、`camp_recall_requested` が出ること。
- 遷移に入る前に帰還先が確定し、月替わりの場面で切られないこと。
- 12話ボス撃破には未到達。目標は active のまま継続します。
