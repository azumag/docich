# VERIFY-5444 — [半熟英雄] 出撃失敗: ch12 ガスパチョ

- 対象: docich#5444（`bug` / `auto-generated` の監査自動起票）/ fingerprint `4a90b2d0af973f56` / occurrence(incident_id) `aba4b70b1a1996ac1901`
- 判定: `sortie_failed`（confidence `high`）/ 決定 `sortie_launch_failed` / 理由コード `sortie_launch_not_observed` / misses 3
- シグナル: `sortie_failed` / `sortie_launch_not_observed` / `monthly_scan_hold` / `battle_grind` / `stuck_march`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1468-battle-menu-bounded-episode`（コミット `00c2b6503`「keep the scrolled battle-menu episode bounded and column-scoped (#5422)」で確認）
- 月: ch12 21-2（観測 975回 / 142.5秒 / max gap 0.8秒 / 974 ticks / started_clean）
- 出撃: ガスパチョ / step `A:18689078:M2419a2` / アルマムーン → ディランダ（**自城**。`world.captured` に含まれる）/ purpose 記録なし（自城宛てのため実質 move 系）
- 検証ベース: worktree `/home/hatch/work/docich-5444` detached HEAD `3402a6944`（`BOT_VERSION = 'hanjuku-chart-v1489-discharge-last-general'`。v1468 から +21。関連修正 v1477(#5442)/v1481(#5450)/v1482(#5441)/v1483(#5451)/v1487(#5467) の差分を直接確認済み）
- 検証範囲: read-only（本ファイルの作成以外の編集なし。実装変更・本番 SSH なし。ラン成果物 `hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/` は VM 側 `g1190-ch12-run` のため直接参照不可）
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）

## 環境制約メモ

- 本環境では `gh`・`python3`・`sha256sum` 等のハッシュ計算・外部 CLI が実行拒否される（VERIFY-5431 と同制約）。issue 本文は公開 GitHub issue ページの webfetch 取得で代替し、incident JSON 全量を取得済み。
- fingerprint 理論値: `sha256("12|sortie_failed|sortie_failed")[:16]`（`month_audit.py` `_fingerprint` L3105-3108、sortie 経路は signals=`[event['kind']]` の1要素のみ L1108）。**本環境では再計算不能のため未照合**。別環境で同式を照合済みの先例あり（VERIFY-5455 が `12|zero_sortie|monthly_scan` を一致確認）。`4a90b2d0af973f56` と一致するか、手段のある環境での確認を残件とする。
- incident_id 理論値: raw = `runtime_id|generation|kind|reason_code|step_local|general|target|int(now)` の sha256[:20]（L1102-1106）。issue の `created_at = 1795534155.673852`（= closed_at）から `int(now)=1795534155` が確定するため、期待 raw は **`g1190-ch1v9|1190|sortie_failed|sortie_launch_not_observed|M2419a2|ガスパチョ|ディランダ|1795534155`**。構成要素は全て既知で外部再計算可能（本環境では不可）。

## 結論: **(d) 判定不能 — ただし「自城宛て move の発射が名簿証拠なしの推定巻き戻しで失敗化した」既知家族（実欠陥側）の疑いが強い**

発火自体は監査契約どおりで誤検知ではない（§1）。misses=3 が miss_limit を超えて `sortie_launch_failed` へ到達した機械的経路も正しい。判定が割れるのは一点:

- **(a) 実欠陥**: 3回の発射コミットは実際に部隊を出しており、観測層が実測証拠を得られないまま推定で巻き戻し・失敗化した（自城宛て move に対する kept パス未整備）。
- **(b) 設計どおり**: コミット A 送信後に部隊を生まない真の不発であり、設計済みの blind 期限切れフォールバックが正しく打ち切った（VERIFY-5206 先例）。

incident JSON の範囲ではどちらも排除できず、決着にはラン成果物が要る（§5）。両側の根拠を §3 に整理する。

### 1. 発火の正当性: 契約どおり（誤検知ではない）

- `_rollback_unverified_launch`（`map.py` L3261-3316）: 巻き戻しごとに `launch_miss[step]+=1`。`miss_limit = LAUNCH_MISS_LIMIT(2)`、ただしその観測の `markers` がちょうど1個なら `×3=6`（g1190 タプロベイン→タタール: 主人公自身の▲のみの視野は新部隊を区別不能とする安全弁）。
- **misses=3 で failed が出た条件**: 3回目の巻き戻し観測で `len(markers)!=1`（0 or ≥2 → limit 2 → 3≥2 で failed）。一方 2回目が単なる unverified で済んだ条件は、その観測で `len(markers)==1`（limit 6 → 2<6 で生存）。巻き戻しごとに視野のマーカー数が異なる = 混雑マップと整合する。
- `SORTIE_TERMINAL`: `sortie_launch_failed` → `('sortie_failed','failed')`、理由コード `sortie_launch_not_observed` 固定 → issue の分類・理由と一致。
- `order_launched 0` は既知の表示アーティファクト: `decisions` 集計が `Counter.most_common(30)` 切り捨て（`_decision_counts` L2955-2958。本件 counts はちょうど30キー）で圏外に落ちた可能性（VERIFY-5206 と同型）。`order_start 2` / `launch_events 2` / `departures 1` は M2419a2 の発送試行（dispatch 記録）を示すが、実測出撃ではない。
- `decisions.before` は月末30行のみ。6件の `sortie_launch_unverified` のうち末尾1件（`A:18689078:Mc5d526` オレガノ）＋ failed レコード（M2419a2 ガスパチョ）だけが見える。

### 2. 巻き戻し経路の同定: blind 期限切れ（推定）— 名簿実測ではない

- 両レコードの reason はデフォルト文言: `出撃確定の入力後に全体マップへ部隊▲が現れないため、出撃を未発射に戻す` / `…再試行の上限に達したため指示を失敗にする`。名簿決着なら `出撃元の将軍一覧にまだ出撃した将軍がいるため…`（`settle_launch_probes_from_roster` L3215-3258 の経路）になるはず → **少なくとも記録上見える巻き戻しは全て `LAUNCH_PROBE_ROSTER_TICKS=200` 期限切れの推定経路**（L3112-3150）。
- 対象ディランダは自城 → `own_target` 分岐（L3168-3185）: 自城宛て move は部隊▲を描かない（コードコメントが g1163 オシロガ>ドンドンの実測を引用）ため **`limit=1` で1回の調査読み後すぐ blind 化** → `_read_source_roster` がアルマムーン名簿読みを要求 → しかし各 200tick 窓で名簿 read が決着に間に合わず期限切れ。
- 名簿証拠が来なかった文脈: アルマムーン駐留は12名で名簿はスクロール（完全読みにはページ往復が要る）。さらに月内は月次スキャン（`post_month_location_confirmed` 24 / `post_month_roster_marker_mapped` 26 / `post_month_status_close_probe` 24）＋戦闘（`battle_*` 60+、battles_judged 12、防衛戦4件）がマップを占有しており（`monthly_scan_hold` シグナルと整合）、名簿読みの発注・遷移が構造的に飢餓していた可能性が高い。

### 3. 真不発 vs 偽巻き戻し — 判定不能の根拠

**(a) 偽巻き戻し側の根拠**

- 自城宛て move で▲が出ないのは既知仕様であり、「名簿証拠なしの期限切れ巻き戻しが実発射を潰す」偽巻き戻し家族はコードコメントに実例が複数ある（g1133 ch6 portrait marcher / g1190 ch9 portrait marcher / g1163 自城 move 偽失敗）。
- その家族への対策 `kept` パス（巻き戻さず名簿・到着・期限の実測へ委ねる）は **ボス宛て (`chart.is_boss_castle`) と `PORTRAIT_MARCH_CHAPTERS={9}` にのみ**整備済み。**ch12・自城宛ての move には kept が無く、blind 期限切れ = 常に推定巻き戻し**。発射が実在すれば、行军中の将軍を帳簿上アルマムーンに復帰→再選択対象化→注文 failed 化、という実害を生む。
- ラン文脈と整合: VERIFY-5455 で同ラン同 ch12 は (a) 確定（v1468 計画層がアルマムーン発の長距離行軍を出し続け、吸収・野営化していた）。`stuck_march` シグナルの実体（`A:5f77e647:M4358d5` フリオニール arrived 表示のまま 28153 tick 滞留）も長距離行軍の停滞を示す。同一 M-step が4 rid（`21fd0bfe`/`2b234764`/`5f77e647`/`18689078`）で再発行され続け、issue コメントに ch12 21-6 で同署名の再発（デーメーテール、確定1/発射3）がある。

**(b) 真不発側の根拠**

- VERIFY-5206 先例: コミット済み出撃が部隊を生まないことは実在し、その検出・上限打ち切りは契約どおりと判定された。blind 期限切れフォールバック自体は設計済みの退路（コードコメント「only the wait expiring falls back to the old rollback」）。
- `decisions.before` 末尾は防衛戦（シェーブル、okunote_select 14/scroll 12/egg_preempt 4 等）→ `situation_held`（unknown 画面）→ select_focus/select_centered → world_map と、直前まで画面が荒れており、コミット A が実際に受理されない機構（発射手続き途中での戦闘・テキスト割り込み）があり得る。v1487(#5467) のオーナー診断も ch4 だが同型の「出撃先選択中の防衛戦中断 → launched_unconfirmed 放置」を実例として記述している。

**検討して解消した疑い（誤検知・帳簿異常ではない）**

- incident の `world.garrison` でガスパチョがアルマムーン12名に不在 → 一見「実発射の証拠」に見えるが、incident の world コンテキストは `audit_before = month_audit.capture(state)`（`brains/hanjuku/bot.py`、**decide() 前の bounded snapshot**）由来。スナップショットは「コミット#3 の `_garrison_move` 除去後・巻き戻し#3 の再追加前」の中間状態を映しただけで、帳簿経路どおり。**実発射の証拠にも簿記異常の証拠にもならない。**
- 同 tick の `Mc5d526` オレガノ巻き戻し（同 rid・同デフォルト reason = 同じく blind 期限切れ）は source が incident 非表示で、ワフェルダ駐留簿記と整合する説明が複数ありこちらも判定不能。
- `plan.chart_plan` の唯一の order（`M44c280` アトランテ→ファンゴル attack「暫定: 膠着のため…」）は別 order であり、M2419a2 自体は chart_plan の orders 一覧に載らない interim 発注と見える（purpose 記録なし・自城宛て = move 系の兵力移動）。

### 4. バージョンカバレッジ（v1468 → HEAD v1489）

- v1477(#5442 `911ffbb55`) / v1481(#5450 `4f7d93e59`): boss_camp_recall 系。発射観測経路に無関係 → 非該当。
- v1482(#5441 `f8793965f`): 「bind departure failure to the current request」= 発射失敗とリクエスト receipt の紐付け厳密化。再試行の帰属を正すが、blind 期限切れの推定巻き戻し機構は不変。
- v1483(#5451 `a4af58faa`): ch12 戦略層の修正（主人公をディランダへ先に進めてからボス）。M2419a2（アルマムーン→ディランダ move）が狙った集結の下流側キャンペーン欠陥を直すが、発射観測層は不変。VERIFY-5455 の (a) 修正とは別層。
- v1487(#5467 `ad9c5c7df`): ch4 四季王限定（閉鎖地点除外・検証不能出撃の pending 戻し上限・戻り行軍）。章限定で非適用。ただし (b) 側機構の存在証拠（§3）。
- まとめ: **own-castle move の blind 期限切れ推定巻き戻しは HEAD でも未修正**。実発射が証明されれば欠陥は現存 (a)、真不発なら検出は正しく (b)。

### 5. 残件: 確定に必要なラン成果物（`g1190-ch12-run` / ch12 21-2）

- `hanjuku_decisions.jsonl`: 当該月の全レコード。特に 6件の `sortie_launch_unverified` の `observed_metric`（general/source/target/markers/reads/misses — M2419a2 の3コミットが全てガスパチョか、blind 移行レコード `roster_pending` の有無）、`order_launched`/`order_start` 行（step・purpose・general）、アルマムーン名簿 read（`garrison_seen` / roster / status 系レコード）の有無と時刻、ガスパチョ・オレガノの到着（`sortie_arrived`/`march_overdue`）・野営・locate/post_month タスク結果。
- `hanjuku_events.jsonl`: コミット時刻前後の departure input receipt（`_departure_receipt_sent` の request_trace/a_inputs — 送信自体は journal 済みと推定される）と、送信後の部隊生成・戦闘割り込み・名簿表示の実測。
- `hanjuku_frames/`: 各コミット直後〜期限切れまでの全体マップ・城名簿フレーム（実部隊の有無の目視確認）。
- decisive な点: コミット後 200tick 窓内にアルマムーン名簿 read が一度も無かったことは incident からほぼ確定的（デフォルト reason）なので、残る判別子は **将軍が実際に城を出たか**（到着・野営・ロケーション記録・フレーム）の一点。

### 6. 切り札サブシグナルについて

issue テンプレの切り札節（在庫 None / 携行なし / 計画cards None / 型「計画が空（自動携行の対象・v1448 以降）」）は副次情報で、発射が未確定のため携行の有無は失敗原因に無関係。v1448(#5336) は計画空時の自動携行のみで発射観測は変更しない。#5317（計画が空の明示的 cardless order → (b)）との切り分け: 本件は cardless 選択の問題ではなく、自城宛て発射の観測不能リトライ上限到達であり原因層が異なる。

## まとめ

- 監査の発火・分類・fingerprint/incident_id の構成: 契約どおり（誤検知ではない）。ハッシュ照合のみ本環境制約で未実施（期待 raw 文字列を上記に記載、外部再計算可能）。
- 実質判定: 全巻き戻しが blind 期限切れの推定経路 → 真不発 (b) か偽巻き戻し (a) かは incident 内で決着しない → **(d) 判定不能**。要 `hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/`。
- 傾向メモ: own-castle move＋名簿受領飢餓という既知偽巻き戻し家族の前提条件が揃っている。もし実発射が確認されれば、修正方向は kept パスの適用範囲を portrait 章・ボス宛て以外（自城 move / ch12）へ広げることになる（現行未修正）。
- 検証ブランチへの記録コミットのみ。issue コメント・push・コード変更なし。
