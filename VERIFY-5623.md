# VERIFY-5623 — [半熟英雄] 月内出撃ゼロ: ch5 出撃開始後に発射されない（zero_sortie）

- 対象: azumag/docich#5623。kind `zero_sortie` / signature `order_start_without_launch` + `monthly_scan occupies map` + `battle_grind` / confidence `high`。
- 観測: ラン `g1190-ch1v9` / generation 1190 / `hanjuku-chart-v1519-sortie-close-retry`、ch5 5-4（観測 1568 回 / 346.3 秒）。
- 出撃: 確定 0 / 発射 0 / `order_start` 2 / `order_launched` 0。窓後 `recovered: false` / `departures: 0` / early seal。
- 検証 base: 現行 main `709e09cdc`（修正適用前 HEAD / marker `hanjuku-chart-v1525-sortie-cards-eligible`）。本ブランチでは修正と併せて marker を `v1528-owned-target-settle` へ bump（v1526/v1527 は別 lane が使用中）。
- 観測版の引用基点: `2df350ee6`（`BOT_VERSION = hanjuku-chart-v1519-sortie-close-retry`）はこの clone に存在しない（VERIFY-5620 と同じ制約。同番号 v1519 の並行 suffix `1004f9ed3` のみ存在）。本記録では「観測 marker が指すコード」としてだけ使い、機構の導入 commit が両版の祖先であることで版差を推定した。
- 検証範囲: 本ファイル追加 + 結論 (a) に伴うコード修正・回帰テスト。本番 SSH・VM 操作・ゲーム入力なし。run 本体の `hanjuku_decisions.jsonl` / frames は VM 側のため直接参照せず、issue 内包 JSON・git 履歴・現行コードを照合した。
- `DOCICH_HANDOFF_PATH` 未設定のため handoff 正本は未読と記録する（AGENTS.md §1）。

## 結論: **(a) 実欠陥 — 奪取済み城を狙う stale 計画ステップが activate→取消を無界ループし、計画再構築を飢餓させた**

`sortie_route_unverified` による取消自体は PR #5348 / v1453 で入れた設計どおりの再検証である。しかし「attack の目標城が既に自軍」は経路問題ではなく**恒久的に達成済み**の失敗であり、それを `pending` に戻す既存処理のままだと、activation 経路が `next_order` にはある「目標保有チェック」を持たないため同じ stale ステップを毎観測で再 active 化する。取消は `sortie_retry_guard` の予算対象外で回数上限もなく、`_plan_pending` 等の「計画はまだ生きている」判定も不可能な指示を runnable と数えるため `_off_chart` による計画再構築に到達しない。実際に月内 3 回・窓後 5 回（翌 5-5 の #5624）の activate→取消ループが issue 内包時系列に残っている。

新方針（2026-11-25 owner 指示）に従い、検証だけでなく**実修正を本ブランチに実装済み**（§5）。

## 1. 発火したループ（issue 内包証拠との対応）

計画 `5b2e1300` の波状攻撃は全5件がバスカビル向けで、先頭 `A:5b2e1300:M1dd4af`（カルーン→バスカビル）が `done`、第2波 `A:5b2e1300:M6a158d`（アイアイエー→バスカビル、クランベリー）が `pending`。バスカビルは月末の奪取一覧・在城 `バスカビル(1)` にある＝**先頭波の成功で奪取済みになり、残り波が stale 化した**のが起点。

月内の記録された連鎖（decision counts: `plan_map_activate` 4 / `sortie_route_unverified` 3 / `source_not_under_cursor` 3）:

1. `order_dispatch_starved`（通常フェーズで pending 指示が order_start に届かない旨）— 検知器としては正しい記録。
2. `plan_map_activate` — 機械ボス計画の pending `M6a158d` を active 化。
3. `castle_jump_open` → `castle_jump_scroll` → `castle_jump_confirm` — 出撃元アイアイエーへ実際に城メニューまで遷移。
4. `sortie_route_unverified`（castle_menu で取消、「第3話の既存城間ルートにない直行を取り消し、中継城経由で選び直す」reason）— A: step は `pending` へ戻る。
5. **0.35 秒後に同じ `plan_map_activate` が同じ step を再 active 化** → `world_map_units` / `source_not_under_cursor` / `cursor_still_yview` とナビゲーションを消費し、月次処理・戦闘・prompt に奪われて月が終わる。

`order_start` が 2 なのは、`plan_map_activate` 経由の active 化では `order_start` が記録されず（記録は dispatch 経路のみ）、窓内の `order_done` 2 / `roster_probe_complete` 2 / `roster_resident_confirmed` 2 と一致する2件の roster probe が dispatch されたため。監査の `order_start_without_launch` 分類は契約どおり（order_start>0 で月内発射ゼロは事実）。

`sortie_unverified_rearmed` ×2 は別計画 `cebc4b2b` の `Ma8aa67`（行軍未観測の launched_unconfirmed を次月へ pending 戻し）の記録であり、本ループとは別機構・別 step。M6a158d のループを制限するものではない。

## 2. 欠陥の機構（現行コードとの照合）

| 段 | 機構 | 評価 |
| --- | --- | --- |
| 取消 | `routes.order_allowed` が `purpose=='attack' and target in owned` で False（PR #5348 / v1453 導入）→ deploy 側 `_hold_unrouted_sortie` が `sortie_route_unverified` | **設計どおり**。stale 攻撃を実際に撃たない防衛線として正しい |
| pending 戻し | `_hold_guarded_sortie`: A: step は `pending`、I: step は `failed` | 第3話の「中継経由で組み直し」には意味があるが、owned-target では永遠に再実行不能な指示を生きたまま残す |
| 再 active 化 | `plan_starved_rearm` の map 分岐（`plan_map_activate`、orchestrator.py L665-690）のゲートに `next_order`（orders.py L2253-2257）が持つ `purpose=='move' or is_boss_castle or target not in owned or roster_probe` が無い | **欠陥の直接の駆動源**。`next_order` は stale 攻撃を正しくスキップするのに active 化経路だけが素通し |
| 計画再構築の飢餓 | `_off_chart` の到達点3箇所が全て塞がれる: (i) map.py L2576 は `request_id` 不変時のみ `_off_chart` を呼ぶ（奪取で digest 変化 → 呼ばれない）、(ii) order=None 経路（map.py L2602 系）は `plan_map_activate` が先に active 化するため到達しない、(iii) `plan_starved_rearm` の 900-tick 再構築は runnable-pending 判定（同じ保有チェック欠落で stale 攻撃を runnable と数える）と行軍中 march（`en_route`/`launched_unconfirmed` 4件）で抑制、`_plan_pending.live()`（orders.py L1022-1034）も同じ欠落で「生きた pending」扱い | **欠陥の核心**。`request_id`/`request_digest` は `captured` を含むため、`_off_chart` が一度走れば計画は破棄され、`interim_candidates`/`targets` の `c not in owned` フィルタでバスカビル向け指示は再生成されない |
| 有界性 | `sortie_route_unverified` は `sortie_retry_guard._CANCEL_DECISIONS`（`sortie_held_source_defender`/`sortie_held_hero_guard`/`hero_move_refused` のみ）に入らず、回数予算も retry hold も無い | **無界**。行軍中 march が残る限り starve 再構築も発火せず、計画が事実上不死 |

まとめると、監査が見た「order_start 後に発射されない」は、**先頭波の奪取成功で stale 化した残り波を、activation 経路が「実行可能」と誤判定し続け、deploy 側の正しい再検証が毎回取消し、計画再構築が永久に先送りされた**ため。窓の空き時間は castle_jump と world_map ナビゲーションに消え、月次処理（`month_sub_step` 261）と戦闘が残りを占有して確定 0 で早期 seal した。

## 3. 既存修正のカバレッジ（検証観点2）

| 修正 | 本件への適用 |
| --- | --- |
| PR #5348 / v1453（`order_allowed` の attack-on-owned 拒否） | 取消自体。設計どおりに機能しているが、「pending 戻しで計画が組み直す」前提が owned-target では成立しない |
| #2931（ch3 崖ルート再検証、`sortie_route_unverified` 起源） | 経路問題への対処であり、恒久的な target-owned 失敗の扱いは対象外 |
| #5116 / v1383（`plan_map_activate` 導入） | ループの駆動源そのもの。導入時から owned-target チェックが無い |
| #4584（`plan_starved_rearm`）・#4642（dead plan 再構築） | 飢餓回復機構だが、runnable-pending 判定が stale 攻撃を runnable と数えるため抑止される。行軍中 march による早期 return も観測と一致 |
| #3265（`sortie_retry_guard` 取消予算持ち越し）・#3326（取消→再試行計測） | `sortie_route_unverified` は予算対象外。 cancel→retry の有界化は本経路に効かない |
| #5136（月内確定発射ありの取消は起票しない） | 適用外（確定発射 0）。起票自体は契約どおり |
| PR #5619 / v1520（frozen `card_flow` 解放前は攻撃を保留）・v1522・v1524（カード系） | 非該当。battle/card 面で本件の sortie/plan 経路に触れていない |
| v1525（#5636 plan-empty 在庫集計） | 監査側の別 metric。本経路に無関係 |

**いずれの既存修正も「stale 攻撃の再発行ループ」と「計画再構築の飢餓」を止めない**ため (b) ではない。

## 4. ラン版 v1519 と現行 main の差異（検証観点3）

| 機構 | 導入 | v1519 時点の存在 | 現行 main での状態（修正前） |
| --- | --- | --- | --- |
| `order_allowed` attack-on-owned | PR #5348 / v1453 | あり | 同一の設計（変更なし） |
| `_hold_unrouted_sortie` / `sortie_route_unverified` | #2931 | あり | 同一（修正で owned-target 分岐を追加） |
| `plan_map_activate` | #5116 / v1383 | あり | 同一（修正で owned-target ゲートを追加） |
| `plan_starved_rearm` runnable-pending 判定 | #4584 / #4642 系 | あり | 同一（修正で述語を追加） |
| `_plan_pending.live()` | #2087 分割時から | あり | 同一（修正で述語を追加） |
| `sortie_retry_guard` `_CANCEL_DECISIONS` | #3265 | あり | 対象外のまま |

marker commit `2df350ee6` は clone に無く直接 diff は不能だが、上記全機構の導入 commit は v1519 より古く、v1519→v1525 の間の変更（v1520 card_flow / v1521 停滞 issue / v1522 召喚獣札 / v1523 卵三択 / v1524 自城札 / v1525 在庫集計）は battle/card/audit 面のみで sortie・plan 経路に触れていない（VERIFY-5635 が deploy/orders/map/sortie_retry_guard の v1523→v1525 同一性を実測済み）。**観測されたループは現行 main と同一機構の同一判定**であり、main にも同じ欠陥が残っていた。

## 5. 実装した修正（結論 (a) のため、新方針に従い本ブランチで実装）

1. `flows/deploy.py` `_settle_owned_target`（新規）+ `_hold_unrouted_sortie` 分岐: `order_allowed` が False で `purpose=='attack' and target in _owned` の場合、step を `pending`/`failed` に戻さず **`done` として閉じ**、専用決定 `sortie_settled_owned_target` を記録する。`mechanical_boss_step_settled` の「征服済み step は done で閉じる」慣行と一致し、`order_retry_retired`（再試行目標が自城なら再出撃しない）の既存契約とも整合。`failed` にしないのは、failure 台帳・rearm 窓を汚染し将来の正当な再攻撃（再奪取）を抑制しうるため。
2. `orchestrator.py` `plan_starved_rearm` map 分岐（`plan_map_activate`）のゲートに `next_order` と同じ `purpose=='move' or is_boss_castle(target) or roster_probe or target not in owned` 述語を追加し、発射不能な stale 攻撃を active 化しない。これで order=None 経路から `_off_chart` の再構築へ素直に到達する。
3. `orchestrator.py` `plan_starved_rearm` の runnable-pending 判定（900-tick 再構築ゲート）に同じ述語を追加し、stale 攻撃が runnable 扱いで再構築を抑止しない。
4. `orders.py` `_plan_pending.live()` に同じ述語を追加し、不可能な指示を「計画を生かす pending」として数えない（`_mechanical_chart` の early-return 抑止を解く）。
5. `version.py`: `hanjuku-chart-v1528-owned-target-settle`（v1526/v1527 は別 lane で使用済み）。
6. 回帰テスト:
   - `tests/test_hanjuku_castle_routes.py`: 既存 `test_adopted_attack_is_held_if_its_target_became_owned` を `..._settled_done_...` へ更新（`sortie_settled_owned_target` + status `done`）、新規 `test_a_plan_attack_on_an_owned_target_is_settled_done_not_pending`（A: step が pending に戻らない）。
   - `tests/test_hanjuku_mechanical_boss_plan.py`: 新規 `test_field_map_never_activates_an_attack_on_an_owned_castle`（`plan_map_activate` が stale 攻撃を active 化しない）。
   - `tests/test_hanjuku_off_chart.py`: 新規 `test_a_pending_attack_on_an_owned_castle_does_not_keep_the_plan_live`（`_plan_pending` が stale 攻撃のみの計画を死んでいると判定）。

**テスト実行は本環境で python3/pytest が実行拒否されるため未実施**（VERIFY-5635 と同じ制約）。静的レビューで import・述語・status 書き込み経路を確認済み。実行は CI / 実行可能な環境の残件。

## 6. なぜ (b)(c)(d) ではないか

- **(b) ではない**: 取消の都度の判定は設計どおりだが、恒久的失敗を `pending` へ戻し保有チェックの無い activation 経路が無界に再発行する連鎖は、どの既存有界機構（取消予算・retry hold・starve 再構築・`_plan_pending`）でも止まらない。実際に月内3回・翌月5回の同一ループが残っている。
- **(c) ではない**: 監査は「月内発射ゼロ」を正しく測定し、`order_start_without_launch` 分類も契約どおり。下層に activate→取消の実ループがあり誤検知ではない。
- **(d) ではない**: issue 内包の decision 時系列だけで、stale 化（先頭波奪取）→ activate → 取消 → pending → 再 activate の全経路が完全に特定できる。run 本体の jsonl なしでも因果は確定できる。

## 7. 補助観察（結論に影響しないが記録）

- `order_dispatch_starved` の検出は正しい（pending が order_start に届かない事実）が、「届かない理由が next_order の owned-target skip」であることは記録からは読めない。stale 理由を starve 記録に添えると類似検証が速い（follow-up 候補、本修正には含めない）。
- `retake` 目的で目標城が自軍・かつルート不成立の指示は、今回の settle（`purpose=='attack'` 限定）の対象外で理論上同型ループが残る。観測されていないためスコープ外とし、発生時は同じ settle へ拡張するだけでよい。
- 修正後も、奪取から stale step の settle までに最大1回の castle_menu ラウンドトリップは残る（fix 2 で通常は0回、他経路から来ても fix 1 で1回で終了）。`pending` のまま残った stale step が見た目上残るのは `order_retry_retired` と同じ帳簿慣行で、`_plan_pending` 修正済みのため再構築は阻害されない。

## 成果物の状態（本レーンからの制約）

- **コミット**: `git add`/`git commit` が実行拒否されるため、修正コード・テスト・本ファイルは worktree 上に未コミットのまま置かれている。ブランチへのコミットは手段のある環境での残件（VERIFY-5635 と同じ扱い。想定コミット文: `fix(hanjuku): 奪取済み城へのstale攻撃をdoneで閉じ出撃ループと計画飢餓を止める (v1528, #5623)`）。
- **issue #5623 へのコメント**: `gh` 実行拒否のため本レーンから投稿不能。下記の文面を代理投稿してほしい（投稿経路は AGENTS.md §3.1 のフォールバック表に従う）:

```text
[process-deviation] docich-5623 worktree / secret_scan 未実施（同環境で実行拒否）/ 代理投稿 / gh・curl・python3 実行拒否

検証完了: 結論 **(a) 実欠陥** — 奪取済み城を狙う stale 計画ステップの activate→取消が無界ループし計画再構築を飢餓させた（VERIFY-5623.md、verify/5623 ブランチ。同ブランチに修正 v1528 実装済み）。

- 経路: 計画 5b2e1300 のバスカビル波状攻撃で先頭 M1dd4af が奪取成功 → 残り波 (M6a158d 他) が stale 化。`plan_map_activate`（orchestrator）が `next_order` の target-owned スキップを持たず毎観測で再 active 化 → deploy 側の `order_allowed` 再検証（#5348/v1453）が `sortie_route_unverified` で正しく取消 → A: step は pending へ戻り、0.35秒後に再度 activate。月内3回・翌月(5-5)5回の同一ループが記録に残る。
- 有界性の欠落: `sortie_route_unverified` は retry_guard の予算対象外、`_plan_pending`/starve判定は不可能指示を runnable 扱い、`_off_chart` 到達点3箇所が全て塞がれ計画が再構築されない（request_digest は captured を含むため一度走れば破棄・再生成でも owned フィルタで排除される）。
- 修正（v1528, verify/5623）: `_settle_owned_target` で attack-on-owned を `done` で閉じ `sortie_settled_owned_target` を記録（`_hold_unrouted_sortie` から分岐）。plan_map_activate / plan_starved_rearm runnable判定 / `_plan_pending.live()` に next_order と同じ target-owned 述語を追加。テスト4件追加・更新（環境制約で未実行、CI へ）。
- 監査側: `order_start_without_launch` 分類は契約どおり（order_start 2 は roster probe 2件、発射ゼロは事実）。#5624（翌5-5）も同一機構の可能性が高い。
- 非対象の補助観察: retake 目的の同型ループは未観測のため今回スコープ外。
```

- **Slack #agent-council 完了報告**: slack-remote MCP 呼び出しも拒否されるため本レーンから投稿不能。docich#5623 スレッドへの Muse Bot (`<@U0CCCH69XMW>`) メンション付き報告は手段のある環境での残件。
