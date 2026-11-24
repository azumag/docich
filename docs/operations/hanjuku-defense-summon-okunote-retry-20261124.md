# 城守り戦の召喚獣戦で奥の手を打ち切らない (v1428, 2026-11-24)

## 症状（本番 g1342 ch2, 2026-11-24 02:59 JST）

フーリック防衛（ゼウス vs ビシソワーズ）で敵がドラゴンフライ (HP263) を召喚。
02:54〜02:58 は `battle_okunote_summon_preempt` で奥の手を 17 回引いていたが、
02:59:05 の最後のスクロール 2 押しの直後、箱が描画途中（カーソル未読・可視行 1 行）
のフレームで「おくのて行が表示されない」と判定して `exhausted` を立て、
`こうげき` に戻ってそのまま敗北・城を失った。

```
02:59:05 battle_okunote_summon_scroll  down_attempt 4, cursor_y 192,
          visible ["こうげき", "もうこうげき"]
02:59:07 battle_okunote_summon_open_failed  down_attempts 4, okunote_draws 17
02:59:07 battle_egg_attack_unread_cursor    visible ["もうこうげき"]（カーソル未読）
02:59:11 battle_egg_general_attack
02:59:51 battle_result  味方HP0 / 敵召喚獣は残存
```

## 原因

`flows/battle_menus.py` `_defender_summon_egg_menu` がスクロール予算
（`DEFENDER_SUMMON_SCROLL_LIMIT = 4` 押し）を使い切った時点で、画面が
**読めていない**ことと**行が存在しない**ことを区別せず `exhausted` を立てていた。
`_summon_scroll_rearm` の再試行は 40 tick（約60秒）後に 2 回までで、この戦闘は
発火前に終了。実測では最後の 2 押しの効果は次観測で出ており、失敗判定の
フレーム自体が描画途中だった。

## 変更

- スクロール予算を使い切ったフレームで、カーソル未読または可視行が 1 行以下なら
  `battle_okunote_summon_unread_box` を記録して再観測する（`SUMMON_SCROLL_UNREAD_WAITS = 3`）。
  読めないフレームを行なしの証拠にしない。確定は従来どおり可視行のみ。
- 城守り戦（`side=defense`）で敵召喚獣が生きている間（`enemy_summon_observed` /
  `card_summon_observed` があり、`summon_enemy_hp` が未読または 4 超）は、
  読み直しを `SUMMON_SCROLL_LIVE_REARM_TICKS = 8`（約12秒）間隔・
  `SUMMON_SCROLL_LIVE_REARM_LIMIT = 12` 回まで続ける（`battle_okunote_summon_rearm`）。
  押下・確定は従来どおり有界で、間は通常の応戦でターンを進める。
- 召喚獣 HP が一撃で倒せる残り（`<= EGG_WALL_DAMAGE`）なら再試行しない
  （owner 2026-11-04 の既存則）。上限到達は `battle_okunote_summon_rearm_limit` で
  1 回だけ記録する。
- それ以外の戦闘は従来の 40 tick / 2 回のまま。

## 検証

- `tests/test_hanjuku_summon_preempt.py` に追加:
  - 未読フレームでは待機し、`open_failed` を出さず、次の可視フレームで
    おくのてを選ぶ（g1342 02:59 の再現）。
  - 城守り + 召喚獣生存では短い間隔で再試行し、上限 12 回で打ち切る。
  - 召喚獣 HP が一撃圏内なら再試行しない。
- 関連スイート（summon/okunote/defender/defense_egg）480 件 passed。

## 本番確認

- decision log に `battle_okunote_summon_unread_box` / `battle_okunote_summon_rearm`
  （`summon_live: true`）が出ること。城守り戦の召喚獣が残っている間に
  `battle_egg_general_attack` で奥の手が途切れないこと。
