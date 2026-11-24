# 半熟英雄: 屋根の占領判定を veto したとき、所有記録を先に「自軍」へ書き換えない（v763、2026-11-13）

## 症状（実測 g1163 2026-11-13 14:05）

`オオキク` の屋根が自軍色で読めましたが、直近の全体マップの旗が敵だったため
`castle_owned_vetoed` で占領扱いを見送りました。

```
14:05:18 castle_owned_vetoed オオキク
  「屋根は自軍色だが直近の全体マップの旗が敵のため占領と扱わない」
```

ところが同じ時点で:

```
castle_ownership['オオキク'] = {'owner': 'own', 'tick': 132989, 'chapter': 9}
captured = ['ハヤク','タオ','サヌト','オシロガ','ドンドン']   # オオキクなし
lost = ['オオキク']
```

`_owned()` は `captured` を使うため corridor の target は `オオキク`（奪還）の
まま、一方で所有記録と配信カードは「自軍」と表示する、という食い違いが
発生しました。

## 原因

`os_layer/world.py::observe_owners` が、veto 判定より**前**に
`owner(mem, castle, kind)` を呼んでいました。

```python
owner(mem, STATUS_NAMES.get(castle, castle), kind)   # ← 先に own を書く
captured = mem.setdefault('captured', [])
if kind == 'enemy' and castle in captured: ...
elif kind == 'own' and castle not in captured:
    if world_flag veto: ... continue                  # ← ここで止まる
    captured.append(castle)
```

veto で `captured` への追加を見送っても、所有記録だけは `own` になっていました。

## 修正（v763 `hanjuku-chart-v763-owner-veto-order`）

- 敵屋根: 所有記録を `enemy` にしてから失陥処理（従来どおり）。
- 自軍屋根で既に `captured`: 所有記録を `own` に同期（従来の再確認）。
- 自軍屋根で未捕獲: **veto 判定を通った後**に `owner(..., 'own')` と
  `captured.append` を行う。veto された場合は所有記録を書き換えない。

## 検証

- `tests/test_hanjuku_defense_loss_roof_recapture.py`:
  veto 後に `castle_ownership[CASTLE]['owner'] != 'own'` を追加。
- `tests/test_hanjuku_off_chart.py` ほか所有・防衛系 **102件 + 136件 passed**。
- 修正前の `world.py` では新しい assertion が失敗することを確認。

## 本番反映後に実測すること

- veto された城の `castle_ownership` が `enemy` のまま（または未更新）で、
  `captured`/`lost` と一致すること。
- `オオキク` のような「所有記録だけ自軍」の城が再発しないこと。
- corridor の target が所有記録と食い違わないこと。

## 残件

- 屋根と全体マップ旗のどちらが正しいかはゲーム状態次第。veto は安全側
  （旗を優先）のまま。旗の観測が古い場合の再確認は別途。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
