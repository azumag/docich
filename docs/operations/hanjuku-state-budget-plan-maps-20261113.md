# 半熟英雄: plan の step 別マップが読込上限を越え、配信カードが未確認になる（v755、2026-11-13）

## 症状（実測 2026-11-13 09:5x）

配信の**左の拡張観測カードが消え**、右の半熟英雄カードが
「話数 未確認 / 年月 未確認」、G 面が「状態: 未確認」になっていました
（`/__soren_overlay/broadcast/state` の `feeds.showStatusG`）。

ゲーム自体は v754 で進行していました（battles_finished 551、terminal なし）。

## 原因

`run-soren-live/runtimes/g1163-42bfb85e/hanjuku_bot.json` が **356 274 バイト**で、
`_bounded_state` の読込上限 **262 144** を超えていました。`soren_corner_stats` は
この超過を `availability: unavailable` として扱い、`render_hanjuku_status` が
「状態: 未確認」を返し、`hanjukuView` が null になるため左カードも描かれません。

`compact_state` はキャッシュ・履歴・決着済み行軍を落としますが、上限に届かず、
残りはすべて `plan` の中の step 別マップでした。

| キー | サイズ | 件数 | うち live |
|---|---|---|---|
| `plan.order_context` | 185 KB | 228 | 49 |
| `plan.card_override` | 30 KB | 312 | 61 |
| `plan.general_override` | 16 KB | 323 | 61 |
| `plan.sortie_general` | 15 KB | 287 | 61 |

`plan.orders` は 358 件で、226 件が `failed`。決着済み step の文脈・上書きが
そのまま残り、記録だけが膨らんでいました（読み手は必ず `order['step']` で引くので
決着済み step の値は誰も読みません）。

## 修正（v755 `hanjuku-chart-v755-plan-step-map-budget`）

`state_budget._prune_retired_step_maps` を追加し、`compact_state` の最後
（settled-march の剪定後）に実行します。

- 残す step: `plan.orders` が `pending` / `launched` / `launched_unconfirmed`、
  `world.sorties` / `world.launched_orders`、`flow`、`plan.active` / `plan.attack`、
  `plan.plan_step_rearm`、`policy.retry_context` から参照されているもの。
- それ以外の `order_context` / `card_override` / `general_override` /
  `sortie_general` / `source_override` を目標サイズまで落とす。
- 目標に達したら止まる（完全 GC ではない）。live の文脈は落とさない。

実 record での実測: 376 764 → **196 592 バイト**（target 196 608）、
`order_context` 228→49、`card_override` 312→61。

## 検証

- `tests/test_hanjuku_state_budget.py`: 決着 step の plan マップが落ちること、
  行軍中 step の文脈が残ること、live 文脈を落とさないことを追加（19 件 passed）。
- 新バージョンを `ops/vm_actions/collect_diagnostics.py` の固定表へ登録。
- 本番反映後、`hanjuku_bot.json` が上限未満になり、`feeds.showStatusG` が
  fresh（状態が未確認でない）へ戻り、左カードが再表示されることを実測する。

## 残件

- `order_context` の生成側（`flows/deploy.py` ほか）が決着時に落とし切れていない
  箇所がある。今回は表示・読取を守る予算側で止めた。生成側の掃除は follow-up。
- 同じ 262 144 上限で `hanjuku_bot.json` を読む箇所（narration / progress / resume /
  run）も、この超過中は policy を読めていなかった。予算が守られている間は問題ない。
