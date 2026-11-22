# 半熟英雄: 部分名簿の将軍も計画内で予約する（v1343、2026-11-22）

## 事象（docich#4967）

ローカル第10話（`g1190-ch1v4`, `46-7`, step 937616）で、`アルマムーン` に多数の
将軍が在城し複数城が喪失しているのに、生成された暫定計画の**先頭5つの奪還注文が
すべて同一将軍**（ブカティーニ）に割り当てられ、先頭1件の出撃後に残りが停滞した。

## 原因

`interim_candidates` の予約登録が
`present = garrison.get(source) or ()` のみを見ており、**部分名簿**
（`garrison_partial`）から選ばれた将軍は `general in present` が False となって
`planned_busy` に登録されなかった。そのため同じ将軍が全目標へ重複割当されていた。

## 変更

- `present = _known_residents(mem, garrison, source) or ()` へ変更。
  完全未読の城は従来どおり `None → ()`（未予約）。
- `tests/test_hanjuku_planned_busy_partial.py` を追加（修正前は失敗することを確認）。
- `BOT_VERSION` v1343、hot-load許可リストへ追加。

## 検証

- 新規テスト＋既存の multi_spare 5 passed。

Fixes #4967
