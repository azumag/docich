# 半熟英雄: 募集で加入した将軍を本国の一覧で確認する（v793、2026-11-13）

## 症状（実測 g1163 2026-11-13 22:5x）

募集で `カシュー` が加入しました（`country_general_count` の `joined` に追加、
人数31）が、どの城の `garrison` にも現れず、配置は「未分類」のままでした。
国の人数と見えている将軍の数が食い違い、新しい将軍を前線へ送れません。

## 原因

加入告知（`recruit_join_announced`）は将軍名を記録しますが、**本国の城の
一覧を読む要求を出していませんでした**。既存の `roster_read_due` の経路
（v772〜v775、v780）は城の取得・帰還・増援の到着で使われていますが、
募集の加入では使われていませんでした。

## 修正（v793 `hanjuku-chart-v793-recruit-roster-due`）

加入告知の直後に、本国（`chart.home_castle`）の一覧を
`RECRUIT_READ_DELAY=40` tick 後に読む要求を登録します。

- corridor の due probe が本国の一覧を読みます。
- `recruit_join_observed` が加入将軍を確認し、`general_location_unknown` や
  未分類から外れ、前線へ送れるようになります。

## 検証

- 既存の `test_paid_recruit_receipt_survives_later_deduction_and_tracks_actual_join_name`
  に、加入後の `roster_read_due[本国] = tick + 40` の確認を追加。
- chart bot / corridor / 前線 関連 **422件 passed**。
- 修正前の `month.py` では追加アサーションが `KeyError` で失敗することを確認。

## 本番反映後に実測すること

- 募集加入の後、本国の一覧読み（`roster_probe_due`）が出ること。
- 加入将軍が `garrison` に現れ、国の人数との差が縮むこと。
- 新しい将軍を前線へ送れること。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
