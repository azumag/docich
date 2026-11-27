# VERIFY-5775 — [半熟英雄] ボスフェーズで月内ボス出撃ゼロ: ch8 ノブナーガ

検証日: 2026-11-27

## 対象と証拠範囲

- issue: azumag/docich#5775
- 判定: `boss_month_zero_sortie`（confidence `high`）
- fingerprint: `b74a8e86236758d9` / occurrence（incident_id）: `52135c9d6725ee15029d`
- ラン: `g1190-ch1v9` / generation `1190` / 観測版 `hanjuku-chart-v1575-ch9-cliff-camp-recall`
- 月: ch8 `10-11`、観測 718 回 / 167.1 秒 / max gap 0.8 秒 / coverage 0.998
- ボスフェーズ: ノブナーガ、残り `ボス`。フェーズ開始 `1795807345.2003`、月内フェーズ 167.3 秒 / 718 観測で `phase_sufficient: true`
- 出撃集計: 確定 0 / 発射 0 / `order_start` 0 / `order_launched` 0。boss departures 0 / boss launch_events 0
- 月末の世界状態: gold 9856。通常城は全て自軍で、`あるまむーん(10)` / `ながはま(6)` / `しらかわ(3)` / `しまばら(2)` など複数城に将軍がいる。行軍中は `A:25cf0f90:M1cee0c:en_route(1731t)`（ベーグル、あるまむーん→いちじょうだに、purpose `move`）と古い `arrived` receipt

### 証拠制約

- 本件ラン `g1190-ch1v9` の本番 decision log はローテーション済みで残っていない。依頼どおり、存在しないログは探さず、issue 本文と現行 main のコードだけを根拠にした。
- `gh` コマンドは実行していない。issue 本文は公開 GitHub ページの read-only 取得で確認した。
- `git fetch` / commit / push などの git 書き込み操作は実行していない。
- `python3` のハッシュ計算 one-liner は実行承認が拒否された。したがってこのセッションでの sha256 独立再計算は未実施で、式と入力値を §1 に残す。検品ゲート側での再計算が必要。
- `DOCICH_HANDOFF_PATH` の参照コマンドも拒否され、handoff 正本は未読。未設定とは断定しない。
- 本検証は read-only。コード変更、テスト実行、本番操作、issue/Slack 投稿は行っていない。

## 結論: **(a) 実欠陥 — ただし直接欠陥は #5758 の companion wait 型ではなく、boss 出撃候補の生成・start 到達不能型**

月内ボス出撃ゼロは実在し、監査の誤検知ではない。boss-only フェーズが月全体を覆い、複数城に将軍・資金がある状態で、`order_start` にすら一度も到達しなかった。現行設計では boss-only フェーズの boss wave は最優先であり、少なくともボス注文・probe・再計画のいずれかが map frame で start へ進むべきである。それが 167 秒 / 718 観測で 0 回だったことは、boss 出撃を優先する設計意図を満たしていない。

一方で issue の証拠には、#5758 と同型の companion 準備カウント（`boss_companion_source_wait` / `boss_companion_source_timeout` / `boss_companion_required` / `boss_companion_timeout` / `order_failed`）がない。`order_start` も 0 なので、本件は「注文が開始された後に companion gate で pending/failed へ戻る」型ではない。直接欠陥は、その前段の「boss 出撃候補が startable な指示として残らない／残っても dispatch へ到達しない」経路と判定する。

## 1. fingerprint / occurrence の生成式と再計算結果

### 1.1 現行 main の式

現行 `src/docich/hanjuku/month_audit.py` では次の通り。

- `_incident_id(identity, chapter, month)`:
  `sha256(f"{identity.get('runtime_id')}|{identity.get('generation')}|{chapter}|{month}".encode('utf-8')).hexdigest()[:20]`（L3516-L3518）
- `_fingerprint(chapter, kind, signals)`:
  `sha256(f"{chapter}|{kind}|{'+'.join(signals)}".encode('utf-8')).hexdigest()[:16]`（L3521-L3523）
- `boss_month_zero_sortie` 系では `_build_incident` が signals の先頭を `boss_month_zero_sortie` に揃え（L3635-L3638）、`_primary_signal` の結果を `[primary]` だけ fingerprint へ渡す（L3697-L3700）。`PRIMARY_SIGNALS` には `boss_month_zero_sortie` がある（L267-L295、特に L272）ため、primary は `boss_month_zero_sortie`。

したがって本件の入力文字列は次の通り。

- fingerprint raw: **`8|boss_month_zero_sortie|boss_month_zero_sortie`**
- occurrence raw: **`g1190-ch1v9|1190|8|10-11`**

issue 記載値:

- fingerprint: `b74a8e86236758d9`
- occurrence: `52135c9d6725ee15029d`

### 1.2 再計算の成否

**この環境では独立再計算は未実施。** `python3` one-liner の実行承認が拒否されたため、sha256 を新規計算していない。検品ゲートが上記 raw 文字列から再計算する必要がある。

補助的な一致根拠として、同じ worktree の `VERIFY-5681.md` は、同じ ch8 / `boss_month_zero_sortie` / primary `boss_month_zero_sortie` の fingerprint 入力 `8|boss_month_zero_sortie|boss_month_zero_sortie` から `b74a8e86236758d9` を得て一致確認済みと記録している。fingerprint の入力が章・kind・primary signal だけなので、これは本件 fingerprint の強いクロスチェックになる。ただし本セッションでの独立計算ではない。occurrence は month `10-11` を含むため VERIFY-5681 の値とは別であり、こちらは未検証のまま残す。

## 2. `boss_month_zero_sortie` の発火条件との照合

現行 main の監査条件は次の通り。

- `_boss_phase` は、通常城がすべて自軍で、boss site が少なくとも 1 つ残り、boss 未撃破のときだけ boss phase とする（`month_audit.py` L603-L637）。
- `_boss_departures` は launch decisions（`order_launched` / `sortie_departed_observed` / `sortie_launch_confirmed` / `sortie_launch_unconfirmed_kept`）のうち、`purpose == 'move'` を除外し、`purpose == 'boss'` または target が boss site のものだけを数える（L3337-L3360）。
- boss phase の月内 coverage は `MIN_BOSS_PHASE_SECONDS = 90` 秒かつ `MIN_BOSS_PHASE_OBSERVATIONS = 20` 観測で sufficient になる（L94-L98、L4154-L4191）。
- confidence は started clean、章境界・月跳びなし、ledger一致、観測 gap、月の長さ、観測数、decision-log coverage、context 有無から決める。理由なしなら high（L3458-L3483）。
- boss phase があり boss departures が 0 なら、confidence high では `boss_month_zero_sortie`、それ以外では `boss_month_zero_unverified` に分類する（L4261-L4269）。

本件は 167.3 秒 / 718 観測で fair window を満たし、coverage 0.998、max gap 0.8 秒、`confidence_reasons` 空、boss departures 0。発火は監査契約どおりであり、誤検知ではない。月末開始フェーズを unverified へ落とす #5137 系 guard の対象でもない。

## 3. `order_start` 0 / `order_launched` 0 の現行コード経路

### 3.1 集計の意味

- `order_start` は、map/deploy 側で注文が具体的な出撃手順へ入った時点で記録される。現行 `flows/map.py` では `next_order` で選ばれた後、起動時に `_record(mem, 'order_start', ...)` される（L2623-L2746）。
- `order_launched` は発射 commit が完了した場合だけ出る。
- したがって `order_start` 0 / `order_launched` 0 は、companion 判定や札選択以前に、ボス注文が一度も出撃パイプラインへ入らなかったことを意味する。

### 3.2 月内は戦闘と月次/月末処理が占有したが、`order_start` 0 は単純な「画面占有だけ」では説明できない

issue の月内 counts では、battle 系が `battles.decisions = 91`、決定総数 201 の約 45% を占める。`monthly_scan_hold` と `battle_grind` が副次 signal として付くのはこの内容と一致する。月末直前の決定表も、world map 確認の後すぐ戦闘へ入り、`battle_command_open_wait` が続き、最後は未分類の確認と `situation_held` で終わる。

ただし、`order_start` が 0 である点は #5681 と違う。#5681 では `order_start` 3 / `order_launched` 0 で、「始まった注文が発射まで完走しない」型だった。本件はそもそも start に入っていない。

現行コードで dispatch 待ちは次のように扱われる。

- orchestrator は foreground owner より先に `order_dispatch_starved` を毎観測評価する（`orchestrator.py` L1873-L1876）。これは #5155/#5681 の blind spot 対策として入っている。
- boss-only phase で starvation が発火すると、post-month scan は free map を出撃計画へ明け渡す（`flows/post_month_status.py` L1831-L1837）。
- map 側も world-map survey や SELECT refocus を starvation 中は保留し、pending order を優先する（`flows/map.py` L2208-L2212、L2588-L2593、L2625-L2634）。
- 逆に `next_order` が「startable なし」を返した場合は `order_dispatch_since` を捨て、pending だが start できない注文は starvation を発火させない（`flows/map.py` L2652-L2664）。

issue の月内 counts に `order_dispatch_starved` はない。したがって現行コードの解釈では、少なくとも map frame が回った観測では `next_order` が startable な boss 注文を見つけられなかった可能性が高い。もちろん、観測版 v1575 での正確な owner 順や map frame 到達率は decision log 不在で確定できないが、単純な「月次処理が 167 秒すべてを隠した」だけではなく、boss 注文の生成/startability 側の失敗を見る必要がある。

### 3.3 boss-only フェーズで boss wave は設計上最優先

現行 `replan.interim_candidates` では、通常城を全て確保し boss 未撃破なら boss を targets の先頭へ置く（`replan.py` L1341-L1365）。boss wave は余剰兵力を連続投入する owner ルールがあり、`_boss_wave_pick` は各城の measured roster から defender を残せる卵持ちを選ぶ（L1395-L1560、L1638-L1688）。さらに `_mechanical_boss_plan` は、通常城を全て確保した後、基本チャートのボス注文と必要な将軍移動を機械的に組み直す（L2873-L3021）。

月末の世界状態は、この設計が動くべき外見条件を満たしている。

- 通常城はすべて自軍で boss だけが残っている。
- gold 9856。
- `あるまむーん` に 10 将軍、`ながはま` に 6 将軍、`しらかわ` に 3 将軍、`しまばら` に 2 将軍が記録されている。
- 単一城しか人がいない sole-starved 状態ではない。

この状態で boss 向けの `order_start` が月内 0 という結果は、「ボスを最優先で攻める」設計から外れている。全候補が measured eggless だった可能性までは issue 証拠だけでは排除できないが、複数城・複数将軍がおり、少なくとも probe や再計画の形跡も boss 出撃へ結び付いていない。設計どおりの resource wait と断定するには証拠が足りず、優先制御が実効していない実欠陥として扱うべきである。

### 3.4 boss companion 準備経路は有界だが、本件ではその経路に入っていない

現行コードでは boss order の companion 準備には 2 段の有界待ちがある。

- `_ready` 側では、boss order について `_boss_companion_source` が見つからず source roster が既知なら、bounded source wait 後に `boss_companion_source_timeout` を記録して dispatch へ進む（`orders.py` L902-L934）。
- deploy 側では、実一覧で candidates が空でも `_boss_companion_wait_expired` までは `boss_companion_required` で待ち、上限後は主人公 fallback または `order_failed` へ進む（`flows/deploy.py` L2548-L2560、L2673-L2678）。

しかし本件の counts にはこれらの decision がなく、`order_start` も 0 である。したがって companion wait の boundedness 自体は現行コードに存在するが、本件の月内停滞を説明する直接経路ではない。#5758 で見えた「`order_start` 後に companion gate で待ち、bounded fail/rearm する」型とは明確に違う。

### 3.5 月次処理・戦闘・決闘の時間消費

月内 counts では battle 系が支配的で、`post_month_status_*`、`month_sub_open`、`house_deferred_sortie`、`chikujou_deferred_sortie` もある。`house_deferred_sortie` / `chikujou_deferred_sortie` は、housekeeping が出撃を優先して控えたことを示す。それにもかかわらず出撃側が start しなかった点は、優先譲りの受益側が動かなかったことを意味する。

after 窓（early seal、約 170 秒）では `month_sub_step` 74、`post_month_*`、`recruit_*`、戦闘系が続き、出撃 0 / 回復 no だった。after 窓は翌月側の月次イベントを含むため 10-11 月内の直接原因とは切り分ける必要があるが、少なくとも月境界後も即座に出撃へ復帰できていない。

## 4. 現行 main にある占有対策と残る疑問

現行 main（`hanjuku-chart-v1592-boss-source-consolidation`）には、#5681 の検証で指摘された占有対策が既に入っている。

1. `order_dispatch_starved` を foreground owner より先に毎観測評価する（`orchestrator.py` L1873-L1876）。
2. boss-only phase で starved の間、post-month scan は free map を出撃計画へ明け渡す（`flows/post_month_status.py` L1831-L1837）。
3. 読めない募集費確認は bounded abort する（`flows/month.py` L2337-L2346）。
4. map の繰り返し前景ワークは starvation 中に譲る（`flows/map.py` L2208-L2212、L2588-L2593、L2625-L2634）。

さらに現行 main には、owner 2026-11-27 系の `_drop_depleted_sources` / `_filter_failed_move_legs` と、boss-only sole-starved への bounded consolidation がある。ただし本件は `あるまむーん(10)` / `ながはま(6)` があるため、全城 1 将軍以下の sole-starved ではなく、v1592 の consolidation だけでは説明も解決もできない。

v1575 は #5681 後の版であり、それでも本件が再発した。したがって「foreground owner より先に starvation を測る」対策だけでは、本件の `order_start` 0 型を防げなかったことになる。現行 v1592 の追加修正で完全に解消したかは、本検証の範囲では証明できない。

## 5. #5758 との異同

### #5758（結論 (b)）

- ch4 四季王。
- `order_start` 10 / `order_launched` 0。
- `boss_companion_source_wait` / timeout、`boss_companion_required` / timeout、`order_failed`、`site_failed_step_rearmed` が揃っていた。
- 適格な卵持ち代役を実一覧で確認できない場合に無理に送らない owner 指定の安全側ゲートで、bounded wait → bounded fail → rearm の設計経路が見えた。

### 本件 #5775

- ch8 ノブナーガ。
- `order_start` 0 / `order_launched` 0。
- companion wait / timeout / failed / rearm の counts はない。
- boss 注文が companion 判定にすら到達していない。
- 月末の世界状態は複数城・複数将軍・gold ありで、#5758 のような「適格 source が実在しない」ことを示す証拠もない。
- 月次処理と戦闘の占有は共通するが、主たる分岐は占有後の発射未完走ではなく、boss 注文の生成/startability 側にある。

したがって #5758 の結論 (b) をそのまま適用できない。本件は #5681/#5155 と同じ fingerprint の再発だが、`order_start` が 0 という点でさらに前段の欠陥である。

## 6. 判定

- **(a) 実欠陥**: boss-only フェーズが月全体を覆い、監査・世界状態・資金・在城数から boss wave を試すべき条件が揃っているのに、boss 注文が一度も `order_start` に入らなかった。boss 最優先の設計意図を満たしていない。
- **(b) 設計どおりではない**: 全候補が measured eggless だった可能性は残るが、issue 証拠だけではその安全側停止を証明できない。逆に probe/replan/housekeeping deferral の形跡が boss 出撃へ結び付いていないため、設計どおりと断定できない。
- **(c) 誤検知ではない**: boss phase、coverage、boss departures 0 は監査契約どおり実測されている。
- **(d) 判定不能ではない**: 詳細な step 内訳や卵状態は不明でも、`order_start` 0 という結果自体が設計の boss 優先制御と矛盾する。ただし欠陥の細部は §7 の限定付きである。

## 7. 残件（結論を変えない限定）

1. fingerprint / occurrence の独立 sha256 再計算は、`python3` 実行承認拒否のため未実施。入力 raw は §1.1 の通り。fingerprint は VERIFY-5681 の同入力 fingerprint と一致するが、occurrence はゲート再計算が必要。
2. decision log 不在のため、月末時点の pending step の target/purpose、各将軍の卵・HP・`boss_no_damage` 状態、`next_order` が None を返した直接条件は未確認。特に「全候補が measured eggless」という反証は issue 本文からは排除できない。
3. v1575 と現行 v1592 の差分のうち、`_drop_depleted_sources` / `_filter_failed_move_legs` / boss consolidation が本件のどの部分をカバーするかは、実ログなしでは確定できない。現行 main での完全解消は未証明。
4. after 窓は early seal で 300 秒未満。翌月 `10-12` に同 fingerprint の再発コメントがあるため、少なくとも翌月も boss 出撃へ復帰していないことは確かだが、本検証は `10-11` の判定を対象とした。

## 検品ゲート再計算（Muse、2026-11-28 08:19 JST）

- fingerprint：`sha256("8|boss_month_zero_sortie|boss_month_zero_sortie")[:16]` = `b74a8e86236758d9` — issue 値と一致。VERIFY-5681 の同入力 fingerprint とのクロスチェックも整合。
- occurrence：`sha256("g1190-ch1v9|1190|8|10-11")[:20]` = `52135c9d6725ee15029d` — issue 値と一致。
- 式は `month_audit.py` の `_incident_id`（L3516〜）、`_fingerprint`（L3521〜）、`_build_incident` の signals 先頭揃え（L3635〜3638）・primary 受け渡し（L3697〜3700）で確認。
