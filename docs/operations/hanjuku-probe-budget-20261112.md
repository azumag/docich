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

# 攻撃で主人公の行から外れる（v720、2026-11-12）

## 事象

v719（攻撃は最も体力のある将軍を選ぶ）を反映しても `attack_strongest_pick` が
一度も記録されなかった。`cursor_order_pick` 自体は動いていた。

## 原因

主人公（どうし）が一覧の先頭にいることが多く、`_cursor_order_pick()` の
「カーソルが主人公なら下へ送る」分岐が先に走って `None` を返していた。私が追加した
「最も体力のある将軍」判定はその後ろにあり、**主人公が先頭のときは到達しなかった**。

## 変更

`src/docich/hanjuku/flows/deploy.py`

- 攻撃（`purpose != 'move'`）の最強将軍判定を、主人公・busy 判定より**前**へ移動。
- `_strongest_listed()`: カーソルが主人公のときは「主人公のHPなし」を下限として
  扱い、他の将軍がいれば必ず候補にする（主人公は候補にしない）。
- 一覧外の行にいる場合は `attack_strongest_offscreen` を記録して、次回の観測で
  分かるようにした。

## 検証

- `tests/test_hanjuku_chapter9_cursor_order_sortie.py`: 主人公が先頭でも下へ送る、を追加。
- 第9話・corridor・state_regions まわり 81 passed。

# 第9話の行軍中は築城をしない（v721、2026-11-12）

## 事象

16:1x〜16:4x の30分で `month_sub_step` が 74 件、`chikujou_scroll` が
5〜8 件/10分。街道を進めている最中に、月次の築城で城一覧をスクロールしていた。

## 根拠

オーナー指示 2026-11-11「９話は機械的に次の城にすべての将軍を送る、それ以外は
disable」。築城は行軍の速度に寄与しない。

## 変更

`src/docich/hanjuku/strategy/economy.py`

- `_mechanical_march()`: `chart.guard_free` かつ `chart.corridor` かつ
  `corridor_next()` がある（＝第9話の行軍中）を判定。最小 chart 名前空間では False。
- `_plan_extras()`: 行軍中は `shop['chikujou'] = 'skipped'` とし、
  `month_chikujou_skipped` を1回だけ記録。ボスフェーズ・他章は従来どおり。

卵回復・修理・募集・兵士補充は従来どおり実施する。

## 検証

- `tests/test_hanjuku_chapter9_skip_chikujou.py`（3件）
- 築城・月次まわり既存テストと合わせて 114 passed。

# 攻撃は「傷ついた将軍」を選ばない（v722、2026-11-12）

## 事象

16:4x〜17:0x、オオキクへの攻撃は **14連敗**。戦闘開始時の味方HPは 18・22・27・53
なのに、守備将軍は 49〜68。`sortie_history` は オオキク lost 14 / オシロガ won 4。

## 原因

攻撃の将軍選びが**最大HP**だけを見ていたため、傷ついた将軍（例: 最大49だが現在5）を
「強い将軍」として選んでしまう可能性があった。戦闘開始時のHPは読めているのに、
その値を状態に残していなかった。

## 変更

- `flows/battle.py`: 戦闘開始時に読めた味方HPを
  `world.general_hp[将軍] = {hp, max, tick}` として保存（主人公は `しゅじんこう` 名）。
- `flows/deploy.py`: `_strongest_listed()` は `world.general_hp` の現在HPを優先し、
  無い場合だけ最大HPを使う。傷ついた将軍へカーソルを送らない。

## 検証

- `tests/test_hanjuku_chapter9_cursor_order_sortie.py`: 傷ついた強将軍には送らない／
  元気な強将軍には送る、を追加（9件）。
- 第9話・state_regions・layers と合わせて 92 passed。

# 前線の城に将軍を1人残す（v724、2026-11-12）

## 事象

17:4x、前線 ドンドン の将軍を オオキク 攻撃へ出し続けた結果、ドンドン が守備戦で
落ちた（`castle_lost_observed`）。自城 6 → 5。奪還には丸1サイクルかかる。
第9話は `guard_free`（城を空にしてよい章）だが、**前線の城は道の足場**なので
空にすると取り直しのコストになる。

## 変更

`src/docich/hanjuku/strategy/corridor.py`

- `FRONT_GUARD = 1` と `_front_guard_short()` を追加。出撃元の在城者が実測できて
  いる（`world.garrison` か `world.garrison_partial` の氏名）ときに限り、残りが
  `FRONT_GUARD` 以下なら出撃させない（`front_guard_held` を記録）。
- 適用は「返り討ちにあった／取ってすぐ失った城」への攻撃・再攻撃のときだけ
  （`_repelled_alone` または `_flip_prone`）。ボス城は対象外。

## 検証

- `tests/test_hanjuku_front_guard.py`（5件: 最後の1人は出さない／2人なら出せる／
  1ページの氏名も数える／未読は根拠なし／ボス城は対象外）
- 第9話・攻撃・corridor・state_regions と合わせて 118 passed。

# 弱い将軍を1人ずつ敵城へ出さない（v725、2026-11-12）

## 指摘と実測

オーナー指摘（2026-11-12）「敵城にひとりずつ派遣してるから効率悪い」。
実測: オオキクへの攻撃は **20件中19件が敗北**（`ally_hp 18〜60` 対 `enemy_hp 50〜74`）。
守備将軍は回復して同じHP帯に戻り、出撃だけが消費されていた。

## 変更

`src/docich/hanjuku/strategy/corridor.py`

- `ATTACK_POWER_RATIO = 0.9` / `_attack_underpowered()` を追加。
  出撃元の在城者のうち **実測HPが最も高い将軍** が、その城の守備将軍の
  **直近実測HP × 0.9** に届かないときは出撃しない（`attack_underpowered` を
  300 tick に1回だけ記録）。HPが未測定のときは従来どおり出撃する。
- 併せて `state/regions.py` の `KEY_OWNERSHIP` に、未宣言だった
  `attack_defeat_hold` / `attack_underpowered` / `corridor_probe` /
  `world_map_pending` を宣言（#3993 の静的検査が検出）。

## 検証

- `tests/test_hanjuku_attack_underpowered.py`（5件: 弱い将軍は出さない／元気なら
  出撃／HP未測定は止めない／守備HP読みなしは止めない／記録は窓に1回）
- 攻撃・corridor・front・state_regions・key_inventory・chapter9 で 253 passed。

# 卵メニューの選択を1回で諦めない（v726、2026-11-12）

## 指摘と実測

オーナー指摘（2026-11-12）「卵持ってるのに使わずに死んだりしている」。
実測: `battle_egg_before_retreat`（退却より先に卵を確認）は発火しているが、
`monster_menu_drawing` / `monster_turn_unchosen`（技メニューが出る前にAが届く）や
`battle_egg_drop_unconfirmed`（効果を白兵ダメージと区別できない）が残り、
**メニューを1回選んだだけで召喚が成立しなかった場合、その戦闘では二度と卵を試さない**
実装だった（`egg_attempted` が恒久フラグ）。

## 変更

- `judgment/battle.py`: `EGG_ATTEMPT_LIMIT = 2` を追加し、`_egg_before_retreat()` は
  選択回数（`egg_attempts`）が上限未満なら再試行を許す。旧来の `egg_attempted` のみ
  （カウンタ無し）は従来どおり1回で打ち切り。
- `flows/battle_menus.py`: 卵メニューを選ぶたび `egg_attempts` を加算。

## 検証

- `tests/test_hanjuku_combat_strength.py`: 1回失敗後の再試行が許され、2回で打ち切られる
  ことを追加。
- egg・survival・retreat・battle_menu・combat のテスト **1476 passed, 1 skipped**。

# 敵城へは波状攻撃にする（v727、2026-11-12）

## オーナー指示

「問題は、将軍自体のHPや戦闘力でなく、波状攻撃していないこと。各個撃破されているから
意味がない。将軍のステータスによらず、どんどん上から順に将軍を送るべき」。

v719〜v725 で入れた攻撃側の抑制（最強将軍の選択・傷ついた将軍の回避・
`ATTACK_COMPANY` の待ち合わせ・連続敗北の冷却・`attack_underpowered` の見送り）は、
いずれも**攻撃を止める**方向で、オーナーの意図と逆でした。

## 変更

`src/docich/hanjuku/strategy/corridor.py`

- 攻撃分岐から、待ち合わせ（`ATTACK_COMPANY`）・冷却（`_defeat_cooldown_active`）・
  強さ判定（`_attack_underpowered`）・前線ガード（`_front_guard_short`）を撤去。
  残る上限は「同時行軍 `SQUAD`（6人）」のみ。攻撃は上から順に、途切れず出す。
- 未使用になった定数とヘルパー（`FRONT_GUARD` / `ATTACK_POWER_RATIO` /
  `DEFEAT_COOLDOWN_*` / `ATTACK_COMPANY*` / `_attack_underpowered` /
  `_defeat_cooldown_active` / `_recent_defeats` / `_reinforcements_inbound` /
  `_front_guard_short`）を削除。

`src/docich/hanjuku/flows/deploy.py`

- 攻撃将軍の「最強／元気な将軍を選ぶ」処理を削除し、カーソル順（上から順）に戻した。
  主人公を出さない規則は維持。

## 検証

- 不要になったテスト（attack_company / attack_defeat_cooldown / attack_underpowered /
  front_guard）を削除し、`test_hanjuku_chapter9_cursor_order_sortie.py` に
  「攻撃はカーソル順（上から）」を戻した。
- corridor・chapter9・cursor・attack・state_regions・key_inventory・road_defeat で
  **352 passed**。

# 決着済みの行軍記録を保持期限後に片付ける（v728、2026-11-12）

## 事象

19:0x、`world.sorties` に `オシロガ>ドンドン` の `overdue` が8件（うち1件は
target が None）残っていた。すでに決着した波の記録で、状態の圧縮
（`SETTLED_MARCH_TICKS`）まで残り続ける。

## 変更

`src/docich/hanjuku/strategy/orders.py`

- `OVERDUE_RETENTION_TICKS = 900` と `_overdue_retired()` を追加。
  `overdue` の行軍記録を、保持期限を過ぎたら片付ける（`sortie_overdue_retired` を記録）。
  ただし次の場合は残す: 行先確認（`flow.location_recheck`）が指している／
  同じ step の指示がまだ `launched`／ボス城への行軍。
- 走査を `list(...)` にコピーして、ループ中の削除で破綻しないようにした。

## 検証

- `tests/test_hanjuku_overdue_retention.py`（3件: 期限超過は片付く／新しいものは残る／
  行先確認が指すものは残る）
- sortie・march・overdue・spent・road・corridor・chapter9 で **565 passed**。

# 取ったばかりの城を空にしない（v729、2026-11-12）

## 事象

19:0x、波状攻撃（v727）で ドンドン を**2回奪取**（`sortie_history`: won 2件）したが、
同じ時間帯に**2回失った**（lost 2件）。取った直後に全員を次へ出して空にしたため、
敵に奪い返されていた（フリップ）。

## 変更

`src/docich/hanjuku/strategy/corridor.py`

- `CAPTURE_GUARD = 1` と `_capture_guard_short()` を追加。`sortie_history` で
  **直近 `FLIP_WINDOW_TICKS` 以内に取った城**（その後まだ失っていない）について、
  実測できている在城者が1人以下なら、その城からの**移動**を見送る
  （`capture_guard_held` を記録）。
- 攻撃（波状攻撃）は止めない。空にするのは「取りたての城からの移動」だけ。

## 検証

- `tests/test_hanjuku_capture_guard.py`（5件: 1人なら保持／2人なら移動可／
  古い奪取は保持しない／失った城は対象外／未読は根拠なし）
- corridor・chapter9・capture・overdue・state_regions・key_inventory・sortie で
  **563 passed**。

# 波状攻撃中は城一覧の確認を挟まない（v730、2026-11-12）

## 事象

19:1x〜19:3x の30分で `roster_probe_idle` / `drained_probe_idle`（出す指示が無いときの
城一覧確認）が12件。同時刻は ドンドン への波状攻撃が進行中で、確認は波の合間の
空振り訪問になっていた。

## 変更

`src/docich/hanjuku/strategy/corridor.py`

- `orders()`: 次の城へ向かう攻撃行軍（`_attackers_marching(mem, target) > 0`）が
  ある間は idle probe を出さない。波が終われば従来どおり確認を再開する。

## 検証

- `tests/test_hanjuku_corridor_probe_budget.py` に「波の間は idle probe を出さない」を追加。
- corridor・drained・capture・chapter9・state_regions・key_inventory で 192 passed。

# 作った idle probe は作り直さず使い回す（v731、2026-11-12）

## 事象

v713 の計測では idle probe（何も無いときの城一覧確認）が10分で19件作られ、
実行されないまま毎観測ごとに新しい step が増えていた。v730 で「波の間は出さない」に
したが、波が無いときは同じ増殖が残る。

## 変更

`src/docich/hanjuku/strategy/corridor.py`

- 直前に作った idle probe（`corridor_probe.idle_step`）がまだ指示として残り、
  実行もされていないなら、新しい probe を作らずにその指示を返す。
- 実行が始まった（`plan.active` がその step）か、指示が消えたら新規作成に戻る。

## 検証

- `tests/test_hanjuku_corridor_probe_budget.py`: 2回目の呼び出しが同じ step を
  返すことを追加（9件）。
- corridor・chapter9・squads・state_regions・key_inventory・capture で 100 passed。

# 波状攻撃中は予算付きの確認も割り込ませない（v732、2026-11-12）

## 事象

20:0x の30分で `roster_probe`（所在不明の将軍を探す城一覧確認）が **20件**。
同時刻は オオキク への波状攻撃中で、確認が攻撃・増援の移動を追い越していた。
v730 は「空いた時の確認」だけを止めており、予算付きの割り込みは残っていた。

## 変更

`src/docich/hanjuku/strategy/corridor.py`

- `orders()`: 次の城へ向かう攻撃行軍がある間は、実指示がある場合に probe で
  追い越さない（`if out and _attackers_marching(mem, target) > 0: return out`）。
  波が終われば従来どおり予算内で確認を出す。

## 検証

- `tests/test_hanjuku_corridor_probe_budget.py` に「波は予算付き確認も追い越させない」を追加。
- corridor・chapter9・squads・state_regions・key_inventory・capture・drained で
  112 passed。

# 手薄になった前線には増援の予算を開く（v733、2026-11-12）

## 事象

20:2x、波状攻撃で ドンドン を取ったあと、攻撃で前線が1人になり、そのまま
守備戦で失った。波の「6人まで」の移動予算はすでに使い切っており、補充の移動が
出ないまま攻撃だけが続いていた。

## 変更

`src/docich/hanjuku/strategy/corridor.py`

- `FRONT_REFILL_MIN = 2` と `_front_thin()` を追加。出撃元（前線）の**実測在城者が
  2人未満**なら、`_wave()` の refill（新しい移動予算）を開く。従来の
  「空一覧を読んだとき」に加えて、手薄でも同じ扱いにする。
- 攻撃は止めない（波状攻撃は維持）。前線への補充だけを別予算で再開する。

## 検証

- `tests/test_hanjuku_chapter9_squads.py`: 「薄い前線は空一覧なしでも予算を開く」を
  追加し、既存の「空一覧で予算を開く」は在城2人の前提に更新（12件）。
- corridor・chapter9・capture・drained・state_regions・key_inventory で 116 passed。

# 前線の最後の1人では攻撃に出ない（v734、2026-11-12）

## 事象

20:2x〜20:4x、ドンドン を取っては前線が1人になり、守備戦で失う往復が4回。
自城 6 ⇄ 5 を繰り返し、そのたびに奪還の出撃（1回あたり約1分）を消費していた。

## 変更

`src/docich/hanjuku/strategy/corridor.py`

- 攻撃分岐: 出撃元（前線）の**実測在城者がちょうど1人**なら攻撃に出さず
  `front_guard_held` を記録する。0人（空読み）は従来どおり（出せる将軍がいない
  ことは deploy 側が判定する）。
- v733 の thin-front refill と組み合わせ、増援が来れば波状攻撃が自動で再開する。

## 検証

- corridor・chapter9・capture・drained・squads・state_regions・key_inventory・attack・front
  で 241 passed（前線が1人の前提だったテストは2人在城へ更新）。

# 前線1人の待ちに期限を付ける（v735、2026-11-12）

## 事象

21:0x、`map` かつ入力ゼロのプランが30分で **166件**（全体の19%）。フィールドで
立ち尽くす時間が戻っていた。v734 の「前線が1人なら攻撃しない」に期限が無く、
増援が来ないと攻撃も移動も出ない状態が続くため。

## 変更

`src/docich/hanjuku/strategy/corridor.py`

- `FRONT_GUARD_WAIT = 600` と `_front_guard_expired()` を追加。前線が1人のまま
  待ち時間の上限に達したら、単独でも攻撃に出る（`front_guard_timeout` を記録）。
  待ち時計は `policy.front_guard_hold`（章スコープ、v735 で宣言）に持つ。

## 検証

- `tests/test_hanjuku_capture_guard.py`: 期限前は待ち、期限後は攻撃できることを追加。
- corridor・chapter9・capture・drained・squads・state_regions・key_inventory で
  241 passed。

# 実行に移った idle probe で計画を止めない（v736、2026-11-12）

## 事象

21:1x、フィールドの `map` かつ入力ゼロのプランが7分で **122件**（全体の59%）。
live の `policy` を読み込んで `corridor.orders()` を呼ぶと **空** を返し、
`corridor_probe.idle_step = 'I:c9:オシロガ>ドンドン:79'` が plan 上は `launched` の
まま残っていた。v731 の「作った idle probe を使い回す」が、**実行済み（launched）の
step** でも毎回そのまま返し、新しい指示も probe も出ない状態を作っていた。

## 変更

`src/docich/hanjuku/strategy/corridor.py`

- `_idle_probe_pending()`: 使い回すのは「plan 上の状態が `pending`」かつ
  「作成から `IDLE_PROBE_STALE_TICKS`（180 tick）以内」のときだけ。外れたら
  `idle_step` を捨てて新規作成に戻る。

## 検証

- `tests/test_hanjuku_corridor_probe_budget.py`: 実行に移った step で計画が止まらない／
  古い idle probe は作り直す、を追加。
- live の state を入力に `orders()` が `('オシロガ','ドンドン','retake')` を返すことを
  ローカルで確認。
- corridor・drained・chapter9・state_regions・key_inventory で 91 passed。

# 将軍が戻った城の枯渇マークを解除する（v737、2026-11-12）

## 事象

21:1x、live の `policy` を入力に `corridor.orders()` を呼ぶと **空**。原因は
`corridor_drained['オシロガ'] = {'kind': 'hero', 'month': '30-5'}` が古い月のまま残り、
`_drained()` の月跨ぎ判定で `True`（枯渇）を返して **ドンドン奪還の指示が出ない**こと。
同じ オシロガ の在城者ページは `どうし+3`（4人）を写していた。

## 変更

`src/docich/hanjuku/strategy/corridor.py`

- `_drained()` の先頭で、実測在城者が2人以上（`garrison` と `garrison_partial` の
  長い方）なら枯渇マークを消して「枯渇していない」と返す。月跨ぎ・hero種別より前。
- `_front_names()` は2つの記録の**長い方**を返す（1ページの方が新しいcompanyを含む）。

## 検証

- `tests/test_hanjuku_drained_probe.py`: hero マーク＋在城3人で解除されることを追加。
- live state で `orders()` が `('ハヤク','タオ','move'), ('オシロガ','ドンドン','retake'),
  ('サヌト','オシロガ','move')` を返すことを確認。
- corridor・chapter9・drained・capture・squads・state_regions・key_inventory で 199 passed。

# カーソルの無い文字画面にも有限回のAを送る（v738、2026-11-12）

## 事象

21:3x の30分で `text` かつ入力ゼロのプランが **101件**。手カーソル（`screen.hand`）が
無い＝選ぶものが無い文字画面で、`unknown_hold_advance` の対象が `kind == 'unknown'`
だけだったため、Aを送らずに待ち続けていた。

## 変更

`src/docich/hanjuku/orchestrator.py`

- `plain_text_hold(screen)`: `kind == 'text'` かつ `hand is None` を判定。
- その画面も `unknown_hold_advance`（同一フレームが続いたらAを1回）の対象にする。
  選ぶものが無いので、Aは「進める」以外の意味を持たない。

## 検証

- `tests/test_hanjuku_text_hold_advance.py`（3件）
- unknown_hold・no_input_gate・state_regions・key_inventory・corridor と合わせて 71 passed。

# 2回目以降の読めない画面は1フレーム早く押す（v739、2026-11-12）

## 事象

22:1x の10分で `unknown` かつ入力ゼロのプランが **66件**（全体の23%）。
`unknown_hold_advance` は同一（または2枚点滅）フレームが6回続くとAを1回送るが、
一度押して**まだ読めないまま**の画面でも毎回6フレーム待っており、3フレーム程度の
短いエピソードが積み上がっていた。

## 変更

`src/docich/hanjuku/orchestrator.py`

- `unknown_hold_advance`: 一度押した画面（`unknown_hold_watch.pressed`）では待ちを
  `UNKNOWN_HOLD_ADVANCE - 1`（5フレーム）に短縮。読める画面に変わると watch ごと
  破棄されるので、次の新しい画面は従来どおり6フレーム。

## 検証

- `tests/test_hanjuku_unknown_hold.py`: 2回目以降のエピソードが1フレーム早く押される、
  を追加／点滅ケースの期待値を更新。
- hold・terminal・no_input・state_regions・key_inventory・text で 287 passed
  （`test_hanjuku_terminal_previous_recovery.py` の13 errors は main でも出る
  xunit `setup()` 衝突）。

# 波に出す指示が無いときは城一覧の確認で動き続ける（v740、2026-11-12）

## 事象

22:5x、`map` かつ入力ゼロのプランが5分で **23件**（17%）。live の state を入力に
`corridor.orders()` を呼ぶと、その瞬間は 4件（`ドンドン>オオキク` attack ほか）を
返すのに、実際の観測では空のフレームが挟まっていた。v730 の「波の間は idle probe を
出さない」が、**波に出す指示が無い瞬間**にも効いて計画が空になっていた。

## 変更

`src/docich/hanjuku/strategy/corridor.py`

- v730 の「攻撃行軍がある間は idle probe を出さない」を撤去。v732（実指示があるときは
  probe で追い越さない）と v731/v736（作った probe の使い回し・期限）で churn は
  抑えられているため、空のときは従来どおり確認で動き続ける。

## 検証

- `tests/test_hanjuku_corridor_probe_budget.py`: 「波に指示が無いときも idle probe が
  1件出る」に更新。
- corridor・chapter9・capture・drained・state_regions・key_inventory・probe で
  300 passed。

# しろ一覧で城名を読んだジャンプはステータス再確認を省く（v741、2026-11-12）

## 事象

出撃1回の内訳を計測すると、`order_start` → `order_launched` まで約45秒。
内訳は `main_menu` 4、`text` 3、`castle_menu` 4、`card_select` 3、`text`/`sortie_confirm` 2 など。
`text` と `castle_menu` の一部は **出撃前に城名をステータスで確認する**処理
（`_check_source_castle`）でした。

## 根拠

Xメニューの「しろ」一覧からSELECTで飛ぶ経路は、**飛ぶ前に城名の行を読んで一致を確認**
しています（`castle_jump_confirm`）。同じ城名をステータスでもう一度読むのは重複確認。

## 変更

`src/docich/hanjuku/flows/map.py`

- しろ一覧の名前付き行からSELECTした時点で `castle_verified = step` を立てる
  （`hero_select` 経路は従来どおり確認する）。
- `deploy._check_source_castle()` は検証済みの step ではステータスを開かない
  （既存の分岐がそのまま効く）。

## 検証

- `tests/test_hanjuku_castle_jump.py`: 名前付き行のSELECT後はステータス確認を省略する、
  を追加。castle・jump・deploy・sortie・chapter9・corridor で 745 passed。

# 取ってすぐ失う城へは2人そろうまで攻めない（v742、2026-11-12）

## 事象

21:0x〜23:2x で ドンドン を取っては失う往復が続いた（`sortie_history` に
`ドンドン won` と `lost` が交互に並ぶ）。前線が1人で攻撃に出ると、取った直後に
守備戦で落ちる。

## 変更

`src/docich/hanjuku/strategy/corridor.py`

- 攻撃分岐の前線ガードを `_keep = 2 if _flip_prone(mem, target) else 1` に。
  「取ってすぐ失った城」（`_flip_prone`）への攻撃は、出撃元の実測在城者が
  `_keep` 以下なら見送る（`front_guard_held`）。待ちは既存の `FRONT_GUARD_WAIT`
  （600 tick）で有界。3人以上いれば従来どおり波状攻撃を続ける。

## 検証

- `tests/test_hanjuku_capture_guard.py`: フリップ中の城へは1人では攻めない／
  3人いれば攻める、を追加。
- corridor・chapter9・capture・drained・squads・state_regions・key_inventory で
  201 passed。

# 取った直後の城からは最後の将軍を出さない（v743、2026-11-12）

## 事象

23:3x、`ドンドン` を **won（味方HP 66）** で取った直後、その将軍が `オオキク` へ
攻撃に出て城内が1人になり、**敵HP 39 の攻撃で落とされた**。

## 変更

`src/docich/hanjuku/strategy/corridor.py`

- `_captured_recently(mem, castle)`: `sortie_history` で直近 `FLIP_WINDOW_TICKS` 以内に
  取った（その後失っていない）城を判定。
- 攻撃分岐の前線ガードの `_keep` を「`_flip_prone(target)` または
  `_captured_recently(source)` なら2、それ以外は1」に。取った直後の城は2人そろうまで
  攻撃に出さない（待ちは `FRONT_GUARD_WAIT` で有界）。

## 検証

- `tests/test_hanjuku_capture_guard.py`: 取った直後の前線から最後の1人を出さない、を追加
  （9件）。corridor 系 202 passed。

# 瀕死の将軍では攻撃に出ない（v744、2026-11-12）

## 事象

23:4x、オオキク への攻撃で味方HP 15・22 のような瀕死の将軍を送って敗北。
`sortie_history`（直近20件）は `オオキク lost 7 / ドンドン lost 9 / ドンドン won 4` で、
オオキク では一度も勝てていなかった。将軍のHPを回復する経路が現状の計画に無く、
傷ついた将軍をそのまま送り続けている。

## 変更

`src/docich/hanjuku/strategy/corridor.py`

- `WOUNDED_ATTACK_HP = 20` と `_wounded_attack()` を追加。出撃元の**実測HPが最も高い
  将軍**でも 20 以下なら攻撃に出さず `front_wounded` を記録する。待ちは既存の
  `_front_guard_expired`（`FRONT_GUARD_WAIT` = 600 tick）で有界。

## 検証

- `tests/test_hanjuku_capture_guard.py`: 瀕死の前線は攻撃しない／元気なら攻撃する、を追加。
- corridor 系 202 passed。
