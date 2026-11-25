# VERIFY-5430 — [半熟英雄] 野営放置: ch12 リゴット (3ヶ月)

対象: docich#5430（`bug` / `auto-generated` の監査自動起票）
ラン `g1190-ch1v9` / generation `1190` /
`hanjuku-chart-v1468-battle-menu-bounded-episode` / 月 `ch12 19-10`
occurrence(incident_id) `a8690e1c3a46dca4ffca` / fingerprint `29609957117c9004`
created_at `1795529824.090715`（2026-11-24 14:17:04Z）

判定: **(d) 判定不能** — 発火は契約どおり。#5418 型 (a) の `boss_move` goal 未解決ではなく、
v1469 / v1470 の修正対象でもない別機構（ch12 の dome 野営の帰還ディスパッチ未確認／
scoped skip 再試行）。read-only では 3 ヶ月滞留の真因を一意に確定できない。

調査基盤: worktree `/home/hatch/work/wt/docich/devincli-5430`
（`origin/main` = `90b35600e` = `hanjuku-chart-v1470-ch4-site-bounded-wait` から detach）。
ラン時点の version は v1468 で、現行 main は v1470（+2）。ラン v1468 のコードは
`/home/hatch/work/docich-5420`（detach `da8d38eb`, version.py = v1468）を併読した。
read-only 検証: 本番 SSH なし / ラン成果物 `hanjuku_decisions.jsonl`・`hanjuku_events.jsonl`・
`hanjuku_frames/` は VM 側で直接参照せず / 秘密情報なし / コード変更・push・PR なし。
根拠は gh issue 本文・コメント、`origin/main` と v1468 のコード、git history、過去 VERIFY 記録。
`handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読。

---

## 結論

**(d) 判定不能。** 根拠は次の 5 点。

1. **発火自体は契約どおりで、誤検知ではない。**
   `CAMP_ABANDON_MONTHS = 3`（`month_audit.py` L55）を閾値に、
   `_track_camp_abandon`（L2487-2556）が 3 ヶ月以上 camp の将軍を検知し、
   `_build_camp_abandon_incident`（L1500-1588）が incident を起票する。
   本件の incident_id と fingerprint はコードの算式から再計算して一致した（§1）。
   `camp_stay.camp_observed = true` で名簿の実測値を投影しており、静的雛形ではない。

2. **#5418 の (a) 実欠陥（boss_move goal 未解決）とは別機構。**
   #5418 は ch4 で、将軍がボスサイト `四季王城(北東)` に隣接し無傷・卵ありだったため
   包囲免除 → `boss_move` 経路になり、その goal が `chart.boss_castle(4) = 'ボス'`
   （ch4 には同名セルが無く 4 つの四季王城に分散）だったため `_y_jump_step` の
   `boss_probe_cell()` が `None` アンパックで TypeError → brain が毎観測 exit 2 となり
   全入力が停止した（VERIFY-5418.md / #5424 コミット）。
   本件は ch12 で、城候補 `ワフェルダ` は**確保済みの通常城**でありボスサイトではない
   （`is_boss_castle('ワフェルダ') = False`、§2）。したがって `_boss_camp_holds_siege`
   は成立せず `boss_move` ではなく `recall` ジョブに分類される。
   また ch12 は `CASTLES[12]['ボス']` が `APPROX_BOSS_CELLS[12]` として存在するため
   （`hanjuku_chart.py` L460-473）、`boss_probe_cell` の解決不能クラッシュ自体が
   ch4 固有の問題である。本件の `flow.recall_verification` は
   `{stage: 'await_dispatch', status: 'dispatched', steps: 1}` で、
   「帰還を試したが到着未確認」の形であり、goal 未解決・steps 0 のクラッシュ形ではない。

3. **v1469 / v1470 の修正対象外。**
   - v1469（#5424 / `7db52e963`）: ch4 の `boss_move` を名簿実測サイトへ解決、
     未測定なら包囲免除を維持し `'ボス'` への Y ジャンプを発行しない ＋
     `goal_unresolved` 記録 / fault_issue 自動起票。本件の将軍は recall 経路で、
     ch12 の `'ボス'` は実在セルを持つため、この修正に該当する欠陥を持たない。
   - v1470（#5432 / `90b35600e`）: 第4話の四季王サイト注文の `_ready` 短絡を外し
     200 tick 締切へ載せる ch4 専用修正。ch12 には適用されない。
   つまり #5418 型 (a) の「v1469 で修正済み」を本件へそのまま転用できない（§3）。

4. **シグナル 4 件の内訳は「実在 2 + 記録アーティファクト 1 + 実況 1」。**
   `signals = ['camp_abandoned', 'camp_abandon_3months', 'battle_grind', 'stuck_march']`。
   - `battle_grind`: `battle_count >= 20`（`month_audit.py` L2953）。決定内訳の
     battle 系は 69 件、`stats.battles_judged = 16` で実在。
     直前決定の `battle_retreat_banned`（「退却は停滞の原因になるため一律禁止し、
     切り札・卵を使い切った後もそのまま戦い切る」）と ch12 ボス出撃（target `'ボス'`）
     と整合する。
   - `stuck_march`: `world.sorties[*].age_ticks > 3000`（L2971）で付くが、
     本件の該当行は `A:5f77e647:Md203ae`（age_ticks 80482, status **`arrived`**,
     target `'ボス'`）である。到着済みの軍がボス戦に張り付いているだけであり、
     `_signals` が status を判定せず age_ticks のみを見るための記録側アーティファクト。
     「行軍が停滞」というシグネチャ文字をそのまま実欠陥の証拠にしない（§4）。
   - よって (c) 誤検知でもない。監査の主判定 `camp_abandoned` は計算上正しい。

5. **真因は read-only では確定不能。**
   `camp_stay.first_tick = last_tick = 570877`（19-8 の一回の読取のみ）で、
   `_camp_generals`（L1439-1453）は status row の鮮度を検証しない。
   `flow` は dome 野営の `recall_skip_target` と `recall_retry`（doubling wait、
   `recall.py` L217-291）を示しており、「dispatch はされたが到着確認が無く、
   分類不能画面で scoped skip → 再試行」という設計内経路の可能性がある。
   一方で同ランの #5434（`camp_recall_failed`, 20-2, v1468, steps 91,
   `camp_recall_limit`）と #5437（`camp_recall_unconfirmed`, 20-4, v1469,
   `arrival_unconfirmed`）は ch12 の帰還完了側に別の問題があることを示す。
   どちらが本件の 3 ヶ月滞留の直接原因かは VM 側ログなしに一意に決まらない（§6）。

以上から (a) / (b) / (c) を排他し、**(d)** を採用する。

---

## 1. issue 提示値 → コード対応（根拠の照合）

| issue の値 | 対応箇所（`origin/main` = v1470） | 検証結果 |
|---|---|---|
| 判定 `camp_abandoned` / kind / `auto_file: true` | `month_audit.py` L1500-1588（L1585 kind, L1587 auto_file） | 一致 |
| `confidence: high` / 判定理由 なし | 同 L1516-1517（camp_abandoned は high 固定・理由配列空） | 一致（記録形の特性） |
| 月 ch12 19-10 / 観測 1 回 / 0.0 秒 / max gap 0.0 秒 | 同 L1498-1508（`_month_summary`） | 一致 |
| 野営 3 ヶ月 / 初回 19-8 (570877) | `CAMP_ABANDON_MONTHS = 3` L55、`_camp_age_months` L1473-1483、`_track_camp_abandon` L2487-2556 | 一致 |
| HP 99/99 / egg null / broken false / 状態 `キャンプのなかにいます` | 同 L1518-1533（statuses row を転記） | 一致 |
| castle_candidate `ワフェルダ` | 同上 ＋ `hanjuku_chart.is_boss_castle` L1365-1367 | 一致。**ボスサイトではない** |
| 出撃 確定3 / 発射4 / order_start4 / order_launched4 | 同 L1535-1540、`sorties` | 一致 |
| signals 4 件 | 同 L1503-1512（基本 2 個＋`_signals()` L2932-2979、最大 8 個） | 一致（内訳は §4） |
| incident_id `a8690e1c3a46dca4ffca` | 同 L1581-1584: `sha256(f"{runtime_id}|{generation}|camp_abandoned|{general}|{first_tick}")[:20]` | **再計算一致** |
| fingerprint `29609957117c9004` | 同 L1586-1587 → `_fingerprint` L3047-3049: `sha256(f"12|camp_abandoned|camp_abandoned")[:16]` | **再計算一致**（kind と primary signal のみ、signals 4 件は使わない） |
| issue タイトル格式 | `_issue_title` L4038-4041: `[半熟英雄] 野営放置: ch{章} {将軍} ({months}ヶ月)` | 一致 |

補足: fingerprint は `chapter|kind|primary` のみで決まるため、signals に `battle_grind` /
`stuck_march` が入っても #5429 / #5431 と同じ `29609957117c9004` になる。
fingerprint 一致は「ch12 × camp_abandoned」を意味するだけで、同じ原因を意味しない。

## 2. なぜ (a) ではないか — ch12 と ch4 の差

| 観点 | #5418（ch4, (a)） | #5430（ch12, 本件） |
|---|---|---|
| 城候補 | `四季王城(北東)`（ボスサイト） | `ワフェルダ`（確保済み通常城） |
| `is_boss_castle` | True | **False** |
| 経路 | 包囲免除 → `boss_move` | **`recall`** |
| goal セル | `chart.boss_castle(4)='ボス'` にセル無く解決不能 | `CASTLES[12]['ボス']` が `APPROX_BOSS_CELLS[12]` として存在 |
| brain 例外 | `boss_probe_cell` None アンパック → exit 2 毎観測 | 記録に例外形なし |
| flow.recall_verification | `{stage:'to_camp', steps:0}` | `{stage:'await_dispatch', status:'dispatched', steps:1}` |
| 修正 | v1469（#5424）で修正済み | v1469 / v1470 の対象外 |

`post_month_status._tasks`（v1468 L198-218、v1470 L210-249）は
`_boss_camp_holds_siege(info)` が真のときだけ boss_move を積み、それ以外は
`queue.append({'kind': 'recall', 'name': name})` へ送る。本件は broken false だが
location は camp、castle_candidate はボスサイトではないため recall 側に落ちる。
v1470 の `_boss_move_goal`（L148-163）は「名簿マーカーが実測した四季王サイト名」を
goal に渡す ch4 向け追加であり、本件に適用する根拠は無い。

## 3. v1469 / v1470 のカバー範囲（本件への非適用）

- v1469（#5424 / `7db52e963`）の変更点: navigation の goal 検証（`goal_unresolved`）、
  `post_month_status._boss_move_goal`、`recall._boss_move_goal`（`recall.py` L2083）、
  fault_issue 自動起票。すべて「Y ジャンプ先セルが解決できない」失敗への対処。
  ch12 の `'ボス'` は `APPROX_BOSS_CELLS` により実在セルを持つため、同失敗の前提を欠く。
- v1470（#5432 / `90b35600e`）の変更点: 第4話四季王サイト注文の `_ready` 短絡除去、
  `_boss_companion_source_wait_expired` の 200 tick 有界化、
  month_audit が ch4 の SEASON_KINGS をボス戦証拠に算入。ch4 専用。
- 両者は #5418 / #5421 とその同型 (ch4) をカバーするが、ch12 の
  recall/dome-skip 経路は対象外。「v1469 で修正済み」の結論は本件へtransfer 不成立。

## 4. シグナル `battle_grind` / `stuck_march` の読み方

- `battle_grind`: `counts` の battle 系合計が 69（`decisions.counts`）、
  `stats.battles_judged = 16` → 実況として正しい。直前決定の
  `battle_retreat_banned` / `battle_card_announce_retry` / `battle_card_list_unclassified`
  は「退却禁止で切り札を使い切るまで戦う」設計と、長引く戦闘の記録と整合する。
  これは ch12 ボスフェーズ（target `'ボス'` への複数出撃、launched 4）の一部分であり、
  camp 放置の原因を単独で証明するものではない。
- `stuck_march`: `_signals` L2968-2971 は `sorties.values()` の `age_ticks > 3000` を
  status に関係なく拾う。該当行は `A:5f77e647:Md203ae` の `arrived` / 80482 tick
  （target `'ボス'`）で、到着して戦闘中である。真に停滞している `en_route` は
  `A:5f77e647:M085c5c` の 1849 tick で閾値 3000 未満。
  「行軍が停滞」は記録側のラベルであって、本件の実害ではない。
- 監査 v1468 はこの `stuck_march` を fingerprint に含めない（§1）ため、
  シグナル 4 件でも #5429 / #5431 と同型 3 件として扱われる。

## 5. 同型 3 件（fingerprint `29609957117c9004`）

| issue | 将軍 | HP/max | 卵 | broken | first_tick | incident_id |
|---|---|---|---|---|---|---|
| #5429 | エピィ | 57/57 | スロットエッグ | False | 570864 | `cd2559a3cdefdbeaf107` |
| **#5430** | **リゴット** | **99/99** | **None** | **False** | 570877 | `a8690e1c3a46dca4ffca` |
| #5431 | ポセイドン | 85/85 | None | False | 570852 | `d27b8de1bc73319b6ef7` |

共通: ラン `g1190-ch1v9` / gen 1190 / v1468 / ch12 19-10 / 観測 1 回 0.0 秒 /
signals 4 件同一 / 城候補 `ワフェルダ` / 出撃 確定3・発射4（#5429/#5431 は発射6）/
created_at 同一。差は将軍のステータスのみで、機構は同一と判断する。
本件 (d) の結論は #5429 / #5431 にもそのまま適用できる（個別 issue の検証は別途）。

## 6. 再発コメントと帰還側の関連 issue

- #5431 に azumag が付した再発コメント（2026-11-24 14:30:24Z）:
  `再発: ch12 20-1` / 野営 3 ヶ月 / **リゴット** / first 577079 /
  incident_id `0fc67465c9c12ab70cf8` / signals は「野営放置 / 3ヶ月以上野営 /
  戦闘が長引く」（stuck_march なし）。fingerprint は同じ。
  これは**同じ将軍の新しい滞在**（`reported` フラグは滞在単位なので再起票される）
  であり、19-8 の一行が stale だっただけではないことを示唆する。
  ただし再発の bot_version はコメントに記載が無く、v1469 / v1470 適用後の
  残存経路かどうかはコメント単体では断定不能。
- 同じランの帰還側監査:
  - #5434 `camp_recall_failed` / ch12 20-2 / **v1468** / `camp_recall_aborted` /
    `camp_recall_limit` / stage `fort_status` / steps 91 / target_cell None。
  - #5437 `camp_recall_unconfirmed` / ch12 20-4 / **v1469** / `arrival_unconfirmed` /
    `camp_recall_input_sent` あり / `camp_recall_goal_failed`。
  前者は v1468 の有限待ち断念、後者は v1469 適用後も残る到着未確認であり、
  本件 (3 ヶ月 camp → recall dispatched → 到着未確認 → dome scoped skip) と
  同じ帰還完了層の問題である可能性が高い。ただし本件の incident には
  `camp_recall_*` 決定が 30 行の before 窓に無く、直接の因果はログ待ち。

## 7. 他の判定を採らない理由

- **(a) でない**: #5418 型の実欠陥は ch4 の goal 未解決 → brain exit 2 であって、
  本件は ch12 で recall 経路かつ `'ボス'` セルが存在する。例外形の証拠も無い。
  帰還完了層に実欠陥がある可能性はあるが、本件の incident 単体では
  「設計どおりの bounded skip」と「実欠陥」を分離できない。
- **(b) でない**: v1469 / v1470 は ch4 専用で本件をカバーしない。
  また 20-1 の再発（新規滞在）は「既存修正で十分」と言い切る根拠にならない。
- **(c) でない**: camp_abandoned / 3ヶ月 / incident_id / fingerprint はすべて
  コード算式どおりで、`camp_observed = true`。主判定は誤検知ではない。
  `stuck_march` は記録側アーティファクトだが fingerprint には入らず、
  主判定の正当性を覆さない。
- **(d) が残る**: 発火の正当性と「#5418 型ではない」ことまでは確定できるが、
  3 ヶ月滞留の直接原因（帰還 A 送信の有無 / 到着 / dome skip 収束 / status row 鮮度）
  は VM 側ログが無いと一意に決まらない。判定は runtime 受入まで Open を維持する。

## 8. 残件（read-only では確定不能）

- `hanjuku_decisions.jsonl`: 19-8〜19-10 の `camp_recall_requested` /
  `camp_recall_input_sent` / `camp_recall_dispatched` / `camp_recall_aborted` /
  `camp_group_screen`（`unclassified_screen_closed`）/ `post_month_recall_requeued` /
  `camp_recall_goal_unresolved` の有無と件数。
- `hanjuku_events.jsonl`: 帰還 A の `input_sent` 行（真に未送信か receipt 不整合か）。
- `general_statuses.statuses['リゴット'].tick`: 570877 の行が 19-10 まで更新され
  なかった理由（`CAMP_ROW_STALE_TICKS = 600` の再読が走ったか、#5032 改善案 3）。
- 再発 incident `0fc67465c9c12ab70cf8` の bot_version と、v1469 / v1470 適用後の
  同一将軍・同一 ch12 滞在が 4 ヶ月目以降も出るか（出るなら残存経路として再評価、
  判断は DSH に委ねる）。

記録: 本スレッド（agent-council）と issue #5430。
PR 化と CI green 確認は検品ゲート（メイン Muse）が行う。
本レーンはコード変更・PR を作成しない（read-only 検証のみ）。

## 付録: 判定シグネチャ

- event / kind: `camp_abandoned`
- signals: `camp_abandoned`, `camp_abandon_3months`, `battle_grind`, `stuck_march`
- confidence: `high`（固定）
- incident_id: `a8690e1c3a46dca4ffca`
- fingerprint: `29609957117c9004`
- 結論: **(d) 判定不能**（#5418 型 (a) でなく、v1469 / v1470 非対象の別機構）
