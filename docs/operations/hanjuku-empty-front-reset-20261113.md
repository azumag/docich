# 半熟英雄: 空の前線は待ち時計を引き継がない（v785、2026-11-13）

## 症状（実測 g1163 2026-11-13 20:3x）

前線 `ドンドン` が空（在城者0）で、`front_guard_hold` に古い時計
（143007）が残っていました。

```
garrison['ドンドン'] = []
partial = {}
front_guard_hold = {"ドンドン": 143007}
```

この状態で増援が1人入ると、古い時計のせいで「待ち時間の上限を超えた」と
判定され、その1人がすぐ単独出撃して城がまた空になる危険がありました。
また、在城者0でも2人待ちの時計（`front_mass_hold` / `front_mass_count`）が
武装されていました。

## 原因

待ち時計は城名ごとに残り、**空の城でもクリアされない**ためです。

## 修正（v785 `hanjuku-chart-v785-empty-front-reset`）

- 攻撃分岐で `_front_names_now` が空リスト（在城者0）のとき、
  `front_guard_hold` / `front_guard_expired_tick` / `front_mass_hold` /
  `front_mass_expired_tick` / `front_mass_count` を削除する。
- 2人待ちの条件に `0 < len` を追加し、在城者0では待ちを武装しない
  （空の前線は増援の移動で埋める）。

## 検証

- 新規 `test_an_empty_front_drops_its_old_wait_clocks`:
  空の前線で古い時計が消え、2人待ちの時計も残らない。
- corridor / 前線 / capture guard / 派遣 関連 **180件 passed**。
- 修正前の `corridor.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- 空の前線に `front_guard_hold` / `front_mass_hold` が残らないこと。
- 増援が1人入ったとき、古い時計で即単独出撃せず、新しい待ちが始まること。
- 在城者0で `front_mass_hold` が武装されないこと。

## 残件

- 空の前線は増援の移動（オシロガ→ドンドン等）で埋める。移動のページ下限は
  v784 で更新される。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
