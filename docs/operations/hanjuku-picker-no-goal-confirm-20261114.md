# 半熟英雄: 帰還先ピッカーが狙えないとき、カーソルの自軍城ですぐ確定する（v831、2026-11-14）

## 症状

帰還先ピッカー（きかん の全島ピッカー）で `_recall_goal` が帰還先を決められない
ことがあります。チャプター9では月次の将軍確認が無効で `general_statuses` が
null、在城一覧も空のため、`origin`・出撃元・主人公の城のどれも束縛できず
`goal` が null のままになります。

このとき v830 までの実装は、**カーソルを右へ押し続けるだけ**でした。

```
if not cursor or not offset or goal is None:
    return [pad('right')] if cursor else []
```

観測の予算（`RECALL_LIMIT` = 90）を使い切るまで押し続け、最後に
「今いる城で確定する」フォールバックへ落ちます。1回あたり約3.5分です。

実測（g1163 2026-11-14）:

- 10:14:31〜10:16:51: ピッカー34ステップ・`goal` null のまま **月場面に切断**
  され、野営の将軍が野に残りました。
- 10:47:39〜10:49:16: 50観測（約2分）`goal` null。ようやく origin が束縛して
  サヌトが選ばれました。

月場面はピッカーの途中でも画面を奪うため、狙えないピッカーを長く放置すると
帰還そのものが失われます。

## 修正（v831 `hanjuku-chart-v831-picker-no-goal-confirm`）

`goal` が null のときの待ち方を変えます。

1. **カーソルの下の自軍城をその場で帰還先にする**（2観測目以降）。
   ピッカーのリングは本拠城から始まり城から城へ吸着するので、カーソルが
   指す自軍城は予算フォールバックが最後に確定する城と同じです。記録は
   `camp_recall_destination` の `origin_source: picker_cursor`。
2. カーソルが自軍城の上にない場合は `PICKER_NO_GOAL_LIMIT`（30観測、
   約2分）で今いる城に確定します。**敵城の上では確定しません**
   （`_picker_on_enemy` は従来どおり予算まで押し続ける）。

`origin` が束縛できる場合（`_recall_goal` が城を返す場合）は従来どおり
最も近い自軍城へ動かします。挙動が変わるのは「狙えない」局面だけです。

## 検証

- 新規 `test_an_aimless_picker_confirms_the_own_castle_under_the_ring`:
  2観測目で自軍城（本拠）を確定し、`origin_source: picker_cursor` を残す。
- 新規 `test_an_aimless_picker_never_confirms_an_enemy_castle`:
  敵城の上では短い予算でも確定しない。
- `test_the_picker_fallback_names_the_capital_when_no_goal` を新しい予算
  （`PICKER_NO_GOAL_LIMIT`）に合わせて更新。
- 帰還先・野営・月場面・field recall・城ジャンプ・キー目録の既存テストを実行。

## 本番反映後に実測すること

- `goal` null のピッカーが `camp_recall_destination`（`picker_cursor`）を
  残して短時間で確定し、月場面に切断される帰還が減ること。
- `camp_recall_requested` が増え、帰還した将軍が城の一覧に現れること。
- 12話ボス撃破には未到達。目標は active のまま継続します。

## CPU incident rollback

2026-11-14 11:25〜11:26 JSTのCPU飽和開始前に配備されたv831のピッカー早期確定を、v833で一時差し戻す。11:24の76.22%から11:26の99.95%への変化との時間的相関が根拠であり、原因確定ではない。記憶圧縮の線形化（#4140）と並行して統合された決着済みstep整理（#4057）は維持する。

v833をSHA-256照合まで配備してもCPU idle=0%、system=63〜87%が継続した。v831の一時差し戻しは飽和の解消に繋がらなかったため、既知の帰還停滞を再導入し続けないようv834で元の修正へ戻す。11:25前後の時間的一致だけで原因とは扱わない。
