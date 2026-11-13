# 半熟英雄: 全体マップ旗の所有記録を、確認済みの captured/lost 更新と同時に動かす（v770、2026-11-13）

## 症状（実測 g1163 2026-11-13 16:0x）

`ドンドン` の所有記録だけが敵になり、`captured` には残り、`lost` にも
入っていない状態が発生しました。

```
castle_ownership['ドンドン'] = enemy
captured = ['ハヤク','タオ','サヌト','オシロガ','ドンドン']
lost     = ['オオキク']          # ドンドンなし
```

`_owned()` は `captured` を使うため、プランは `ドンドン` を自城として扱い、
配信・防衛の表示は敵、という食い違いになります。

## 原因

`os_layer/world.py::_apply_world_flags` が、**確認前**の旗の読みで
`owner()`（所有記録）を書いていました。一方、`captured` / `lost` の更新は
`_confirmed_flags()` を通った旗だけで行われます。

```
for name, kind in flags.items():
    owner(mem, ..., kind)          # ← 1回の読みで所有記録を書く
...
flags = _confirmed_flags(mem, flags)
for castle, owner in flags.items():
    ... captured/lost の更新 ...    # ← 確認済みだけ
```

1回の誤読で所有記録だけが変わり、`captured`/`lost` と食い違っていました。

## 修正（v770 `hanjuku-chart-v770-flag-owner-confirm`）

- ホーム城・ボス城は従来どおり即時（ホームは home_lost の独自規則、ボスは
  通常の所有セルではない）。
- それ以外の城の所有記録は、`_confirmed_flags()` を通った
  `captured`/`lost` 更新と**同時**に書く。
- 1回の誤読では所有記録・captured・lost のどれも動かない。
- 2回一致で所有記録・captured/lost が揃って動く。

## 検証

- 新規テスト4件（`tests/test_hanjuku_owners_value_change.py`）
  - 1回の own 誤読では所有記録が動かない
  - 2回一致で所有記録と captured が動く
  - 1回の enemy 誤読では所有記録・captured が動かない
  - 2回一致で所有記録 enemy・captured から削除・lost 追加
- 所有・防衛・第9話関連 **244件 passed**。
- 修正前の `world.py` では新テスト2件が失敗することを確認。

## 本番反映後に実測すること

- 1回の旗の誤読で `castle_ownership` だけが変わらないこと。
- 確認後は `castle_ownership` / `captured` / `lost` が一致して動くこと。
- `ドンドン` のような「所有記録だけ敵」の食い違いが再発しないこと。

## 残件

- ホーム城は従来の即時規則のまま（home_lost と所有記録が別経路）。
  必要なら別途統一する。
- 第9話の進行と12話ボス撃破は引き続き実測で確認する。
