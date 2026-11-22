# 半熟英雄: 止め待ちの局面では月次のカード買いを後回しにする（v791、2026-11-13）

## 症状（実測 g1163 2026-11-13 22:2x）

`オオキク` の守備将軍が現在パネルで **3 HP** のまま、40年11月の月次処理と
商人のカード買いが続き、止め攻撃が実行されませんでした。守備将軍は
戦闘の間に回復・交代するため、止めの機会は長く残りません。

```
castle_defenders['オオキク'] = {"general": "グリッシーニ", "hp": 3, ...}
（その間、月次処理 bargain_plan / army_count_check が進行）
```

## 原因

月次の任意カード買い（`plan_bargains`）に、前線の止め待ちを見るガードが
ありませんでした。必須の兵士補充・募集は別経路です。

## 修正（v791 `hanjuku-chart-v791-finish-pending-shopping`）

- `corridor.finish_pending(mem)` を追加。機械モードで、現在の対象城の
  守備将軍パネルHPが `FINISHING_HP` 以下なら True。
- `plan_bargains` の先頭で `finish_pending` が真なら何も計画せず戻る
  （任意カードの買いを翌月へ回す）。店は `nothing_planned` で閉じ、
  止め攻撃が先に実行される。
- 兵士補充・募集・兵数確認は従来どおり。

## 検証

- 新規 `test_finish_pending_reads_the_current_panel`:
  パネル3HPで True、60HPで False。
- 新規 `test_a_pending_finish_skips_the_optional_card_planning`:
  止め待ち中は `plan_bargains` が商品を計画しない。
- 商人・前線・corridor 関連 **234件 passed**。
- 修正前の `corridor.py` / `bargains.py` では新テスト2件が失敗することを確認。

## 本番反映後に実測すること

- 止め待ち中はカード買いが計画されず（`planned` 空）、止め攻撃が先に出ること。
- カード買いは止めが解消した月に再開されること。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
