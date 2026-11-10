# 半熟英雄: 複数守備将軍の城には6人までの波状攻撃を送る（v628、2026-11-10）

## 症状（実測 08:00〜10:00）

しまばらは**守備将軍が複数**で、1人倒すと次の守備将軍が出ます
（`attack_defender_successor`: 味方18 / 敵85）。一方 ながはま には**8人の余剰**が
あるのに、波状攻撃は `INTERIM_WAVE_LIMIT = 4` で頭打ちでした。

## 修正（v628 `hanjuku-chart-v628-wave-six`）

`INTERIM_WAVE_LIMIT` を **6** に引き上げます。

- 「城を空にしない」（`sender_min = 1`）は従来どおり。ながはま8人なら最大6人まで出せます。
- 予約済み将軍の除外、実測された道（`routes.allowed`）、近い城優先（v620）、
  強い将軍優先（v627）はいずれも不変。

## 確認

- `tests/test_hanjuku_plan_exhausted_interim.py` に1件追加。
- 波状・計画・出撃優先度・診断 128件 passed。
- 本番反映後: しまばらの守備将軍を同一波状で複数倒し、`castle_owned_observed` が出ることを
  実測する。
