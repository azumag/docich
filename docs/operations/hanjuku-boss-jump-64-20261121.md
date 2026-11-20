# 半熟英雄: ボス/waypointのYジャンプ移動上限を64へ（v1283、2026-11-21）

## 症状（本番 g1288 01:1x〜01:3x の実測）

新しいボス波の出撃が成立しない。`order_start ... ボスを攻撃` の直後に
`y_jump_failed` が連続し、出撃先（waypoint）を確定できずに取り消されていた。

- `residual` が 18〜27 view px（144〜216 field px）で、`moves=24` の上限に達して失敗。
- 例: `world_cursor [152.0,85.0] residual [-18.0,-11.0]`、
  `[108.5,56.5] residual [25.5,17.5]`。
- ワールドマップのカーソルは**1押下あたり約1 view px**しか動かないため、
  島の反対側までの移動に数十押下が必要。24では足りない。

## 変更

- `os_layer/navigation.BOSS_JUMP_MOVES` 24 → **64**（有界。最悪でも64観測）。
  城ジャンプの `Y_JUMP_MOVES=8` は不変。
- `tests/test_hanjuku_boss_jump_budget.py` の上限を 128 に更新。
- `BOT_VERSION` v1283、hot-load許可リストへ追加。

## 検証

- `tests/test_hanjuku_boss_jump_64.py`（1件）、ジャンプ系・off_chart 108 passed。

## 期待

- 出撃先Yジャンプが成立し、新しいボス波が継続して出る。
- 波が新鮮に保たれ、追撃・待ち伏せが続く。
