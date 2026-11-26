# VERIFY-5419 — [半熟英雄] 野営放置: ch4 レモン (3ヶ月)

対象: docich#5419（`bug` / `auto-generated` の監査自動起票）
ラン `g1342-1fe0734c` / generation `1342` /
`hanjuku-chart-v1467-status-panel-owner-unreadable-egg` / 月 `ch4 8-1`
occurrence(incident_id) `a7241256cbc702713ec1` / fingerprint `3bd148295593ce20`
created_at `1795525253.5534441`（2026-11-24 13:00:53Z = 22:00:53 JST）

判定: **(d) 判定不能** — 発火は契約どおり。#5418 型 (a) の `boss_move` goal 未解決
クラッシュは本件に適用できない（城候補 `不明` で包囲免除が不成立 → recall 経路、
かつクラッシュ実測 22:14 JST は起票 22:00:53 JST の後）。v1469 / v1470 / v1471 /
v1477 のいずれの修正も本件の滞留経路を直接カバーしない。read-only では
3 ヶ月滞留の真因を一意に確定できない。

調査基盤: worktree `/home/hatch/work/docich-5419`（`origin/main` = `911ffbb55` =
`hanjuku-chart-v1477-boss-camp-recall` から detach）。ラン時点の version は v1467 で、
現行 main は v1477（+10）。v1469・v1470・v1471・v1477 の差分は git history で個別に確認した（§3）。
read-only 検証: 本番 SSH なし / ラン成果物 `hanjuku_decisions.jsonl`・`hanjuku_events.jsonl`・
`hanjuku_frames/` は VM 側で直接参照せず / 秘密情報なし / コード変更・push・PR なし。
根拠は issue 本文・コメント（公開 issue ページ経由）、`origin/main` のコード、
git history、過去 VERIFY 記録（VERIFY-5418 / VERIFY-5420 / VERIFY-5429 / VERIFY-5430）。
`handoff.md` 正本は参照可否を確認するコマンドが本環境で実行できず、未読と記録する。

補足（取得経路）: この環境では `gh` の実行が承認されなかったため、issue 本文・
コメントは GitHub の公開 issue ページ経由で取得した（内容は issue 本文＋Activity と同等）。
sha256 再計算コマンド（python3 / node / perl / sha256sum / openssl いずれも）も実行が
承認されなかったため、算式はコード（`month_audit.py` L1582-1584, L1586-1587,
L3089-3091）で確認し、fingerprint は同一入力・同一 fingerprint の兄弟 issue
（#5418/#5420 の VERIFY で再計算一致済み）を併せて根拠とする（§1）。

---

## 結論

**(d) 判定不能。** 根拠は次の 5 点。

1. **発火自体は契約どおりで、誤検知ではない。**
   `CAMP_ABANDON_MONTHS = 3`（`month_audit.py` L55）を閾値に、`_track_camp_abandon`
   （L2502-2575）が 3 ヶ月以上 camp の将軍を検知し、`_build_camp_abandon_incident`
   （L1500-1589）が incident を起票する。レモンは `months = 3`・
   `first_tick = 55243`（7-11 の読取）で閾値到達。`camp_stay.camp_observed = true` で
   名簿行の実測値（HP 27/27・卵 ワンダーエッグ・broken False）を投影しており、
   静的雛形ではない。incident のキー構成は `_build_camp_abandon_incident` の出力と
   一致する（§1）。

2. **#5418 の (a) 実欠陥（boss_move goal 未解決クラッシュ）は本件に適用できない。**
   レモンの `castle_candidate` は `null`（城候補「不明」）であり、
   `_boss_camp_holds_siege`（`post_month_status.py` L116-132）は
   `isinstance(candidate, str) and chart.is_boss_castle(candidate)` を要求するため
   不成立 → `_tasks`（同 L217-250）は `queue.append({'kind': 'recall'})` 側に送る。
   つまりレモンは包囲免除 → `boss_move` 経路ではなく通常帰還（recall）経路であり、
   `'ボス'` ラベルへの Y ジャンプ（v1469 の修正対象）は彼女の経路には存在しない。
   さらに時系列でも、クラッシュ実測は 2026-11-24 22:14 JST（`7db52e963` コミット
   メッセージ、リーキ/キッシュ帰還中）= 13:14Z であり、本 incident の
   created_at 13:00:53Z より**後**である。「クラッシュの全入力停止が帰還も止めた」
   という VERIFY-5418 §5 の「(a) 準適用」は、起票後の凍結（13:14Z 以降）の説明には
   なっても、7-11 から 8-1 へ積み上がった 3 ヶ月の滞留と本 incident 自体の原因には
   なりえない。VERIFY-5429 / VERIFY-5430 が recall 経路の ch12 dome 野営について
   「#5418 型 (a) の転用は不成立」と退けたのと同じ構造である。

3. **v1469 / v1470 / v1471 / v1477 の修正は本件の滞留経路を直接カバーしない（§3）。**
   - v1469（#5424 / `7db52e963`）: `'ボス'` goal Y ジャンプのクラッシュ防止。recall 経路・
     時系列とも非該当（上記 2）。
   - v1470（#5432 / `90b35600e`）: ch4 四季王サイト出撃注文の `_ready` 有界化。
     ボス出撃側の修正で、野営将軍の帰還経路ではない。
   - v1471（#5141 / `975090bcb`）: due 城の名簿読みを全章で起票する到着確認 probe。
     「dispatched なのに着かない」帰還の**検証**機構であり、本件の滞留自体を
     解消する修正ではない（ただし残件調査には直接関連する）。
   - v1477（#5442 / `911ffbb55`）: ボス至近野営の免除カウント冪等化＋古い退却再確認の
     破棄（`task_recall` / `release_for_task`）。前者は免除対象将軍（キッシュ型）の
     修正で、免除不成立のレモンには無関係。後者の「再確認によるタスク帰還の乗っ取り」は、
     変換条件 `state.get('camp_kind') != 'dome'` が修正前から存在し、レモンの
     `recall_retry.kind = 'dome'` と整合するなら彼女には元々適用されない経路である
     （`camp_kind` 未測定なら適用され得るが、incident からは確定不能）。

4. **シグナルは実在の 2 件のみで、原因推定材料は incident 内に乏しい。**
   `signals = ['camp_abandoned', 'camp_abandon_3months']`。`monthly_scan_hold` /
   `stuck_march` / `camp_recall_loop` 等は付いていない（`_signals` L2974-3023 の
   各条件に非該当: 直前 120 秒に `post_month_status` 系決定なし、行軍
   `I:81e158:04114122` は `en_route` 164 tick で閾値 3000 未満、camp/recall 系決定 1 件）。
   `flow.recall = {stage: 'to_camp', steps: 0}` と `recall_retry = {kind: 'dome'}`、
   `retreat_anonymous_budget.reads = 2`、13:00:46 の `camp_recall_interrupted` から、
   帰還機構が「存在しない」型ではなく「試行→中断/失敗→有界再試行」という設計内の
   往路を繰り返していた形であることは読み取れる。しかし 3 ヶ月分の各月で
   どの段（dispatch 未到達 / メニュー不成立 / 到着未確認 / scene 中断 / scoped skip）
   で止まったかは、issue の 15 件の直前決定では確定できない（§5、§7）。

5. **兄弟 issue の扱いと本件固有の留保。**
   #5418（リーキ、(a)）/ #5420（デュオニソス、(b)）/ #5421（キッシュ、#5442 で実欠陥修正）と
   同一 fingerprint（章×kind 粒度）・同一ラン・同月起票である。本件は城候補 `不明`
   （マーカー結合の欠落、`MAP_CASTLE_TOLERANCE` 外 — VERIFY-5418 §5 と同じ読み）のため
   4 件中唯一「免除も boss_move もない純粋な recall 対象」であり、適用できる
   修正結論が最も少ない。#5421 の再発コメントにはレモンの別 incident
   `2519afe428275aa8dce6`（ch4 8-1、first 56569、`monthly_scan_hold` 付き、
   観測 373 回 / 521.8 秒）があり、野営がその後も残ったか新規滞在として
   再検出されたことを示す。「既存修正で十分」と言い切る根拠にはならない
   （VERIFY-5430 §7 と同じ扱い）。

以上から (a) / (b) / (c) を排他し、**(d)** を採用する。

---

## 1. issue 提示値 → コード対応（根拠の照合）

| issue の値 | 対応箇所（`origin/main` = v1477） | 検証結果 |
|---|---|---|
| 判定 `camp_abandoned` / kind / `auto_file: true` | `month_audit.py` L1529-1531, L1588 | 一致 |
| `confidence: high` / 判定理由 なし | 同 L1531-1532（camp_abandoned は high 固定・理由配列空。`_confidence()` L3026-3051 を通らない） | 一致（記録形の特性） |
| 月 ch4 8-1 / 観測 1 回 / 0.0 秒 / max gap 0.0 秒 | 同 L1518（`_month_summary` L3054-3074 を転記） | 一致 |
| 野営 3 ヶ月 / 初回 7-11 (55243) | `CAMP_ABANDON_MONTHS = 3` L55、`_camp_age_months` L1488-1497、`_track_camp_abandon` L2502-2575 | 一致（7-12・8-1 の 2 月開始をまたぎ 1+2=3） |
| HP 27/27 / 卵 ワンダーエッグ / broken false / `キャンプしています` | 同 L1536-1550（statuses row を転記） | 一致 |
| castle_candidate `null`（城候補「不明」） | 同 L1546-1547（`marker.castle_candidate` を転記）＋ `_boss_camp_holds_siege` L129-131 | 一致。免除不成立の根拠（§2） |
| `camp_stay.last_tick = 55243`（first_tick と同値） | 同 L1549（row の `tick`） | 一致。名簿行が初回読取から未更新（§6、#5032 改善案 3 の残項目） |
| 出撃 確定0 / 発射0 / order_start0 / order_launched0 | 同 L1551-1557 | 一致 |
| signals `camp_abandoned` / `camp_abandon_3months` | 同 L1511-1515（基本 2 個＋`_signals()` L2974-3023、最大 8 個） | 一致 |
| incident_id `a7241256cbc702713ec1` | 同 L1582-1584: `sha256(f"{runtime_id}|{generation}|camp_abandoned|{general}|{first_tick}")[:20]` | 算式一致（再計算は環境制約で未実施。入力 `g1342-1fe0734c|1342|camp_abandoned|レモン|55243`。同算式の再計算一致は VERIFY-5420 §1 で確認済み） |
| fingerprint `3bd148295593ce20` | 同 L1586-1587 → `_fingerprint` L3089-3091: `sha256("4|camp_abandoned|camp_abandoned")[:16]` | 入力は兄弟 issue と完全同一のため同一値になる（#5418/#5420 で再計算一致済み、VERIFY-5418 L14-15 記載） |
| issue タイトル格式 | `_issue_title` L4119-4122: `[半熟英雄] 野営放置: ch{章} {将軍} ({months}ヶ月)` | 一致 |
| flow `recall {stage:'to_camp', steps:0}` / `recall_retry {kind:'dome'}` / `retreat_anonymous_budget {reads:2}` | `_summarize_flow` L433-473 の投影（recall は stage/steps/target/kind） | 一致。帰還往路が存在したことの証跡 |

監査が起票する incident はラン成果物から事後生成される記録であり、この一致は
「issue の数値が検出ロジックから改変なく来ていること」を示す。
当該将軍が起票時点で実際に野営していたかを直接証明するものではない（§6）。

## 2. 経路判定 — castle_candidate `null` がすべてを分ける

- 世界状態: 奪取 6 城＋本国アルマムーンで通常城は全確保、残る敵目標は四季王サイトのみ
  = ch4 ボスフェーズ。`boss_camp_exempt` 機構は有効。
- `_boss_camp_holds_siege`（`post_month_status.py` L116-132）:
  `marker.castle_candidate` が `is_boss_castle` の str でかつ `broken is not True` を
  要求する。レモンは `castle_candidate = null` → 第一条件で不成立。
  → `_tasks` L230-249 の免除/boss_move 分岐に入らず、L250 の
  `queue.append({'kind': 'recall'})` へ送られる。
- したがって兄弟 4 件の分類は:
  - #5418 リーキ / #5421 キッシュ: `四季王城(北東)` かつ無傷 → 免除 → `boss_move` 経路
    （#5418 = (a) goal 未解決クラッシュ、#5421 = 免除カウント消失の実欠陥 → #5442）
  - #5420 デュオニソス: `四季王城(北東)` だが broken → 免除外 → recall 経路（(b)）
  - **#5419 レモン: candidate なし → 免除外 → recall 経路（本件）**
- `first_month 7-11` / `first_tick 55243` はリーキ 55206・キッシュ 55227・
  デュオニソス 55233 とほぼ同時刻であり、北東サイト近辺の同一集団野営に
  参加していた可能性は高いが、彼女の名簿マーカーは城に結合しなかった
  （`MAP_CASTLE_TOLERANCE` 外 or 遮蔽）。免除の対象外である以上、
  「包囲の一部として放置」する設計は彼女には適用されない。

## 3. 各修正のカバー範囲（本件への適用可否）

| 修正 | 内容 | 本件への適用 |
|---|---|---|
| v1469（#5424 / `7db52e963`） | navigation の goal 検証（`goal_unresolved`）、`post_month_status._boss_move_goal`・`recall._boss_move_goal` が実測サイト名を要求、fault_issue 自動起票 | **非適用**。クラッシュは boss_move の `'ボス'` goal でのみ発生。レモンは recall 経路で goal は実在の自城セル。かつクラッシュ実測 22:14 JST > 起票 22:00:53 JST で、3 ヶ月滞留の原因になりえない |
| v1470（#5432 / `90b35600e`） | ch4 四季王サイト注文の `_ready` 短絡除去・200 tick 有界化、SEASON_KINGS をボス戦証拠に算入 | **非適用**。ボス出撃注文側の修正。野営将軍の帰還ではない |
| v1471（#5141 / `975090bcb`） | 帰還の到着確認として due 城の名簿読みを全章で起票（`strategy/replan.py`・`routes.py`） | **診断機構**。「dispatched なのに未着」を検出する probe で、滞留を解消する修正ではない。残件調査（§7）には関連 |
| v1477（#5442 / `911ffbb55`） | 免除カウントの (将軍,月) 冪等化・監査 `camp_watch_hint` シード、`task_recall` 付与と `release_for_task` による古い再確認行の破棄、再確認変換から task_recall を除外 | **大部分は非適用**。免除カウント系は免除対象将軍向けで、免除不成立のレモンには無関係。再確認乗っ取りは `camp_kind != 'dome'` の除外が修正前からあり、`recall_retry.kind='dome'` の彼女の野営には元々適用されない可能性が高い（未測定なら別。incident では確定不能） |
| 既存設計（ラン時点 v1467 に存在） | `RECALL_INTERRUPT_GRACE = 240`（`post_month_status.py` L104）/`RECALL_TASK_RETRIES = 2`/`CAMP_RECALL_SCENE_RESUMES = 2`（`orchestrator.py`）、`RECALL_LIMIT = 90`/`RECALL_SKIP_TICKS = 400`/`DOME_SCREEN_WAIT = 6`（`recall.py` L20-33）、`FORT_STAGES` の砦経路（同 L2166-2249） | recall 機構は存在し試行されていた（`flow.recall`・`camp_recall_interrupted`・`recall_retry` の証跡）。「機構欠落」ではないが、失敗が毎月繰り返されたかはログ待ち |

## 4. incident から読み取れる帰還の状況証拠

- `flow.recall = {stage: 'to_camp', steps: 0}`: 帰還フローが `to_camp` 段で存在
  （作成直後、または `recall.py` L1479-1484 の非操作 map 画面ホールドで steps が
  進まない状態）。`target`/`kind` は `_summarize_flow` の投影対象外形のため不明。
- `recall_retry = {kind: 'dome'}`: 直前の失敗が **dome（砦）型野営**に対する帰還で、
  `_arm_recall_skip`（`recall.py` L277-299）により scoped skip（上限 400 tick、
  繰り返しで逓増）が掛かっている。dome は `enter_fort`（L2202-2204）→
  `FORT_STAGES`（しゅつげき経路）の別系統メニューを使う。
- `retreat_anonymous_budget.reads = 2`: 匿名退却再確認が 2 読を消費
  （誰の行かは incident から不明。予算は共有）。
- `camp_recall_interrupted`（13:00:46）: 月初の舞台場面が帰還先ピッカーを閉じて
  中断 → `RECALL_INTERRUPT_GRACE` 内の無償再試行対象（#4468 修正済み領域）。
  その直後に `month_seen`/`month_plan`/`army_count_check`（13:00:53）が続き、
  月次処理が前景を持っている。
- つまり帰還機構は月境界をまたいで作動していたが、`to_camp` 段までの 3 ヶ月分の
  各失敗理由（メニュー不成立・到着未確認・scene 中断・scoped skip 連続）は
  本 incident からは一意に決められない。

## 5. 兄弟 issue・再発コメントとの関係

| issue | 将軍 | HP/max | 卵 | broken | castle_candidate | 経路 | 判定（既 VERIFY） |
|---|---|---|---|---|---|---|---|
| #5418 | リーキ | 30/30 | カラフルエッグ | False | 四季王城(北東) | 免除→boss_move | (a) v1469 で修正済み |
| **#5419** | **レモン** | **27/27** | **ワンダーエッグ** | **False** | **なし** | **recall** | **(d) 本記録** |
| #5420 | デュオニソス | 6/54 | なし | True | 四季王城(北東) | recall（免除外） | (b) |
| #5421 | キッシュ | 26/26 | スーパーエッグ | False | 四季王城(北東) | 免除→boss_move | (a) #5442 で修正済み |

共通: ラン `g1342-1fe0734c` / gen 1342 / v1467 / ch4 8-1 / 観測 1 回 0.0 秒 /
出撃 0 / 決定列 15 件 / fingerprint `3bd148295593ce20` / created_at 同一。

- #5421 のコメントにある再発（22:09 JST 頃付記）: レモン `2519afe428275aa8dce6` /
  キッシュ `207ef50ee0e2e028d9a3` / リーキ `e261a0ca5e076693805d`（いずれも ch4 8-1、
  first 56569、`monthly_scan_hold` 付き、観測 373 回 / 521.8 秒）と、
  キッシュの 8 ヶ月再発 `3e036acee9eb025d5968`（ch4 8-8）。
  デュオニソスは含まれない。incident_id が本件と異なる別発生であり、bot_version は
  コメント未記載のため v1469 以降の残存経路かどうかはコメント単体では断定不能
  （VERIFY-5420 §5 / VERIFY-5430 §6 と同じ限界）。
- 同じ判定枠組みを持つ先行記録: ch12 の #5429 / #5430 / #5431 はいずれも
  「発火は契約どおり・#5418 型でない・recall/dome 経路の真因は read-only では
  確定不能」で **(d)**。本件は ch4 だが経路構造（非免除の recall・dome 野営・
  再発あり）が同型であり、同じ結論が妥当である。

## 6. 他の判定を採らない理由

- **(a) でない**: 本件が参照できる唯一の特定済み実欠陥は #5418 型の
  boss_move goal クラッシュだが、(i) レモンの経路は recall で goal は自城の実在セル、
  (ii) クラッシュ実測は起票後（22:14 JST > 22:00:53 JST）で、7-11→8-1 の滞留を
  説明しない。滞留側に別の実欠陥（例: dome 帰還の未完了ループ、再確認乗っ取りの
  camp_kind 未測定経路）がある可能性は残るが、incident 単体では「設計どおりの
  bounded 失敗・中断」と「実欠陥」を分離できない。
- **(b) でない**: 発火契約どおりであることと「既存修正でカバー済み」は別命題。
  v1469/v1470/v1471/v1477 のいずれも本件の滞留経路を直接カバーせず（§3）、
  同一将軍の再発 incident（`2519afe4…`）も存在するため「修正済み・設計どおり」と
  言い切る根拠がない（VERIFY-5430 §7 と同じ判断）。
- **(c) でない**: `camp_abandoned` / months=3 / signals / incident 構成は
  すべてコード算式どおりで、`camp_observed = true` の実測投影。誤検知ではない。
  ただし `camp_stay.last_tick = first_tick = 55243` で名簿行は初回読取から未更新
  （`CAMP_ROW_STALE_TICKS = 600` 超、#5032 改善案 3 の残項目）であり、
  「起票時点でも野営中」は stale 行に依る点は留保する。
- **(d) が残る**: 発火の正当性、経路判定（非免除の recall）、各修正の非適用までは
  確定できるが、3 ヶ月滞留の直接原因（各月の帰還試行がどの段で止まったか）は
  VM 側ログが無いと一意に決まらない。

## 7. 残件（read-only では確定不能）

- `hanjuku_decisions.jsonl`: 7-11〜8-1 の `camp_recall_requested` /
  `camp_recall_dispatched` / `camp_recall_input_sent` / `camp_recall_aborted` /
  `camp_menu_not_opened` / `camp_group_screen` / `post_month_recall_requeued` /
  `post_month_task_result`（task=recall の成否）/ `camp_recall_goal_unresolved` の
  有無・件数・将軍名。
- `hanjuku_events.jsonl`: 帰還 A の `input_sent` 行（真に未送信か receipt 不整合か）。
- `general_statuses.statuses['レモン']`: tick が 55243 のまま 8-1 まで更新されなかった
  理由（`CAMP_ROW_STALE_TICKS = 600` の再読が走ったか。走って失敗したか）。
- 再発 incident `2519afe428275aa8dce6`（レモン、first 56569、`monthly_scan_hold`）の
  runtime_id / bot_version（v1467 継続か、v1469+ 適用後の残存経路か）と、
  `monthly_scan_hold` が月次スキャンの長時間占有を意味する実害だったか。
- レモンの野営 `camp_kind`: 実際に `'dome'` なら v1477 の再確認破棄は無関係と確定、
  未測定なら再確認乗っ取り経路の可能性を再評価。

記録: 本ファイル（VERIFY-5419.md）。issue への結論コメント投稿・PR 化は
検品ゲートが行う。本レーンはコード変更・push・PR を作成しない（read-only 検証のみ）。

## 付録: 判定シグネチャ

- event / kind: `camp_abandoned`
- signals: `camp_abandoned`, `camp_abandon_3months`
- confidence: `high`（固定）
- incident_id: `a7241256cbc702713ec1`
- fingerprint: `3bd148295593ce20`
- 結論: **(d) 判定不能**（発火は契約どおり。#5418 型 (a) は時系列・経路とも非適用、
  v1469/v1470/v1471/v1477 非対象の recall・dome 経路。真因はラン成果物待ち）
