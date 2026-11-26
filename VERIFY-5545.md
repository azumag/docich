# VERIFY-5545 — [半熟英雄] 野営放置: ch9 カシス (3ヶ月)

対象: docich#5545（`bug` / `auto-generated` の監査自動起票）
ラン `g1190-ch1v9` / generation `1190` /
`hanjuku-chart-v1498-recall-battle-mode-lost` / 月 `ch9 8-6`
occurrence(incident_id) `d05e5c434f1b69576d1a` / fingerprint `d09ee2cc6216092c`
created_at `1795583171.700812`（2026-11-25 05:06:11Z）

判定: **(b) 設計どおり** — 第9話 corridor 機械行軍モード（ボスフェーズ前）の
野営はオーナー指示（2026-11-11、v665 / #3936）により月次帰還の対象外とされ、
回収はボスフェーズ（`camp_stays`＋ボス再指向）かフィールドでテントが見えた
場合の既存帰還フロー（#3958）に委ねられる。監査の発火は契約どおりの記録であり、
当該将軍の野営が実在する点は `camp_observed: true` で裏取りできる。

調査基盤: worktree `/home/hatch/work/docich-5545`（`origin/main` = `757f24fcd` =
v1509、detached）。観測版は **v1498** = コミット `0193891d3`
（#5452 目的地操舵中の戦闘中断・いどうモード喪失の修正、#5518）。
`git diff 0193891d3..HEAD --` は `flows/recall.py`・`flows/post_month_status.py`・
`strategy/corridor.py`・`orchestrator.py` で**空**であり、`month_audit.py` の
camp 系関数（`_track_camp_abandon`・`_build_camp_abandon_incident`・`_fingerprint`
等）も差分なし —— 現行 main の読解を v1498 の契約へそのまま適用できる（§7）。
read-only 検証: 本番 SSH なし / ラン成果物 `hanjuku_decisions.jsonl`・
`hanjuku_events.jsonl`・`hanjuku_frames/` は VM 側で直接参照せず /
秘密情報なし / コード変更・push・PR なし。
根拠は issue 本文・コメント（GitHub 公開 issue ページ経由。`gh` は本環境で
実行不可）、`origin/main` のコード、git history、過去 VERIFY 記録
（VERIFY-5420 / VERIFY-5431 / VERIFY-5455）。
`handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読。
sha256 再計算コマンドは本環境で実行が承認されなかったため、算式と入力値の
照合のみを記録し、再計算は未実施とする（§1）。

---

## 結論

**(b) 設計どおり。** 根拠は次の 5 点。

1. **発火自体は契約どおりで、誤検知ではない。**
   `CAMP_ABANDON_MONTHS = 3`（`month_audit.py` L61）を閾値に
   `_track_camp_abandon`（L2674-2747）が 3 ヶ月以上 camp の将軍を検知し、
   `_build_camp_abandon_incident`（L1638-1727）が incident を起票する。
   カシスは `months = 3`・`first_month = '8-4'`・`first_tick = null` で、
   月境界加算（8-4 で新規 entry → 8-5、8-6 の月替わりで 1+1+1=3）による
   閾値到達と一致する。`camp_stay.camp_observed = true`・`hp 73/73`・
   `egg いっぱつエッグ`・`broken false`・`キャンプしています` は名簿行の
   実測転記であり、静的雛形ではない。

2. **8-6 時点の ch9 はボスフェーズではなく corridor 機械行軍モードであり、
   野営の非回収はオーナー設計そのもの。**
   - `world.captured = ['ハヤク','タオ']`、lost なし → `_owned` =
     `{アルマムーン, ハヤク, タオ}`（`world.py` L872-875）。
   - `chart.corridor(9)` = `('アルマムーン','ハヤク','タオ','サヌト','オシロガ',
     'ドンドン','オオキク','ナッテ','シマウ','カラ','イソ','イデ！')`
     （`hanjuku_chart.py` L1251-1254、12 城・`'ボス'` は含まない）。
   - `corridor_next(9, owned)` = `サヌト`（L1279-1285）→ `boss_phase` = False
     （`recall.py` L90-105）→ `camp_stays` = False（L131-138）。
   - `corridor.mechanical` = True（`strategy/corridor.py` L84-89:
     guard_free(9) ∧ corridor 非空 ∧ corridor_next ≠ None）。
   - `plan.orders` は全件 `I:c9:` corridor step（サヌト攻撃・タオへの move）
     で機械行軍が稼働中であることを裏付ける。`world.sorties` も
     `タオ>サヌト`・`ハヤク>タオ` の en_route のみ。
   - このモードでは `_road_skips_checks` = True（`post_month_status.py`
     L191-203: guard_free(9) ∧ corridor 非空 ∧ not boss_phase）となり、
     `_tasks`（L225-308）の `road` 分岐（L234-238）が**全将軍を continue**
     して野営将軍に recall/locate/boss_move のいずれのタスクも積まない。
     オーナー 2026-11-11: 「月次の位置チェックも必要がないので省略できる。
     キャンプになった将軍は仕方ないのでこの章は放置」（L193-197）。
     導入は v665 / #3936（`a0c9034c8`）で v1498 よりはるかに古い。

3. **回収経路は設計上存在し、本件の滞留はその適用条件にまだ達していない。**
   - フィールド野営帰還（#3958、commit `3c55e0808`）:
     `_chapter9_field_recall`（`orchestrator.py` L73-101）が
     `corridor.mechanical` なら True を返し、アイドルな map フレームを
     `camp_recall_step`（L2125-2128, L2231）へ渡す。ただし同フローが起動する
     のは**フレーム内にテントが見えた場合だけ**（`recall.py` L1320 以降
     `own_camps`）。カシスの野営は `castle_candidate = 'ボス'`、すなわち
     `marker_castle` が `MAP_CASTLE_TOLERANCE = 96`（`readers.py` L458）内で
     **実測ボスセル**へ束縛した位置にあり、回廊カメラビューには映らない。
     本件の決定内訳に `camp_hunt: 1`（テント非検出の記録、L1376-1406）があり、
     帰還系決定は 120 秒窓に 0 件 —— 「機構が動いて失敗した」形ではなく、
     「見えない野営は仕方なく放置」の設計内状態である。
   - ボスフェーズ（corridor 全 12 城確保後）: `camp_stays` = True となり
     `_tasks` L239-240 が野営将軍を continue（帰還させない）し、フィールド
     野営フローが `boss_redirect_goal`（`recall.py` L108-128）で期限切れボス
     行軍の野営を**ボスの最新実測セルへ再指向**する（オーナー 2026-11-09/
     11-15 の設計、`gather_castle` L141-151 の イデ！集合を含む）。
     `wave_stale` なら通常帰還→次の波（v1258 / #4805）。
   - つまりカシスは「ボスフェーズ集合・包囲の待機予備」として設計どおりの
     野営であり、城候補 `ボス` はその将来役割と整合する。

4. **`_boss_camp_holds_siege` の名義条件は満たすが、ch9 では到達不能。**
   `post_month_status.py` L116-132: `castle_candidate` が
   `chart.is_boss_castle`（L1409-1410、`BOSS_CASTLES[9] = 'ボス'` L455-459）
   かつ `broken is not True` —— カシスは `'ボス'`・broken False で条件自体は
   **成立**。`_boss_move_ready`（L135-146）も hp 73 > 73//2 で成立。
   しかし `_tasks` では (i) ボスフェーズ前は `road` 分岐（L234-238）が、
   (ii) ボスフェーズでは `camp_stays` 分岐（L239-240）が、この検査
   （L241-282 の免除/boss_move）より**先に continue** する。免除機構は
   ch4/10-12 向けであり、ch9 は独自の camp 設計（road skip → camp_stays →
   boss redirect）がこれを包括する。`_boss_unlocked(9)` = True（L206-222、
   ch10-12 以外は常に True）だが、そのゲート自体に到達しないため終端形へ
   影響しない。

5. **`flow: {}`（recall_verification なし）は「帰還未試行」と整合し、
   月次タスクが積まれなかった設計と一致する。**
   本件の incident JSON は `"flow": {}` で、`flow.recall_verification` は
   **存在しない**。`_recall_dispatch_step` が verification を書くのは
   ディスパッチ受理（input_sent ∧ picker_closed ∧ !interrupted ∧
   !scene_covered、`recall.py` L2628-2708）の後だけであり、本件では
   月次タスク非生成（road skip）かつテント非可視で帰還フロー自体が
   一度も起動していないため、投影する flow が無い。
   仮にあっても `_summarize_flow`（`month_audit.py` L441-481）の投影キーに
   `general` は含まれず（'stage','kind','status','result','attempts',
   'reads','steps' のみ）、本人帰属は不能である点は #5431 と同じ。

以上から (a)（実欠陥）・(c)（誤検知）・(d)（判定不能）を排他し、(b) を採る。

---

## 1. issue 提示値 → コード対応（根拠の照合）

| issue の値 | 対応箇所（`origin/main` = v1509 / v1498 と同一） | 検証結果 |
|---|---|---|
| 判定 `camp_abandoned` / kind / `auto_file: true` | `month_audit.py` L1667-1668, L1726 | 一致 |
| `confidence: high` / 判定理由 なし | 同 L1669-1670（camp_abandoned は high 固定・理由配列空。`_confidence()` を通らない） | 一致（記録形の特性） |
| 月 ch9 8-6 / 観測 1 回 / 0.0 秒 / max gap 0.0 秒 | 同 L1654-1662（`_month_summary` を転記） | 一致。月初第 1 観測での評価 |
| 野営 3 ヶ月 / 初回 8-4 / first_tick null | `CAMP_ABANDON_MONTHS = 3` L61、`_camp_age_months` L1626-1635、`_track_camp_abandon` L2674-2747 | 一致。`first_tick=null` は `camp_status_seen.actors['カシス']` に tick 未登録（名前付き野営パネル未観測、`camp_recheck.observe_camp_status` L323-）のため `_camp_age_months` が None を返し、月境界加算のみで 3 に達した形 |
| HP 73/73 / 卵 いっぱつエッグ / broken false / `キャンプしています` / last_tick 163950 | 同 L1674-1688（statuses row を転記） | 一致。row tick 163950 は月 tick 166512 より 2562 古い（前月の名簿読み） |
| castle_candidate `ボス` | 同上（`marker.castle_candidate`）＋ `hanjuku_chart.is_boss_castle` L1409-1410 | 一致。**ボス城の実測セルへ束縛**（`castles(9)` は動的測定時のみ `'ボス'` を含む、L1509-1517） |
| 出撃 確定7 / 発射4 / order_start9 / order_launched4 | 同 L1689-1695（month_sorties.launches・`_departures`・決定カウント） | 一致 |
| signals 4 件 `camp_abandoned` / `camp_abandon_3months` / `battle_grind` / `no_source` | 同 L1649-1653（基本 2 個＋`_signals()` L3197-3246、最大 8 個） | 一致（内訳は §8） |
| `flow: {}` | `_summarize_flow` L441-481 → context.flow 空を転記 | 一致（帰還未起動。§4） |
| incident_id `d05e5c434f1b69576d1a` | 同 L1720-1722: `sha256(f"{runtime_id}|{generation}|camp_abandoned|{general}|{first_tick}")[:20]` | 算式・入力（`g1190-ch1v9\|1190\|camp_abandoned\|カシス\|None`）と一意に対応。**再計算は本環境で実行拒否のため未実施** |
| fingerprint `d09ee2cc6216092c` | 同 L1724-1725 → `_fingerprint` L3312-3314: `sha256("9|camp_abandoned|camp_abandoned")[:16]` | 算式・入力が一意に対応。**再計算未実施**（同一算式の再計算一致例は VERIFY-5420/5455 に実績あり） |
| issue タイトル格式 | `_issue_title`: `[半熟英雄] 野営放置: ch{章} {将軍} ({months}ヶ月)` | 一致 |

補足: `first_tick` が null のため incident_id の raw 文字列は Python f-string の
`None` レンダリングを含む（`...|カシス|None`）。fingerprint は
`chapter|kind|primary` のみで決まるため、signals に `battle_grind`/`no_source`
が入っても値は変わらない。

## 2. ch9 の野営設計（road skip → camp_stays → boss redirect）

| 局面 | 条件 | `_tasks` の野営将軍への扱い | 回収経路 |
|---|---|---|---|
| corridor 未完（本件 8-6） | `corridor.mechanical` → `road=True` | 全将軍 `continue`、**タスクなし**（オーナー「仕方ないのでこの章は放置」v665/#3936） | フレーム内にテントが見えた野営だけ `camp_recall_step` が通常帰還（#3958）。テント非可視の野営は残る設計 |
| ボスフェーズ（corridor 全制覇） | `boss_phase` → `camp_stays=True` | L239-240 で `continue`、帰還しない | フィールド野営フローが `boss_redirect_goal` でボス最新セルへ再指向。`wave_stale` なら通常帰還→次波（v1258/#4805） |

- ボス再指向の到達不能性: `boss_redirect_goal` は `boss_phase` かつ
  `chart.moving_boss_cell(9)` 非空を要求（L117-118）。本件時点は
  boss_phase=False のためこの経路は未適用で、期待される挙動でもない。
- `castle_candidate = 'ボス'` は、移動ボスの skull セルが章内に一度は実測
  されており、その 96px 許容内にカシスのテントがあることを意味する。
  彼は事実上「ボス隣接の期限切れ行軍野営」であり、ボスフェーズ移行後の
  集合・包囲メンバーとして設計と整合する。

## 3. #5522（ch6）・#5431（ch12）との異同

| 観点 | #5522 ミルフィーユ (ch6, g1342, v1495) | #5431 ポセイドン (ch12, 同ラン, v1468) | **#5545 カシス (ch9, 同ラン, v1498)** |
|---|---|---|---|
| camp_stays/road 機構 | **存在しない**（ch6 は corridor 章でない） | **存在しない**（ch12 は corridor 章でない） | **存在し、発動中**（mechanical → road skip） |
| 月次タスク | `{'kind':'recall'}` が積まれうる | 同上（非免除 recall 経路） | **積まれない（設計）** |
| flow 証跡 | —（結論 (d)） | `recall_verification {await_dispatch, dispatched, steps:1}` | `flow: {}`（帰還未起動） |
| 城候補 | 不明 | ワフェルダ（通常城・非免除） | ボス（ボス城・免除条件は名義成立だが未到達） |
| 滞留の構造 | 帰還機構が動いたが未収束（原因一意に確定不能） | 同上 | **機構を動かさないことが設計**（滞留は欠陥でなく保留） |
| 結論 | (d) | (d) | **(b)** |

差異の核心: #5522/#5431 は「帰還タスクを積むべき章で 3 ヶ月残った」ため真因を
runtime ログに委ねる (d) だった。本件は**帰還タスクを積まないことがオーナー
設計**の章であり、「試行して失敗した」問題の存在自体が無い。滞留の説明は
コード内オーナー指示で完結するため (b) が採れる。

なお本件は ch12 クラスタ（#5429/#5430/#5431/#5434/#5437/#5455）と
**同一 generation 1190** だが、当該ランは ch12 22-12（v1468、11-24 16:56Z）の
後に新ゲームへ入り、本件はその新ゲームの ch9 8-6（v1498、11-25 05:06Z）。
章・ボット版・ゲーム世代内局面が異なるため同一経路とは扱わない。

## 4. flow.recall_verification の有無と意味

- **本件には無い**（`"flow": {}`）。`recall_verification` は
  `_recall_dispatch_step` の受理契約（input_sent ∧ picker_closed ∧
  !interrupted ∧ !scene_covered → `status:'dispatched'`、
  `recall.py` L2591-2708）を通過したときだけ `_flow` に書かれる
  （L2682）。`scene_covered`（L449-463, L2595, L2625）は「帰還中に場面が
  前面を覆った」記録で、v1495（#5437、commit `05b0363a1`）で
  scene-covered 越しの dispatch 保持が修正済み —— いずれも本件では
  帰還自体が未起動のため関与しない。
- `camp_arrival_check`（L1217, L1286）は帰還完了後の名簿到着確認であり、
  同様に未起動。
- 仮に verification が存在しても `_summarize_flow` は `general` を投影
  しないため、カシス本人の帰還かどうかの帰属はできない（#5431 と同じ
  投影限界）。本件では flow が空なので帰属問題自体が発生しない。

## 5. 月次タスク `_tasks` とカシスへの積載（終端形）

`post_month_status._arm`（L440-616）→ `_tasks`（L225-308）の分岐順:

1. `road`（= `_road_skips_checks`、本件 True）: 各将軍は
   `location=='castle' and broken` のとき `{'kind':'repair'}` のみ積み、
   即 `continue`（L234-238）。**カシス（location='camp'）はここで終端**。
2. （未到達）`camp_stays` 分岐 L239-240。
3. （未到達）`_boss_camp_holds_siege`/`_boss_move_ready`/`_boss_move_goal`
   の免除・boss_move 分岐 L241-281、および通常 `{'kind':'recall'}` L282。

よって本件の終端形は **「タスクキューに一切の野営関連ジョブが積まれない」** であり、
`{'kind':'recall'}`/`{'kind':'boss_move'}` のどちらにも分類されない。
`_count_boss_camps`（L344-、`_arm` L571 で毎月評価）の免除月数カウンタは
情報として進むが、road モードでは消費先（免除/boss_move 分岐）に到達しない。
v1498 時点の recall 機構（`_recall_dispatch_step`・`scene_covered`・
`camp_arrival_check`）はいずれもディスパッチ以後の検証であり、
**ディスパッチを作る側（タスク積載）が設計上スキップされる本件とは無関係**。

## 6. signals の内訳（`battle_grind` / `no_source`）

- `battle_grind`（戦闘が長引く）: battle 系決定合計 87 ≥ 20（L3217-3218）。
  実況として正しい —— サヌト攻撃・タオ move の回廊行軍が途中の迎撃・
  攻城戦を量産しており、`sortie_history` にも `won`/`lost` が並ぶ
  （アマンディーヌ →タオ `lost`）。敗北・退却→野営化は ch9 の野営発生源
  として設計内であり、カシス個別の野営起因はラン成果物待ちだが、
  「戦闘が長引く」が野営放置の主因という因果は本件では設計側（road skip）
  で完結する。
- `no_source`（出撃元の将軍がいない）: `world.garrison` の各城が ≤1 名
  （L3229-3232）。本件 garrison は `アルマムーン:[] / タオ:[エストラゴン] /
  ハヤク:[]` で max 1 → 発火は計算上正しい。ただしこれは `guard_free(9)`
  （最後の 1 将軍まで出せる、オーナー 2026-11-09）＋ drained 機構による
  **設計どおりの城の空き**であり、野営の直接原因ではない。シグナル名は
  「出撃元の将軍がいない」だが機械行軍の定常状態を表す。
- よって (c) 誤検知でもない: 主判定 `camp_abandoned` は計算上正しく、
  camp の実在も `camp_observed` で裏取りされる。

## 7. v1498 → 現行 main（v1509）の差分カバー範囲

`git log 0193891d3..HEAD` で本件関連 5 ファイルに触れたコミット:

| コミット | 版 | 内容 | 本件への適用 |
|---|---|---|---|
| `cd6d7a24d` | v1509 | ボス戦以外の相手HP1での切り札使用を監査起票 | **非適用**。戦闘監査のみ |
| `64d5112a1` | v1501 | 第10話の進入路・ボス出撃元制約 | **非適用**。ch10 専用 |
| `5e41759ff` | v1461 | battle_result の生存一覧・監査降格を公開 | **非適用**。戦闘監査 |
| `1b13aeb0d` | (#5500) | 実測ボス突入直後の無戦闘誤報を防ぐ | **非適用**。boss_phase_no_battle 系 |
| `6c1fd176f` | (#5525/#5530) | 観測決定型の保持 | **非適用**。監査記録の汎用化 |

- `flows/recall.py`・`flows/post_month_status.py`・`strategy/corridor.py`・
  `orchestrator.py` は **v1498→HEAD で diff 空**。`month_audit.py` の
  camp_abandoned 検知系（`_track_camp_abandon`・`_build_camp_abandon_incident`・
  `_camp_generals`・`_camp_age_months`・`_fingerprint`）にも差分なし。
- したがって「camp_abandoned 検知機構」「ch9 帰還経路」に v1499-v1509 の
  変更は**入っていない**。本件をカバーする修正 PR は存在しないが、
  そもそもカバーを要する欠陥ではない（§結論）。

## 8. 残件（read-only では確定不能、判定 (b) の限界）

判定 (b) は「監査レコードの実在性」と「滞留がオーナー設計で説明完結する」
点への結論であり、以下は VM 側ラン成果物が無いと確定できない。

- カシスが `castle_candidate='ボス'` の位置へ野営化した**直接経緯**
  （回廊行軍の敗北野営か、旧計画のボス指向行軍の停滞か）。
  `hanjuku_decisions.jsonl` の `sortie_outcome`/`sortie_history`・
  `battle_result`（カシス名）、8-4 以前の `camp_found`/`own_camps` 記録。
- 8-4〜8-6 の各月でカシスのテントがフレーム内に映ったか（映っていた
  場合に `camp_recall_step` が起動しなかったか）。決定ログの
  `camp_found`/`camp_recall_*` 系の有無。
- `general_statuses.statuses['カシス'].tick` の推移（163950 行が
  `CAMP_ROW_STALE_TICKS=600` の再読で更新されなかった理由 —— road モード
  でも `stale` 検出 L487-488 は走るが、再読が失敗したか未実施か）。
- ボスフェーズ移行後（corridor 全制覇）にカシスが `boss_redirect_goal` で
  実際に再指向されたかの追跡（次以降のラン観測）。
- 監査ポリシーの要否: `_track_camp_abandon` には ch9 機械行軍（road 設計）
  への免除が存在しない。設計どおりの野営を毎回 issue 化することの是非は
  監査ポリシーの問題であり、コード欠陥ではない。変更要否は DSH 判断に
  委ねる（VERIFY-5420 §6 と同じ取り扱い）。

## 9. 他の判定を採らない理由

- **(a) でない**: 野営将軍への月次帰還タスク非積載は `road` 設計そのもので、
  例外形・未解決 goal・機構失敗の証跡が incident に無い（flow 空、
  recall 系決定 0、camp_hunt は設計内の非検出記録）。
- **(c) でない**: 野営は実在（`camp_observed`・実測 HP/卵/状態文）し、
  3 ヶ月の算定も契約どおり。「野営していないのに起票」ではない。
- **(d) でない**: #5522/#5431 と違い、滞留の説明がコード内オーナー指示
  （v665/#3936 の road skip）で一意に完結する。帰還機構の成否を調べる
  必要がない（機構を回さないのが設計）。

## 付録: 判定シグネチャ

- event / kind: `camp_abandoned`
- signals: `camp_abandoned`, `camp_abandon_3months`, `battle_grind`, `no_source`
- confidence: `high`（固定）
- incident_id: `d05e5c434f1b69576d1a`（式: `sha256("g1190-ch1v9|1190|camp_abandoned|カシス|None")[:20]`、再計算未実施）
- fingerprint: `d09ee2cc6216092c`（式: `sha256("9|camp_abandoned|camp_abandoned")[:16]`、再計算未実施）
- 結論: **(b) 設計どおり** — 第9話 corridor 機械行軍モードの野営放置は
  オーナー設計（v665/#3936 road skip → boss phase で camp_stays＋ボス再指向）。
  検知は契約どおり、実欠陥なし、カバーする既存修正は不要（機構が未適用なのは
  設計であり修正対象ではない）。

記録: 本ファイル（VERIFY-5545.md）。本環境では `git checkout -b` /
`git switch -c` / `git add` が承認されず verify/5545-camp-abandoned
ブランチへのコミットは未実施のため、ファイルはワークスペース直下の
未コミット状態で残す（VERIFY-5431 レーンと同じ扱い）。コミット・issue への
結論コメント投稿・PR 化は検品ゲートが行う。本レーンはコード変更・push・
PR を作成しない（read-only 検証のみ）。
