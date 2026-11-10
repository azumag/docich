# 半熟英雄: 所持上限の札は商人に聞かずにやめる（v543、2026-11-09）

## 症状

g1163 第6話 月11-11、2026-11-09 08:53:32〜08:57:07 JST。月次の商人で
`buy` → `buy_skip` を**15回**繰り返し、約3.5分を消費しました。
`buy_skip` の観測はすべて
`deviation_reason: holding_limit` / `card_stock: ノリウツール99 …`
（商人が所持上限99で売らない）で、どの札も**すでに上限まで持っている**状態でした。

## 原因

`shop_step` は購入キューを先頭から処理し、`held_estimate(mem, name)` が上限99でも
いったん商人に数量を提示していました。断られてから `hold_room` が0と分かり諦めるため、
**札1枚ごとに「Aを押す→断り文を待つ」往復**が発生していました。

## 修正（v543 `hanjuku-chart-v543-capped-card-skip`）

`shop_step` の購入ループ先頭で `held_estimate(mem, name) >= HOLD_CAP` を判定し、
上限の札は**商人に聞かずに**キューから外して `buy_skip`（`deviation_reason='holding_cap'`）
を記録します。候補札の選定（`plan_bargains`）には手を入れません。

## 確認

- `tests/test_hanjuku_merchant_bargains.py` に2件追加:
  上限ちょうどの札はAを押さずに外れる、上限-1の札は従来どおり提示する。
- 商人・購入・経済 108件 passed。
- 本番反映後: `buy_skip(holding_limit)` の連続と月次商人の滞在時間が減ることを実測する。
