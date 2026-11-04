## 目的

v398 で入れた「同じ判断は1回だけ記録する」抑止が実機で効いていなかったのを直す。policy は観測ごとに JSON へ保存され読み戻されるため、tuple の署名は list になって比較が毎回不一致になっていた。

## 実測（2026-11-05, runtime g1133-a3499a00）

- `chart_adjust_applied`: 11分間に24件。うち5件以上が同じ `request_id` と同じ step 列（`bf8c3dee6398cea3` / `["A:bf8c3dee:M594ff8"]`）。
- `world_map_owners`: 1時間に147件、異なる旗の組み合わせは9種類のみ。
- live の `hanjuku_bot.json` の `chart_adjust_signature` / `world_map_signature` はどちらも **list**（JSON保存後）。

## 変更

- `os_layer/world.py`: `tuple(sorted(flags.items()))` → `[[k, v] for k, v in sorted(flags.items())]`。
- `strategy/replan.py`: `(rid, tuple(steps))` → `[rid, [step, ...]]`。
- 判定条件・記録内容・記録タイミングは変えず、署名の型だけを JSON 往復で安定させる。
- `BOT_VERSION` を `hanjuku-chart-v400-dedupe-json-signature` へ、`ops/vm_actions/collect_diagnostics.py` の allowlist に追加。

## 前ラウンドの訂正（同PR内）

v399（#3395）の doc に書いた「192件の `house_input_free_hop` は入力を1つも送らない停止」は**誤り**でした。同じ時刻の `action_plan` を突き合わせると、192件はすべて `down` を送る通常の名簿走査（1行=1観測）です。初報は別時間帯の `planned_actions: []` と混同していました。

- `docs/operations/hanjuku-house-idle-escape-20261105.md` に「訂正」節を追加し、事実に合わせて書き直す。
- `flows/house.py` のコメントも「名簿走査の安全網（実機では未発火）」に修正。
- 入力なしの連続観測自体は存在する（63分で144 run、最長13.4秒）が、中身は召喚獣メニューの描画待ちと `situation_held` で、アニメーション待ちだった。

## テスト

- `tests/test_hanjuku_decision_dedupe.py`: JSON 往復を挟むテストを追加（往復後も1件、計画が変われば2件）。
- 修正前コードではこの2件が失敗（2 failed）し、修正後は 5 passed。
- 関連スイート: `test_hanjuku_chart_adjust` / `mechanical_conquest` / `off_chart` / `map_discovery` / `castle_routes` / `season_king_sites` / `boss_flag_not_read` / `gameover_feedback` / `house_idle_escape` / `house` / `overwhelming_egg_conservation` / `hotload` で 600 passed（ローカル）。
- 必須CIは最新HEADで確認する。

## 未確認・残件

- 実機での `chart_adjust_applied` / `world_map_owners` 減少は v400 稼働後に確認する（未確認）。
- 第6章は通常11城を制圧済みでボス解禁。各城1将軍のためボス隊の余剰2人を編成中で、ボス突入は未実測。ボスセル (818,184) も未検証。
- 投稿前スキャン `agent-guards/secret_scan.py` はこの環境に存在しないため未実施（本文に秘匿情報は含まない）。

詳細: `docs/operations/hanjuku-dedupe-json-signature-20261105.md`
