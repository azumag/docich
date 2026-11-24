# 半熟英雄: 敵召喚獣に効かない奥の手を繰り返さない（v782、2026-11-13）

## 症状（実測 g1163 2026-11-13 19:28〜19:41）

城防衛の召喚戦で `battle_okunote_last_resort`（奥の手）が**5回**記録され、
そのたびに敵召喚獣のHPは **27 のまま**、味方将軍は **32 → 49 → 19 → 0**
と削られて敗れました。戦闘は約15分続きました。

```
19:28 last_resort ally 32 / enemy 27
19:31 last_resort ally 32 / enemy 27
19:34 last_resort ally 32 / enemy 27
19:40 last_resort ally 49 / enemy 27
19:41 last_resort ally 19 / enemy 27
19:42 battle_result ally 0
```

奥の手（自傷あり、最大88）は敵に効かず、自分の将軍だけを削っていました。

## 原因

奥の手の選択は「敵召喚獣が出ていて味方の卵・札が使えない」局面で毎ターン
再評価されますが、**前回の奥の手で敵HPが減ったか**を見ていませんでした。
効かない奥の手を同じ戦闘で繰り返し、自傷で将軍を失っていました。

## 修正（v782 `hanjuku-chart-v782-last-resort-stall`）

`_okunote_last_resort_gate(cur, last_resort, now)` を追加。

- 前回の奥の手から **60 tick（約1ターン）以上**経過し、敵召喚獣のHPが
  減っていなければ「効かなかった」と判定し、`okunote_last_resort_failed` を
  立てて次の救済メニューへ切り替える。
- 同じ tick 内の再利用（メニューが開き直っただけ）は従来どおり許可する
  （オーナーの再利用ルールを維持）。
- 失敗は `battle_okunote_last_resort_failed` として1回記録する。

## 検証

- 新規テスト4件（`tests/test_hanjuku_okunote_last_resort_gate.py`）
  - 1ターン経過して敵HPが変わらなければ繰り返さない
  - 同じターン内の再利用は許可
  - 敵HPが減っていれば許可
  - 奥の手でない場合はそのまま
- 既存の `test_hanjuku_okunote_reuse.py`（同じ戦闘内の2回目メニュー）を含む
  okunote / バトル / 防衛系 **103件 passed**。
- 修正前の `battle_menus.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- 効かない奥の手の後、次のターンに `battle_okunote_last_resort_failed` が
  記録され、救済メニュー（撤退・別手段）へ移ること。
- 同じ戦闘で `battle_okunote_last_resort` が繰り返されないこと。
- 味方将軍の自傷による敗北が減ること。

## 残件

- 奥の手が1ターンで効く場合は従来どおり継続する（60 tick の判定）。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
