# 半熟英雄: 野営回収の将軍ジャンプにも不在スキップを適用（v1328、2026-11-21）

## 症状（本番 g1288 19:47〜19:52 の実測）

- `general_jump_failed`（アニス、`general_missing`・`target_seen=false`・`seen_count=33`）が約2分おきに連続。
- `general_jump_skip` には `アニス: {absent: true, tick: 104302}` が入っているのに再発。
- 19:52 の `general_jump` は age 0 / scrolls 22 で、**新しく開始**されていた。

## 原因

`general_jump` を開始する箇所が2つあり、`map.py` の開始はスキップを見るが、
`recall.py` の `camp_revisit`（期限切れボス行軍将軍へSELECTで飛ぶ）は
**スキップを見ずに開始**していた。不在将軍を2分おきに名簿探索し続けていた。

## 変更

- `recall._camp_recall_step` の `camp_revisit` 開始前に `general_jump_skip` を確認。
  `absent` なら 3600 tick、通常 200 tick は開始しない。
- `BOT_VERSION` v1328、hot-load許可リストへ追加。

## 検証

- 野営→ボス再送・第9話野営帰還・不在スキップ・台帳 54 passed。

## 期待

- 不在将軍の名簿探索ループが止まり、フィールドが他の処理へ戻る。
