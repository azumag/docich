# 第9話 城一覧の確認回数に上限を付ける（v707、2026-11-12）

## 事象

g1163 2026-11-12 12:3x–12:4x、画面は 15 分間つぎの巡回を繰り返した。

```
main_menu → text(しろ一覧) → map → castle_menu → general_list(しょうぐんはおりません) → map → …
```

この間、`hanjuku_decisions.jsonl` の `decision` は 1110 件、`action_plan` は 2853 件
あったのに、出撃・占領の記録は 0 件。全国の将軍は 32 人、`world.garrison` は
アルマムーン・サヌト・タオ・ハヤク の 4 城すべて `[]` だった。

## 原因

`corridor._roster_probe()` / `_drained_probe()` は、所在不明の将軍（
`country_general_count - (garrison + unknown + marching)`）がいるとき、
`corridor_drained` の付いた城を 1 周につき 1 城だけ読み直す。ところが
**野営中の将軍はどの城の一覧にも出ない**ため、この確認は何度やっても
「しょうぐんはおりません」で終わり、`corridor_drained` を付け直すだけで
終わる。確認は無期限に回り続け、出撃の順番を毎回奪っていた。

## 変更

`src/docich/hanjuku/strategy/corridor.py`

- `PROBE_BUDGET = 6`（1 か月に使える確認の回数）と
  `PROBE_REARM_TICKS = 3600` を追加。
- `_probe_budget_left()`: つぎのいずれかで確認を再開する。
  - 月が変わった
  - 城一覧に将軍が現れた（`_garrison_seen()` が前回より増えた＝帰還・入城の証拠）
  - 前回の確認から `PROBE_REARM_TICKS` 経った
  - まだ `PROBE_BUDGET` 回に達していない
- `_probe_spend()`: 確認を 1 回使ったことを `policy.corridor_probe` に記録し、
  `roster_probe` / `drained_probe` の decision を残す（観測値: 使用回数・上限・
  所在不明数）。
- `_probe_exhausted_once()`: 上限に達して確認を打ち切ったとき
  `corridor_probe_exhausted` を 1 回だけ記録する。理由には「所在不明の将軍は
  野営中の可能性が高く城一覧では見つからない。帰還・入城か月替わりで再開する」を
  残す。
- `orders()` は上限に達したら probe を出さず、通常の攻撃・移動・主人公配置の
  指示だけを返す。打ち切り時も指示が無い場合にだけ `corridor_probe_exhausted`
  を記録する。

## 期待

- 城一覧の巡回が 15 分単位で居座らなくなり、その分が帰還（野営の発見）・出撃・
  占領にあたる。
- 将軍が城に入った時点（`garrison` の増加）で確認は自動的に再開するので、
  「所在不明」の追跡自体は失われない。

## 検証

- `tests/test_hanjuku_corridor_probe_budget.py`（新規 5 件）
- `tests/test_hanjuku_drained_probe.py` / `tests/test_hanjuku_corridor_drained.py`
  既存 20 件と合わせて 20 passed。

# 行軍の代役に主人公を出さない（v708、2026-11-12）

## 事象

live の `world.sorties` に `I:c9:サヌト>オシロガ:328` が `general: どうし`
（主人公）で `en_route` として残っていた。この指示は通常の移動指示（`move`）で、
`_cursor_order_pick()` が「カーソルが主人公で、ほかに出せる将軍がいない、目的が移動」
のときだけ主人公を出撃させていた。結果として

- 主人公が前線（オシロガ）へ出て、常駐先の自城から離れた
- サヌトの一覧が空になり、次の巡回で「出せる将軍がいない」として閉じられた
- 次の周回で `_hero_post_order()` が主人公を後ろへ戻す

という往復が起きていた。オーナー指示（2026-11-12）は「主人公は前線の自城一つ前の
自城に常駐」。行軍の代役に出すのは指示違反。

## 変更

`src/docich/hanjuku/flows/deploy.py`

- `_cursor_order_pick()`: カーソルが主人公のときは、ほかに出せる将軍がいなくても
  出撃させず、`hero_move_refused` を記録して通常経路へ戻す。通常経路は代役から
  主人公を除いたうえで、出撃元の変更または指示の終了を選ぶ。
- 主人公の移動は従来どおり `corridor_hero` の専用指示（`_corridor_hero_pick()`）と
  `corridor._hero_post_order()` だけが行う。

## 検証

- `tests/test_hanjuku_chapter9_cursor_order_sortie.py`:
  `test_a_march_never_sends_the_hero_even_when_he_is_the_only_one`
- `tests/test_hanjuku_chapter9_mechanical_march.py`:
  `test_a_move_never_takes_the_hero_even_when_he_is_the_last`
- 第9話まわりの既存テスト 136 + 122 件と合わせて green。

# 出す指示が無いときだけ城一覧の確認を続ける（v710、2026-11-12）

## 事象

v707 の上限（`PROBE_BUDGET = 6`）だけで打ち切ったところ、13:1x に
**`map` かつ入力ゼロの action_plan が 169 回連続**した。約5分間、一度も
ボタンを押さずにフィールドマップ上で止まっていた。出撃できる城が全部
`corridor_drained` で埋まり、確認も上限で止まると、`orders()` が空になり
オーケストレータに出す指示が無くなるため。

## 変更

`src/docich/hanjuku/strategy/corridor.py`

- `orders()`: 上限に達していても、**ほかに出す指示がある場合だけ**確認を止める
  （`if out and not _probe_budget_left(mem): return out`）。
- ほかに指示が無いときは確認を 1 件出し続ける。この「空いた時の確認」は
  `_probe_spend(..., idle=True)` として `roster_probe_idle` /
  `drained_probe_idle` に記録し、**月ごとの上限を消費しない**。
- これで「確認が通常の攻撃・移動を追い越し続ける」問題（v707）と
  「確認を止めたら何も出さずに立ち尽くす」問題（今回）を両方避ける。

## 検証

- `tests/test_hanjuku_corridor_probe_budget.py`（4件）
  - 上限中は確認が先頭、上限後は実移動が出て確認は出ない
  - 何も無いときは確認が 1 件出て、上限を消費しない
  - 城一覧に将軍が現れた／月が変わったら再武装
- 第9話まわり既存テストと合わせて 66 passed。

# 第9話の行軍中は「空いた城を埋める」目標を作らない（v711、2026-11-12）

## 事象

`policy.goals` に、すでに自城である ハヤク・アルマムーン・タオ・サヌト への
`move` 目標が attempts 0 のまま 86867〜92397 tick（数時間前）から残っていた。
同時刻の `world.garrison` は アルマムーン・サヌト・タオ・ハヤク がすべて `[]`。

## 原因

`strategy/goals._wanted()` は「自城なのに駐留が空」の城すべてに `move` 目標を
立てる。第9話は `chart.guard_free` で城を空にするのが前提で、移動は
`strategy/corridor.py` の機械行軍が担当する（オーナー指示 2026-11-11
「９話は機械的に次の城にすべての将軍を送る、それ以外はdisable」）。そのため
この補充目標は誰も実行せず、台帳だけが埋まっていた。

## 変更

`src/docich/hanjuku/strategy/goals.py`

- `_wanted()`: `chart.guard_free(chapter)` かつ `chart.corridor(chapter)` かつ
  `corridor_next()` がある（＝機械行軍の途中）ときは、空いた城の `move` 目標を
  作らない。街道の城を取り切ったボスフェーズでは従来どおり補充目標を作る。

## 検証

- `tests/test_hanjuku_goals_chapter9_march.py`（3件）
  - 第9話の行軍中は空き城の move 目標も `goal_queued` 記録も出ない
  - 第8話では従来どおり move 目標が出る
  - 街道を取り切った後（ボスフェーズ）は move 目標が戻る
- 目標台帳・第9話まわり既存テストと合わせて 133 passed。

# 出撃の成立確認を城一覧の1ページでも行う（v712、2026-11-12）

## 事象

14:0x、`I:c9:オシロガ>ドンドン:41`〜`44` の4件が**同じ観測で一斉に**
`sortie_launch_failed`（「出撃確定の入力後も部隊▲が現れず再試行の上限に達した」）
になった。しかし同時刻の `world.sorties` には ブカティーニ・バーミセリ が
`en_route` で残っており、実際には出撃していた。`launch_miss` は
`I:c9:タオ>サヌト:86/87/88` で 13/14/15 まで積み上がっていた。

## 原因

`world.units`（全体マップの部隊▲）が空のままで、出撃検証は毎回
`blind` に落ちる。blind の probe は出撃元の城一覧を待つが、その確定は
`settle_launch_probes_from_roster()` が**完全な名簿**を読んだときだけ。
4行（`GENERAL_LIST_FULL`）のページは「一覧がスクロールする」という意味なので
`complete` にはならず、待ち時間 `LAUNCH_PROBE_ROSTER_TICKS` が尽きて巻き戻る。
巻き戻すと指示は `pending` に戻り、同じ移動が再送され、miss が積み上がる。

## 変更

- `flows/map.py`: `settle_launch_probes_from_roster(..., complete=False)` を追加。
  - 一覧に将軍が**いる** → 出撃は成立していないので巻き戻す（1ページでも確実）。
  - いない かつ probe が blind → 出撃成立とみなす（記録に `complete: false`）。
  - いない かつ marker 読み取りの余地が残る → 何もしない。
- `flows/deploy.py`: 部分ページを記録した直後に上記を呼ぶ。

## 検証

- `tests/test_hanjuku_launch_settle_partial_page.py`（4件）: 在籍→巻き戻し／
  blind＋不在→成立／未blindは保留／完全名簿は従来どおり不在で成立。
- 出撃・boss probe まわり既存テストと合わせて 44 passed。

# 空いた時の城一覧確認を作りすぎない（v713、2026-11-12）

## 事象

14:2x の10分間で `roster_probe_idle`（出す指示が無いときの城一覧確認）が **19 件**、
`order_start` は 12 件。idle probe の `order_start` は無く、**作られただけで実行されない**
指示が毎観測のように追加されていた（`_order()` は毎回新しい step id を振る）。

## 原因

`corridor.orders()` は「ほかに指示が無い」たびに idle probe を1件作る。
ところが実際には、選択中・出撃要求中の指示（`sortie_attempt` / `departure_request`）が
進行中でも `out` が空に見えるため、その観測のたびに probe を鋳造していた。

## 変更

`src/docich/hanjuku/strategy/corridor.py`

- `_sortie_in_flight()`: 選択・出撃要求が開いている間は idle probe を作らない
  （進行中の出撃を次の観測で置き換えない）。
- `_idle_probe_due()` と `IDLE_PROBE_SPACING = 60`: idle probe は60 tickに1件まで。
  何も無いときに立ち尽くさない性質（v710）はそのまま。

## 検証

- `tests/test_hanjuku_corridor_probe_budget.py`（6件）: 間隔内は追加しない／
  次の窓で1件／出撃進行中は作らない／上限中は先頭・上限後は実移動、など。
- 第9話・目標まわり既存テストと合わせて 83 passed。

# 古い出撃試行を「出撃中」と数えない（v714、2026-11-12）

## 事象

v713 の反映後、14:4x の6分間で `order_start` も idle probe も **0 件**になった。
live の `flow.sortie_attempt` は `confirmation_tick: 95774` のまま tick 96376
（約600 tick＝10分）残っており、`_sortie_in_flight()` が永久に真になっていた。
全城が `corridor_drained` のため `orders()` も空で、指示が出ない状態。

## 変更

`src/docich/hanjuku/strategy/corridor.py`

- `SORTIE_ATTEMPT_TTL = 120` を追加。
- `_sortie_in_flight()`: `sortie_attempt.confirmation_tick` が TTL 以内のときだけ
  「出撃中」とみなす。古い試行は証拠として無効。
- `departure_request` は従来どおり即時に「出撃中」。

## 検証

- `tests/test_hanjuku_corridor_probe_budget.py`: 古い attempt では idle probe が
  出ることを追加（40 passed、state_regions と合わせて）。

# 空いた時の確認の間隔を空けず、出撃中だけ抑える（v715、2026-11-12）

## 事象

v713 で idle probe を60 tickに1件へ間引いたところ、15:0x の5分間で
`map` かつ入力ゼロのプランが **79 / 162 件**に戻った（v710 で消した
「フィールドで立ち尽くす」状態）。間引いた分だけ `orders()` が空になる時間が増えた。

## 変更

`src/docich/hanjuku/strategy/corridor.py`

- `IDLE_PROBE_SPACING = 0`（何も無いときは毎観測1件。v710 の性質を維持）。
-  churn の原因だった「出撃進行中でも作ってしまう」は `_sortie_in_flight()`
  （v713）と `SORTIE_ATTEMPT_TTL`（v714）で抑える。

## 検証

- `tests/test_hanjuku_corridor_probe_budget.py`: 何も無いときは毎回1件出ること、
  出撃進行中は出さないこと、古い attempt は無視すること（44 passed）。

# 味方が出撃元へ向かっている間は単独突撃しない（v716、2026-11-12）

## 事象

15:1x、`I:c9:ドンドン>オオキク:10` で シュゼット が単独出撃し、目的地前の戦闘で
敗北・退却（`sortie_road_defeat`）。直後に `:11` が始まり、`attack_company_timeout`
（味方を待つ上限切れ）でまた単独突撃になっていた。オオキクの守備将軍は HP 18/34/44
と減らず、単独攻撃は毎回同じ結果になる。

## 変更

`src/docich/hanjuku/strategy/corridor.py`

- `_reinforcements_inbound(mem, castle)`: その城へ向かっている行軍
  （`en_route` / `launched_unconfirmed` / `pending`）を数える。
- `ATTACK_COMPANY_WAIT` の期限が来ても、出撃元へ向かう味方がいる間は待ちを延長する
  （`attack_company_reinforced` を1回だけ記録）。合流する味方がいなければ従来どおり
  期限で単独突撃する（v706 の有界性は維持）。

## 検証

- `tests/test_hanjuku_attack_company.py`: 味方が向かっている間は攻撃しない／
  延長の記録は待ち1回につき1件、を追加（第9話・state_regions と合わせて 97 passed）。

# 同じ城への連続敗北に冷却時間を置く（v717、2026-11-12）

## 事象

14:5x〜15:3x、`I:c9:ドンドン>オオキク` を毎分のように攻撃し、`sortie_road_defeat`
（目的地前の戦闘で敗北）を繰り返した。オオキクの守備将軍の HP は 44 → 54 → 55 → 77 と
動くだけで、城は落ちず、攻撃のたびに1出撃（約1〜2分）を消費していた。

## 変更

`src/docich/hanjuku/strategy/corridor.py`

- `_recent_defeats()`: `sortie_history` の同じ城への敗北を `DEFEAT_COOLDOWN_WINDOW`
  （1500 tick）で数える。
- `_defeat_cooldown_active()`: 窓内に `DEFEAT_COOLDOWN_LIMIT`（3）回以上の敗北が
  あれば `DEFEAT_COOLDOWN_TICKS`（900 tick）だけその城への攻撃を休む
  （`attack_defeat_cooldown` を記録）。休止明けは一度攻撃を許し、また負けたら数え直す。
- ボス城（`chart.is_boss_castle`）はこの休止の対象外。

## 検証

- `tests/test_hanjuku_attack_defeat_cooldown.py`（5件）: 3回で休止／明けたら再開／
  2回では休止しない／窓外の敗北は数えない／ボス城は対象外。
- 第9話・攻撃まわり既存テストと合わせて 92 passed。

# 路上敗北を結果台帳にも残す（v718、2026-11-12）

## 事象

v717 の冷却（`attack_defeat_cooldown`）が 15:4x に一度も発火しなかった。
live の `sortie_history` は `I:c9:ドンドン>オオキク:11`（tick 97670）で終わっており、
`:12`〜`:15` の路上敗北が台帳に載っていなかった。`_recent_defeats()` は
`sortie_history` を数えるので、敗北は1件しか見えていなかった。

## 原因

`sortie_road_defeat`（目的地前の敗北）は `world.sorties[step]['road_defeat']` と
決定ログにしか残らず、`sortie_history` へは追記していなかった。`_repelled_alone()`
など「結果台帳を読む」ルールから路上敗北が見えていなかった。

## 変更

`src/docich/hanjuku/flows/battle.py`

- 路上敗北・路上退却を `sortie_history` にも1件追記（`outcome: lost`, `road: true`,
  `ally_hp` / `enemy_hp` / `tick`）。既存の `road_defeat` ガードにより1敗北1件。

## 検証

- `tests/test_hanjuku_road_defeat_settle.py`: 台帳に1件載ること／2回呼んでも1件、
  を追加。battle・sortie・attack・corridor まわり 605 passed。

# 攻撃は一覧で最も体力のある将軍を選ぶ（v719、2026-11-12）

## 事象

14:5x〜15:5x、`I:c9:ドンドン>オオキク` の攻撃はカーソルがあった将軍
（ポワソン・バゲット・モモ…）をそのまま送り、**10件連続で路上敗北**
（`ally_hp 0` 対 `enemy_hp 54〜77`）していた。第9話のカーソル順ルール
（オーナー 2026-11-11「カーソルがあった順に出撃させれば短縮」）は速度のためのもの
だが、負ける出撃はカーソル2回より遅い。

## 変更

`src/docich/hanjuku/flows/deploy.py`

- `_strongest_listed()`: 一覧の将軍の最大HP（`hanjuku_egg_reference.general_max_hp`）を
  比べ、カーソルの将軍より `STRONG_ATTACK_MARGIN`（10）以上強い将軍がいれば、
  その行へカーソルを送る（`CURSOR_PICK_DOWNS` の範囲内、`attack_strongest_pick` を記録）。
- 対象は第9話の**攻撃・再攻撃**のみ。**移動は従来どおりカーソル順**（速度ルールを維持）。
  主人公は候補にしない。

## 検証

- `tests/test_hanjuku_chapter9_cursor_order_sortie.py`: 同格ならカーソルのまま／
  10以上強い将軍がいればカーソルを送る、を追加。
- 第9話・corridor・state_regions まわり 76 passed。
