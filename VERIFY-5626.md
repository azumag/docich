# VERIFY-5626 — [半熟英雄] 野営放置: ch8 ポワソン (3ヶ月)

- 対象: azumag/docich#5626。kind `camp_abandoned` / signature `camp_abandoned` / confidence `high`。
- 観測: ラン `g1342-1fe0734c` / generation 1342 / `hanjuku-chart-v1520-summon-card-flow-hold`、ch8 14-5（観測 1 回 / 0.0 秒）。
- 事象: ポワソンが 3ヶ月野営（初回 13-10、HP 80/80、卵 None、状態「キャンプしています」、城候補 `いちじょうだに`、最終観測 tick 124510）。出撃 確定1/発射0/order_start 0。
- 検証 base: worktree `verify/5626` base `191d9b390`（12 behind）。origin/main はローカル参照で `a3fc83776` まで取得済みだが `git fetch`/`git merge` は環境で却下（残件）。読み取りは `git show origin/main:...` で現行 main を直接参照して行った。
- 観測版 `hanjuku-chart-v1520-summon-card-flow-hold`（PR #5619 系）と本検証の差は §4 を参照。
- `DOCICH_HANDOFF_PATH` 未設定のため handoff 正本は未読と記録する（AGENTS.md §1）。

## 結論: **(a) 実欠陥 — `launched_unconfirmed` の通常城オーダーが行軍記録の実測終了後に永久ゾンビ化し、攻撃stepが解決・再試行・閉塞のいずれもできない**

ポワソンの攻撃 `A:d485f334:M6b750d`（あるまむーん→いちじょうだに attack）は `launched_unconfirmed`（発射1886tick、issue 内包の行軍一覧・plan.orders で確認）。彼の行軍は目標城隣接での野営に終わり（名簿行 `location:'camp'` / `castle_candidate:'いちじょうだに'` / tick 124510 > sortie tick ≈122947）、次回 `next_order` → `_expire_spent_sorties` で `_observed_march_end` が真となり記録が実測終了で消える。ここで通常城向け `launched_unconfirmed` の step を開き直す経路が一つも存在しない（§2）。一方、月次処理はキャンプした彼を毎月 `recall` タスクで自城へ戻し（`recall_verification` は `await_dispatch`/`dispatched` = ゲーム受理済み）、帰還後に別攻撃指示が再び彼を送れば同じ野営に戻る —— recall↔再攻撃の循環が camp_watch の月次境界計数を断続的に積み上げ（3ヶ月/7ヶ月窓）、`camp_abandoned` が発火した。`いちじょうだに` は ch8 の通常城（`CASTLE_NAMES[8]`、ボスは `'ボス'`）なので boss_camp 免除・boss_move の対象外。

新方針（2026-11-25 owner 指示）に従い、検証だけでなく**実修正を本ブランチに実装済み**（§5）。BOT_VERSION は `hanjuku-chart-v1530-unconfirmed-march-end-reopen`（現行 main = v1528、#5623 lane = v1529、次の空き = v1530）。

## 1. 指紋・発生IDの照合（検証観点1）

再計算スクリプト・ハッシュ実行コマンドは本環境で却下されるため、式と入力の照合で記録する（過去 VERIFY 同様の制約）。

- `_fingerprint`（month_audit.py ~L3333）: `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]`。`camp_abandoned` は `_fingerprint(chapter, 'camp_abandoned', ['camp_abandoned'])` で呼ばれる → 入力 `"8|camp_abandoned|camp_abandoned"` → 期待値 `00584e00f6e80938`（issue 記載値と式・入力は整合。ハッシュ再計算は未実行・残件）。
- `incident_id`（`_build_camp_abandon_incident` ~L1720）: `sha256(f"{runtime_id}|{generation}|camp_abandoned|{general}|{first_tick}")[:20]` → 入力 `"g1342-1fe0734c|1342|camp_abandoned|ポワソン|124510"` → 期待値 `2c54f8fc24a4eb1fb5f8`（同様に式・入力は整合）。

## 2. 欠陥の機構（現行コードとの照合）

`orders.py::_expire_spent_sorties` の実測終了分岐（~L1778-1833）:

| 段 | 機構 | 評価 |
| --- | --- | --- |
| 行軍記録の実測終了 | `_observed_march_end`（L1596-1614）: 出撃後の `camp`/`castle` 実測行で march 終了 → `sorties.pop(step)` | **設計どおり**。ポワソンの野営行（tick 124510 > 122947）はこれに該当し、記録は消える |
| 未確認発射の reopen | L1784-1786（修正前）: `unconfirmed = launch_kept or probe.kept or probe.blind` かつ `orders[step] == 'launched'` のときのみ `pending` へ reopen（`sortie_launch_unverified`、miss 上限2で `sortie_launch_failed`） | **欠陥**。`orders[step]=='launched_unconfirmed'` は「launch 未確認」そのものなのに条件外 → else へ落ちる |
| else 分岐 | `_rearm_site_step_after_return`（L1658-1720）: `chapter != 4` で即 return False | **ch4 専用**。ch8 では何もしない |
| 他の回復経路 | `_rearm_phantom_site_steps`（ch4 専用、L1861）・`_rearm_failed_site_steps`（ch4 専用）・`_rearm_phantom_boss_steps`（`chart.is_boss_castle(target)` のみ、L2004）・`_rearm_unconfirmed_site_step`（ch4 専用、deploy.py L91）・`_overdue_retired`（`orders[step]=='launched'` or ボス城のみ保持、L1583-1584） | **全て対象外**。通常城 `launched_unconfirmed` の救出経路がゼロ |
| 再検証の再実行 | `_verify_sortie_source`（deploy.py L127-215）は active step の deploy 中のみ走る。`next_order`（L2173+）は `None`/`pending` のみ返し、`launched_unconfirmed` は二度と active 化されない。issue 内包でも `plan.active: null` | **検証自体が再実行不能**。step は永久に `launched_unconfirmed` のまま残る（issue の plan.orders でも現状確認） |
| 月次 recall | `_tasks`（post_month_status.py ~L262/282）: 通常城の野営は `boss_camp_exempt` 外 → `{'kind':'recall'}`。`task_recall:True` + `release_for_task`（#5421 キッシュ修正）は recall/boss_move 両方に適用済み（L2372-2385）で、stale `retreat_rechecks` 乗っ取りは防がれている | **recall 機構自体は健全**（dispatched で受理済み）。ただし recall→帰還→別攻撃→野営の循環には bound がない |
| 在城簿記 | 実測終了時 `garrison[source]` へ将軍を書き戻す（L1834-1847、#4415 由来の既存仕様） | 野営中の将軍を出撃元在城として記帳する副次的な虚偽。今回は本筋ではないが残件として記録 |

結果: 攻撃 step `A:d485f334:M6b750d` はゾンビ化（issue 時点で `launched_unconfirmed(1886t)`、記録は残存するが野営行が実測終了条件を満たすため次の `next_order` で消えてゾンビ確定）。step は `いちじょうだに` を二度と攻めず、片や月次 recall が野が野営を掃き続ける → 野営の月次計数が 3 に達し監査発火。

## 3. 既存修正のカバレッジ（検証観点2/3）

| 修正 | 本件への適用 |
| --- | --- |
| #5421 キッシュ修正群（v1477/v1478: `boss_camp_counted` べき等計数、`camp_watch_hint` 播種、`task_recall:True`+`release_for_task`） | ボス野営の免除計数と帰還乗っ取りの対策。**ポワソンは通常城野営で免除経路に入らない**が、recall の乗っ取り防止は通常 recall にも効いている（dispatched 受理の裏付け）。主因ではない |
| #5623 修正（v1528、a92cb198a: owned-target の stale attack を `done` で閉じる + `live()` の owned ゲート） | `pending` かつ目標城が自軍のケースのみ。本件は `launched_unconfirmed` かつ敵城 → **非カバー** |
| `_rearm_phantom_boss_steps` の `launched_unconfirmed` 孤児対応（#4832 コメント） | `is_boss_castle` ゲートでボス城のみ → **非カバー** |
| `_relaunch_overdue_march`（overdue 化→pending+relaunch≤2） | 記録生存中かつ `_arrival_limit` 超過が前提。実測終了の方が先に発火し記録が消えるため到達しない（race: 野営行が既に存在する今、次パスで expiry が先に pop する） |
| `_drop_orphan_active` の `launched_unconfirmed` 保護（L2349） | active を解放しないため検証も再実行されない —— ゾンビの温存側 |
| PR #5643（v1528 card-list 未確認証拠）・v1525/v1527（在庫・監査） | 非該当（card/audit 面） |

**いずれも通常城 `launched_unconfirmed` step の解決をカバーしない**ため (b) ではない。

## 4. 観測版 v1520 と現行 main の差異

`_expire_spent_sorties` の当該分岐（L1778-1833 修正前）、`_observed_march_end`、`_rearm_site_step_after_return`、`_rearm_phantom_boss_steps`、`_overdue_retired`、`_tasks`/`task_recall` の導入 commit はいずれも v1520 より古い。v1520→v1528 の orders.py 履歴（`git log origin/main`）は 425f1c38a（#5365 purpose 保持）等で当該分岐に触れていない。**観測された状態は現行 main と同一機構**であり、main にも同じ欠陥が残っていた。

## 5. 実装した修正（結論 (a) のため本ブランチで実装）

1. `strategy/orders.py::_expire_spent_sorties`: `unconfirmed` の判定に `order_state == 'launched_unconfirmed'` を追加し、reopen 条件を `order_state in ('launched','launched_unconfirmed')` に拡張。`launched_unconfirmed` の step は定義上 launch 未確認なので、実測された野営・在城終了に対して kept/blind 発射と同じ bounded reopen（`launch_miss` 上限2 → `_mark_order_failed`）を適用する。
2. 同じ reopen 分岐で `launched_orders[step]` の pop を「step が現行 plan に存在する場合のみ」に限定。現行 plan 外の step（ポワソンの M6b750d のように旧計画由来）は `next_order` の `retries` 経路が `launched_orders` を読むため、消すと reopen しても再ディスパッチできない。
3. `version.py`: `hanjuku-chart-v1530-unconfirmed-march-end-reopen`。
4. 回帰テスト（tests/test_hanjuku_spent_sortie_expiry.py に4件追加）:
   - `test_an_unconfirmed_normal_castle_order_reopens_on_a_measured_camp`: 通常城向け `launched_unconfirmed` + 出撃後野営読み → 記録消去・`pending`・`sortie_launch_unverified`。
   - `test_the_unconfirmed_reopen_is_bounded_by_the_miss_limit`: miss 累積2 → `failed` + `sortie_launch_failed`。
   - `test_a_dead_plan_step_keeps_its_retry_copy_when_reopened`: 現行 plan 外 step の reopen で `launched_orders` が残ること。
   - `test_a_pending_order_state_is_not_reopened_by_a_measured_camp`: `pending` step の実測終了が reopen しないこと。

### 修正効果

野営・在城を実測した `launched_unconfirmed` step は即 `pending` へ戻り、現行 plan 内なら次の `next_order` で、plan 外なら `launched_orders` retry として再ディスパッチされる。再送が再度野営に終わっても `launch_miss` 上限2で `sortie_launch_failed` → step が閉じる。ゾンビは生じない。月次 recall は従来どおり野営者を自城へ戻し、攻撃側は bounded retry で城へ再圧をかけるため、ポワソン型の「野営→帰還→再攻撃→野営」の無界循環に終端条件が入る。

### 残存リスク・後続候補（今回は未対応）

- 実測不能なまま記録だけが age-expire した `launched_unconfirmed`（未測定 expiry 経路）は引き続きゾンビ化しうる（本件は実測終了経路で再現確認済みのため、証拠のある経路のみに限定）。
- 通常城向けの「野営先=目標城での包囲推進」（boss_move の通常城版）は設計判断が要るため本修正に含めない。`ch4` の `_boss_camp_holds_siege` 慣行を一般化するかは owner 判断。
- 実測終了時の `garrison[source]` 書き戻しは、野営中の将軍を出撃元在城扱いする副次的虚偽（#4415 で意図的に保持された既存仕様）。

## 6. 残件（環境で却下された操作）

- `git fetch origin` / `git merge origin/main`（verify/5626 への取り込み）: 却下。読み取りは `git show origin/main:` で代替済み。コミット時に base が 12 behind である旨、検品ゲートへ引き継ぐ。
- fingerprint/incident_id の sha256 再計算: python/ハッシュ系の exec が却下。§1 の式・入力照合のみ。
- `pytest tests/test_hanjuku_spent_sortie_expiry.py`・`python3 -m py_compile`: 却下。**修正・回帰テストは未実行**。検品ゲート（メイン Muse）でのテスト実行を要請する。
- `gh`（issue 本文・コメント）: 却下。本文は公開 issue ページから取得済み。コメント文面は下記に代理投稿文として残す。
- Slack 投稿: `slack-remote` MCP サーバーは列挙されるがツール一覧取得自体が失敗（到達不可）。下記に代理投稿文を残す。
- `git add` + `git commit`（VERIFY-5626.md・修正3ファイル）: **却下**。検品ゲート（メイン Muse）で以下の変更をコミットしてもらう: `VERIFY-5626.md` / `src/docich/hanjuku/strategy/orders.py`（`_expire_spent_sorties`）/ `src/docich/hanjuku/version.py`（v1530）/ `tests/test_hanjuku_spent_sortie_expiry.py`（回帰4件）。push はしない。

## 7. 代理投稿文

### GitHub issue #5626 コメント案

```
検証結果 (read-only, VM Devin CLI lane, branch verify/5626):

結論: (a) 実欠陥

ポワソンの攻撃 step `A:d485f334:M6b750d`（あるまむーん→いちじょうだに）が `launched_unconfirmed` のまま、彼の行軍が目標城隣接の野営で実測終了されたケースです。`_expire_spent_sorties` の measured-end 分岐は未確認発射の reopen を `orders[step]=='launched'` + kept/blind 旗に限定しており、`_rearm_*`/`_verify_sortie_source` の救済経路は全て第4話サイト・ボス城専用です。通常城向け `launched_unconfirmed` の step は行軍記録が消えると reopen も rearm もされず永久ゾンビになり、その間に月次 recall が野営を掃き続けるため camp_abandon の3ヶ月計数に達しました（recall は dispatched で受理済み・いちじょうだに は非ボス城のため boss 免除外）。

実修正（同ブランチ, BOT_VERSION v1530）: `_expire_spent_sorties` で `launched_unconfirmed` の order status を未確認発射として bounded reopen（launch_miss 上限2 → sortie_launch_failed）し、現行 plan 外 step の retry 用 `launched_orders` は保持するよう調整。回帰テスト4件を tests/test_hanjuku_spent_sortie_expiry.py に追加（この環境では pytest 実行不可のため CI/検品ゲートでの実行を要請）。
```

### Slack #agent-council 報告案（thread ts 1795628644.950249）

```
<@U0CCCH69XMW> docich#5626 検証完了（verify/5626）。
結論: (a) 実欠陥 — 通常城向け `launched_unconfirmed` オーダーが行軍記録の実測終了後に永久ゾンビ化（再開・rearm 経路が ch4/ボス専用）。ポワソンの M6b750d（あるまむーん→いちじょうだに）が該当し、月次 recall との循環で野営3ヶ月計数に達した。
実修正: _expire_spent_sorties で launched_unconfirmed を bounded reopen 化 + plan 外 step の launched_orders 保持。回帰テスト4件追加、BOT_VERSION v1530。VERIFY-5626.md に詳細・残件（pytest 未実行、merge/fetch/gh/slack 却下）を記録。
```

## 8. 検証者メモ

- ポワソンの camp_status_seen エピソードは tick 124510 が起点で first_month(13-10) より新しい → 途中で一度帰還（または episode リセット）して再野営した形跡。camp_watch の `months` は episode ではなく月次境界の `current` メンバーシップで数えるため、断続的な野営でも 3 に達する。13-10〜14-5 の7ヶ月間で 3 境界のみ計数 = 他境界は行軍中/不完全スキャンのいずれか。
- `いちじょうだに` への他将軍の攻撃（シフォン 120557 勝、トレビス 121776 負）は行軍が戦闘に到達している = 城自体は攻撃可能。ポワソンの行軍が野営に終わる理由（地形・aimed cell・経路）はゲーム側要因で、帳簿側の本欠陥とは独立。
