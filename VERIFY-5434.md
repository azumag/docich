# VERIFY-5434 — [半熟英雄] キャンプ帰還失敗: ch12 キール

対象: docich#5434（`bug` / `auto-generated` の監査自動起票）
ラン `g1190-ch1v9` / generation `1190` /
`hanjuku-chart-v1468-battle-menu-bounded-episode`（v1468 = `00c2b6503`）
月 `ch12 20-2`（観測 562回 / 75.5秒 / max gap 0.7秒）
fingerprint `5c8b75bf94c21051` / occurrence(incident_id) `be4e56de31ce8d991826`
再発コメント（issue #5434）: ch12 21-9、将軍 ドリアン、同 fingerprint、
occurrence `db27618ea49ca3748bbf`

判定: **(a) 実欠陥** — 監査の発火自体は契約どおり（誤検知ではない）。
欠陥は二層: (i) #5252 で指摘された `fort_status` の副有界欠如が現行 main
（v1487）でも未修正のまま、パネル一致＋A 送信済みの成功途中形でも
全予算を空転させたこと。(ii) 砦ルートが自分で開いた目的地ピッカー
（`map_target`）が fort ステージ中は帰還フローへ届く保証経路を持たず、
本件では出撃注文側の `y_jump_open/confirm`（ファンゴル）に同画面が
処理されていたこと。いずれも v1469 / v1470 / v1471 / v1477 / v1481 の
カバー範囲外であり、現行 main で再発しうる。

調査基盤: worktree `/home/hatch/work/docich-5434`（HEAD `ad9c5c7df` =
`hanjuku-chart-v1487-ch4-southern-kings`、detached）。read-only 検証:
本番 SSH なし / ラン成果物 `hanjuku_decisions.jsonl`・`hanjuku_events.jsonl`・
`hanjuku_frames/` は VM 側で直接参照せず / 秘密情報なし / コード変更なし
（成果物は本ファイルの追加のみ）。
`gh` は本環境で起動が承認されなかったため、issue 本文・コメントは
GitHub の公開 issue ページ経由で取得した。`python3` / `sha256sum` 等の
ハッシュ計算コマンドも承認されなかったため、fingerprint / incident_id の
sha256 再計算は**未実施**であり、算式と入力のコード照合のみ行った
（VERIFY-5306 / VERIFY-5429 と同じ環境制約）。
`handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する。

---

## 結論

**(a) 実欠陥。** 根拠は次の 4 点。

1. **発火は契約どおりで誤検知ではない。** `camp_recall_aborted` の
   `steps > RECALL_LIMIT (90)` 終端（`recall.py` L1499-1517）が
   `CAMP_TERMINAL` で `camp_recall_failed`/`failed` へ写像され
   （`month_audit.py` L751-757）、理由文の「上限」から
   `camp_recall_limit` の reason_code が出る（同 L777-778）。
   signals `[camp_recall_failed, camp_recall_limit]`（L858-873）、
   issue 提示値と整合する（§1）。
2. **#5252 の (a) と同じ stage 形で、欠陥半分は未修正。** 砦ルートの
   `fort_status`/`fort_list`/`fort_menu` は非一致画面へ `return []` を
   返し続け、画面ごとの副有界を持たない（`recall.py` L2263-2309）。
   PR #5254 系の診断半分（`_observe_fort_frame`、L2193-2219）は
   マージ済みで本 incident にも投影されているが、**動作修正半分**
   （副有界・早期離脱）は v1487 でも存在しない。本件はその診断投影が
   初めて捉えた「滞留の内訳」である。
3. **新事実: `fort_status_reads=1`・最終画面 `map_target`。** #5252
   では reads=0（パネル不一致で空転）だったが、本件はパネルが 1 度だけ
   キールと一致し A が 1 発送られている（`FORT_STATUS_PRESS_EVERY=4`、
   L2307-2308）。その後パネルは二度と一致せず、最終観測は `map_target`。
   `_fort_step` は `map_target` を受ければ `dest` へ遷移する
   （L2298-2300）ため、**最後の 1 観測まで `map_target` は帰還フローに
   届いていなかった**（届いていれば stage は `dest` になっていた）。
4. **砦ピッカーの到達経路に構造的な穴がある。** fort ステージ中の
   `map_target`/`map` は優先経路（`fort_route` は `text`/`unknown` 限定、
   `field_picker` は `to_camp`/`dest`/`menu` 限定 — orchestrator
   L2127-2135）にも `camp_recheck._ALLOWED_KINDS`（fort ステージ無し、
   camp_recheck.py L24-35）にも含まれず、`_recall_owns_dialog` の
   低優先 fallback（L2195-2197）にだけ依存する。そこは
   `_sortie_owns_the_field`（出撃確定後 300 tick の譲渡、L894-910）と
   `month_sub`（L930-931）で閉じる。閉じている間の `map_target` は
   `map_flow.target_step`（L2318-2319）へ落ち、**アクティブ注文の
   出撃先ピッカーとして操舵される**。issue の直前決定はこの形を
   そのまま示す: `y_jump_open`（14:35:15, `map_target`）→
   `y_jump_confirm`（14:35:18, `world_map`）とも target=**ファンゴル**、
   chart_step=`A:5f77e647:M71b18e`（タルタル move 注文、帰還とは無関係）。

---

## 1. issue 提示値 → コード対応（根拠の照合、HEAD `ad9c5c7df`）

| issue の値 | 対応箇所 | 検証結果 |
|---|---|---|
| 判定 `camp_recall_failed` (high) | `month_audit.py` L751-757 `CAMP_TERMINAL` + L890 `confidence='high'` | 一致 |
| 決定 `camp_recall_aborted` / 理由文 | `recall.py` L1513-1514 | 一致（文言一字一致） |
| reason_code `camp_recall_limit` | `month_audit.py` L777-778（理由中の「上限」） | 一致 |
| signals `[camp_recall_failed, camp_recall_limit]` | L858-873（kind + reason_code、重複除外・8 上限） | 一致 |
| stage `fort_status` / steps `91` | `recall.py` L1499-1501（`steps += 1` → `> RECALL_LIMIT(90)` で abort、metric に stage/steps） | 一致 |
| 将軍 `キール` | L1504 `state.get('general')`、fort_list 選出時に設定（L2293-2296、`camp_fort_sortie` 記録） | 一致（fort 経路でのみ general が残る設計） |
| 野営 `None` / 帰還先 `不明` / status `不明` / target_cell `None` | abort metric に `camp`/`castle`/`status`/`target_cell` を含めない（L1501-1512） | 一致（**証拠欠落として残存**、§4） |
| `input_sent`/`input_receipt_confirmed`/`picker_closed`/`interrupted` = False | abort metric に `input_sent` キー無し → `_camp_event` L841 が False（`_request_recall` の dispatch 層未達の表示） | 一致（ディスパッチ未達を正しく示す） |
| 砦観測 menu 0 / list 1 / status 91 | `_observe_fort_frame` L2202-2203（`{stage}_observations`、cap 91）。menu=0 は「menu ステージ中の frame で enter_fort したため fort_menu 帰属が無い」ことと整合（`enter_fort` L2224 は当該 frame で stage 切替） | 一致 |
| `fort_status_reads` 1 | `_fort_step` L2306-2308（`general_status` の general が `state['general']` と一致した時だけ +1、`reads % 4 == 1` で A） | 一致（パネル一致 1 回 → A 1 発） |
| 最終画面 `map_target` / 将軍照合 キール→不明 / 文言 なし | `fort_last_screen_kind`（L2205）/ `fort_last_observed_general`（L2212-2219、`general_status` 非解析で None）/ `fort_last_screen_text`（map_target に text 無し → None） | 一致 |
| `escape_move` False | metric キー無し → `_camp_event` L843 | 一致 |
| `flow.recall_verification` `{stage:'await_dispatch', status:'dispatched', steps:1}` | `_recall_dispatch_step` L2444 が残す**別の先行受理済み帰還**の verification（本 incident の abort では上書きされない） | 一致（本件とは別将軍の成功形。混同注意） |
| `flow.recall_skip_target` `{kind:'dome'}` | `_arm_recall_skip`（abort 後 L1517）が dome 単位の scoped skip を残す | 一致 |
| fingerprint `5c8b75bf94c21051` | `_fingerprint`（L3105-3107）`sha256("12\|camp_recall_failed\|camp_recall_failed")[:16]` — L931 の呼出は chapter×kind のみ | 算式・入力一致（**再計算は環境制約で未実施**。21-9 の再発コメントが同 fingerprint = 章×kind 集約設計どおり） |
| incident_id `be4e56de31ce8d991826` | L926-929 `sha256(runtime_id\|generation\|kind\|reason_code\|camp\|general\|int(now))[:20]` | 形式一致（`int(now)` を含み再現不可。20 hex・一意性要件に整合） |
| `auto_file: true` | L932 | 一致 |

## 2. なぜ (a) か — #5252・#5311・#5306 との異同

- **#5252（ch9 / stage `fort_status` / (a)）**: 同一ステージ・同一 91 steps。
  当時は abort metric が `{stage, steps}` のみで滞留画面が不明だった。
  PR #5254 系の診断投影が入った今、本件は `fort_status_observations=91`
  （副有界が無いまま全予算を fort_status で消費）・`reads=1`・
  `screen=map_target` が取れた。**欠陥の診断半分は効いたが、
  VERIFY-5252 修正案 2（副有界）と 3（abort metric への camp/general
  含む拡充のうち camp/castle）は未実装のまま**であり、滞留の動作面は
  同一欠陥の再発。
- **#5311（ch10 / stage `dest` / (b)）**: 対照的に `dest` ステージは
  マーカー歩行・Y ジャンプ・崖離脱・ボスリダイレクトに副有界があり、
  91 steps は「有界な副経路の非収束」として (b)。`fort_status` には
  その副経路が存在しない — 本件は #5311 ではなく #5252 の側に属する。
- **#5306 / #5248 / #5250（`camp_recall_unconfirmed` 家族）**:
  `await_dispatch`・`input_sent=True`・6 ステップ終端の「送信後未確認」系。
  本件は `input_sent=False`（`_request_recall` 未達）で層が違う。
- **#5452（ch4 / `arrival_unconfirmed` / (a)）**: 「防衛戦闘が帰還の
  前景（dest/y_route）を潰す」欠陥。本件も防衛戦闘
  （`defense_observed` 14:35:02 → battle → `battle_result` 14:35:15）が
  同じ窓に割り込んでいるが、潰れた層が「目的地ピッカーへの画面到達」
  であり別機構。テーマとしては「帰還が他所有者に前景を奪われる」系列で
  関連する。
- **#5437（ch12 / `camp_recall_unconfirmed` / バジル / v1469）**:
  同一ラン・同章の兄弟件。VERIFY-5452 によれば砦 `camp_fort_sortie` で
  1 回目は `dispatched` まで受理、2 回目は会話横取りで未確認という形。
  `input_sent=True` の dispatch 層で、本件（picker 未達の failed 層）とは
  別ステージ別機構。キュー待機中で未検証のため関係は記録に留める。
- **#5429 / #5430 / #5431（ch12 `camp_abandoned` / (d)）**: 同一ランの
  野営放置件。VERIFY-5429 §6 が指摘する「帰還完了層の残件」に本件は
  該当する — abort 後 `_arm_recall_skip` が dome 単位の scoped skip を
  残し（`flow.recall_skip_target {kind:'dome'}`）、野営は残ったままに
  なる。滞留の供給源として関連。

## 3. v1468 → HEAD（v1487）間のカバー確認

- `git diff 00c2b6503..HEAD` で `flows/recall.py` は `_boss_move_goal`
  追加（ch4 専用、v1469）の 2 hunk のみ。**fort ルートはラン時点と同一**。
  `orchestrator.py` の帰還ルーティング（L913-938, L2127-2197）にも
  関連変更なし。
- **v1469（#5424）**: ch4 `boss_move` の goal 未解決クラッシュ修正。
  ch12 かつ fort_status 経路なので無関係。
- **v1470（#5432）**: ch4 四季王サイト待機の有界化。無関係。
- **v1471（#5141）**: `roster_read_due` の消費経路全章化。
  到着確認層であり、本件は dispatch 未達（`input_sent=False`）で対象外。
- **v1477（#5442）**: 月次帰還 `task_recall` / `release_for_task` /
  免除カウント消失の修正。月次タスク・再確認の取り違え層で、
  fort ステージの画面ルーティング・副有界には触れない。
- **v1481（#5450）**: `task_recall` の `_recall_goal` None 時に本城へ
  フォールバック＋ hint 配置修正。goal 解決前段であり、本件は goal 選択後
  （ピッカー段）の失敗で対象外。
- → **いずれもカバーしない。現行 main で再発しうる。**

## 4. 欠陥の内訳（実装観点）

1. **`fort_status` の副有界欠如（#5252 継続、未修正）**:
   `_fort_step` L2309 の fallthrough `return []` は「パネル不一致・
   フェード・別画面」を区別せず記録も待機上限もない。`reads` は入力
   ペース用で上限を持たず、一致画面が二度と現れなくても 90 観測全部を
   使い切る。
2. **砦ピッカーのオーナー経路ギャップ（新規の切り分け）**:
   `enter_fort`→`fort_status` まで進んだ帰還は、A が開く `map_target`
   を自分で `dest` へ渡せる設計（L2298-2300 の「`None` hands the
   map_target frame on to that route」）だが、orchestrator の所有者
   判定がその到達を保証しない。閉塞中（出撃譲渡・月次実行中・
   先行ハンドラ消費）は `target_step` がピッカーを注文側へ操舵する。
   本件では `y_jump_open`/`y_jump_confirm` が注文ターゲット
   （ファンゴル）へ走った記録が残る。ピッカーが帰還のものだった場合は
   宛先を別注文へ書き換えられうる誤入力面もある（最悪ケースは
   キールをファンゴルへ出す誤出撃。abort の B が先に閉じたため本件で
   確定はしない）。
3. **abort metric の残る穴**: `camp`/`castle`/`status`/`target_cell` が
   常に null のため、監査側の「野営・帰還先」は全欠損（VERIFY-5252
   修正案 3 の未済分）。

## 5. 残件（read-only では確定不能）

一意確定にはラン成果物（VM 側 `hanjuku_decisions.jsonl` /
`hanjuku_events.jsonl` / `hanjuku_frames/`）が必要。結論 (a) はいずれの
分岐でも変わらないが、修正対象の絞り込みに影響する:

- 14:35 前後の `map_target` が**帰還側が開いたピッカー**か
  **注文側の出撃ピッカー**か（`camp_fort_sortie` → status 一致 A の
  時刻と、`y_jump_open` 前の出撃系決定列で確定できる）。
- 初期 `map_target` が帰還へ届かなかったゲートの内訳
  （`_sortie_owns_the_field` の 300 tick 譲渡 / `month_sub` 実行中 /
  `castle_jump` confirm フェーズの `return []` 呑み込み 等）。
- 89 観測の内訳（`text`/`unknown` の画面分布、`fort_last_screen_text`
  直前値、`observed_general` が途中で別将軍を示したか）。
- キールの status パネル A（reads=1）が実際にピッカーを開いたか、
  直前の画面がパネル→何に遷移したか。

## 6. 修正候補（実装はしない、検証の結論として）

1. fort ステージを `field_picker` の対象（`orchestrator.py` L2133）へ
   加えるか、`fort_route`（L2127）に `map`/`map_target` を含め、
   `_fort_step` の `map_target`→`dest` が設計どおり最初の到達で
   発火するようにする。あわせて `_recall_dialog_wanted` の fort 期
   `map_target` が `_sortie_owns_the_field` で注文側へ譲渡されない
   ガードを検討する（出撃中はピッカー到達を bounded hold する等）。
2. `fort_status`（および `fort_menu`/`fort_list`）の非進行観測を
   副有界化する（VERIFY-5252 修正案 2 と同旨: 例 8–12 観測で証拠を
   残して fort ルート離脱または有界再試行）。
3. abort metric に `camp`/`target_cell`/`status`（不明なら `None` 明示）
   を含める（VERIFY-5252 修正案 3 の残り）。
4. `target_step` が `_flow['recall']` の fort 系で開かれたピッカーを
   注文のピッカーと区別するガード（picker の所有者記録。fail-closed
   側へ倒し、不明なら `unexpected_target` として B で閉じる現行
   セーフティを維持）。

記録: 本スレッド（agent-council）と issue #5434。
PR 化・issue コメント投稿・CI green 確認は検品ゲート（メイン Muse）が
行う。本レーンはコード変更・PR を作成しない（read-only 検証のみ）。

## 付録: 判定シグネチャ

- event / kind: `camp_recall_failed`（reason_code `camp_recall_limit`）
- signals: `camp_recall_failed`, `camp_recall_limit`
- camp_event: `camp_recall_aborted` / stage `fort_status` / steps 91 /
  fort_status_observations 91 / reads 1 / screen `map_target` /
  general キール / input_sent・receipt・picker_closed・interrupted 全て False
- fingerprint `5c8b75bf94c21051` / incident_id `be4e56de31ce8d991826`
- 結論: **(a) 実欠陥**（#5252 副有界欠如の継続＋砦ピッカーの所有者
  ルーティング欠落。v1469–v1481 いずれもカバー外、現行 main で再発しうる）
