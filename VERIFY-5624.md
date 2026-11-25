# VERIFY-5624 — [半熟英雄] 月内出撃ゼロ: ch5 月次処理がマップを占有（zero_sortie）

- 対象: azumag/docich#5624。kind `zero_sortie` / signature `monthly_scan`（signals: `monthly_scan_hold` + `battle_grind`）/ confidence `high`。
- 観測: ラン `g1190-ch1v9` / generation 1190 / `hanjuku-chart-v1519-sortie-close-retry`、ch5 5-5（観測 848 回 / 182.6 秒 / max gap 0.8 秒）。
- 出撃: 確定 0 / 発射 0 / `order_start` 0 / `order_launched` 0。窓後 `recovered: false` / `departures: 0` / early seal。`auto_file: true`（zero_sortie + high の起票契約どおり）。
- 検証 base: worktree HEAD `709e09cdc`（marker `hanjuku-chart-v1525-sortie-cards-eligible`）。`git fetch` / `git merge` が本環境で実行拒否されたため origin/main (`1eb90d221`) へのマージは未実施。ただし `git diff --stat 709e09cdc origin/main` で、差分は監査証拠（#5643 v1528 / #5644 v1527）・診断 allowlist・テスト・tsuitate 文書のみで、deploy / orchestrator / orders / routes の sortie・plan 経路は**無差分**。以下の現行コード照合は `git show origin/main:<path>` で行い、worktree base と同一の機構状態を確認した。
- 観測版の引用基点: v1519 marker commit はこの clone に存在しない（VERIFY-5623 と同じ制約）。VERIFY-5635 が deploy/orders/map/sortie_retry_guard の v1523→v1525 同一性を実測済みで、v1519→v1525 の間の変更は battle/card/audit 面のみ。観測ループは現行 main と同一機構とみなせる。
- handoff 正本: 環境参照の確認コマンド自体が実行拒否のため `DOCICH_HANDOFF_PATH` の有無を判定できず、正本は**未読と記録**する（AGENTS.md §1）。コピー作成・内容推測はしない。
- issue 本文は webfetch で取得（`gh` は実行拒否）。sealed incident JSON・decision 時系列・前月計画・世界状態を内包データとして照合した。

## 結論: **(a) 実欠陥 — #5623 と同一機構（stale 攻撃の activate→取消ループ＋計画飢餓）。PR #5645（v1529、verify/5623）でカバー済みのため追加修正は不要**

監査 signature は `monthly_scan`（月次処理がマップを占有 / 戦闘が長引く）だが、発射がゼロだった直接の原因は月次処理・戦闘そのものではなく、**計画が出し続けた唯一の出撃指示が「既に自軍のバスカビルへの stale 攻撃」であり、それを毎観測で active 化→取消しする無界ループが、実行可能な新規指示を生む計画再構築を飢餓させた**こと。#5623（前月 5-4）と同じ計画 `5b2e1300`・同じ step `M6a158d`・同じループが翌月へ持ち越されたもので、VERIFY-5623 §6 の「#5624 は同じ機構の可能性が高い」が本記録の照合で確定した。

## 1. fingerprint / incident_id の式照合（再計算は環境拒否のため未実施）

| 値 | 式（`month_audit.py`） | 期待 raw |
| --- | --- | --- |
| fingerprint `a5a7f56105a4b603` | `_fingerprint` L3341-3343: `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]`、呼出は `_build_incident` L3445 の `[primary]` | `5|zero_sortie|monthly_scan` — chapter=5（JSON `"chapter": 5`）、kind=`zero_sortie`、primary=`monthly_scan`（issue `"signature"` 値と一致。`order_start`=0 のため `order_start_without_launch` は signals に入らず、`monthly_scan_hold`（L3233-3235: `post_month_status_close_probe` 等の counts あり）が先勝ち） |
| incident_id `2cfefefa93e8c0e733c7` | `_incident_id` L3336-3338: `sha256(f"{runtime_id}|{generation}|{chapter}|{month}")[:20]` | `g1190-ch1v9|1190|5|5-5` — identity は issue の `evidence.identity` と一致 |

sha256 の実再計算は本環境がハッシュ計算コマンド（python3 / sha256sum / openssl / node）を全て拒否したため**未実施**（VERIFY-5538/5545/5522 と同じ制約）。式と入力値は一意に対応し、issue 記載値と矛盾する要素はない。

## 2. 月内に出撃を止めたもの（issue 内包証拠との対応）

月の内訳（decisions.total 194）: 月次処理系 ~76 件（`chikujou` 系 35、`post_month_*` 8、`house_*` 6、`month_*` 8、店 10・卵回復 3・補充 2・募集 1・召喚対話 3）、戦闘系 ~56 件（`battle_*` 32 + `monster_*` 21 + `defense_observed` 3、battles.decisions 45）、**stale ループ系 ~36 件**（`plan_map_activate` 6 / `sortie_route_unverified` 5 / `castle_jump_*` 13 / `cursor_still_yview`+`world_anchor_from_yview`+`world_map_units` 9 / `source_not_under_cursor` 3）。

記録された連鎖（全て step `A:5b2e1300:M6a158d`＝アイアイエー→バスカビル attack・クランベリー）:

1. `plan_map_activate` — 機械ボス計画の pending `M6a158d` をフィールドで active 化（月内 6 回）。
2. `castle_jump_open` → `scroll` → `confirm` — 出撃元アイアイエーへ実際に城メニューまで遷移。
3. `sortie_route_unverified`（castle_menu で取消、月内 5 回）— `routes.order_allowed` L76 の `purpose=='attack' and target in owned` が False を返す設計どおりの再検証（バスカビルは `world.captured`・在城 `バスカビル(2)` にあり、先頭波 `M1dd4af` が done＝奪取済み）。step は `pending` へ戻る。
4. **0.36 秒後に同じ `plan_map_activate` が同じ step を再 active 化** → `world_map_units` / `source_not_under_cursor` / `cursor_still_yview` とナビゲーションを消費。月内 5-6 サイクル・約 50-60 秒（窓の約 3 割）を占有。
5. 残りの窓は召喚獣戦を含む 3 戦闘（`battle_start` 3 / `defense_observed` 3）と月次処理（築城 7 件・店・国ステータス・卵回復・補充・`post_month_*` シーケンス）が占有して月が終わる。
6. 窓後（翌5分）にも `plan_map_activate` 5 / `sortie_route_unverified` 4 とループが継続し、回復せず early seal。

`order_start` が 0 なのは `next_order`（orders.py L2251-2255）が `purpose=='attack' and target in owned` を正しくスキップするためで、監査の `monthly_scan` 分類は「窓を占有したもの」の記録として契約どおり。発射ゼロの直接因は「計画が提示し続けた唯一の出撃候補が発射不能な stale 攻撃であり、再構築が起きなかった」こと——月次処理と戦闘は窓を埋めたが、それ自体は設計どおりの月次業務とゲームイベントであり、欠陥ではない。

`sortie_unverified_rearmed` ×1 は別計画 `cebc4b2b` の `Ma8aa67`（行軍未観測の launched_unconfirmed を次月へ pending 戻し）の記録で、VERIFY-5623 §1 と同じく別機構・有界。`sortie_march_blocked_settled` / `sortie_overdue_retired` / `sortie_move_overdue_settled` / `sortie_record_observed_ended` / `arrival_unverified` は `cebc4b2b` の旧行軍（2690-3498t）の後始末で、本ループの駆動ではない。

## 3. 欠陥機構の現行コード照合（origin/main で確認）

| 段 | 機構 | origin/main の状態 |
| --- | --- | --- |
| 取消 | `routes.order_allowed` L76 `purpose=='attack' and target in owned` → False → `_hold_unrouted_sortie` → `sortie_route_unverified` + pending 戻し（deploy.py L1893-1895、`#5348`/v1453・`#2931` 由来） | **修正前のまま**（`_settle_owned_target` 分岐なし）。取消自体は正しいが、恒久的失敗を pending へ戻す |
| 再 active 化 | `plan_starved_rearm` の map 分岐 `plan_map_activate`（orchestrator.py L675-687）のゲートに `next_order`（orders.py L2251-2255）の `purpose=='move' or is_boss_castle or roster_probe or target not in owned` が無い | **欠陥あり**（述語なし）。バスカビルは BOSS_CASTLES（'けっかい'/'ボス' のみ、hanjuku_chart.py L455-459）に非該当で、M6a158d は `target in owned` → `next_order` はスキップするのに activate 経路だけ素通し |
| 計画生存判定 | `_plan_pending.live()`（orders.py L1021-1028）に同述語が無く stale 攻撃を runnable と数える | **欠陥あり**。計画 `5b2e1300` の chart 7 指示は全てバスカビル向け attack（issue plan.chart_plan）のため全件 non-live 扱いにできず計画が不死 |
| 再構築ゲート | `plan_starved_rearm` 900-tick 再構築の runnable-pending 判定（orchestrator.py L712-723）に同述語が無い | **欠陥あり**（live 行軍 `cebc4b2b:Mc8eff7` launched_unconfirmed による早期 return とも併存） |

いずれも VERIFY-5623 §2 の表と同一の欠落で、月内 6 回・窓後 5 回の activate→取消ループがこの欠落のまま駆動された。

## 4. 既存修正のカバレッジ（検証観点3: PR #5645 / v1529 でカバー済みか）

PR #5645（verify/5623、HEAD `dbd249675`、main 未マージで open）は4点の修正を持ち、本件の全段をカバーする:

| #5645 の修正 | 本件への適用 |
| --- | --- |
| `deploy.py` `_settle_owned_target`（`_hold_unrouted_sortie` で `purpose=='attack' and target in _owned` なら pending 戻しせず `done` で閉じ `sortie_settled_owned_target` を記録） | M6a158d は attack→owned バスカビルで `order_allowed` L76 が必ず False → 初回の取消で `done` に閉じる。**ループは最悪1往復で終了** |
| `orchestrator.py` `plan_map_activate` ゲートへ next_order 同述語を追加 | M6a158d（および同計画の残り5波＝全て attack→owned）は active 化自体をスキップ → **activate→取消は0回**。order=None 経路から `_off_chart` 再構築へ到達 |
| `orchestrator.py` `plan_starved_rearm` runnable-pending 判定へ同述語を追加 | stale 攻撃のみが残る計画は「生きた pending なし」と判定 → 900-tick 再構築が発火可能に |
| `orders.py` `_plan_pending.live()` へ同述語を追加 | 計画 5b2e1300 は全 pending が attack→owned のため dead 判定 → `_mechanical_chart` の early-return が解け、`request_digest`（captured を含む）変化で破棄・再生成され、`c not in owned` フィルタでバスカビル向け指示は再生成されない |

- **5-6 再発**（issue コメント、incident `766951197549d45cb604`、compact 記録のみで JSON なし）: 同一 fingerprint・同一月系のため同機構とみなせ、同じくカバーされる。
- **本件で #5623 と異なる点**: signature が `monthly_scan`（`order_start` 0 のため `departure_failure` 系 signal は不発）・月次処理と3戦闘が窓の大半を占有した点。これらは「ループが解放した map 時間を月次業務が使った」構図であり、ループと飢餓の機構そのものは同一で、#5645 のどの修正点も月次処理・戦闘の占有を問題にしない（対象は stale 指示の生存と再活性化）。
- **`sortie_unverified_rearmed`（`cebc4b2b:Ma8aa67`）** は別機構で月1回に有界、#5645 の対象外だが本件の発射ゼロの駆動でもない（VERIFY-5623 §1 と同じ評価）。
- 結論として **#5624 は PR #5645 で完全にカバーされる**。追加のコード修正・回帰テスト・BOT_VERSION bump は不要。

## 5. なぜ (b)(c)(d) ではないか

- **(b) ではない**: 月次処理・戦闘の占有は設計どおりだが、恒久的失敗の指示を pending に戻し保有チェックの無い activation 経路が無界に再発行する連鎖は、どの既存有界機構（取消予算・retry hold・starve 再構築・`_plan_pending`）でも止まらず、月内6回・窓後5回・翌月5-6へ再発した。発射不能指示を計画が不死のまま保持するのは欠陥。
- **(c) ではない**: 監査は「月内発射ゼロ」を正しく測定し、`monthly_scan` signature も counts 由来で契約どおり。下層に activate→取消の実ループがあり誤検知ではない。
- **(d) ではない**: issue 内包の decision 時系列・plan・world だけで、stale 化（先頭波奪取済み）→ activate → 取消 → pending → 再 activate の全経路と、月次処理・戦闘による窓占有の内訳が特定できる。run 本体の jsonl なしでも因果は確定できる。

## 6. 補助観察（結論に影響しないが記録）

- `order_start` 0 ゆえ signature が `monthly_scan` になったが、#5623（`order_start` 2 で `departure_failure`）と fingerprint が分かれるのは dedup 契約どおりで、同機構でも signal 構成で issue が分かれることは検証上の注意点（横断照合で判明済み）。
- 月内に `chikujou` 7 件・店・`post_month_*` シーケンス等、月次処理が問題なく走っていることは、ループ解放後の map 時間が月次業務に吸収されたことと整合する。#5645 適用後は stale ループ分の map 時間が解放され、再構築計画からの正当な出撃または正当な「出撃なし」へ収束する想定。
- `retake` 目的の同型ループは VERIFY-5623 §7 と同じく未観測のためスコープ外。

## 成果物の状態（本レーンからの制約）

- **マージ**: `git fetch` / `git merge origin/main` が実行拒否。差分照合は `git show origin/main:` と `git diff --stat` で代替し、検証対象経路に base→main の差分が無いことを確認（§0/§3）。ブランチへの main 取り込みは残件。
- **コミット**: `git add`/`git commit` が実行拒否のため本ファイルは worktree 上に未コミット。ブランチへのコミットは手段のある環境での残件（想定コミット文: `docs(verify): #5624 zero_sortie ch5 5-5 の検証記録 (結論 a・PR #5645 でカバー済み)`）。
- **再計算**: fingerprint/incident_id の sha256 再計算は計算コマンドの実行拒否により未実施（§1 に式照合を記録）。
- **テスト**: 追加修正なしのため新規テストなし。python3/pytest は実行拒否のため #5645 側テストのローカル実行も未実施（CI へ）。
- **issue #5624 へのコメント**: `gh` 実行拒否のため本レーンから投稿不能。下記の文面を代理投稿してほしい（投稿経路は AGENTS.md §3.1 のフォールバック表に従う）:

```text
[process-deviation] docich-5624 worktree / secret_scan 未実施（同環境で実行拒否）/ 代理投稿 / gh・curl・python3・git書き込み実行拒否

検証完了: 結論 **(a) 実欠陥 — #5623 と同一機構で、PR #5645（v1529, verify/5623）でカバー済み**。追加修正不要（VERIFY-5624.md、verify/5624 ブランチ）。

- 月内の内訳: 月次処理 ~80・戦闘 ~56・stale ループ ~36 件/194。発射ゼロの直接因は月次処理・戦闘ではなく、計画 5b2e1300 が提示し続けた唯一の出撃候補が attack→自軍バスカビルの stale 指示（M6a158d）で、plan_map_activate 6 回 → order_allowed（attack-on-owned）で sortie_route_unverified 5 回 → pending → 0.36秒後に再 activate の無界ループ＋計画再構築の飢餓。order_start 0 は next_order の正しい owned スキップ。
- 5-6 再発（incident 766951197549d45cb604）も同 fingerprint・同機構とみなせ、同じくカバーされる。
- fingerprint/occurrence は式照合で確認（raw `5|zero_sortie|monthly_scan` / `g1190-ch1v9|1190|5|5-5`、環境拒否で実再計算は未実施）。
- #5645 の4点（_settle_owned_target / plan_map_activate ゲート / starve runnable 判定 / _plan_pending.live）が本件の activate→取消・計画不死の全段をカバー。マージ後はループ最悪1往復・通常0回で終了。
```

- **Slack #agent-council 完了報告**: slack-remote MCP 呼び出しも拒否されるため本レーンから投稿不能。docich#5624 スレッド（ts 1795626118.567319）への Muse Bot (`<@U0CCCH69XMW>`) メンション付き報告は手段のある環境での残件。文面:

```text
<@U0CCCH69XMW> docich#5624 検証完了。結論 (a) 実欠陥 — #5623 と同一機構（stale 攻撃の activate→取消ループ＋計画飢餓）で、PR #5645 (v1529) が全段カバー済みのため追加修正なし。VERIFY-5624.md は worktree に作成済み（コミットは環境拒否のため残件）。月内は月次処理 ~80/戦闘 ~56/stale ループ ~36 件で、order_start 0 は next_order の正しい owned スキップ。5-6 再発も同機構。
```
