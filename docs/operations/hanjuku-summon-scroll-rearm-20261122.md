# おくのて行の読み取り失敗を戦闘中ずっと固定しない (v1364)

## 症状（本番 g1288 ch10 93-8, 2026-11-22 08:2x）

召喚獣戦で「おくのて」行のスクロールが 4 回とも読めず、ボットは
`okunote_summon_preempt.exhausted = true` を**その戦闘のあいだ固定**した。
その時点の戦況:

```
ally ユイートル HP 22 / 敵将軍 セミヨン 36 / 召喚獣 HP 187
okunote_draws = 11, summon_egg_unavailable = true, side = defense
```

召喚獣が 187 HP 残っているのに 奥の手 の再試行が一切なく、白兵のまま押し切られる
（ユーザー指定の「奥の手の不使用」「召喚獣戦の反復」に該当）。

## 原因

`flows/battle_menus.py` `_defender_summon_egg_menu` は、有界スクロール
（2 押し × 2 回）で `おくのて` のラベルが読めないと
`guard['exhausted'] = True` を立て、`_defender_summon_preempt` が以後 None を返す。
**ラベルが読めない**ことと**行が存在しない**ことを区別せず、戦闘単位で固定していた。
ボックスは開き直すたびに描画されるので、後続の観測では読める可能性がある。

## 変更

- `SUMMON_SCROLL_REARM_TICKS = 40`（約60秒）と
  `SUMMON_SCROLL_REARM_LIMIT = 2`（1戦闘あたりの追加試行）を追加。
- スクロール失敗で exhausted にしたとき `exhausted_reason='scroll_unread'` と
  `exhausted_tick` を記録。
- 一定 tick 経過後、`battle_okunote_summon_rearm` を記録して
  `exhausted/downs/stage` をリセットし、もう一度だけ読み直す。
- **親メニュー確定後の exhausted（`selected` 経路）は対象外**（二重確定を避ける）。
- 予算を使い切ったら従来どおり固定（無限スクロールにしない）。

## 検証

- `tests/test_hanjuku_summon_preempt.py` に追加:
  - 失敗直後は従来どおり応戦（`[A]`）。
  - `SUMMON_SCROLL_REARM_TICKS` 経過後に 2 押しの読み直しが再開し、
    `battle_okunote_summon_rearm` が記録される。
  - 追加試行は `SUMMON_SCROLL_REARM_LIMIT` 回で打ち止め。
- 同ファイル **39 passed**（従来38 + 1）。
