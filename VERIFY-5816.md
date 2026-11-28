# VERIFY-5816 — [半熟英雄] 野営放置: ch4 タピオカ (3ヶ月)

- 対象: azumag/docich#5816。kind/signature `camp_abandoned` / confidence `high`。
- ラン: `g1373-b4c4126f` / generation `1373` / 発生時 `hanjuku-chart-v1597-chikujou-defer-reason`。
- 現行コード: この worktree の main 相当（`BOT_VERSION = hanjuku-chart-v1608-recall-y-recovery`）。
- 検証範囲: read-only。git 書き込み・`gh` コマンド・コード修正・PR 化はしていない。本番 decision log はローテーション済み前提で参照していない。issue 本文と公開 issue ページ、現行コード、既存の VERIFY-5815/5789/5792 を照合した。
- `DOCICH_HANDOFF_PATH` は前段の確認で環境に見えず、handoff 正本は未読扱い。

## 結論: **(d) 判定不能**

ただし範囲はかなり絞れる。

1. **誤検知とも設計どおりとも断定できない。** タピオカは HP 50/50、broken false、城候補 `ビクトリア` の camp 行であり、監査の `_camp_generals` 条件（`location='camp'`、`location_observed` が false でない、HP>0）を満たす。`ビクトリア` は ch4 の四季王城 boss site ではないので、ピオーネ（#5815）のような boss-site 包囲免除・`boss_move` 経路には入らない。現行設計では通常 `recall` タスクへ積むべき対象であり、3ヶ月残ることは意図状態ではない。
2. **一方で、通常 recall がどの段階で失敗したかは issue 本文だけでは確定できない。** 初回 issue は月替わり直後の観測1回 / 0.0秒で、月次 scan・task 実行前に切られている。再発コメントではタピオカとピオーネが同月内に再度 camp 扱いされているが、そこにも `post_month_task_result`、`camp_recall_dispatched`、`camp_recall_arrival_timeout` などの本人帰属ログは写らない。`flow.recall` / `recall_verification` には general や goal が残らず、本人の操作へ帰属できない。
3. **継続月数 `3` 自体にも過大計数の余地がある。** `camp_status_seen` は将軍ごとの最初の camp 観測 tick を保持し、城内観測では `castle_tick` を足すだけで episode を消さない。行軍出発を実観測しない限り、一度帰還後に再出撃・再野営した場合でも古い first tick から月数を数え得る。本件では 3ヶ月ちょうどなので #5792 の 6ヶ月より影響は小さいが、「同一野営が途切れず3ヶ月続いた」ことは issue 本文だけでは未証明である。

したがって、**意図的な設計放置ではないことは確かだが、現行 main の具体的なコード欠陥へ因果を確定できない**ため (d) とする。実欠陥の可能性は残るが、監査の月数計数誤り・有界な再試行や月末/場面割り込みの反復・ recall 成否の観測欠落を区別できない。

## 1. fingerprint / occurrence の式照合

現行 `src/docich/hanjuku/month_audit.py` の camp_abandoned 固有の実装:

- `_build_camp_abandon_incident()` L1858-L1860:
  `incident_id = sha256(f"{runtime_id}|{generation}|camp_abandoned|{general}|{first_tick}")[:20]`
- 同 L1862-L1863 と `_fingerprint()` L3545-L3547:
  `fingerprint = sha256(f"{chapter}|camp_abandoned|camp_abandoned")[:16]`

本件の入力値:

| 項目 | raw 入力 | issue 値 | 今回の独立再計算 |
|---|---|---|---|
| fingerprint | `4|camp_abandoned|camp_abandoned` | `3bd148295593ce20` | **未計算** |
| occurrence / incident_id | `g1373-b4c4126f|1373|camp_abandoned|タピオカ|56866` | `8c221cd1533e24ce7fa2` | **未計算** |

補足:

- fingerprint の `chapter` は JSON 本文どおり数値の `4` であり、文字列 `ch4` ではない。
- incident_id に入る `first_tick` は camp_stay の初回野営 tick `56866` であり、issue key `3bd148295593ce20/8-10/58790` の `58790`（month_summary の当月 first_tick）ではない。
- 今回も `python3` 実行が承認却下されたため、独立したハッシュ再計算は未実施である。式と入力値は上記のとおり現行コードから特定できたので、一致確認は検品ゲート側で再計算してほしい。なお fingerprint の raw 入力は #5815 と完全に同じで、VERIFY-5815 の検品ゲート確認ではこの raw から `3bd148295593ce20` になることが記録されているが、本 VERIFY ではそれを自分の再計算としては扱っていない。

## 2. 監査発火と「観測1回 / 0.0秒」の意味

`camp_abandoned` は月末の完走監査ではなく、永続化された `general_statuses` / `camp_status_seen` を月境界で数える検出である。

- `_track_camp_abandon()` L2900-L2971 は camp 行を将軍ごとに追跡し、月が変わっても行が残れば `months` を増やし、`CAMP_ABANDON_MONTHS = 3` 以上で incident を作る。
- `_camp_generals()` L1730-L1744 は row の tick freshness を直接要求しない。HP>0 の camp 行なら採用する。
- 本件の初回 issue では camp row の `last_tick` は `58251`、当月 first tick は `58790`。差は 539 tick で、post-month 側の再読閾値 `CAMP_ROW_STALE_TICKS = 600` 未満である。したがって初回発火は「前月末に観測された、まだ freshness 閾値内の camp row」に基づく可能性があり、8-10 当月にタピオカを直接再観測した証拠ではない。
- もっとも issue の再発コメントでは、同じ ch4 8-10 でタピオカとピオーネの camp first が `59286` に更新され、観測 498回 / 721.5秒、signal に `monthly_scan_hold` がある。少なくとも監査状態の上では、月次 scan を経た後も両者が camp 行として残っていたことを示す。ただしこの再発も scan/task 処理の結果ログを含まず、recall が実行されて失敗したのか、まだ実行前だったのかは分からない。

したがって観測1回 / 0.0秒は直ちに誤検知を意味しないが、証拠は薄い。初回 issue 単体では「当月も本当に camp だった直接証拠」が弱く、再発コメントを入れても recall 成否は未証明である。

## 3. 現行 main でタピオカが取るべき経路

`post_month_status._tasks()` L225-L308 の分岐:

- `road` / `camp_stays` は主に ch9 系で、本件の ch4 では該当しない。
- boss-site camp で boss unlocked かつ免除月数内なら `boss_move` または包囲継続になるが、`ビクトリア` は ch4 boss site ではないため該当しない。
- したがって `location == 'camp'` のタピオカは通常 `{'kind': 'recall'}` に積まれる。recall/boss_move は repair より先へ並ぶ。

task 実行側では:

- SELECT 中心の cursor と camp のズレが 12px 以内なら camp menu へ進み、`flow.recall` を `task_recall: True` で作る（L2325-L2407）。
- 月末推定 200 tick 以内なら新規 recall を始めず `recall_deferred_month_end` にする（L2365-L2372）。これは設計上の延期であり、翌月の scan で再対象化される。
- 戦闘・場面割り込みで送信前に flow が消えた場合は、`RECALL_TASK_RETRIES = 2` / `RECALL_INTERRUPT_GRACE = 240` の範囲で同月再キューする（L2503-L2552）。
- ただし `_recall_was_dispatched()` L1554-L1570 が真なら同月再送しない。これは受理済みかもしれない帰還を二重送信しないための安全側設計である。

この設計上は、通常城隣の healthy なタピオカが複数月そのまま残るのは正常系ではない。しかし、当月タスクが積まれなかったのか、camp cursor を確認できなかったのか、月末延期が続いたのか、A送信後に到着しなかったのか、到着後に再出撃したのかは、本文の投影だけでは判別できない。

## 4. #5815（ピオーネ）との fingerprint/key 同一性と検証結論との整合

#5815（ピオーネ）と本件は fingerprint `3bd148295593ce20`、key `3bd148295593ce20/8-10/58790` が同じだが、これは式上当然である。

- fingerprint は `chapter|kind|primary_signal` だけで、将軍名・地点を含まない。ch4 の `camp_abandoned` は全件同じになる。
- key は `(fingerprint, month, month_summary.first_tick)` であり、camp の first_tick や将軍名を含まない。同じ月 `8-10`、同じ month first tick `58790` なら一致する。
- 一方 incident_id には将軍名と camp first tick が入る。タピオカ `56866`、ピオーネ `56823` で別 incident である。

#5721 の抑制は、順次実行なら同じ `(fingerprint, month, first_tick)` の2件目を `duplicate_month` にする。しかし camp_abandoned は将軍ごとに incident を seal し、そのたびに detached な `spawn_issue_filer` を起動する。複数将軍が同じ観測で閾値へ達すると、両方の filer が既存 open issue 無しを読んで並行に `issue create` する race があり得る。したがって #5815/#5816 は「同一 incident の重複起票」ではなく、**将軍ごとの別 incident が同一 month key の並行起票 race で別 issue になった可能性が高い**。これは起票パイプラインの問題であって、タピオカが野営したゲーム内原因とは別層である。

VERIFY-5815 の結論は (d) 判定不能だった。ただし理由は、ピオーネが `四季王城(北西)` の boss-site camp で、HP 46/46・broken false・ちょうど3ヶ月という条件から、home recall ではなく `boss_move`/包囲免除の設計内に入り得たためである。本件タピオカは `ビクトリア` の通常城側なので、その免除理屈は適用できない。この差は VERIFY-5815 と矛盾しない。ピオーネは「設計内の包囲か、boss_move 失敗か」が未確定、タピオカは「設計免除はないが、通常 recall の失敗段階が未確定」という別の判定不能である。

## 5. #5817（camp_recall_unconfirmed、四季王城(南西)）との関係

#5817 は同じラン・同じ ch4 8-10 で、時系列的には本件の直後に起票されている。

確定している事実:

- `camp_recall_scene_covered` が 04:38:50 頃に記録され、送信済み帰還ピッカーが月初場面に覆われた。
- 終端は `camp_recall_unconfirmed` / `camp_arrival_unconfirmed`。`input_receipt_confirmed: True`、`input_sent: True` だが、`picker_closed: False` で、受理・到着は確認できていない。
- 目標は `四季王城(南西)`、camp は `[124,160]`、target cell は `[124,124]`、general は不明。

これは、同じ月境界で少なくとも1件の camp 移動/帰還が「A入力 receipt はあるが picker close も到着も証明できない」終端へ落ちた直接証拠である。月境界をまたぐ recall/boss_move の脆弱性を示す。

ただしタピオカ本人の失敗とは断定できない。

- タピオカの城候補は `ビクトリア` で、通常 recall なら帰還先は自軍城のはずであり、`四季王城(南西)` ではない。
- #5817 の general は null で、month_audit の投影から本人を特定できない。
- `四季王城(南西)` は boss site なので、#5817 はタピオカの通常 recall より、boss 向け `boss_move` または boss 関連 camp の移動と読む方が自然である。

したがって #5817 は「同月に recall 系の月境界競合が実在した」周辺証拠ではあるが、タピオカの通常 recall 失敗の直接証拠ではない。

## 6. 過去の同判定との異同

### #5789（グレイ 3ヶ月・ch10・実欠陥）

#5789 は ch10 White House/boss 経路で、home recall が意図的に抑止・再分類され、唯一の解消経路 `boss_move` が当時のコードでは同月再試行されないことが実欠陥と確認された。現行 main では `wait_recall` の再キュー条件が `job.get('kind') in ('recall', 'boss_move')` へ拡張されており、この specific defect は修正済みである。

本件は ch4・通常城 `ビクトリア` の ordinary recall なので、#5789 の boss_move 再試行欠陥をそのまま適用できない。逆にタピオカには ordinary recall の再試行経路が最初から存在する。

### #5792（ヴィーナス 6ヶ月・ch3・判定不能）

#5792 は通常城 `グリン` 隣の healthy camper で、boss/ch9/ch10 の免除が成立しない点では本件に最も近い。あちらも「意図的放置ではないが、通常 recall のどの段階が6ヶ月失敗したかは本文だけでは決められない」ため (d) だった。さらに `camp_status_seen` の古い first tick 再利用による月数過大計数の可能性が指摘されている。

本件は継続3ヶ月で #5792 より短く、初回 row は閾値内ではあるが前月観測だった。同一 run ではあるが章・城・期間が異なり、#5792 の結論をそのまま移植はできない。それでも「通常 recall 対象なのに残っているが、失敗段階が未証明」という構造は同じである。

## 7. v1597 発生、v1608 現行コードの差分で残る不確実さ

発生時は `hanjuku-chart-v1597-chikujou-defer-reason`、現行は `hanjuku-chart-v1608-recall-y-recovery`。この間には ch4 boss 経路や recall Y-jump recovery、boss_move requeue 等の修正が入っている。したがって v1597 で実際に踏まれた経路が、現行 main でも同じ頻度・同じ形で再発するとは限らない。

特に本件では `ビクトリア` 自体は boss site ではないものの、同じ月の plan/world には四季王城(南西)/(北西) への boss 関連行軍が残っている。boss 側の `boss_move` や recall singleton flow が月初に占有・未確認化すると、通常 recall の実行タイミングへ間接影響する可能性はある。しかし issue 投影は general/goal を落とすため、この间接効果がタピオカへ実際に作用したかは証明できない。

## 8. 未決の点

確定には、少なくとも次の証拠が必要である。いずれもローテーション済み decision log が無い限り issue 本文だけでは得られない。

1. 8-8・8-9・8-10 の各月で、タピオカに対する `post_month_task_result`、`post_month_recall_requeued`、`camp_recall_dispatched`、`camp_recall_arrival_timeout`、`camp_recall_goal_failed` が発火したか。
2. `flow.recall` / `recall_verification` の `await_dispatch` / `dispatched` がタピオカ本人のものか。現行投影は general/goal/camp/tick を残さない。
3. 月次 scan 完走後の process ステージで、タピオカの recall task が実際に開始されたか。再発コメントは scan 占有と camp 残存を示すが、task 実行結果を示さない。
4. タピオカが一度帰還して再出撃・再野営したのか、同一野営が連続したのか。`camp_status_seen` の `castle_tick` や departure 観測が必要。
5. 初回 issue の row tick `58251` から当月 first tick `58790` までの間に recall dispatch があったか。差は 539 tick で閾値内だが、当月の直接観測ではない。
6. fingerprint / occurrence の今回の独立ハッシュ再計算。式と入力値は §1 に記録したが、実行承認が得られず未計算。

## 最終整理

- 結論: **(d) 判定不能**。
- 設計どおりではない方向: `ビクトリア` は boss site ではなく、HP full・broken false のタピオカは現行設計では通常 recall 対象である。
- 誤検知と断定できない理由: camp 行は検出条件を満たし、再発コメントでも月内に camp 扱いが残る。初回観測は薄いが、単なる静的雛形や他種別流用ではない。
- 実欠陥と断定できない理由: recall 未実行・月末延期・camp 未確認・scene/interrupt・dispatch 後未到着・再出撃・監査月数の過大計数を、本人帰属の証拠なしに区別できない。
- ハッシュ再計算: **未実施**。式と入力値は §1 のとおり確定済み。
