# 受理した野営帰還の到着を将軍名で確認する (v1367)

## 症状（本番 g1288 ch10, docich#5032）

野営からの帰還は `camp_recall_dispatched`（受理）まで進むのに、将軍が野営のまま
残り、月次監査の `camp_abandoned`（3ヶ月）が続いていた。

```
06:59:20 camp_recall_dispatched {"camp": [132, 120], "castle": "アルマムーン", "general": null}
```

- `general: null` — この帰還は将軍に束縛されず、到着は匿名の名簿読み任せ。
- 当該将軍の status tick は dispatch より古いまま（age 1,300 tick 超）で更新されず、
  「帰った／帰っていない」を誰も判定していなかった。

## 変更

- 受理時に「そのとき野営だった将軍」（`general_statuses.statuses` の
  `location == 'camp'`、最大8名）と camp/castle/tick を
  `flow.camp_verify` に保存する。
- camp フローが呼ばれるたび（帰還が動いていないとき）に `camp_arrival_check` を実行:
  - `CAMP_ARRIVAL_TIMEOUT_TICKS = 600`（約15分）経過後に判定する。
  - status が dispatch より後に**読み直されていない**将軍は「未読」として保留
    （読めていない行を証拠にしない）。
  - 読み直し後も `camp` の将軍がいれば `camp_recall_arrival_timeout` を
    **将軍名つき**で記録し、その (camp, castle) を `recall_goal_failures` に
    1件加えて、次回は別の城を選ぶ。
  - 全員が camp を離れていれば `camp_recall_arrival_confirmed` を記録する。
- 入力は一切出さない（記録のみ）。`mem['uncertain']` は失敗確定時だけ立てる。

## 変えないこと

- 帰還シーケンス（いどう→Y→自城）と、受理時の記録（v1350）。
- 監査の `camp_abandoned` 判定そのもの（次サイクルで「dispatch より古い status で
  確定しない」を足す余地を残す）。

## 検証

- `tests/test_hanjuku_camp_receipts.py` に3件追加:
  - 読み直し後も野営 → `camp_recall_arrival_timeout` と goal failure + 将軍名。
  - status が dispatch より古い → 保留（記録しない）。
  - 城内に変わった → `camp_recall_arrival_confirmed`。
- `test_hanjuku_camp_receipts` / `recall_arrival_status` / `recall_month_scene` /
  `recall_interrupt` / `recall_goal_retry` で **73 passed**。
