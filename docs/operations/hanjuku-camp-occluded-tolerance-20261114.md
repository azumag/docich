# 半熟英雄: カーソルが野営画像を覆ったときの許容幅を広げる（v798、2026-11-14）

## 症状（実測 g1163 2026-11-14 00:22）

野営を見つけた（target `[84,73]`）あと、カーソルが `[90,78]`（約6px 離れた
位置）にあり、tent がカーソルのスプライトに覆われて `own_camps` が空に
なり、`camp_position_unconfirmed` で帰還が中断しました。

```
00:22:13 camp_found {"camp": [84, 73]}
00:22:39 camp_position_unconfirmed {"camp_world": null, "cursor": [90, 78],
                                    "missing": 1, "previous_target": [84, 73]}
```

## 原因

「カーソルが野営画像を覆っている」場合のA許容は `RECALL_ARRIVE_PX=4` で、
スプライトの覆い（数px）に対して厳しすぎました。直前の実測で target と
last_seen が一致していても、カーソルが6px 離れているだけで未確認扱いに
なっていました。

## 修正（v798 `hanjuku-chart-v798-camp-occluded-tolerance`）

- `RECALL_OCCLUDED_PX=8` を追加。
- occluded enter の**カーソル対 target** の判定だけを 8px に広げる。
- `last_seen` と `target` の一致判定（4px）と、未確認位置からのA禁止は
  従来どおり。

## 検証

- 新規 `test_a_cursor_within_the_sprite_can_open_the_last_confirmed_tent`:
  target/last_seen `[84,73]`、カーソル `(90,78)`、tent 非検出でも
  occluded A でメニューを開く。
- 野営・帰還・field recall 関連の既存テストを実行（オーナーの
  「失った野営を追わない」テストを含む）。
- 修正前の `recall.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- 野営発見後にカーソルが数px 離れて tent が隠れても、occluded enter で
  きかんメニューが開くこと。
- 未確認の古い座標からのAは出ないこと。
- 帰還が成立し、野営の将軍が城に戻ること。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
