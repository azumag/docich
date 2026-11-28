# 半熟英雄: ボス戦以外の相手HP1では資源を使わない（v1506、2026-11-25）

## オーナー指示

> ボス戦以外では、相手のHPが１なら切り札や奥の手や卵を使わない

## 再発検知（v1509）

同じ違反（ボス戦以外で相手将軍HP1の `battle_card`）を `month_audit` が検知し、
`battle_card_at_hp1` の auto-generated Issue を起票します。カードの選択・実使用
告知を待たず、カードを開く判断が出た時点で起票します。詳細は
[`hanjuku-card-at-hp1-audit-20261125.md`](./hanjuku-card-at-hp1-audit-20261125.md)。

## 実測（本番 g1342、2026-11-25 17:04 JST）

```
マフィン HP1（敵兵士1） / こちら HP23
battle_card: ミックミー
reason: 調整チャート既定: 検証済み戦術のない携行切り札を開幕使用
observed_metric.card_damage_gate.role: single_card_lethal
```

相手将軍は HP1 で、戦闘はほぼ決着していましたが、敵兵士が1残っていたため
`_one_hit_lethal`（敵兵士ゼロが条件）が成立せず、チャート既定の開幕札が
選ばれていました。ボス戦ではありません。

## 変更

- `judgment/battle._boss_battle` を追加。章ボス名（`chart.BOSSES`）、reference の
  ボス将軍・ボスモンスター、ボス城、実測 `measured_boss_entry` をボス戦として扱う。
- `judgment/battle._hp1_resource_hold` を追加。**ボス戦以外**で、現在の戦闘パネルが
  相手将軍 HP1・自軍 HP正数・未読/召喚/卵戦の除外を満たすとき True。敵兵士の
  残数は条件にしない（2026-11-22 の「HP1でも兵士が残れば押し切れない」は
  一撃フィニッシュの条件であり、資源温存の条件ではない）。
- `flows/battle.battle_step` は、この判定が成立したら `battle_hp1_resource_hold` を
  1回だけ記録し、白兵の A 連打へ進む。卵落とし札（dropper）があっても HP1 では
  開かない。既存の `_one_hit_lethal`（兵士ゼロ）が成立する HP1 は従来どおり
  `battle_one_hit_finish` のまま。
- 開いているカード一覧・戦闘メニュー・おくのて候補は、HP1 の温存判定でも
  選択せず B で閉じる（`flows/battle_menus`）。
- 防衛の切り札一覧読み（`_defense_kit_read_window`）とおくのて最後の手段
  （`_defender_last_resort`）も HP1 では開かない。
- `BOT_VERSION` v1506、診断の hot-load 既知バージョンへ追加。

## ボス戦の扱い

ボス戦はこの温存判定の対象外です。既存の `_one_hit_lethal` は変更していないため、
ボスが HP1・敵兵士ゼロなら従来どおり白兵の一撃フィニッシュ、敵兵士が残る場合は
従来どおりカード・卵・おくのての戦術が使えます。今回の変更は「ボス戦以外」の
HP1 にだけ追加の温存を課します。

## 検証

- 新規 `tests/test_hanjuku_hp1_resource_hold.py`（11件）
  - 敵兵士1の HP1 でカードを選ばず A 連打、`battle_hp1_resource_hold` を記録
  - ボス名・ボス城・`measured_boss_entry` では判定しない
  - 未読・召喚・卵戦のフラグでは判定しない
  - 開いたカード一覧・戦闘メニュー・おくのてを未選択で閉じる
  - 敵兵士ゼロの HP1 は従来の `battle_one_hit_finish`
- `tests/test_hanjuku_egg_plan.py`: HP1 の卵落とし開幕を白兵へ変更し、HP2 では
  従来の卵落とし開幕を維持する回帰を追加。
- 関連する半熟の battle/card/egg/okunote/survival/retreat テスト
  2171 passed, 1 skipped。

## 未確認・残件

- 自然ランでの `battle_hp1_resource_hold` の発火と、HP1 でのカード・卵・おくのて
  選択が消えることは未確認。次回の本番ログで確認する。
- 自軍 HP が 1〜4 で相手 HP1 のときも温存する（オーナー指示どおり相手 HP のみを
  条件にする）。この状況で白兵負けが観測された場合は、救済の優先を別途判断する。
