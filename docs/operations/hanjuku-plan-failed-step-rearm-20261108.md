# 半熟英雄: 失敗した出撃指示が計画に残り、敵城1つで月送りになる（v514、2026-11-08）

## 症状

稼働中の g1153（第3話、月7-11）。敵城はドンダム1つ、将軍は国ステータスで32人、
自城8つに十分な将兵がいるのに、出撃が起きず月だけが進んだ。

10:20〜12:41 の判断ログは月次処理（国ステータス・兵士補充・築城・購入）と
防衛戦だけだった。攻撃・奪還の出撃は 11:39 の移動（セミヨン→アバロン）以降なく、
月は約6分ごとに進んでいた。

## 実測（g1153、JST）

- `castle_ownership`: `ドンダム` のみ `enemy`、他8城は `own`。
- `chart_plan`（`source: mechanical`、request `fa94e7a5`）の3件:
  - 中継移動 ヘパイストス コラダイン→イーピヨル（`orders[step] = failed`）
  - 波状奪還 ユイートル パタゴーヌ→ドンダム（未発射）
  - 波状奪還 エピィ メガパタ→ドンダム（未発射）
- 第3話の実測ルート `routes.legs(3)` でドンダムへ入れるのは
  `イーピヨル→ドンダム` だけ。上の2件は `orders._ready` の chapter 3
  ルート検査で恒久的に false。
- `world.garrison['イーピヨル'] = ['ココット']`。出撃元に2人残す規則のため
  イーピヨルから直接は出せず、コラダインからの**中継移動が唯一の道**。
- その中継移動の step は failed。`next_order` は failed を恒久的にスキップし、
  非ボス step を再発射する機構が無かった。

## 再現

実行中の policy スナップショットに現行コードを当てると `next_order` は None。
`orders['A:fa94e7a5:Mdd0c77'] = 'pending'` にすると中継移動が返り、到着
（イーピヨルに2人）を仮定すると `interim_candidates` が
`イーピヨル→ドンダム` の奪還（startable / `_ready` true）を生成した。

## 原因

1. `_wave_pick` が第3話のルート制約を見ず、到達できない城から波状指示を作っていた。
   そのため起動不能な指示が計画を埋め、`_plan_pending` は常に false になった。
2. failed になった非ボス step を pending に戻す経路が無い。request id が同じ間は
   同じ content から同じ execution step が再計画されるため、1回の失敗でその指示
   （今回は唯一の攻略路）が状況の間ずっと死んだ。

## 変更（v514）

- `_wave_pick`: `routes.allowed` を通る城だけを波状の出撃元にする。
- `_release_stale_failures` を追加: 再計画が同じ step を出し続け、失敗が
  `PLAN_STALE_TICKS` を超えていたら pending に戻す。月内・step あたり3回まで。
  再発射時は失敗した試行の `general_override` / `source_override` / 携行札などの
  per-step 記録（`RELEASE_KEYS`）と、world 領域の `launched_orders` の同 step を
  消す。実測では failed step にココット（当時イーピヨル在城）の override が
  残っていた。ボスは既存の `_rearm_failed_boss_steps` に任せる。
- 両再発射を計画の同一判定より前に移し、計画が変わらない場合でも走るようにした。
- `version.py` を v514 へ更新し、`ops/vm_actions/collect_diagnostics.py` の
  `bot_version` 許可集合へ追加。

## 変更しないこと

- ボスの再発射上限・間隔、他章のルート判定、`routes` の leg 集合。
- 出撃元に2人残す規則、主人公の城の guard、月次処理。

## 検証

- 新規 `tests/test_hanjuku_plan_failed_step_rearm.py` 5件。修正前のコードでは
  3件が失敗することを確認（ルート検査・中継の再発射・月内上限）。
- 関連スイート: castle_routes / plan_exhausted_interim / chart_adjust / off_chart /
  boss_wave_double / castle_jump / plan_stale / plan_readopt / defense_wait /
  mechanical_conquest / multi_spare_plan / confirmed_source_actor /
  release_sortie_cleanup / overwhelming_egg_conservation / hotload を対象に、
  まとまりごとに 218 / 267 / 125 / 107 件成功（対象は重複）。

## 本番反映（2026-11-08 13:09〜13:22 JST）

- main `40ab1f27`（PR #3728）。VM operations の deploy は `success`、反映後の
  production checkout HEAD は `40ab1f27`、`version.py` は v514。
- 稼働中 g1153 は 13:09:28 の判断から v514 を記録（ホットロード）。
- 13:12:58 `plan_step_rearmed` `A:fa94e7a5:Mdd0c77`（中継移動 コラダイン→イーピヨル）。
  同時に `order_start`。再構築後の計画は到達不能な波状2件が消え、中継1件だけになった。
- 13:14 中継を出撃（`world.launched['イーピヨル'] = ヘパイストス`）。到着後、
  イーピヨルは ココット＋ヘパイストス の2人になった。
- 13:19〜13:21 イーピヨルから ドンダム へ攻撃
  （`attack = {castle: ドンダム, general: ココット, side: attack}`）、13:22:08 戦闘結果。
  `castle_ownership['ドンダム'] = own` になり、第3話の9城すべてが自軍になった。
- 以後はボスへ進行（`order_retry`「城を取らないと後続手順とボスへ進めない」）。

## 残件

- 計画の同一判定は `previous_steps`（実行 step）と再構築側（local step）を比べて
  いて一致せず、early return が常に外れている。今回は再発射をその前に置いて
  回避したが、比較自体の修正は #3729 で行う。
