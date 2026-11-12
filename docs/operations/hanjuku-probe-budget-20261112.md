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
