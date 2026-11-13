# 半熟英雄: occluded の野営メニュー開放に判断材料を残す（v799、2026-11-14）

## 症状

v798 の「カーソルが野営画像を覆っている」場合の occluded enter は、
`camp_enter` に `camp` と `occluded` しか記録していませんでした。8px の
許容（`RECALL_OCCLUDED_PX`）で実際に何px の差で開いたのか、last_seen と
target が一致していたのかを事後に確認できず、閾値の妥当性を検証できません
でした。

## 修正（v799 `hanjuku-chart-v799-camp-enter-record`）

occluded の `camp_enter` の `observed_metric` に、`cursor` / `last_seen` /
`target` を追加します。挙動は変更しません。

## 検証

- 既存の `test_a_cursor_within_the_sprite_can_open_the_last_confirmed_tent` を
  拡張し、記録の内容（cursor `[90,78]`、last_seen/target `[84,73]`）を確認。
- 野営・帰還・field recall 関連の既存テストを実行。
- 修正前の `recall.py` ではテストが失敗することを確認。

## 本番反映後に実測すること

- occluded の `camp_enter` で、カーソルと target の差（何px で開いたか）が
  確認できること。
- v798 の8px 許容が実際の局面で妥当か（差が4〜8px に収まっているか）を
  次の野営で検証すること。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
