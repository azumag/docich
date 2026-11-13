# 半熟英雄: 城を取ったらその城の一覧を読む（v775、2026-11-13）

## 症状（実測 g1163 2026-11-13 17:3x）

`オオキク` を再奪還しても、直後に敵に奪い返される往復が続いていました。
さらに、城を取った直後に **その城の将軍一覧を読む経路がなく**、
`general_location_unknown` に将軍が残り続けました。

```
general_location_unknown = ["バゲット", "ジェラート", "チコリ", "ビーツ"]
garrison = {"オオキク": ["バゲット"], "ドンドン": ["クランベリー", "ペコリーノ"], ...}
```

城を取ったときは、勝った将軍が入城し、退却していた住人が戻ってくる
可能性があります。しかし一覧を読まないため、戻った将軍が見えないまま
前線へ送れませんでした。

## 修正（v775 `hanjuku-chart-v775-capture-roster-read`）

城が自軍になったとき、その城の一覧を **`CAPTURE_READ_DELAY=30` tick 後**に
読む要求を登録します。

- `os_layer/world.py::_apply_world_flags` の確認済み `own` 取得分岐。
- `flows/battle.py` の攻撃勝利によるcaptured分岐。

読み取りは v772〜v774 の `roster_read_due`（due 時刻・TTL・1要求1回）を
そのまま使います。城の一覧が読まれると `_observe_garrison` が在城者を
記録し、`general_location_unknown` から該当将軍が消えます。

## 検証

- 新規 `test_a_captured_castle_is_due_for_its_own_roster_read`:
  確認済みの own 取得で `roster_read_due[城] = tick + CAPTURE_READ_DELAY`。
- 所有・防衛・バトル・corridor 関連 **570件 passed**。
- 修正前の `world.py` では新テストが `KeyError` で失敗することを確認。

## 本番反映後に実測すること

- 城を取った約30 tick 後に `roster_probe_due` がその城を読むこと。
- 取った城の `garrison` に住人が現れ、`general_location_unknown` が減ること。
- `オオキク` の往復で将軍が不明のまま失われないこと。

## 残件

- 30 tick は入城の猶予。読み取りが早すぎて空だった場合は次の取得要求で
  再登録される。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
