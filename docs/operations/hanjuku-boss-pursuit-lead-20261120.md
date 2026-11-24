# 半熟英雄: 移動ボスへ「先行」して狙う（v1268、2026-11-20）

## 症状（本番 g1288 の実測）

第9話のボス波は出撃・再照準まで成立するが、ボスへ入城しない。

- 17:46 ボスセル `[938,461]` → 17:56 `[762,629]`（10分で約230px移動）。
- waypoint からボスまでの行軍は約6分。**現在セルを狙うと、到着時にはボスが去っている**。
- `world_map_units` の▲は2隊のまま。ただし `sortie_launch_unconfirmed_kept` が
  記録しているとおり、第9話の行軍部隊は**肖像で描かれ▲に出ない**ため、
  ▲の不変は行軍していない証拠にはならない。

## 変更

- `flows/boss_pursuit.py`:
  - 追撃開始時のボス実測 `boss_cell0` を保存し、destination 確定時に
    `_lead()` で「1回ぶんの実測変位 × 行軍時間」を計算（`PURSUIT_LEAD_TICKS=360`、
    合計 `PURSUIT_LEAD_MAX=240px` でクランプ）。小さすぎる/古すぎる変位は使わない。
  - 追撃のYジャンプ直前に `pursuit_lead` を立て、確定・破棄で消す。
- `os_layer/navigation.boss_probe_cell`: `step == 'PURSUIT'` のときだけ
  `pursuit_lead` を加えたセルを返す。城からの第一脚（waypoint）は不変。
- `state/regions.py`: `pursuit_lead` を章スコープで宣言。
- `version.py`: v1268。hot-load許可リストへ追加。

## 検証

- 追加: `tests/test_hanjuku_boss_pursuit_lead.py`（4件）
  - 1回ぶんの変位を先行に変換し、上限でクランプする
  - 小さすぎる変位・同時刻・古すぎる実測は使わない
  - PURSUIT だけ先行セル、城注文は waypoint のまま
  - dest 相がYジャンプ前に `pursuit_lead` を立てる
- 追撃系・台帳・状態領域 85 passed。

## 未確認・残件

- 実機で先行セルが到達可能か（Yジャンプが失敗しないか）は未確認。
  失敗が続く場合は先行量を縮めるか、先行なしへ戻す。
- 入城そのもの（ボスが同じセルに居る瞬間を作れるか）は次の実測で確認する。
