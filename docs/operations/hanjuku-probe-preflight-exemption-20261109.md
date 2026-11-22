# 半熟英雄: 読取注文（source_probe）は守備人数の事前判定から除外する（v534、2026-11-09）

## 症状

v532 の「主人公の城が薄いときは出撃を開始しない」判定が、`source_probe`
（将軍名を確定せず一覧を開いて余剰を選ぶ読取注文）にも掛かっていました。
読取注文は一覧を読んだ後に deploy が守備人数を判定する設計で、一覧の読取自体は
`_observe_garrison` が先に記録します。事前判定で注文を止めると、この読取が
失われます。

g1075 11:04-11:48（`test_hanjuku_roster_unplaced`）は、未配置将軍を発見する
唯一の経路が「アルマムーンの一覧を開くこと」だった事例です。事前判定で
読取注文まで止めると、同じ hold に戻ります。

## 修正（v534 `hanjuku-chart-v534-probe-preflight-exemption`）

`strategy/orders._source_spare_missing` の v532 判定に
`order.get('source_probe') is not True` を追加し、読取注文は従来どおり開始する。

- `roster_probe`（読み取り専用）は元から対象外。
- 実際の配備判断は従来どおり deploy の `_keep_hero_guard`（一覧読取後）。
- 攻撃・移動の通常注文への v532 判定は変わらない。

## 確認

- `tests/test_hanjuku_hero_castle_guard.py` に1件追加: 薄い主人公の城でも
  source_probe は開始でき、source_probe でない注文は従来どおり弾く。
- `test_hanjuku_roster_unplaced.py` を含む未配置・計画・チャート外 138件 passed。
- 本番反映後: 主人公の城の一覧読取（`roster_probe_complete` /
  `garrison_seen`）が従来どおり記録されることを実測する。
