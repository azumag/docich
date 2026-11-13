# 半熟英雄: 月次処理中でも帰還先の選択は止めない（v806、2026-11-14）

## 症状（実測 g1163 2026-11-14 02:49-02:55）

野営のメニューを開き「きかん」まで到達したあと、帰還先の選択（dest）が
**5分間 steps 0 のまま**進まず、月替わり（43-4）の場面で閉じられました。

```
02:49:58 きかん（here）
02:55:14 camp_recall_interrupted {"stage": "dest", "steps": 0, ...}
```

`recall_dialog` は `not mem.get('month_sub')` を要求するため、月次処理が
有効な間は recall が画面を所有できず、dest 段が1回も実行されませんでした。

## 修正（v806 `hanjuku-chart-v806-recall-month-priority`）

- `recall_dialog` の判定を `_recall_dialog_wanted` に切り出し。
- 月次処理中でも、**dest 段が map_target（帰還先の実ピッカー）を表示して
  いる間**は recall が画面を所有できるようにする（月次処理が所有するのは
  メニュー画面であり、フィールドのピッカーではない）。
- その他の段（to_camp/menu）と月次処理の関係は従来どおり。

## 検証

- 新規 `test_a_recall_destination_is_not_frozen_by_the_month_sub`:
  dest + month_sub + map_target で recall が画面を所有（True）、
  to_camp + month_sub + map では所有しない（False）、month_sub が無ければ
  to_camp + map でも所有（True）。
- 野営・帰還・月場面・field recall 関連の既存テストを実行。
- 修正前は `_recall_dialog_wanted` が存在せずテストが失敗することを確認。

## 本番反映後に実測すること

- 月次処理中でも dest の steps が進み、帰還先のAが出ること。
- 月替わりの場面までに dest が完了し、帰還が成立すること。
- 12話ボス撃破には未到達。目標は active のまま継続します。
