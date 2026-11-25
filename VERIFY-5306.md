# VERIFY-5306 — [半熟英雄] キャンプ帰還未確認: ch10 デーメーテール

対象: docich#5306（監査自動起票）/ ラン `g1190-ch1v9` / BOT_VERSION `v1435` /
月 `ch10 16-10`、観測 602回 / 116.3秒
判定: `camp_recall_unconfirmed` (confidence: high)、reason_code
`camp_arrival_unconfirmed`（シグネチャ「帰還到着を確認できない」）
camp_event: 将軍 デーメーテール / 野営 [131,113] / 帰還先 ボス /
stage `await_dispatch` / steps 6 / status `arrival_unconfirmed` /
escape_move False / input_receipt_confirmed True / input_sent True /
interrupted False
fingerprint `bcef4bcfdbc6633b`、occurrence `57d64ce763ba4d45ebe2`
再発コメント（2026-11-23T23:56:22Z）: ch10 16-11、将軍 アマンディーヌ、
同一 fingerprint、occurrence `1f2673272f414820806c`、観測 521回 / 99.6秒

調査基盤: worktree `/home/hatch/work/docich-5306`（HEAD `ae9ea33b1` =
origin/main = `hanjuku-chart-v1435-recall-dispatch-unattributed`、**ランと同一
バージョン**）。read-only 調査で実装変更なし。
`gh` バイナリは存在するが本環境では起動が拒否されたため未使用。PR の
マージ状態は git 履歴で確認した: `654c4024c`「未確認終了の帰還にも到着名簿読みを
予約する (#5127, v1387) (#5129)」は origin/main に含まれる。
`c9770d9b4`「帰還の到着確認として due 城の名簿読みを全章で起票する (#5127, v1393)」
は origin/main の祖先ではなく、issue 記載どおり **#5141 相当が未マージ**
であることを確認した。`handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため
未読と記録する（AGENTS.md §1）。`python3`/`pytest` も実行が拒否されたため
テストは **未実施**、fingerprint の SHA-256 再計算も未実施（`_fingerprint` の
式 `sha256(f"{chapter}|{kind}|{kind}")[:16]`、`month_audit.py` L3030-3032 を
コード上確認したのみ。`10|camp_recall_unconfirmed|camp_recall_unconfirmed`
から `bcef4bcfdbc6633b` が出ること、および #5127 が同じ ch10×同 kind で
同一 fingerprint になることは式から確定できる）。
本番 VM SSH なし。ラン成果物 `hanjuku_decisions.jsonl` /
`hanjuku_events.jsonl` / frames は VM 側にあり直接参照できない（§5）。

---

## 結論

**(a) 実欠陥。** ただし二段に分かれる:

1. **終端判定そのものは契約どおり（誤検知ではない）**。`input_sent=True`
   だが `picker_closed` が 6 観測以内に立たなかったため
   `status='arrival_unconfirmed'` で `camp_recall_unconfirmed` を記録し、
   `mem['uncertain']=True`・`_arm_recall_skip` で有限終了する。設計文書の
   契約そのもの（`docs/operations/hanjuku-camp-recall-20261001.md` L16:
   「送信待ちは最大6観測。map帰還と送信記録でも受理/到着を断定せず、
   dispatch_unconfirmed又はarrival_unconfirmedとして通常操作へ戻る」）。
   記録形の欠陥（#5248/#5250 系）は v1435 で修正済みで、issue 本文の
   stage/steps/status/input_receipt_confirmed/escape_move が全部出ている。
   reason_code `camp_arrival_unconfirmed` も実 status と一致し**正しい**。
2. **到着確認の後続経路が ch10 で機能しない — #5129 単体では不十分で、
   未マージの #5141 が欠けたピース。** `roster_read_due['ボス']` の
   *予約*は現行 main にある（要求時 `recall.py` L2321-2322、#5129 の
   終端側 L2451-2459）が、**due を名簿読みへ消費する経路が ch10 には
   存在しない**。消費側は (i) ch9 限定の corridor probe、
   (ii) ボス波 launch_probe 待ちの source に限る probe mint、
   (iii) 完全読み取り時の付随的 pop のみで、due 城の一覧を開く注文を
   起票する者がいない。結果として帰還将軍は章末まで所在不明のまま
   （`dirty_status` で要再読にだけなる）であり、同じ署名が月を跨いで
   再発する。`c9770d9b4`（#5141）のコミットメッセージと IMPLEMENT 文書は
   この機構をそのまま説明しており（「ch10 では due が誰にも起票されず
   TTL 切れで消えていた」）、`interim_candidates` の due→読取専用
   roster probe 起票が本件の欠けたピースである。

副次の欠陥面（再発の形を作るが、それぞれ独立）:

- **fort 帰還は `_arrival_request` の要件を構造的に満たせない。**
  `general` 記録を持つのは fort 帰還だけ（`recall.py` L2417）だが、
  砦引継ぎ（`camp_recheck.py` L725 `_hand_over_to_fort`）で本人の
  `retreat_rechecks` キュー行が pop され、`observe_camp_status`
  （L385-392）は送信済み未確認の verification がある本人を再キュー
  しない。`_arrival_request`（L866-888）はキュー行が前提のため、
  fort 帰還では本人ステータス再読みが**発動不能**。
  匿名野営帰還も verification の `general=None` で同様に対象外。
- **goal='ボス' が敵城（boss_move 経路）だった場合、読める名簿自体が
  存在しない。** ボス未撃破の boss_move 帰還は `state['goal']='ボス'`
  （enemy）へ `roster_read_due` を登録するが、敵城の将軍一覧は開けず、
  #5141 の `due_sources` も `c in owned` で除外するため、**#5141 マージ後も
  この枝では到着 receipt が成立しない**（§2-4、§5）。
- **`camp_recall_goal_failed` の学習が fort/boss_move の goal 選定に
  反映されない。** `(camp,'ボス')` の失敗カウンタは発火するが、
  `_goal_after_failures`/`_recall_exhausted` は全体マップ picker 経路
  （`map.py` L672-684）専用で、fort の `_fort_recall_goal`
  （`orders.py` L2711-2749）も boss_move の強制 goal（`recall.py`
  L1706-1708, L1416-1417, L1448-1449）もそれを参照しない。同じ
  (砦→ボス) が計上され続けるだけで宛先は変わらない。

根本の「ピッカーが 6 観測で閉じなかった理由」（ゲームが移動を拒否したか、
前景を別画面に奪われたか、受理はされたが閉鎖を観測できなかったか）は
ラン成果物が必要で read-only では確定不能（§5）。ただし §2-§4 の
消費経路欠落は原因行に依存せずコード上で証明できる。

---

## 1. issue 値 ↔ コード対応（現行 main `ae9ea33b1` = v1435）

| issue の値 | コード | 検証結果 |
|---|---|---|
| 決定 `camp_recall_unconfirmed` | `recall.py` L2448-2450（`_recall_dispatch_step` 終端の unconfirmed 枝の唯一の記録点） | 一致 |
| `stage=await_dispatch` | `_request_recall` が `state.update(stage='await_dispatch', steps=0, ...)`（L2306） | 一致 |
| `steps=6` | L2340 で +1 ずつ。終端条件 `picker_closed or steps >= 6`（L2355）→ steps=6 で打ち切り | 一致（6 観測上限の到達） |
| `status=arrival_unconfirmed` | `accepted = input_sent and picker_closed and not interrupted`（L2365）が偽、`input_sent` 真 → `arrival_unconfirmed`（L2367） | 一致 |
| `input_receipt_confirmed=True` / `input_sent=True` | L2341-2352: `_journal['_recall_inputs']` と `request_trace` の同run照合で送信証明済み | 一致（送信証明あり、受理未観測） |
| `picker_closed=False`（issue に非明示だが status から裏付け） | L2353-2354: `input_sent` 後に `screen.kind=='map'` で立つ。立たずに steps=6 | 一致（ピッカーが閉じてマップへ戻る観測なし） |
| `interrupted=False` | L2356-2357: `_flow['recall_interrupted']` 未検出 | 一致 |
| `escape_move=False` | L2370, L2412 | 一致（崖離脱ではない） |
| 将軍 `デーメーテール` | 終端 metric `general = state.get('general') if state.get('fort') else None`（L2417）→ 記録されるのは **fort 経路のみ**。fort_list で選出時 `state['general']=current[2]`（L2243） | **本件は砦（dome）帰還であることを確定** |
| 野営 `[131,113]` | terminal_metric `'camp'`（L2407） | 一致 |
| 帰還先 `ボス` | `goal`。ch10 の boss castle 名は `'ボス'`（`hanjuku_chart.py` L458 `BOSS_CASTLES[10]`） | 一致（§2-4 で経路候補を分岐） |
| 理由文「帰還A送信後の受理・到着を確認できず…」 | L2446-2447 の arrival_unconfirmed 用文言 | 一致 |
| reason_code `camp_arrival_unconfirmed` | `_camp_reason_code` が `event=='arrival_unconfirmed'` で決定（`month_audit.py` L752-753）。#5248 修正済みで resulting_event が authoritative | **正しい署名**（#5250 の誤署名とは別） |
| confidence high / auto_file | `_build_camp_incident` `auto_file=True`（L914） | 契約どおり |
| fingerprint | `_fingerprint(chapter, kind, [kind])`（L913 → L3030-3032）、章+kind のみ | #5127 と同一になる設計（ch10×同 kind）。再発コメント集約も設計どおり |
| 観測 602回 / 116.3秒 | `month_summary`（月次監視の観測統計） | issue 値、コード非依存 |

## 2. roster_read_due: 予約はある、消費する者が ch10 にいない（焦点 2）

### 予約側（現行 main で実在 — #5129 はマージ済み）

- `_request_recall`（`recall.py` L2313-2322）: goal 束縛かつ
  `escape_move` でない全要求で `roster_read_due[goal] = tick + 180`
  （`RECALL_READ_DELAY=180`、L25）。v772 由来（
  `docs/operations/hanjuku-recall-roster-due-20261113.md`）。
- 終端側（`recall.py` L2451-2459）: `status=='arrival_unconfirmed'` かつ
  goal 文字列かつ `input_sent` で `roster_read_due[goal] = tick + 180`。
  **これが PR #5129（`654c4024c`、v1387、origin/main 祖先）の差分本体**。
  テスト `tests/test_hanjuku_recall_month_scene.py` L92-99
  `test_a_sent_but_unclosed_recall_schedules_the_destination_roster_read`
  が契約を固定。
- 本件は両方に該当するため **`roster_read_due['ボス']` は確実に登録された**
  （要求時と終端時の2回）。

### 消費側（現行 main の全経路 — ch10 では全て不発）

| 経路 | 場所 | ch10 で動くか |
|---|---|---|
| `corridor._due_probe`（due 城へ probe mint + stale 掃除） | `corridor.py` L791-826。呼出は `corridor.orders` 経由で `corridor.mechanical(mem)` 時のみ（`orders.py` L794-795）。`mechanical` は `chart.guard_free(chapter)` 必須（`corridor.py` L83-88）、`GUARD_FREE_CHAPTERS = frozenset({9})`（`hanjuku_chart.py` L1300） | **否 — ch9 限定** |
| ボス波 `_boss_wave_pick` の due 優先 | `replan.py` L1210-1228。`waiting = any(p.source==c and (blind or kept))` の `launch_probe` が同一 source に必要（docich#3515）。帰還 due 単独では不発 | **否 — launch_probe 前提** |
| roster_probe タグ付け | `replan.py` L1336-1343 / L1408-1417。同上の launch_probe 前提 | **否 — 同上** |
| `_source_list_open` | `orders.py` L883-887。roster_probe 注文が空城一覧を開く許可。注文が無ければ不発 | 許可のみ |
| `_observe_garrison` の due pop | `deploy.py` L1619-1628。結論付き（complete）名簿読みが**別の理由で**起きた時だけ pop | 付随的のみ |
| roster_probe 注文の完了処理 | `deploy.py` L2323-2348。`_observe_garrison`→`roster_probe_complete/incomplete`→due pop + `arrival_probe_tick` 武装 | 起票される注文が無い |
| `unread_dispatches`/`arrival_probe_due` | `routes.py` L144-191。`_moves_into`＝`sorties` 記録ベース。**野営/砦帰還は sorties を書かない**（recall.py/camp_recheck.py に sorties への書き込みなし） | 帰還を対象外 |
| `probe_stuck_1` 膠着 fallback | `replan.py` L1903-1997。`not out`（候補ゼロ）の時のみ、unread_dispatches/陳腐名簿ベース | due 非参照・条件限定 |

加えて main では **stale（TTL=900）の掃除自体も ch9 の
`_due_probe` 内にしか無い**（`corridor.py` L810-812）ので、ch10 の
due は誰にも消費されず、掃除もされず、完全読み取りが偶発するまで
mem に残り続ける（chapter ライフタイム、`regions.py` L440）。

### 設計上の到着確認の第二経路 `_arrival_request` も本件では不発

`camp_recheck._arrival_request`（L866-888）は `input_sent=True` かつ
`status ∈ {arrival_unconfirmed, dispatched}` の verification を受理し、
`_step_flow`（L956-960）がキュー内の actor へ `recall_arrival_status_started`
（本人ステータスの有界再読、上限 `ARRIVAL_READ_LIMIT=3`、L15）を起票する。
受理済み帰還の設計説明もこの経路を到着確認の担い手と明記する
（`docs/operations/hanjuku-recall-dispatched-20261122.md` L41-43）。
しかし要件は `proof['general'] == actor` かつ `row['tick'] <=
proof['requested_tick']` の **キュー行が残っていること**（L873-877）:

- fort 帰還: `_hand_over_to_fort`（L725）が引継ぎ時に `_scope(mem).pop(actor)`
  → 行が無い。`observe_camp_status`（L385-392）は送信済み未確認の
  verification が本人に残る間は再キューを拒否 → **行が二度と作られない**。
- 匿名野営帰還: verification の `general` は fort 以外では None
  （`recall.py` L2417）→ どの actor にも一致しない。
- `_remember_camp_verify`→`camp_arrival_check`（`recall.py` L1130-1189）は
  `accepted` 枝（L2436）でのみ記録される → `arrival_unconfirmed` では不発。

**結論: ch10 の fort 帰還では、設計上の到着確認経路が全て閉じている。**
`roster_read_due` は「誰も起票しない予約」、`recall_verification` は
「キュー行を失った照合不能な証明」のまま、将軍は所在不明として残る。

## 3. #5129 vs #5141 の切り分け（焦点 1）

- **PR #5129（`654c4024c`、v1387、origin/main にマージ済み）**:
  `recall.py` +9 行＝L2451-2459 の終端側予約のみ。`roster_read_due` の
  *消費*には一切触れていない。「予約を追加した」修正であり、
  「ch10 で消費経路が無い」問題は未着手。
- **#5141 相当（`c9770d9b4`、v1393、origin/main 非含有=未マージ）**:
  コミットメッセージが本件と同一の機構を説明（「消費経路は ch9 の回廊
  probe とボス波の launch_probe だけで、ch10 では due が誰にも起票されず
  TTL 切れで消えていた」）。差分:
  - `routes.roster_read_due(mem)` 新設＋`ROSTER_READ_DUE_TTL` 集約
    — 章共通の due 抽出と stale 掃除（現行 main の `routes.py` には無い）。
  - `replan.interim_candidates` — `routes.roster_read_due(mem)` の
    due 城（`c in owned`、既起票 source 除外）へ
    `roster_probe`/`source_probe`/`purpose='move'` の読取専用注文を
    先頭候補として mint。mint では pop せず、結論付き読み取り
    （`deploy._observe_garrison`）で消費。`_mechanical_interim.
    stable_step` に roster_probe 用 identity を追加。
  - `corridor._due_probe` は共有ヘルパーへ委譲（挙動不変）。
  - 新規 `tests/test_hanjuku_roster_due_consume.py`（ch10 主対象 7件） —
    **main には存在しない**。
- 現行 main で `interim_candidates`（`replan.py` L1802-1803）が繰り出す
  候補列に `due_orders` は無く、`routes.roster_read_due` も未定義 — 
  **#5141 が origin/main に無いことをコード上でも確認**。

**判定: 再発は #5129 単体では不十分な証拠であり、欠けたピースは #5141
である。** ただし留保: #5141 の消費経路は `interim_candidates` 内にあり、
interim 注文は「計画注文が待っていない時」だけ走る（`orders.py`
L797-801、`map_registration.py` L421、`replan.py` L2124）。計画が常に
pending なら due 読みは依然待つ。また `c in owned` フィルタにより
**敵城 'ボス' の due は #5141 でも読まれない**（§2-4 参照 — goal が
boss_move 経由の敵城だった場合、本件の再発は #5141 マージ後も消えない
可能性がある。ラン成果物で `captured`/`owned` を確認する必要あり、§5）。

### goal='ボス' の二つの可能経路

- **`_fort_recall_goal`（`orders.py` L2711-2749）**: 砦に最も近い*自軍城*。
  これが 'ボス' を返すには 'ボス' ∈ owned が必要（`_recall_owned` 経由）。
- **`boss_move` 強制（`recall.py` L1706-1708）**: fort の dest 段階で
  `_whitehouse_camp` が立てば `goal='ボス'`（enemy でも可）。
  `_whitehouse_camp`（L145-209）は `_whitehouse_open`（全通常城保有 or
  ボスcell実測）+ リング内野営/ボス波将軍の在営。本件の野営セル
  [131,113]（≒px 1048,904）は `CH10_WH_AREA`（px 280-680,220-520）
  の外だが、wave メンバー在営なら位置を問わず boss_move になる
  （L198-208「expired boss march wherever the tent stands」）。
- issue テキストからはどちらか確定不能。`captured` に 'ボス' があるかで
  分岐する（§5 の残件）。

## 4. #5248/#5250 系との切り分け・camp_recall_goal_failed（焦点 3, 4）

| 項目 | #5248/#5250 系 | **#5306** |
|---|---|---|
| 失敗段階 | `input_sent=False` → `dispatch_unconfirmed`（**送信前** receipt 喪失） | `input_sent=True` + picker 未閉鎖 → `arrival_unconfirmed`（**送信後**） |
| 記録形 | 終端 metric に stage/steps/status 無し・「到着」部分一致の誤署名・escape_move 未分離 | v1435 で修正済み: `terminal_metric`（L2405-2417）に stage/steps/status/escape_move/input_receipt_confirmed 全部あり、reason_code は `resulting_event` 決定的（`month_audit.py` L750-753）。**issue 本文の全値が整合** |
| blocked 帰属 | `camp_recall_blocked` への送信側失敗の誤計上（VERIFY-5250 §3-4） | 該当箇所は `recall_goal_failures` へ置き換え済み（L2371-2374 のコメント「送信 receipt 未証明は城非依存」と L2460 の `arrival_unconfirmed` 限定ガード）。`camp_recall_blocked` は書き込み側が終端に無い |
| 機構 | receipt 鎖（journal 照合）の失敗 | **受理確認（picker 閉鎖観測）と到着確認（名簿読み消費）の欠落** — 別機構 |

→ **同じ「記録形式の欠陥」ではない。別の機構**（到着確認の消費経路欠落）で、
fingerprint が同一なのは fingerprint が `chapter|kind` のみを含む集約設計のため
（`month_audit.py` L3030-3032）。

### camp_recall_goal_failed の記録（焦点 4）

- 発火: `recall.py` L2460-2479 — `status=='arrival_unconfirmed'` かつ
  `not picker_closed` で `_flow['recall_goal_failures'][camp][goal] += 1`。
  本件は両条件を満たすため **発火している**（(131,113)→ボス）。
- 効果: `_goal_after_failures`（`map.py` L345-380）が次回 picker 経路の
  goal を未失敗の最寄り城へ差し替え、`_recall_exhausted`（L325-342,
  `RECALL_GOAL_FAILURE_LIMIT=4`）が全自軍城失敗で野営を見送る。
- **限界**: この学習は全体マップ picker 段階（`map.py` L672-684）のみで
  参照される。fort 帰還の `_fort_recall_goal` は参照せず、boss_move は
  goal を強制するため、**砦→ボスの繰り返しは回数だけ積み上がり宛先は
  変わらない**。anonymous camp が通常 picker を通る場合に限り別城を試す。

## 5. 残件（read-only では確定不能、判定には影響しない）

一意確定にはラン成果物（VM 側 `hanjuku_decisions.jsonl` /
`hanjuku_events.jsonl` / frames / `hanjuku_run.json`）が必要:

- **'ボス' の owned 状態**（16-10 時点の `captured`/`lost`）— goal 経路
  （`_fort_recall_goal` か `boss_move` か）と、#5141 の `c in owned`
  フィルタが救えるかの判定に必要。
- **ピッカーが閉じなかった直接原因**: await_dispatch 6 観測の各
  `screen.kind`（picker 残留 = 拒否/未到達、map_target 残留、別前景 = 奪取）。
  fort の dest は `picker_from_menu=True` の sortie picker — 確定先 marker が
  歩行不能セルならゲーム側が拒否し続ける可能性がある。
- `roster_read_due['ボス']` が実際に完全読み取りで pop されたか
  （偶発消費の有無）。pop されていても garrison が将軍を載せなければ
  「A は受理されなかった」ことが確定できる。
- 2 件の occurrence が同じ野営（[131,113]）か — 同一砦の連続失敗か、
  別砦の同型失敗かで重み付けが変わる。

## 6. 修正候補（実装はしない、検証の結論として）

1. **PR #5141（`c9770d9b4`、v1393）のマージ**: `routes.roster_read_due`
   の章共通化 + `interim_candidates` の due 城 roster probe 起票。
   **owned 城 goal の帰還については本件の直接的な治療**であり、issue の
   「欠けたピース」の主回答。マージ後に ch10 で due → 名簿読み →
   garrison 反映の実測確認が必要（同 PR の IMPLEMENT 文書も実測待ち）。
2. **fort 帰還 actor の `_arrival_request` 不通を解消**:
   `_hand_over_to_fort` の `_scope(mem).pop(actor)`（`camp_recheck.py`
   L725）を保留にするか、`recall_verification` に general がある
   arrival_unconfirmed の actor を arrival-check 専用行として
   `retreat_rechecks` へ再登録する経路を追加（`observe_camp_status`
   L385-392 の抑止条件は維持したまま、登録だけ別経路で行う）。
   ボス城宛 boss_move の場合も本人ステータス読みは
   'moving'/'camp'/'castle' を区別できる唯一の receipt になりうる。
3. **読めない due の登録抑制**: `_request_recall`（L2321）と終端側
   （L2458）で `goal ∈ owned`（または `chart.castles` 内の読取可能城）
   に限定し、敵城 'ボス' の boss_move に `roster_read_due` を登録しない。
   #5141 の `c in owned` フィルタと整合し、無効な予約を残さない。
4. **fort/boss_move の失敗学習**: `(camp,goal)` カウンタを fort/boss_move
   の宛先選定にも参照させるか、boss_move の繰り返し失敗をボス再送失敗
   （`camp_boss_redirect` 系）として分離記録するかを設計判断とする。
   現状は計上だけされて宛先が変わらず、`_recall_exhausted` の見送りも
   picker 経路にしか効かない。

## 付録: issue 事実 → コード対応表

| issue の値 | 対応箇所（全て `ae9ea33b1` 上の行番号） |
|---|---|
| 決定 `camp_recall_unconfirmed` | `src/docich/hanjuku/flows/recall.py` L2448-2450 |
| stage `await_dispatch` / steps 6 | `recall.py` L2306（stage 設定）/ L2340, L2355（上限 6 で終端） |
| status `arrival_unconfirmed` | `recall.py` L2365-2368 |
| `input_sent`/`input_receipt_confirmed` True | `recall.py` L2341-2352（journal receipt 照合）, L2413-2414 |
| `interrupted` False | `recall.py` L2356-2357 |
| 将軍 `デーメーテール`（fort のみ記録） | `recall.py` L2417, L2243 |
| 野営 `[131,113]` / 帰還先 `ボス` | `recall.py` L2406-2408; `BOSS_CASTLES[10]='ボス'` `hanjuku_chart.py` L458 |
| 理由文 | `recall.py` L2446-2447 |
| `camp_recall_goal_failed` 発火 | `recall.py` L2460-2479 → `_flow['recall_goal_failures']` |
| `roster_read_due` 予約（要求時） | `recall.py` L2321-2322（`RECALL_READ_DELAY=180` L25） |
| `roster_read_due` 予約（#5129 終端側） | `recall.py` L2451-2459（commit `654c4024c`） |
| due の消費欠落（ch10） | `corridor.py` L83-88/L1300（ch9 限定）; `replan.py` L1210-1228, L1336-1343, L1408-1417（launch_probe 前提）; `deploy.py` L1619-1628（付随的 pop のみ） |
| `_arrival_request` の不通（fort） | `camp_recheck.py` L866-888（要件）、L725（handover で pop）、L385-392（再キュー抑止） |
| `_remember_camp_verify` 非対象 | `recall.py` L2436（accepted 枝のみ） |
| #5141（未マージの消費経路） | commit `c9770d9b4`: `routes.roster_read_due` / `interim_candidates` due_orders / `test_hanjuku_roster_due_consume.py`（いずれも main 不在） |
| 監査・起票 | `month_audit.py` L733-739（CAMP_TERMINAL）、L752-753（reason_code）、L913-914（fingerprint/auto_file）、L3030-3032（`_fingerprint`） |
| 設計契約（6 観測・未確認保持） | `docs/operations/hanjuku-camp-recall-20261001.md` L16 |
| 受理後の到着確認の設計 | `docs/operations/hanjuku-recall-dispatched-20261122.md` L41-43 |
