# 半熟英雄: 月次ステータスの主人公位置を帰還先の判断に使う（v826、2026-11-14）

## 症状（実測 g1163 2026-11-14 08:59/09:01）

帰還先ピッカーの `goal` が **null** のまま 90 observations の予算を使い、
`own` には自軍5城が入っていました。`_recall_goal` は主人公の城を
`world.garrison` から探しますが、在城一覧が空だと主人公の所在を束縛できず、
「首都」も「単一の自軍城」も無いため None を返していました。一方、月次の
ステータス走査（`general_statuses`）には主人公の観測済み位置が残っています。

## 修正（v826 `hanjuku-chart-v826-hero-status-goal`）

`_recall_goal` は、`world.garrison` に主人公が見つからないとき、
`general_statuses[NAME]` の `location` が **観測済み** かつ **自軍城** で
あれば、それを主人公の城として使います。未観測・敵城・移動中は従来どおり
使わず、既存の「未確認の主人公を合流先にしない」ルールを維持します。

## 検証

- 新規 `test_a_status_location_locates_the_hero_for_the_goal`:
  観測済みの自軍城位置から goal が決まる。
- 既存の `test_unconfirmed_hero_is_not_used_as_a_merge_target`（4ケース）と
  帰還先・野営・月場面関連のテストを実行。
- 修正前の `orders.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- goal null だった局面で、`camp_recall_picker_progress` の `hero` が
  入り、ピッカーが目標へ進めること。
- 未観測・敵城の位置では従来どおり goal が null のままであること。
- 12話ボス撃破には未到達。目標は active のまま継続します。
