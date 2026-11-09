# 半熟英雄: 失陥記録で「守備不明」と「守備なし」を区別する（v582、2026-11-09）

## 症状（実測）

19:49:19 の フィロメラ 失陥記録は `garrison: []` でしたが、戦闘パネルの守備将軍は
**ラビオリ**でした。つまり実際は守備がいたのに、駐留の記録が無かった（未読）状態です。

`garrison: []` だけでは「守備なしで落ちた」のか「守備の記録が無かった」のかを
区別できず、守備強化の判断材料になりません。

## 修正（v582 `hanjuku-chart-v582-loss-garrison-known`）

`castle_lost_observed` の `observed_metric` に **`garrison_known`** を追加しました。

- `true`: 駐留リストがあり、その内容（`garrison`）が分かる。
- `false`: 駐留リスト自体が無い＝**未読**（守備なしとは断定しない）。

## 確認

- `tests/test_hanjuku_defense_loss_recorded.py` に1件追加（駐留行が無いとき
  `garrison_known: False`）＋既存テストに `True` の確認を追加。
- 失陥・損失会計 17件 passed。
- 本番反映後: 失陥時に `garrison_known` が付き、守備不明の城を優先して増援する判断に使う。
