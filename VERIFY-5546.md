# VERIFY-5546 — [半熟英雄] 野営放置: ch9 ソーピニヨン (3ヶ月)

対象: docich#5546（`bug` / `auto-generated` の監査自動起票）
ラン `g1190-ch1v9` / generation `1190` /
`hanjuku-chart-v1498-recall-battle-mode-lost` / 月 `ch9 8-6`（観測 1回）
occurrence(incident_id) `40e792ed1f529ddeaeb5` / fingerprint `d09ee2cc6216092c`
created_at `1795583171.700812`（2026-11-25 05:06:11Z）

判定: **(b) 設計どおり** — 第9話 corridor 機械行軍モード（ボスフェーズ前）の
野営はオーナー指示（2026-11-11、v665 / #3936）により月次帰還の対象外であり、
回収はフィールドでテントが見えた場合の既存帰還フロー（#3958）と、ボス
フェーズ移行後の `camp_stays`＋ボス再指向に委ねられる。#5545（同ラン・同月・
ch9 カシス、結論 (b)）との差異は城候補のみで、`castle_candidate = 不明`
（null）は ch9 のいずれの帰還経路条件にも使われないため結論を変えない。
監査の発火は契約どおりの記録であり、野営の実在は `camp_observed: true` で
裏取りできる。

調査基盤: worktree `/home/hatch/work/docich-5546`（`origin/main` =
`ef2a351b8` = v1512 `hanjuku-chart-v1512-attack-route-revalidation`、
detached HEAD）。観測版は **v1498** = コミット `0193891d3`（#5452 目的地
操舵中の戦闘中断・いどうモード喪失の修正、PR #5518）。`0193891d3..HEAD`
で `flows/recall.py`・`flows/post_month_status.py`・`flows/camp_recheck.py`・
`strategy/corridor.py`・`hanjuku_chart.py`・`os_layer/readers.py`・
`flows/house.py` に触れたコミットは**ゼロ**（§7）。read-only 検証:
本番 SSH なし / ラン成果物（`hanjuku_decisions.jsonl`・
`hanjuku_events.jsonl`・`hanjuku_frames/`）は VM 側で直接参照せず /
秘密情報なし / コード変更・push・PR なし。根拠は issue 本文（GitHub 公開
issue ページ経由。`gh` は本環境で実行拒否）、`origin/main` と `0193891d3`
（`git show`）のコード、git history、VERIFY-5545 / VERIFY-5522 記録。
`handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため**未読**（AGENTS.md
§1）。sha256 再計算コマンドは本環境で実行が承認されなかったため、算式と
入力値の照合のみを記録し、再計算は**未実施**とする（§1）。

---

## 結論

**(b) 設計どおり。** 根拠は次の 5 点。

1. **発火自体は契約どおりで、誤検知ではない。**
   `CAMP_ABANDON_MONTHS = 3`（`month_audit.py` L61）を閾値に
   `_track_camp_abandon`（L2674-2748）が 3 ヶ月以上 camp の将軍を検知し、
   `_build_camp_abandon_incident`（L1638-1727）が incident を起票する。
   ソーピニヨンは `months = 3`・`first_month = '8-4'`・`first_tick = null`
   で、月境界加算（8-4 で新規 entry → 8-5、8-6 の月替わりで 1+1+1=3）
   による閾値到達と一致する。`camp_stay.camp_observed = true`・
   `hp 48/48`・`egg イビルエッグ`・`broken false`・`キャンプしています`
   は名簿行の実測転記（house scan が status 行に立てる camp フラグ、
   `flows/house.py` L253・L746）であり、静的雛形ではない。

2. **8-6 時点の ch9 はボスフェーズではなく corridor 機械行軍モードであり、
   野営の非回収はオーナー設計そのもの。**（#5545 と同一の世界状態）
   - `world.captured = ['ハヤク','タオ']`、lost なし → `_owned` =
     `{アルマムーン, ハヤク, タオ}`。
   - `chart.corridor(9)` = `('アルマムーン','ハヤク','タオ','サヌト',
     'オシロガ','ドンドン','オオキク','ナッテ','シマウ','カラ','イソ',
     'イデ！')`（`hanjuku_chart.py` L1251-1254、12 城・`'ボス'` は含まない）。
   - `corridor_next(9, owned)` = `サヌト`（L1279-1285）→ `boss_phase` =
     False（`recall.py` L90-105）→ `camp_stays` = False（L131-138）かつ
     `corridor.mechanical` = True（`strategy/corridor.py` L84-89:
     guard_free(9) ∧ corridor 非空 ∧ corridor_next ≠ None）。
   - `plan.orders` は全件 `I:c9:` corridor step で、`world.sorties` も
     `タオ>サヌト`・`ハヤク>タオ` の en_route のみ —— 機械行軍稼働中。
   - このモードでは `_road_skips_checks` = True（`post_month_status.py`
     L191-203: guard_free(9) ∧ corridor 非空 ∧ not boss_phase）となり、
     `_tasks`（L225-308）の `road` 分岐（L234-238）が**全将軍を
     `continue`** して野営将軍に recall/locate/boss_move のいずれの
     タスクも積まない。オーナー 2026-11-11: 「月次の位置チェックも必要が
     ないので省略できる。キャンプになった将軍は仕方ないのでこの章は放置」
     （L193-197）。導入は v665 / #3936 で v1498 よりはるかに古い。

3. **城候補「不明」は ch9 の帰還経路条件に一切使われず、結論を変えない。**
   - `_boss_camp_holds_siege`（`post_month_status.py` L116-132）は
     `marker.castle_candidate` が `is_boss_castle` を満たすことを要求する
     が、これは**非 road 章**のボス包囲免除のための条件。ch9 road モード
     では `_tasks` L234-238 の `road` 分岐が先に `continue` するため
     評価自体に到達しない（§5）。仮にボスフェーズでも `camp_stays` 分岐
     （L239-240）が先に立つため、ch9 ではこの関数は実質使われない。
   - `boss_redirect_goal`（`recall.py` L108-128）の条件は章レベル
     （`boss_phase` ∧ `moving_boss_cell(9)` 非空 ∧ `wave_stale` でない）
     であり、`castle_candidate` を参照しない。**城候補が不明でも
     ボスフェーズ移行後のボス再指向の対象になりうる**（ただし適用には
     フィールド上のテント検出が必要 — §3）。
   - `#5545` の `castle_candidate = 'ボス'` との差異は、カシスが実測ボス
     セルの `MAP_CASTLE_TOLERANCE = 96`（`readers.py` L458）内に野営して
     いたことの記録であって、帰還可否の条件差ではない。

4. **回収経路は設計上存在し、本件の滞留はその適用条件にまだ達していない。**
   - フィールド野営帰還（#3958）: `_chapter9_field_recall`
     （`orchestrator.py` L73-101）が `corridor.mechanical` なら True を
     返し、アイドルな map フレームを `camp_recall_step` へ渡す。ただし
     同フローが起動するのは**フレーム内にテントが見えた場合だけ**
     （`recall.py` L1320 以降 `own_camps` → `camp_found` L1501）。
     本件の決定内訳は `camp_hunt: 1`（テント非検出の設計内記録、
     L1376-1406）で、`camp_found` / `camp_recall_*` 系は 120 秒窓に
     0 件 —— 「機構が動いて失敗した」形ではなく、「見えない野営は
     仕方なく放置」の設計内状態。`castle_candidate` 未解決は「野営位置が
     城近傍と確定できない」ことの記録であり、フィールド回収の条件を
     変えない。
   - ボスフェーズ（corridor 全 12 城確保後）: `camp_stays` = True で
     `_tasks` L239-240 が野営将軍を `continue` し帰還しない。フィールド
     野営フローは `boss_redirect_goal` でボス最新セルへ再指向し、
     `wave_stale` なら通常帰還→次波（v1258/#4805、L120-127）。

5. **検知機構と ch9 帰還経路に v1498→現行 main（v1512）の変更は無い**（§7）。
   カバーする修正 PR は存在しないが、そもそもカバーを要する欠陥ではない。

## 1. fingerprint / incident_id の式照合

| 項目 | コード（現行 main `ef2a351b8`） | issue 値 | 照合 |
|---|---|---|---|
| fingerprint 式 | `_fingerprint` `month_audit.py` L3319-3321: `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]` | `d09ee2cc6216092c` | 呼出は `_build_camp_abandon_incident` L1724-1725 の `_fingerprint(chapter, 'camp_abandoned', ['camp_abandoned'])` → 入力は `9|camp_abandoned|camp_abandoned` に一意に確定。**sha256 再計算は本環境が計算コマンドを拒否したため未実施** |
| incident_id（occurrence）式 | 同ビルダ L1720-1722: `sha256(f"{runtime_id}|{generation}|camp_abandoned|{general}|{first_tick}")[:20]`（汎用 `_incident_id` L3314-3316 とは別式） | `40e792ed1f529ddeaeb5` | 入力は `g1190-ch1v9|1190|camp_abandoned|ソーピニヨン|None`（`first_tick` が null のため f-string の `None` レンダリングを含む）。**再計算未実施**。将軍名が入力に含まれるため #5545（カシス → `d05e5c434f1b69576d1a`）とは別値になる |
| 検知月数 | `CAMP_ABANDON_MONTHS = 3`（L61） | 継続 3ヶ月 | 一致（閾値ちょうどで発火） |
| 月数の数え方 | `_camp_age_months`/`_camp_month_starts`（L1609-1635）＋ `_track_camp_abandon` の月境界加算（L2692-2721） | 初回 8-4 → 発火月 8-6 | 8-4 で entry 作成（months=1）、8-5・8-6 の月替わりで +1 ずつ → 3 で整合。`camp_status_seen` の tick が無い（first_tick null）ため `_camp_age_months` は None を返し、月境界加算側で 3 に達した |
| 追跡状態 | `_track_camp_abandon`（L2674-2748）: `camp_watch.generals`・`reported` フラグで一度だけ発火 | 観測 1回・判定理由 なし | 契約どおり（`confidence` は `'high'` 固定 L1669） |

**fingerprint が #5545 と同じ値になる理由**: 式の入力は
`chapter|kind|primary_signal` の 3 要素だけで、将軍名・城候補・月・
signals の残りを含まない。両件とも `chapter=9`・`kind='camp_abandoned'`・
primary=`'camp_abandoned'` のため入力 `9|camp_abandoned|camp_abandoned` が
一致し、出力 `d09ee2cc6216092c` も一致する（signals の `battle_grind` /
`no_source` は fingerprint に入らない）。dedup 用途の family キーであり、
incident の一意性は incident_id 側（将軍名を含む）が担う設計。

## 2. #5545（camp_abandoned ch9 カシス、結論 (b)）との異同

| 観点 | #5545 カシス | #5546 ソーピニヨン | 結論への影響 |
|---|---|---|---|
| ラン / generation / 版 | g1190-ch1v9 / 1190 / v1498 | 同左 | — |
| 月・章 | ch9 8-6（観測 1回） | 同左 | — |
| 野営期間 | 3ヶ月・初回 8-4 | 同左 | — |
| HP / 卵 / 壊れ | 73/73・いっぱつエッグ・False | 48/48・イビルエッグ・False | なし（健全な戦力温存でも repair 対象でもない） |
| 城候補 | `ボス`（実測ボスセルへ束縛） | `不明`（null、`_marker` 未解決） | **なし** — 下記 |
| flow | `{}` | `{}` | なし（どちらも帰還未起動） |
| 世界状態 | 奪取 ハヤク・タオのみ | 同左（同一月・同一スナップショット） | どちらも `corridor.mechanical` → road skip |
| 最終観測 tick | 163950 | 164019（月 tick 166512 より 2493 古い＝前月の名簿読み） | なし |
| fingerprint / incident_id | `d09ee2cc6216092c` / `d05e5c434f1b69576d1a` | `d09ee2cc6216092c` / `40e792ed1f529ddeaeb5` | fingerprint は式上一致が必然（§1） |

城候補の差異が結論を変えない理由: ch9 の corridor 機械行軍モードでは
`_tasks` が野営将軍へそもそもタスクを積まない（城候補の値を参照する
分岐に到達しない）。`_boss_camp_holds_siege` は「ボス城近傍の野営を
包囲継続として免除する」非 road 章向けの条件であり、城候補が未解決
でもボス再指向（`boss_redirect_goal`）の対象性には影響しない
（§3）。したがって本件も **ch9 設計による意図的放置**として (b) が採れる。

## 3. 城候補「不明」の意味と関連条件

- **「不明」の意味**: issue JSON では `castle_candidate: null`。
  `post_month_status._marker`（L1082-1163）が名簿選択三角から城を
  束縛する際、`marker_castle`（`readers.py` L597-）で
  `MAP_CASTLE_TOLERANCE = 96` 内かつ `icons >= 1` の一意な近城が得られ
  なかった場合に `castle_candidate` は設定されず null のまま残る
  （selection_marker 不在・複数候補・許容範囲外・章内ボスセル未測定の
  いずれか）。**`_marker` の未解決値**であり、「ボス城でない」ことの
  確定ではなく「どの城とも束縛できなかった」ことの記録。
- **`_boss_camp_holds_siege` との関係**: `isinstance(candidate, str)`
  かつ `is_boss_castle(candidate)` かつ `broken is not True` を要求する
  ため、不明では False。ただし ch9 road モードでは `_tasks` がこの分岐に
  到達しない（§5）ため結果に影響なし。#5545 でカシスが `ボス` だった
  のは「ボス隣接の期限切れ行軍野営」を示す補足情報であり、ch9 の
  放置判定の主因ではない。
- **ボスフェーズ移行後の `boss_redirect_goal` 対象性**: 条件は
  `boss_phase` ∧ `chart.moving_boss_cell(9)` 非空 ∧ `wave_stale` でない
  （`recall.py` L108-128）で、個々の野営の `castle_candidate` を見ない。
  野営フロー内のボス化判定（L1461-1484）は `boss_wave_generals` の
  `camp_status_seen` 所属・`CH9_BOSS_WAYPOINT` 近傍・屋根なし open
  terrain のいずれかで立つ。したがってソーピニヨンの野営がフィールド
  フレームに映ればボス再指向の対象になりうる。**不明であっても
  ボス再指向を妨げない**が、適用自体はテントの可視に依存する点は
  城候補の値と無関係に共通する。
- 参考: `castles(9)` は `'ボス'` を Y ビュー実測（`DYNAMIC_BOSS_CELLS`）
  時のみ含む（`hanjuku_chart.py` L1504-1522）。#5545 で `ボス` が束縛
  されたことは当該月にボスセルが実測されていたことの傍証。

## 4. flow.recall_verification の有無と意味

- **本件には無い**（`"flow": {}`）。`recall_verification` は
  `_recall_dispatch_step` の受理契約（input_sent ∧ picker_closed ∧
  !interrupted ∧ !scene_covered → `status:'dispatched'`、`recall.py`
  L2591-2708 付近）を通過したときだけ `_flow` に書かれる（L2682、
  終端は `dispatched` / `arrival_unconfirmed` / `dispatch_unconfirmed`）。
  本件では帰還自体が未起動（road モードでタスク非積載＋テント非可視）
  のため、この層の記録が存在しないのは設計と整合する。
- 仮に存在しても `_summarize_flow`（`month_audit.py` L441-481）は
  `recall_verification` を汎用ループ（L469-480）で `stage/kind/status/
  result/attempts/reads/steps` のみに縮小し、`general`・`goal`・`camp`
  を落とすため本人帰属はできない（#5431・VERIFY-5522 §4 と同じ投影限界）。
  本件では flow が空なので帰属問題自体が発生しない。

## 5. 月次タスク `_tasks` のソーピニヨンへの積載（終端形）

`post_month_status._arm`（L440-616）→ `_tasks`（L225-308）の分岐順:

1. `road`（= `_road_skips_checks`、本件 True）: 各将軍は
   `location=='castle' and broken` のとき `{'kind':'repair'}` のみ積み、
   即 `continue`（L234-238）。**ソーピニヨン（location='camp'）はここで
   終端 —— recall タスクは積まれなかった（設計）。**
2. （未到達）`camp_stays` 分岐 L239-240。
3. （未到達）`_boss_camp_holds_siege`/`_boss_move_ready`/`_boss_move_goal`
   の免除・boss_move 分岐 L241-281、および通常 `{'kind':'recall'}` L282。

よって本件の終端形は **「タスクキューに一切の野営関連ジョブが積まれない」**
であり、`{'kind':'recall'}`/`{'kind':'boss_move'}` のどちらにも分類
されない。v1498 時点の recall 機構（`_recall_dispatch_step` による
`await_dispatch` 検証・`scene_covered`・`camp_arrival_check`・
`RECALL_TASK_RETRIES=2`）はいずれもディスパッチ以後の検証であり、
**ディスパッチを作る側（タスク積載）が設計上スキップされる本件とは
無関係**。`_road_skips_checks` は v1498（`0193891d3`）に既に存在し、
関数は v1498→HEAD で無変更。

## 6. 判定シグネチャの内訳（`battle_grind` / `no_source`）

- `battle_grind`（戦闘が長引く）: battle 系決定合計 87 ≥ 20
  （`_signals` L3217-3218 付近）。サヌト攻撃・タオ move の回廊行軍が
  迎撃・攻城戦を量産している実況として正しい。敗北・退却→野営化は
  ch9 の野営発生源として設計内。
- `no_source`（出撃元の将軍がいない）: `world.garrison` の各城が ≤1 名
  （`アルマムーン:[] / タオ:[1名] / ハヤク:[]`）。`guard_free(9)`
  （最後の 1 将軍まで出せる、オーナー 2026-11-09）＋ drained 機構による
  **設計どおりの城の空き**であり、野営の直接原因ではない。
- よって (c) 誤検知でもない: 主判定 `camp_abandoned` は計算上正しく、
  camp の実在も `camp_observed` で裏取りされる。

## 7. v1498 → 現行 main（v1512）の差分カバー範囲

`git log --oneline 0193891d3..HEAD` で camp 検知・ch9 帰還経路の関連
ファイルに触れたコミット:

- `flows/post_month_status.py`・`flows/recall.py`・`flows/camp_recheck.py`・
  `strategy/corridor.py`・`hanjuku_chart.py`・`os_layer/readers.py`・
  `flows/house.py`: **変更ゼロ**（v1498 と byte 同一）。
- `month_audit.py`: 変更あり（+376 行）だが、camp_abandoned 系は
  `_track_camp_abandon`・`_build_camp_abandon_incident`・`_camp_generals`・
  `_camp_age_months`・`_camp_month_starts`・`_fingerprint`・`_incident_id`
  を両版で読み比べ**差分なし**。入った変更はボス突入直後の無戦闘誤報抑止
  （`BOSS_ENTRY_BATTLE_GRACE_SECONDS`/`_boss_entry_evidence`、#5500 系）、
  `battle_card_at_hp1` 起票（v1509）、`_decision_counts` の top-30 切り詰め
  全件化（#5525/#5530）等で、camp_abandoned 検知には非適用。
- `flows/month.py`（#5167 メニューカーソル検証）、`orchestrator.py`
  （v1510 ステータスパネル B 閉じ・plain-text hold）、`state/regions.py`
  （`status_panel_holds` キー追加のみ）、`strategy/replan.py`・`routes.py`
  （v1500 ボス波ストック・v1501 ch10 進入路・deploy 時経路再検証 #5348）、
  battle 系 3 ファイル（v1505-v1510 切り札系）: いずれも camp 帰還・
  ch9 road 免除とは無関係。`_chapter9_field_recall`（L73-101）も差分なし。

→ **現行 main（v1512）までに camp_abandoned の検知機構と ch9 の帰還経路に
変更は入っていない。** 本件をカバーする修正 PR は存在しないが、
そもそもカバーを要する欠陥ではない（結論 (b)）。

## 8. 残件（read-only では確定不能、判定 (b) の限界）

判定 (b) は「監査レコードの実在性」と「滞留がオーナー設計で説明完結する」
点への結論であり、以下は VM 側ラン成果物が無いと確定できない。

- ソーピニヨンが野営化した**直接経緯**（回廊行軍の敗北野営か、旧計画の
  停滞か）。`hanjuku_decisions.jsonl` の `sortie_outcome`/`sortie_history`・
  `battle_result`（ソーピニヨン名）、8-4 以前の `camp_found`/`own_camps`
  記録。`first_tick: null`（`camp_status_seen` に tick なし）は
  camp_recheck の名前付き野営確認を経ていないことも示唆する
  （`camp_recheck.py` L353-397）。
- 8-4〜8-6 の各月でソーピニヨンのテントがフレーム内に映ったか
  （映っていた場合に `camp_recall_step` が起動しなかったか）。決定ログの
  `camp_found`/`camp_recall_*` 系の有無。
- `general_statuses.statuses['ソーピニヨン'].tick` の推移（164019 行が
  `CAMP_ROW_STALE_TICKS=600` の再読で更新されなかった理由 —— road モード
  でも stale 検出は走るが、再読対象の選別で除外されたか未実施か）。
- ボスフェーズ移行後（corridor 全制覇）にソーピニヨンが
  `boss_redirect_goal` で実際に再指向されたかの追跡（次以降のラン観測）。
- 監査ポリシーの要否: `_track_camp_abandon` には ch9 機械行軍（road 設計）
  への免除が存在しない。設計どおりの野営を毎回 issue 化することの是非は
  監査ポリシーの問題であり、コード欠陥ではない。変更要否は DSH 判断に
  委ねる（VERIFY-5420 §6 / VERIFY-5545 §8 と同じ取り扱い）。

## 9. 他の判定を採らない理由

- **(a) でない**: 野営将軍への月次帰還タスク非積載は `road` 設計そのもの
  で、例外形・未解決 goal・機構失敗の証跡が incident に無い（flow 空、
  recall 系決定 0、camp_hunt は設計内の非検出記録）。
- **(c) でない**: 野営は実在（`camp_observed`・実測 HP/卵/状態文）し、
  3 ヶ月の算定も契約どおり。「野営していないのに起票」ではない。
- **(d) でない**: #5522/#5431 と違い、滞留の説明がコード内オーナー指示
  （v665/#3936 の road skip）で一意に完結する。帰還機構の成否を調べる
  必要がない（機構を回さないのが設計）。

## 付録: issue 事実 → コード対応表

| issue の値 | 対応箇所（`ef2a351b8` = 現行 main 上の行番号） |
|---|---|
| 判定 `camp_abandoned` / confidence high | `month_audit.py` `_build_camp_abandon_incident` L1638-1727、`CAMP_ABANDON_MONTHS=3` L61 |
| 継続 3ヶ月の数え方 | `month_audit.py` `_camp_age_months`/`_camp_month_starts` L1609-1635、`_track_camp_abandon` L2674-2748 |
| fingerprint 式 | `month_audit.py` `_fingerprint` L3319-3321（呼出 L1724-1725） |
| incident_id（occurrence）式 | `month_audit.py` L1720-1722（`sha256(runtime_id|generation|camp_abandoned|general|first_tick)[:20]`） |
| 城候補「不明」（castle_candidate null） | `post_month_status._marker` L1082-1163、`readers.marker_castle` L597- + `MAP_CASTLE_TOLERANCE=96` L458 |
| ch9 road skip（月次タスク非積載） | `post_month_status._road_skips_checks` L191-203、`_tasks` road 分岐 L234-238（v665/#3936、オーナー 2026-11-11） |
| ch9 ボスフェーズの野営維持 | `recall.py camp_stays` L131-138（= `boss_phase` L90-105） |
| ボス再指向（castle_candidate 非依存） | `recall.py boss_redirect_goal` L108-128、`wave_stale` L125-127 |
| ボス包囲免除（城候補必須・非 road 章向け） | `post_month_status._boss_camp_holds_siege` L116-132、`_boss_move_goal` L168-188 |
| フィールド野営帰還（テント可視が条件） | `orchestrator._chapter9_field_recall` L73-101、`recall.py` camp_found L1442-1513（#3958） |
| corridor 定義・判定 | `hanjuku_chart.py` `CORRIDORS[9]` L1251-1254、`corridor_next` L1279-1285、`GUARD_FREE_CHAPTERS={9}` L1344、`corridor.mechanical`（`strategy/corridor.py` L84-89） |
| 移動ボスセル | `hanjuku_chart.py` `MOVING_BOSS_CHAPTER=9` L1458、`moving_boss_cell` L1495-1497、`castles(9)` のボス除外 L1509-1517 |
| flow 投影（general を落とす） | `month_audit.py` `_summarize_flow` L441-481（汎用ループ L469-480） |
| 決定窓 120 秒 | `month_audit.py` `_build_camp_abandon_incident` 内 `since=now-120` |
| `camp_observed` の由来 | `flows/house.py` L253・L746（house scan の状態行 camp フラグ） |
| camp_status_seen の記録 | `flows/camp_recheck.py` L353-397・L748 |
| v1498 版特定 | `version.py` — `0193891d3`（v1498、`#5452`/#5518）で設定、`89324e141`（v1499）で更新 |

記録: issue #5546 への結論コメントは検品ゲートが行うため投稿しない。
本レーンはコード変更・マージを行わない（read-only 検証のみ）。
`git checkout -b` / `git switch -c` / `git branch` / `git add` / `git commit`
は本環境で実行が承認されなかったため、本ファイルは worktree 直下に
**未コミット**のまま残す（VERIFY-5522・VERIFY-5545 と同じ状況・同じ手順）。
拾う場合は `verify/5546-camp-abandoned` ブランチを切って本ファイルだけを
コミットすればよい（push は不要）。
