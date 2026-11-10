# 半熟英雄: 通常出撃は切り札一覧から早く抜ける（v625、2026-11-10）

## 症状（実測 08:40〜09:03）

`situation_held` が **30分で215件**、うち大半が「切り札一覧…構造不一致」の待機。
`card_list_unreadable_depart` は **18件**で、1回あたり**6観測**（約8秒）待ってから
B→A の脱出経路に入っていました。

## 変更（v625 `hanjuku-chart-v625-ordinary-card-escape`）

- **通常の出撃**（ボス以外）は `NORMAL_CARD_UNREADABLE_LIMIT = 3` で脱出経路へ。
- **ボス出撃は従来どおり** `CARD_UNREADABLE_LIMIT = 6`（キャンセル予算を維持）。

待機が半分になるぶん、同じ30分で出撃を試せる回数が増えます。

## 確認

- `tests/test_hanjuku_sortie_evidence.py` の該当テストを新定数へ更新。
- 切り札・出撃・確認・ボス切り札・診断 **343件 passed**（ボス予算のテストは不変で合格）。
- 本番反映後: `situation_held` の減少と `order_launched` の増加を実測する。
