# VERIFY-5431 — [半熟英雄] 野営放置: ch12 ポセイドン (3ヶ月)

対象: docich#5431（`bug` / `auto-generated` の監査自動起票）
ラン `g1190-ch1v9` / generation `1190` /
`hanjuku-chart-v1468-battle-menu-bounded-episode` / 月 `ch12 19-10`
occurrence(incident_id) `d27b8de1bc73319b6ef7` / fingerprint `29609957117c9004`
created_at `1795529824.090715`（2026-11-24 14:17:04Z）

判定: **(d) 判定不能** — 発火は契約どおり。#5418 型 (a) の `boss_move` goal 未解決ではなく
（城候補ワフェルダ＝通常城のため非免除の `recall` 経路）、v1469 / v1470 / v1477 は
本件の経路に非適用、v1471 は診断機構、v1481（task_recall の goal 未解決フォールバック）だけが
章非依存で本件経路に届きうるが、incident から該当形（goal=None の非移動 dispatched）を
確定できない。read-only では 3 ヶ月滞留の真因を一意に確定できない。

調査基盤: checkout `/home/hatch/work/docich-5431`
（`origin/main` = `77372b809` = `hanjuku-chart-v1485-camp-watch-hint-identity`、detached）。
ラン時点の version は v1468 で、現行 main は v1485（+17）。v1469・v1470・v1471・
v1477・v1481・v1485 の差分は git history で個別に確認した（§3）。
read-only 検証: 本番 SSH なし / ラン成果物 `hanjuku_decisions.jsonl`・`hanjuku_events.jsonl`・
`hanjuku_frames/` は VM 側で直接参照せず / 秘密情報なし / コード変更・push・PR なし。
根拠は issue 本文・コメント（公開 issue ページ経由）、`origin/main` のコード、git history、
過去 VERIFY 記録（VERIFY-5419 / VERIFY-5421 / VERIFY-5429 / VERIFY-5430 / VERIFY-5452）。
`handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読。

補足（取得経路）: この環境では `gh` の実行が承認されなかったため、issue 本文・
コメントは GitHub の公開 issue ページ経由で取得した（issue 本文＋Activity と同等）。
sha256 再計算コマンド（python3 / sha256sum いずれも）も実行が承認されなかったため、
算式はコード（`month_audit.py` L1583-1585, L3105-3107）で確認し、fingerprint は
同一入力・同一値の兄弟 issue #5430 での再計算一致（VERIFY-5430 §1）を併せて根拠とする。

---

## 結論

**(d) 判定不能。** 根拠は次の 5 点。

1. **発火自体は契約どおりで、誤検知ではない。**
   `CAMP_ABANDON_MONTHS = 3`（`month_audit.py` L55）を閾値に、
   `_track_camp_abandon`（L2518-2591）が 3 ヶ月以上 camp の将軍を検知し、
   `_build_camp_abandon_incident`（L1501-1590）が incident を起票する。
   ポセイドンは `months = 3`・`first_tick = 570852`（19-8 の一回の読取）で閾値到達。
   `camp_stay.camp_observed = true` で名簿の実測値（HP 85/85・卵 null・
   broken false・`キャンプしています`）を投影しており、静的雛形ではない。
   incident のキー構成は `_build_camp_abandon_incident` の出力と一致する（§1）。

2. **#5418 の (a) 実欠陥（boss_move goal 未解決）とは別機構。**
   #5418 は ch4 で、将軍がボスサイト `四季王城(北東)` に隣接し無傷・卵ありだったため
   包囲免除 → `boss_move` 経路になり、その goal `chart.boss_castle(4) = 'ボス'`
   （ch4 には同名セルが無く 4 つの四季王城に分散）を `boss_probe_cell()` が解決できず
   None アンパックで TypeError → brain が毎観測 exit 2（VERIFY-5418 / v1469 #5424）。
   本件は ch12 で、城候補 `ワフェルダ` は **CASTLES[12] に列挙される通常城**
   （`hanjuku_chart.py` L438-440）であり、`is_boss_castle('ワフェルダ') = False`
   （同 L1401-1402: BOSS_CASTLES.values() は {'けっかい','ボス'}、boss_sites(4) も
   四季王城のみ）。world.garrison でも `ワフェルダ: [リースリング]` と自軍城。
   よって `_boss_camp_holds_siege`（`post_month_status.py` L116-132）は不成立で
   `_tasks` L250 の `{'kind': 'recall'}` に分類される。
   また ch12 は `CASTLES[12]['ボス']` が定義済み（同 L438-440 ＋ `APPROX_BOSS_CELLS`
   L466-476）のため goal 解決不能クラッシュ自体が ch4 固有。
   本件の `flow.recall_verification` は `{stage:'await_dispatch', status:'dispatched',
   steps:1}` で、受理契約（`recall.py` L2395-2398: `input_sent && picker_closed &&
   !interrupted`）上は「ディスパッチ済み」の形であり、goal 未解決・steps 0 の
   クラッシュ形ではない。

3. **v1469 / v1470 / v1471 / v1477 / v1481 / v1485 のカバー範囲（§3）。**
   - v1469（#5424 / `7db52e963`）・v1470（#5432 / `90b35600e`）: いずれも ch4 専用。
   - v1471（#5141 / `975090bcb`）: due 城名簿読みを全章で起票する**到着確認 probe**。
     診断機構であり滞留の解消機構ではない。
   - v1477（#5442 / `911ffbb55`）: 免除カウント冪等化＋`camp_watch_hint` シードは
     **免除将軍（boss_move 経路）向け**で、免除不成立の本件には無関係。
     `task_recall`/`release_for_task` の再確認乗っ取り修正は、変換条件
     `state.get('camp_kind') != 'dome'`（`camp_recheck.py` L1023-1025）が修正前から
     存在し、本件の `recall_retry.kind='dome'` の野営には元々適用されない経路。
   - v1481（#5450 / `4f7d93e59`）: `task_recall` で `_recall_goal` が None のとき
     章の本城へフォールバック（`orders.py` L2993-3003）。**章非依存で本件の recall
     経路に届きうる唯一の修正**。しかし (i) ランは v1468 で未適用、(ii) 本件の
     `status:'dispatched'` が「goal=None でピッカーが野営地点を確定した非移動
     dispatched」（キッシュ 01:11 実測と同形）だったか真の移動ディスパッチだったかは
     incident から確定不能、の 2 点で「本件を修正済み」とは言えない。
   - v1485（#5461 / `77372b809`、現 HEAD）: camp-watch hint の run identity 照合。
     hint は免除シード専用で非免除経路に無関係。

4. **シグナル 4 件の内訳は「実在 2 + 記録アーティファクト 1 + 実況 1」。**
   `signals = ['camp_abandoned', 'camp_abandon_3months', 'battle_grind', 'stuck_march']`。
   - `battle_grind`: `battle_count >= 20`（`month_audit.py` L3010-3011）。決定内訳の
     battle 系は 69 件、`stats.battles_judged = 16` で実在。直前決定の
     `battle_retreat_banned`（退却一律禁止・切り札使い切り）と ch12 ボス出撃
     （target `'ボス'` の複数 launched 行）と整合する。
   - `stuck_march`: `world.sorties[*].age_ticks > 3000`（L3026-3029）で付くが、
     該当行は `A:5f77e647:Md203ae`（アポロン、アファニア→ボス、age 80482,
     status **`arrived`**）。到着済みの軍がボス戦に張り付いているだけであり、
     `_signals` が status を見ず age_ticks のみを見る記録側アーティファクト。
     未着の実在行は `A:5f77e647:M085c5c` `en_route` 1849t・`A:a66e1c3e:M0d35f3`
     `overdue` 1417t でいずれも閾値 3000 未満。
   - よって (c) 誤検知でもない。主判定 `camp_abandoned` は計算上正しい。

5. **真因は read-only では確定不能。**
   `camp_stay.first_tick = last_tick = 570852`（19-8 の一回の読取のみ）で、
   `_camp_generals`（L1455-1469）は status row の鮮度を検証しない。
   `CAMP_ROW_STALE_TICKS = 600`（`post_month_status.py` L99, L456）の再読経路が
   19-10 までに走ったかは VM 側ログ待ち。
   `flow` は `recall_skip_target`/`recall_retry`（いずれも `kind:'dome'`、
   `_arm_recall_skip` の doubling wait、`recall.py` L268-299）と
   `recall_verification {dispatched, steps:1}` を示しており、
   「dome 野営への帰還 dispatch は受理されたが到着が確認できず scoped skip
   → 有界再試行」という**設計内経路**の形である。一方で同ランの
   #5434（`camp_recall_failed`, 20-2, v1468, `camp_recall_limit`, steps 91）と
   #5437（`camp_recall_unconfirmed`, 20-4, v1469, `arrival_unconfirmed`）、
   ch4 の #5452（(a) 実欠陥: 防衛戦闘で dest/y_route が潰れ view モードの
   castle_menu へ入力、現行未修正）は、帰還完了層に別の問題が残ることを示す。
   どれが本件の直接原因かは VM 側ログなしに一意に決まらない（§6, §8）。

以上から (a) / (b) / (c) を排他し、**(d)** を採用する。

---

## 1. issue 提示値 → コード対応（根拠の照合）

| issue の値 | 対応箇所（`origin/main` = v1485） | 検証結果 |
|---|---|---|
| 判定 `camp_abandoned` / kind / `auto_file: true` | `month_audit.py` L1529-1531, L1589 | 一致 |
| `confidence: high` / 判定理由 なし | 同 L1532-1533（camp_abandoned は high 固定・理由配列空。`_confidence()` L3042 を通らない） | 一致（記録形の特性） |
| 月 ch12 19-10 / 観測 1 回 / 0.0 秒 / max gap 0.0 秒 | 同 L1517-1525（`_month_summary` L3070-3090 を転記） | 一致 |
| 野営 3 ヶ月 / 初回 19-8 (570852) | `CAMP_ABANDON_MONTHS = 3` L55、`_camp_age_months` L1489-1498、`_track_camp_abandon` L2518-2591 | 一致（19-9・19-10 の 2 月開始をまたぎ 1+2=3） |
| HP 85/85 / egg null / broken false / `キャンプしています` | 同 L1537-1551（statuses row を転記） | 一致。表記はエピィの「キャンプのなかにいます」とは別だがいずれも camp 状態の実測読み |
| castle_candidate `ワフェルダ` | 同上（`marker.castle_candidate`）＋ `hanjuku_chart.is_boss_castle` L1401-1402 | 一致。**ボスサイトではない** |
| 出撃 確定3 / 発射4 / order_start4 / order_launched4 | 同 L1552-1558（`context.month_sorties.launches`・`_departures`・決定カウント） | 一致（JSON の `launch_events: 6` はイベント総数で別指標） |
| signals 4 件 | 同 L1512-1516（基本 2 個＋`_signals()` L2990-3039、最大 8 個） | 一致（内訳は §4） |
| flow `recall_verification {await_dispatch, dispatched, steps:1}` / `recall_skip_target {dome}` / `recall_retry {dome}` | `_summarize_flow` L434-474 の投影 ＋ `recall.py` L2395-2398, L2424, L268-299 | 一致。受理済みディスパッチの証跡（将軍は投影外形で特定不能） |
| incident_id `d27b8de1bc73319b6ef7` | 同 L1583-1585: `sha256(f"{runtime_id}|{generation}|camp_abandoned|{general}|{first_tick}")[:20]` | 算式・入力（`g1190-ch1v9\|1190\|camp_abandoned\|ポセイドン\|570852`）と一意に対応。同一算式は #5430 で再計算一致済み（本レーンは hash 実行不可のため再計算は未実施） |
| fingerprint `29609957117c9004` | 同 L1586-1588 → `_fingerprint` L3105-3107: `sha256("12|camp_abandoned|camp_abandoned")[:16]` | 算式・入力が #5429 / #5430 と完全同一。#5430 で再計算一致済み |
| issue タイトル格式 | `_issue_title` L4142-4145: `[半熟英雄] 野営放置: ch{章} {将軍} ({months}ヶ月)` | 一致 |

補足: fingerprint は `chapter|kind|primary` のみで決まるため、signals に `battle_grind` /
`stuck_march` が入っても #5429 / #5430 と同じ `29609957117c9004` になる。
fingerprint 一致は「ch12 × camp_abandoned」を意味するだけで、同じ原因を意味しない。

## 2. なぜ (a) ではないか — ch12 と ch4 の差

| 観点 | #5418（ch4, (a)） | #5431（ch12, 本件） |
|---|---|---|
| 城候補 | `四季王城(北東)`（ボスサイト） | `ワフェルダ`（確保済み通常城） |
| `is_boss_castle` | True | **False** |
| 経路 | 包囲免除 → `boss_move` | **`recall`**（非免除） |
| goal セル | `chart.boss_castle(4)='ボス'` にセル無く解決不能 | `CASTLES[12]['ボス']` が実在 |
| brain 例外 | `boss_probe_cell` None アンパック → exit 2 毎観測 | 記録に例外形なし |
| flow.recall_verification | `{stage:'to_camp', steps:0}` | `{stage:'await_dispatch', status:'dispatched', steps:1}` |
| 修正 | v1469（#5424）で修正済み | v1469 / v1470 / v1477 の対象外（v1481 は条件付き隣接、§3） |

`post_month_status._tasks`（L201-250）は `_boss_camp_holds_siege(info)` が真のときだけ
免除/boss_move を積み、それ以外は `queue.append({'kind': 'recall'})`（L250）へ送る。
本件は broken false だが location は camp、castle_candidate はボスサイトではないため
recall 側に落ちる。なお `recall_verification` は `_flow(mem)` 単位の投影であり
将軍名を含まないため、`dispatched` がポセイドン本人の帰還だったかも確定できない。

別の (a) 候補として VERIFY-5452（ch4 アルマムーン、防衛戦闘が dest/y_route を潰し
view モードの castle_menu へ誤入力、v1482 でも未修正）があるが、その観測形は
`arrival_unconfirmed` であり、本件の `dispatched` とは形が異なる。機構は章非依存のため
ch12 の帰還でも起こりうるが、本 incident に対応する証拠は無い。

## 3. 各修正のカバー範囲（本件への適用可否）

| 修正 | 内容 | 本件への適用 |
|---|---|---|
| v1469（#5424 / `7db52e963`） | `'ボス'` goal Y ジャンプの未解決クラッシュ防止（`goal_unresolved`・fault_issue 起票） | **非適用**。recall 経路・ch12 に `'ボス'` セル実在 |
| v1470（#5432 / `90b35600e`） | ch4 四季王サイト注文の `_ready` 有界化 | **非適用**。ch4 専用 |
| v1471（#5141 / `975090bcb`） | due 城の名簿読みを全章で起票（`replan.interim_candidates`） | **診断機構**。「dispatched なのに未着」を検出する probe で、滞留を解消する修正ではない。残件調査には関連 |
| v1477（#5442 / `911ffbb55`） | 免除カウント (将軍,月) 冪等化＋`camp_watch_hint` シード＋`task_recall`/`release_for_task` で古い退却再確認行を破棄 | **非適用**。免除カウント系は免除将軍向け。再確認乗っ取りは `camp_kind != 'dome'` の除外が修正前から存在し（`camp_recheck.py` L1023-1025）、本件の dome 野営には元々適用されない。incident に `retreat_anonymous_budget` の投影も無く再確認干渉の証跡なし |
| v1481（#5450 / `4f7d93e59`） | `_apply_camp_watch_hint` を `state['policy']` 配下へ配置 ＋ `task_recall` の `_recall_goal` None → 本城フォールバック（`orders.py` L2993-3003） | **条件付きで隣接**。hint 側は免除専用。goal フォールバックは章非依存で本件の task_recall に届きうるが、(i) ランは v1468、(ii) `dispatched` が goal=None 非移動形だったか確定不能 |
| v1485（#5461 / `77372b809`、現 HEAD） | camp-watch hint を run identity で照合 | **非適用**。hint は免除シード専用 |
| 既存設計（ラン時点 v1468 に存在） | `RECALL_LIMIT=90` / `RECALL_SKIP_TICKS=400`（doubling）/ `DOME_SCREEN_WAIT=6`（`recall.py` L20-33, L1729-1742）、`CAMP_ROW_STALE_TICKS=600` の camp 行再読 | recall 機構は存在し試行されていた（`recall_verification`・`recall_retry` の証跡）。「機構欠落」ではないが、失敗が毎月繰り返されたかはログ待ち |

## 4. シグナル `battle_grind` / `stuck_march` の読み方

- `battle_grind`: `counts` の battle 系合計が 69（`decisions.counts`）、
  `stats.battles_judged = 16` → 実況として正しい。直前決定の
  `battle_retreat_banned` / `battle_card_announce_retry` / `battle_card_list_unclassified`
  は「退却禁止で切り札を使い切るまで戦う」設計と、長引く戦闘の記録と整合する。
  ch12 ボスフェーズ（`A:5f77e647:*` / `A:a66e1c3e:*` の target `'ボス'` launched 行）の
  一部分であり、camp 放置の原因を単独で証明するものではない。
- `stuck_march`: `_signals` L3026-3029 は `sorties.values()` の `age_ticks > 3000` を
  status に関係なく拾う。該当行は `A:5f77e647:Md203ae`（アポロン、
  アファニア→ボス）の `arrived` / 80482 tick で、到着して戦闘中である。
  `A:a66e1c3e:Mbe0579`（エストラゴン、ファンゴル→ボス）も `arrived` 1099t。
  「行軍が停滞」は記録側のラベルであって、本件の実害ではない。
- 監査はこの `stuck_march` を fingerprint に含めない（§1）ため、
  シグナル 4 件でも #5429 / #5430 と同型 3 件として扱われる。

## 5. 同型 3 件（fingerprint `29609957117c9004`）

| issue | 将軍 | HP/max | 卵 | broken | first_tick | incident_id |
|---|---|---|---|---|---|---|
| #5429 | エピィ | 57/57 | スロットエッグ | False | 570864 | `cd2559a3cdefdbeaf107` |
| #5430 | リゴット | 99/99 | None | False | 570877 | `a8690e1c3a46dca4ffca` |
| **#5431** | **ポセイドン** | **85/85** | **None** | **False** | **570852** | **`d27b8de1bc73319b6ef7`** |

共通: ラン `g1190-ch1v9` / gen 1190 / v1468 / ch12 19-10 / 観測 1 回 0.0 秒 /
signals 4 件同一 / 城候補 `ワフェルダ` / 出撃 確定3・発射4・order_start4・
order_launched4 / created_at 同一。差は将軍のステータスのみで、機構は同一と判断する。
#5429 / #5430 は別レーンで **(d)** と結論済み（VERIFY-5429.md / VERIFY-5430.md）。
本件はポセイドン固有の事実（HP 85/85・卵 null・first_tick 570852・incident_id・
「キャンプしています」の表記）を個別に照合した上で同じ結論を採る（§1, §5）。

## 6. 再発コメントと帰還側の関連 issue

本 issue には 5 件の再発コメントがある（いずれも azumag 付記、bot_version 記載なし）:

| 再発月 | 将軍 | first_tick | signals（基本2件以外） | incident_id |
|---|---|---|---|---|
| ch12 20-1 | リゴット | 577079 | 戦闘が長引く | `0fc67465c9c12ab70cf8` |
| ch12 21-8 | ロックフォール | 610783 | 月次処理がマップを占有 / 戦闘が長引く / 行軍が停滞 | `661a9aa837e8b8d431b0` |
| ch12 23-1 | レオンハルト | 636824 | 野営帰還を繰り返す / 戦闘が長引く | `16dfd93c741409746fe1` |
| ch12 23-1 | ミュスカデ | 636824 | 野営帰還を繰り返す / 戦闘が長引く | `4d86af9bb9093fb4e88c` |
| ch12 23-1 | マリボー | 636824 | 野営帰還を繰り返す / 戦闘が長引く | `4b3f02a66792ad8d1f14` |

- いずれも**別将軍の新しい滞在**（`reported` フラグは滞在単位なので再起票される）。
  20-1 はリゴットの新規滞在で、#5430 の 19-8 の行が単なる stale 残留ではないことを示す。
- 23-1 の `camp_recall_loop`（野営帰還を繰り返す）は camp/recall 系決定が直前窓で
  10 件以上（`_signals` L3007-3009）を意味し、**帰還機構が反復作動していた**ことを示す。
  同月に 3 将軍が同時閾値到達（first_tick 同一 636824）は、19-10 の時点で残った
  集団野営が継続したことを示唆する。`monthly_scan_hold`/`camp_recall_loop` は
  いずれも v1468 以前から存在するシグナルのため、コメントから稼働版は確定できない。
- ポセイドン本人の再発コメントは無く、19-10 以降に彼の野営が解消したかは不明。
- 同じランの帰還側監査（VERIFY-5429/5430 でも引用）:
  - #5434 `camp_recall_failed` / ch12 20-2 / **v1468** / `camp_recall_aborted` /
    `camp_recall_limit` / stage `fort_status` / steps 91 / `input_receipt_confirmed: False`。
  - #5437 `camp_recall_unconfirmed` / ch12 20-4 / **v1469** / `arrival_unconfirmed` /
    `camp_recall_input_sent` あり / `input_receipt_confirmed: True`。
- ch4 の #5452（VERIFY-5452、(a)）: 防衛戦闘で目的地操舵が潰れ view モードの
  `castle_menu` へ誤入力する帰還層の実欠陥（現行未修正）。機構は章非依存だが
  観測形が `arrival_unconfirmed` で、本件の `dispatched` とは異なる。

## 7. 他の判定を採らない理由

- **(a) でない**: #5418 型の実欠陥は ch4 の goal 未解決 → brain exit 2 であって、
  本件は ch12 で recall 経路かつ `'ボス'` セルが存在する。例外形の証拠も無い。
  帰還完了層に実欠陥がある可能性はある（#5434/#5437/#5452）が、本件の incident
  単体では「設計どおりの bounded skip・再試行」と「実欠陥」を分離できない。
- **(b) でない**: v1469 / v1470 / v1477 は本件の経路に非適用。v1471 は診断機構、
  v1481 の goal フォールバックは条件付きで届きうるがランは v1468 で未適用かつ
  該当形の証拠なし。23-1 まで続いた再発列は「既存修正で十分」と言い切る根拠にならない
  （bot_version 未記載のため残存経路の確定材料にもならない）。
- **(c) でない**: camp_abandoned / 3ヶ月 / incident 構成はすべてコード算式どおりで、
  `camp_observed = true`。主判定は誤検知ではない。
  `stuck_march` は記録側アーティファクトだが fingerprint には入らず、
  主判定の正当性を覆さない。
- **(d) が残る**: 発火の正当性、経路判定（非免除の recall・dome 野営）、各修正の
  適用可否までは確定できるが、3 ヶ月滞留の直接原因（各月の帰還試行がどの段で
  止まったか／dispatched の内実が真の移動か非移動か／status row の鮮度）は
  VM 側ログが無いと一意に決まらない。判定は runtime 受入まで Open を維持する。

## 8. 残件（read-only では確定不能）

- `hanjuku_decisions.jsonl`: 19-8〜19-10（および 23-1 までの再発月）の
  `camp_recall_requested` / `camp_recall_input_sent` / `camp_recall_dispatched` /
  `camp_recall_aborted` / `camp_recall_unconfirmed` / `camp_group_screen`
  （`unclassified_screen_closed`）/ `post_month_recall_requeued` /
  `post_month_task_result`（task=recall の成否）/ `camp_recall_goal_unresolved`
  の有無・件数・将軍名（対象: ポセイドン）。
- `hanjuku_events.jsonl`: 帰還 A の `input_sent` 行（真に未送信か receipt 不整合か、
  `dispatched` の picker_closed が真の `map` 復帰だったか）。
- `general_statuses.statuses['ポセイドン'].tick`: 570852 の行が 19-10 まで更新され
  なかった理由（`CAMP_ROW_STALE_TICKS = 600` の再読が走ったか。走って失敗したか）。
- 本件の `recall_verification {dispatched, steps:1}` がポセイドン本人の帰還かどうか
  （`_flow(mem)` 投影に将軍名が無い）。本人なら `task_recall` だったか・
  `_recall_goal` が None → 野営地点確定の非移動 dispatched（v1481 の対象形）
  だったか。
- 再発 5 件の bot_version（v1468 継続か v1477+/v1481+ 適用後か）。23-1 の
  `camp_recall_loop` が post-v1481 なら残存経路として再評価、判断は DSH に委ねる。
- ポセイドンの野営が最終的に解消したか（19-10 以降の status row・帰還決定）。

記録: 本ファイル（VERIFY-5431.md）。本環境では `git checkout -b` / `git add` /
`git commit` が承認されず verify ブランチへのコミットは未実施のため、
ファイルはワークスペース直下の未コミット状態で残す。コミット・issue への
結論コメント投稿・PR 化は検品ゲートが行う。本レーンはコード変更・push・PR を
作成しない（read-only 検証のみ）。

## 付録: 判定シグネチャ

- event / kind: `camp_abandoned`
- signals: `camp_abandoned`, `camp_abandon_3months`, `battle_grind`, `stuck_march`
- confidence: `high`（固定）
- incident_id: `d27b8de1bc73319b6ef7`
- fingerprint: `29609957117c9004`
- 結論: **(d) 判定不能**（#5418 型 (a) でなく、非免除 recall/dome 経路。
  v1469/v1470/v1477 非対象、v1471 は診断、v1481 fallback は条件付き隣接だが証拠不十分）
