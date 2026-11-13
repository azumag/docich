# 半熟英雄: 自軍でない城の前線タイマーを捨てる（v783、2026-11-13）

## 症状（潜在バグ）

前線の待ち時計（`front_guard_hold` / `front_guard_expired_tick` /
`front_mass_hold` / `front_mass_expired_tick` / `front_mass_count`）は城名ごとに
保存されます。城を失って敵のものになった後もタイマーが残り、**同じ城を
再奪還したときに古い待ち時計がそのまま使われ**、最初の攻撃や2人待ちが
不当に抑制される可能性がありました。

第9話は `オオキク` / `ドンドン` を何度も取り合っており、再奪還直後の
攻撃が遅れると敵に立て直す時間を与えます。

## 修正（v783 `hanjuku-chart-v783-front-marks-clean`）

`corridor.orders()` の先頭で、現在の自軍城（`_owned` ∩ 章の城）に含まれない
城の前線タイマーを削除します。

- `front_guard_hold`
- `front_guard_expired_tick`
- `front_mass_hold`
- `front_mass_expired_tick`
- `front_mass_count`

## 検証

- 新規 `test_front_marks_for_a_castle_no_longer_ours_are_dropped`:
  敵城（イソ）のタイマーが `orders()` 後に消える。
- 第9話 corridor / 前線 / capture guard / probe 関連 **171件 passed**。
- 修正前の `corridor.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- 失った城のタイマーが状態に残らないこと。
- 再奪還した城が古い待ち時計に引きずられず、増援が揃い次第すぐ攻撃に
  出られること。

## 残件

- 城名は章内で一意のため、同名の別城との衝突はない。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
