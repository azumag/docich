# 半熟英雄: 野営を見失った記録にカーソル・欠測回数・ワールドセルを残す（v796、2026-11-13）

## 症状（実測 g1163 2026-11-13 23:29/23:30）

野営を見つけた28秒後に `camp_position_unconfirmed` が記録され、帰還が
中断しました。しかし記録には `previous_target`（以前の画面座標）しかなく、
どのカーソル位置で・何回目の欠測で・ワールド座標が何だったのかを
事後に確認できませんでした。

```
camp_position_unconfirmed {"previous_target": [...]}
```

## 修正（v796 `hanjuku-chart-v796-camp-loss-record`）

`camp_position_unconfirmed` の `observed_metric` に次を追加します。

- `cursor`: 欠測時の実カーソル画面座標
- `missing`: 何回目の連続欠測か
- `camp_world`: v795 で保存した野営のワールドセル

挙動（オーナーの「失った野営を追わない」ルール、未確認位置からのAを
出さないこと）は変更しません。

## 検証

- 新規 `test_a_lost_tent_records_where_it_was_looked_for`:
  欠測記録に cursor / missing / camp_world が入る。
- 野営・帰還・field recall 関連の既存テストを実行。
- 修正前の `recall.py` では新テストが `KeyError` で失敗することを確認。

## 本番反映後に実測すること

- `camp_position_unconfirmed` の記録で、カーソル・欠測回数・ワールドセルが
  確認できること。
- 次の改善（カメラ移動の再現条件の特定）に使えること。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
