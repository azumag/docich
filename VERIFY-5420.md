# VERIFY-5420 — [半熟英雄] 野営放置: ch4 デュオニソス (3ヶ月)

対象: docich#5420（`bug` / `auto-generated` の監査自動起票）
ラン `g1342-1fe0734c` / generation `1342` /
`hanjuku-chart-v1467-status-panel-owner-unreadable-egg` / 月 `ch4 8-1`
occurrence(incident_id) `380d9c0f82efe04a43b2` / fingerprint `3bd148295593ce20`
created_at `1795525253.5534441`（2026-11-24 13:00:53Z）

判定: **(b) 設計どおりまたは既存修正でカバー済み**

調査基盤: worktree `/home/hatch/work/docich-5420`（`origin/main` = `4692ff34b9` から detach）。
`src/docich/hanjuku/version.py` L7 の `BOT_VERSION` が `hanjuku-chart-v1467-status-panel-owner-unreadable-egg`
で issue の bot_version と一致するため、現行 main の読解をラン時点の契約へそのまま適用できる
（過去 verify ブランチを読むことによる version drift は無い）。
read-only 検証: 本番 SSH なし / ラン成果物 `hanjuku_decisions.jsonl`・`hanjuku_events.jsonl`・
`hanjuku_frames/` は VM 側で直接参照せず / 秘密情報なし / コード変更・push・PR なし。
根拠は gh issue 本文・コメント、`origin/main` のコード、git history、過去 VERIFY 記録。

---

## 結論

**(b) 設計どおりまたは既存修正でカバー済み。** 根拠は次の 5 点。

1. **監査シグナル自体は実在し、設計どおりの記録である。**
   `CAMP_ABANDON_MONTHS = 3`（`month_audit.py` L55）を閾値に、`_track_camp_abandon`
   （同 L2487-2556、L2548）が 3 ヶ月以上 camp の将軍を検知し、`_build_camp_abandon_incident`
   （同 L1485-1573）が incident を起票する。issue の incident_id と fingerprint はコードの
   算式から再計算して一致した（§1）。監査は入力を送らず計画も変更しない証拠記録であり、
   欠陥フラグではない。同種の `camp_abandoned` を検証した #4976 の CLINE read-only 結果
   （「設計どおり（by design）、欠陥ではない」）と同じ分類になる。

2. **同一将軍・同一章の先行ケースが既に同じ判断で閉じられている。**
   #5022「野営放置: ch4 デュオニソス (3ヶ月)」（ラン `g1190-ch1v5` / v1352）は本件と同じ
   将軍・同じ章・同じ fingerprint で、owner は
   「この野営放置の記録は、終了したランでの発生記録です。根本対応は #5032（到着確認, v1367）
   と #5000（月次巡回の明け渡し上限, v1356）で入り、本番で稼働中です。同じ将軍が再び3ヶ月
   野営になれば監査が自動で再起票します」として close した。#5420 は予告されていた
   「自動再起票」そのものであり、新たな未修正欠陥を自動的に意味しない。

3. **`HP 6/54・卵なし・壊れ True` は放置を正当化する例外ではなく、むしろ帰還対象側に
   正しく分類されている。**
   `_boss_camp_holds_siege`（`post_month_status.py` L115-131）は
   `info.get('broken') is not True` を要件とし、コメント（L119-125）は
   「broken な将軍は包囲免除にしない。城に戻して次回の月次で修理できるようにする
   （g1153 ヴィーナス HP3/82 broken が けっかい で放置された事例）」と明記する。
   `_tasks`（同 L198-218）は broken の野営将軍に包囲免除を与えず `queue.append({'kind': 'recall'})`
   へ送る。さらに `_hp_rank`（同 L233-243）は HP/max 比で帰還ジョブをソートし、
   6/54 ≒ 0.11 の本将軍を帰還キューの先頭に置く。つまりコード上この条件は
   「最初に帰還されるべき将軍」であり、#3659 follow-up の修正で既にカバーされた形である。
   ch4 の `四季王城(北東)` は boss site（`hanjuku_map_sources.py` L95-101,
   `hanjuku_chart.py` L1365-1366 `is_boss_castle`）だが、broken=True なので
   `BOSS_CAMP_EXEMPT_MONTHS = 3` の包囲免除には当たらない。

4. **月次処理のマップ占有・行軍停滞は、本件のシグナルとしては立っていない。**
   `_signals`（`month_audit.py` L2917-2968）では `monthly_scan_hold` は
   `post_month_status` / `month_sub_step` 等のカウント（L2931-2933）、
   `stuck_march` は行軍の `age_ticks > 3000`（L2953-2956）で付く。本件の signals は
   `camp_abandoned` / `camp_abandon_3months` のみで、これらは含まれない。
   世界状態の行軍 `I:81e158:04114122` は `en_route` / `age_ticks 164` であり、
   停止閾値 3000 を大きく下回る。直前決定には `camp_recall_interrupted`
   （月初の場面が帰還先の選択を閉じたため帰還を中断）と `month_seen` / `month_plan` /
   `army_count_check` があり、月次が帰還の前景を奪った事実は読み取れるが、
   これを放置の単一原因と断定するシグナルは本件には無い。同月再試行の経路は
   `CAMP_RECALL_SCENE_RESUMES = 2`（`orchestrator.py` L865, L933-970 の
   `_recall_after_scene`）と `RECALL_TASK_RETRIES = 2` / `RECALL_INTERRUPT_GRACE = 240`
   （`post_month_status.py` L99-103, L2240-2262 の `wait_recall`）として設計上存在し、
   #4468 で scene cut が预算を消費しないように修正済みである。

5. **兄弟 issue との差は将軍ステータスのみで、機構は同一。**
   #5418 / #5419 / #5421 はいずれも同じラン・同じ月・同じ fingerprint・同じ incident
   生成時刻・同じ決定列で、3 ヶ月野営の検出という同一機構である（§5）。
   #5421 に付いた再発コメント（後続ラン、first 56569、観測 373 回 / 521.8 秒 /
   max gap 28.8 秒、`monthly_scan_hold` 付き）は incident_id `2519afe4…` / `207ef50e…` /
   `e261a0ca…` の別発生で、対象は レモン・キッシュ・リーキ であり デュオニソス は含まれない。
   本件 `380d9c…` と混同して月次占有を原因に転用しない。

---

## 1. issue 提示値 → コード対応（根拠の照合）

| issue の値 | 対応箇所（`origin/main`） | 検証結果 |
|---|---|---|
| 判定 `camp_abandoned` / kind / `auto_file: true` | `month_audit.py` L1485-1573（L1514-1515 event/kind, L1573 auto_file） | 一致 |
| `confidence: high` / 判定理由 なし | `month_audit.py` L1516-1517（camp_abandoned は high 固定・理由配列空。`_confidence()` L2969-2995 を通らない） | 一致（記録形の特性。§6） |
| 月 ch4 8-1 / 観測 1 回 / 0.0 秒 / max gap 0.0 秒 | `month_audit.py` L1498-1508（`_month_summary` L2997-3029 をそのまま転記） | 一致 |
| 野営 3 ヶ月 / 初回 7-11 (55233) | `CAMP_ABANDON_MONTHS = 3` L55、`_camp_age_months` L1473-1483、`_track_camp_abandon` L2487-2556 | 一致 |
| HP 6 / max 54 / egg null / broken true / status_text | `month_audit.py` L1518-1533（statuses の row を転記） | 一致 |
| castle_candidate `四季王城(北東)` | 同上（`marker.castle_candidate`）＋ `hanjuku_map_sources.py` L97 | 一致。boss site だが broken で免除外 |
| 出撃 0 / 0 / 0 / 0 | `month_audit.py` L1535-1540（context.month_sorties.launches と決定カウント） | 一致 |
| signals `camp_abandoned` / `camp_abandon_3months` | `month_audit.py` L1496-1504（基本 2 個＋`_signals()`、最大 8 個） | 一致 |
| incident_id `380d9c0f82efe04a43b2` | `month_audit.py` L1568-1569: `sha256(f"{runtime_id}|{generation}|camp_abandoned|{general}|{first_tick}")[:20]` | **再計算一致**（`g1342-1fe0734c|1342|camp_abandoned|デュオニソス|55233`） |
| fingerprint `3bd148295593ce20` | `month_audit.py` L1571-1572 → `_fingerprint` L3032-3034: `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]` | **再計算一致**（`4|camp_abandoned|camp_abandoned`） |
| issue タイトル格式 | `_issue_title` L4038-4041: `[半熟英雄] 野営放置: ch{章} {将軍} ({months}ヶ月)` | 一致 |

監査が起票する incident はラン成果物から事後生成される記録であり、この一致は
「issue の数値が検出ロジックから改変なく来ていること」を証明する。
逆に、当該将軍が当月末に実際に野営していたかを直接証明するものではない（§7）。

## 2. camp_abandoned 検出の経路

- `month_audit.py` L1439-1453 `_camp_generals`: `general_statuses.statuses[*]` のうち
  `location == 'camp'` かつ `location_observed is not False` かつ `hp > 0` を現在の野営とする。
- 同 L1473-1483 `_camp_age_months`: `camp_status_seen.actors[将軍].tick` を初回 tick とし、
  `month_durations` から作った月開始 tick の数から継続月数を算出（上限 24）。
- 同 L2487-2556 `_track_camp_abandon`: 月が変わるたび `camp_watch` の months を递增し、
  `months >= 3` で `_build_camp_abandon_incident` を 1 回だけ呼ぶ（`reported` フラグ）。
- 同 L1516 は `confidence: 'high'` を固定で書き、理由を空にする。したがって
  「観測 1 回 / 0.0 秒」でも high になるのは `_confidence()` を通さない設計による。

本件の `first_tick = last_tick = 55233` は、この将軍の野営が 1 回のステータス読取で
記録され、その後同じ行が更新されなかったことを示す。`_camp_generals` は row の tick
鮮度を要求しないため、月境界（当月初 tick 56198、差分 965 tick）でも野営として扱われる。
片や月次側には `CAMP_ROW_STALE_TICKS = 600`（`post_month_status.py` L98, L330-341）
による古い camp 行の再読経路があり、965 tick はこれを超える。再読が成功していれば
起票前に status が更新されたはずだが、本件の row は古いままである。
この「古い status で確定する」点は #5032 の改善案 3（stale status なら未確認として保留）
として提起されており、#5054（v1367）で到着確認側が修正された。read-only では
再読が試みられたか否かを確定できない（§7）。

## 3. 月次処理のマップ占有・行軍停滞との関係

- 本件の signals は 2 個のみ。`monthly_scan_hold` は `_signals` L2931-2933 の条件
  （`post_month_status` / `post_month_status_close_probe` / `month_sub_step` /
  flow の `post_month_status` / `month_sub`）が 120 秒窓の決定に無いため付かない。
  `month_seen` / `month_plan` / `army_count_check` は月次画面の決定だが、
  この判定条件の対象外である。
- `stuck_march` は L2953-2956 の `age_ticks > 3000`。本件の行軍は 164 tick で該当しない。
  「行軍中」であることと「行軍停滞」は別であり、本件は前者のみ。
- 直前決定に `camp_recall_interrupted` がある。これは `orchestrator.py` L2040-2062 の
  scene cut 処理で、`_recall_after_scene`（同 L933-970）が
  `scene_resumes` を進め、`CAMP_RECALL_SCENE_RESUMES = 2` を超えた場合にのみ
  野営を skip する。skip 時にも `flow.recall_interrupted` を刻み、
  `post_month_status.wait_recall`（L2240-2262）が同月内に再キューする。
  つまり「月初の場面が帰還を中断した」こと自体は既知の想定内事象であり、
  それだけで実欠陥にはならない。
- #5421 のコメントにある `monthly_scan_hold` は後続ランの別 incident に付いており、
  本件の fingerprint 共有は「同じ章・同じ kind」を意味するだけで、
  同じ原因・同じ防止策の適用を意味しない。

## 4. HP 6/54・卵なし・壊れ True の意味

- broken=True → `_boss_camp_holds_siege`（L115-131）が False → 包囲免除なし →
  L218 で `recall` ジョブ化。#3659 follow-up（ヴィーナス HP3/82 broken）の修正済み領域。
- 卵なし → 包囲中の `boss_move`（L215-216）にも進まず、城に戻った後に `repair` ジョブ
  （L226-227）が積まれる経路になる。卵がある #5418/#5419/#5421 とここで分岐する。
- HP 6/54（約 0.11）→ `_hp_rank`（L233-243）で帰還キュー先頭。
  #3008（ヘラ HP 20→10→2）の修正済み領域。
- すなわち提示された 3 条件は「放置が正当化される根拠」ではなく、
  「既存修正が最も優先して帰還しようとする条件」である。

## 5. 兄弟 issue との異同（fingerprint `3bd148295593ce20`）

| issue | 将軍 | HP/max | 卵 | broken | castle_candidate | status_text | first_tick | incident_id |
|---|---|---|---|---|---|---|---|---|
| #5418 | リーキ | 30/30 | カラフルエッグ | False | 四季王城(北東) | キャンプしています | 55206 | `9e7f3cea5a3af126a9ed` |
| #5419 | レモン | 27/27 | ワンダーエッグ | False | なし | キャンプしています | 55243 | `a7241256cbc702713ec1` |
| **#5420** | **デュオニソス** | **6/54** | **なし** | **True** | 四季王城(北東) | キャンプしています | 55233 | `380d9c0f82efe04a43b2` |
| #5421 | キッシュ | 26/26 | スーパーエッグ | False | 四季王城(北東) | キャンプのなかにいます | 55227 | `876cc932ae553d32be64` |

共通: ラン `g1342-1fe0734c` / gen 1342 / v1467 / ch4 8-1 / 観測 1 回 0.0 秒 /
出撃 確定0・発射0・order_start0・order_launched0 / 決定列 15 件（内訳も同一）/
fingerprint と created_at 同一。相違は上表の将軍ステータスのみ。

- broken なのは #5420 のみ。#5418/#5421 は boss site 隣接かつ intact で、
  `BOSS_CAMP_EXEMPT_MONTHS = 3` の包囲免除の対象になりうる（3 ヶ月時点で免除期間中）。
  #5419 は castle_candidate が無く免除対象外。
- #5421 にのみ再発コメント 3 件（後続ラン、first 56569、373 回 / 521.8 秒 /
  `monthly_scan_hold`）が付くが、incident_id は本件と異なる別発生である。

## 6. 既存修正のカバー状況（すべて v1467 に含まれる）

| 修正 | 内容 | 本件との関係 |
|---|---|---|
| #5000 / #5006 (v1356, commit `b5aa2b875`) | `SORTIE_FLOOR_IDLE_YIELDS = 3`。出撃試行なしのマップ明け渡しが 3 回続いたら月次へ戻し、巡回で帰還・修理タスクを積む（`post_month_status.py` L61-70, L1600-1632） | 月次巡回飢餓による帰還タスク未作成を防止。#5000 の完了条件「同一将軍の camp_abandoned が連月で再発しない」を監査で測る設計 |
| #5032 / #5054 (v1367) | dispatch 時に野営将軍を保存し、600 tick 後の名簿再読で到着確認／timeout を将軍名付きで記録 | 匿名帰還の「dispatched なのに野営が残る」を防止 |
| #3505 / #3659（follow-up 含む） | boss 隣接野営の包囲免除を 3 ヶ月で期限切れにし、broken は免除対象外、負傷者は帰還キュー先頭（`post_month_status.py` L115-131, L198-243） | HP 6/54・卵なし・broken の本件を「帰還すべき側」に分類 |
| #4468 | scene cut を_interrupt として扱い、`RECALL_INTERRUPT_GRACE` 内は予算を消費せず再キュー（`orchestrator.py` L933-970, `post_month_status.py` L99-103, L2240-2262） | `camp_recall_interrupted` 後の同月再試行を担保 |
| #4888 | 月次が `process` / `manifest` / `locate_reset` のタスク中もマップを明け渡し、月をまたいで帰還が消えるのを防止 | 月次占有と帰還の競合を防止 |

補足: 記録形の特性（新たな結論変更ではない）: camp_abandoned は `_confidence()` を通らず
high 固定のため、観測 1 回 / 0.0 秒の月でも high で起票される（§1）。また
`_camp_generals` は status row の鮮度を検証しないため、古い status で 3 ヶ月と算出され
うる（§2、#5032 改善案 3 の残項目）。監査ポリシーの変更要否は DSH 判断に委ねる。

## 7. 残件（read-only では確定不能、判定 (b) の限界）

以下は VM 側のラン成果物が無いと一意に確定できない。判定 (b) は
「監査シグナルの実在性と既存修正のカバー状況」に対する結論であり、
当該ランで帰還 A が実際に送信されたか、到着したか、`camp_recall_scene_abandoned`
まで進んだかを保証するものではない。

- `hanjuku_decisions.jsonl`: 直前 120 秒より前の `camp_recall_requested` /
  `camp_recall_dispatched` / `camp_recall_scene_abandoned` /
  `post_month_recall_requeued` / `post_month_sortie_floor_reclaimed` の有無と件数。
- `hanjuku_events.jsonl`: 帰還 A の `input_sent` 行（真に未送信か receipt 不整合か）。
- `general_statuses.statuses['デュオニソス'].tick`: 55233 の古い行が月境界まで更新され
  なかった理由（`CAMP_ROW_STALE_TICKS=600` の再読が走ったか）。

特記事項: 本件ラン v1467 は上記修正がすべて入った後のランである。同じ将軍が
v1467 以降でも 3 ヶ月野営を繰り返す場合は「既存修正では防げなかった残存経路」の
可能性を考慮し、ラン成果物を添えて再評価する（その際の判断は DSH に委ねる）。

## 付録: 判定シグネチャ

- event / kind: `camp_abandoned`
- signals: `camp_abandoned`, `camp_abandon_3months`
- confidence: `high`（固定）
- incident_id: `380d9c0f82efe04a43b2`
- fingerprint: `3bd148295593ce20`
- 結論: (b) 設計どおりまたは既存修正でカバー済み
