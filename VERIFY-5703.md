# VERIFY-5703 — [半熟英雄] ボスフェーズで月内ボス出撃ゼロ: ch12 クーモン

- 対象: azumag/docich#5703 / fingerprint `a3452e7bc09c8aab` / occurrence(incident_id) `8275afb91775d63a95d1`
- 判定: `boss_month_zero_sortie`（confidence `high`）/ event `month_boss_zero_sortie` / kind `boss_month_zero_sortie`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1525-sortie-cards-eligible`
- 月: ch12 `26-2`（観測 2412 回 / 522.1 秒 / max gap 0.9 秒 / tick 225792–228203）
- 再発コメント: ch12 `26-3` / incident_id `b56932481d0f6056d8b2`（観測 806 回 / 186.3 秒 / max gap 0.7 秒、確定 0 / 発射 2 / order_start 5）
- 検証ベース: worktree `docich-5703` の `HEAD` = `5dbcf1af7`（`origin/main`、`hanjuku-chart-v1565-camp-recall-dome-text-hold`）。観測版 v1525 の base commit は `05f05c050`（`BOT_VERSION='hanjuku-chart-v1525-sortie-cards-eligible'`）を確認した
- 検証範囲: read-only。issue 本文・再発コメント・公開された証拠 JSON、v1525 と現行 main の静的コード照合。push・PR・issue コメント・Slack 投稿・本番 VM 操作・コード変更なし
- `handoff.md` 正本は、この環境では `DOCICH_HANDOFF_PATH` の確認自体が拒否されたため未読と記録する（AGENTS.md §1）。内容は推測していない

## 結論: **(b) 設計どおり — v1483 の boss_staging は動作しており、#5448 の欠陥再発ではない**

26-2・26-3 の「月内ボス出撃 0」は事実として発生したが、証拠が示す主経路は #5448 の本国発ボス行軍の失控ではなく、v1483 で意図的に導入された **boss_staging の gathering 期間**である。ch12 では主人公どうしが拠点ディランダへ着き、拠点の将軍一覧へ実読みで載るまで、ボス出撃を全城的に抑止するのが設計である。本件では `boss_staging_started` は 1 件ある一方、26-2 内にも 26-3 とほぼ重なる after window にも `boss_staging_ready` / `boss_staging_abandoned` はなく、主人公を乗せた relay 方向の行軍と複数の非ボス移動が進行中だった。したがって v1483 が無効だったとは判定しない。

あわせて、26-2 の high 自動起票はボスフェーズの月内覆盖が約 72.3 秒しかなく、観測版 v1525 に未搭載だった fair-window 判定の不足によるものである。現行 main の v1551（#5137、commit `fad137d43`）では `short_boss_phase` として low / `boss_month_zero_unverified` に落ち、自動起票されない。26-3 は同じボスフェーズが月またぎで継続するため現行 main でも high 判定自体は残りうるが、利用可能な証拠では staging の許容期限内の gathering であり、実欠陥とは判定しない。したがって本件の (b) は「設計どおり」との意味であり、26-3 型の監査レコード自体が現行 main で必ず消えるという意味ではない。

## 1. issue・コメント・証拠 JSON の確認範囲

issue 本文、26-3 の再発コメント、検証割当コメント、kanban 登録コメントを確認した。26-2 本文の主要値は依頼文と一致する。

- 判定 `boss_month_zero_sortie` / confidence `high`
- 月: ch12 `26-2`、観測 2412 回 / 522.1 秒 / max gap 0.9 秒
- ボス: クーモン / 月内ボス出撃 0 / launch_events 0 / フェーズ開始 `1795709334.019797` / 観測 292 回
- 出撃: 確定 0 / 発射 5 / order_start 15 / order_launched 5
- evidence.identity: game `hanjuku-hero` / generation `1190` / runtime_id `g1190-ch1v9`
- evidence.bot_version: `hanjuku-chart-v1525-sortie-cards-eligible`
- incident_id: `8275afb91775d63a95d1` / fingerprint: `a3452e7bc09c8aab`

証拠 JSON は issue に公開されている範囲をすべて読んだが、レンダリング仕様上 `json.dumps(...)[:24000]` で切られる（現行 `src/docich/hanjuku/month_audit.py` L4802-L4805）。本件も `plan.orders` の途中で終わり、JSON としては閉じていない。したがって `world.sorties`・`launched_orders` 全体には到達できない。26-3 コメントにも詳細 JSON はないが、26-2 の after window counts が 26-3 とほぼ同じ区間を覆盖するため、今回は counts・chart_plan・末尾決定・after counts・再発コメント集計で staging 状態を十分に切り分けられる。

## 2. fingerprint / occurrence の式照合

builder の現行実装を確認した。

- `_incident_id`（`src/docich/hanjuku/month_audit.py` L3389-L3391）: raw = `runtime_id|generation|chapter|month`、sha256 先頭 20 hex
- `_fingerprint`（L3394-L3396）: raw = `chapter|kind|'+'.join(signals)`、sha256 先頭 16 hex
- `_primary_signal`（L3382-L3386）と `_build_incident`（L3495-L3498）: fingerprint には全 signals ではなく primary 1 件だけを渡す。primary は `PRIMARY_SIGNALS`（L267-L295）の優先表で決まり、`boss_month_zero_sortie` は同表 L272 にある。本件の primary は `boss_month_zero_sortie` なので、raw の signals 部分は全 signals ではなくこの 1 件だけである

理論 raw は次のとおり。

| 項目 | raw | 期待値 | issue/コメントの値 | 照合 |
| --- | --- | --- | --- | --- |
| fingerprint | `12\|boss_month_zero_sortie\|boss_month_zero_sortie` | `a3452e7bc09c8aab` | `a3452e7bc09c8aab` | **文字列一致** |
| occurrence 26-2 | `g1190-ch1v9\|1190\|12\|26-2` | `8275afb91775d63a95d1` | `8275afb91775d63a95d1` | **文字列一致** |
| occurrence 26-3 | `g1190-ch1v9\|1190\|12\|26-3` | `b56932481d0f6056d8b2` | `b56932481d0f6056d8b2` | **文字列一致** |

この環境では `python3` と `sha256sum` の実行がどちらも拒否されたため、検証者自身の独立再計算は **未実施**である。上記は builder の式・raw 文字列と、issue/検品ゲート記載値の厳密な文字列照合であり、sha256 をこちらで再計算した結果ではない。検品ゲートが両方の一致を確認済みという前提情報と矛盾する点は見つからなかった。

## 3. v1525 / 現行 main の boss_staging 実装

### 3.1 設計本体

v1483（PR #5451、commit `a4af58faa`）の機構は現行 main に次の形で残っている。

- `src/docich/hanjuku_chart.py` L1323-L1347 `boss_staging(chapter)`: ch12 では boss order の source を拠点、主人公の relay から path を導出する。テストでは `castle='ディランダ'`、path = `('アルマムーン', 'ブラックランド', 'ヘリオポリス', 'ディランダ')`
- `src/docich/hanjuku/strategy/routes.py` L343-L360 `staging_arm`: relay 全城が自軍なら `gathering` を開始し `boss_staging_started` を記録
- 同 L370-L424 `staging_update`: 拠点ディランダの将軍一覧に主人公 `どうし` を実読みで確認したときだけ `ready` へ進み `boss_staging_ready` を記録。拠点/中継城喪失、ready 非再確認の反復、`STAGING_MAX_TICKS=6000` 無進捗、`STAGING_MAX_TOTAL=24000` 超過で `boss_staging_abandoned`
- 同 L427-L440 `staging_blocks`: gathering 中は全ボス出撃を止め、ready 後は拠点以外発だけを止める。`purpose='move'` と roster probe は対象外
- `src/docich/hanjuku/strategy/orders.py` L863-L870 `_ready`: boss target で staging_blocks に掛かる order は開始できない
- 同 L1279-L1313 `_relaunch_overdue_boss`: overdue ボス出し直し前に `staging_update` を評価し、staging_blocks 適用対象は出し直さない
- `src/docich/hanjuku/strategy/replan.py` L565-L631 `_staging_moves`: 主人公を 1 hop ずつ先に進め、他の将軍を追従させる。主人公移動を先頭に返す
- 同 L1386-L1391 `_boss_wave_pick`: `gathering` なら `None`、つまり新しいボス波を計画しない。`ready` なら source を拠点だけに限定する
- 同 L1667-L1675: staging moves を他の移動生成より先に積み、relay 上の将軍を他用途へ引き戻さない

重要なのは、`STAGING_MAX_TICKS` のコメントが「1 relay hop は launch→roster で 560..5000 tick 実測」としている点である。本件の 1 か月は 26-2 で 2411 tick、26-3 は観測 806 回規模なので、boss_staging の gathering が複数月にまたぐことは設計上あり得る。

### 3.2 v1525 と現行 main の差分

- v1525 base `05f05c050` と現行 main `5dbcf1af7` で、`boss_staging` の状態遷移、`_staging_moves`、`_boss_wave_pick` の gathering/ready 分岐、`_ready`・`_relaunch_overdue_boss` の抑止に差はない。`git diff 05f05c050..HEAD` でも staging 本体の hunk は出ず、routes.py の差分は移動将軍の実測一致補強のみだった
- v1525 には `_boss_month_coverage` / `MIN_BOSS_PHASE_SECONDS` / `short_boss_phase` がない
- 現行 main には v1551（commit `fad137d43`）が入り、`MIN_BOSS_PHASE_SECONDS=90`・`MIN_BOSS_PHASE_OBSERVATIONS=20` を使って月内 boss phase が短い月を low / unverified へ落とす（`month_audit.py` L94-L98、L3878-L3915、L3974-L3984）
- 現行 main にはさらに v1556（commit `49b6109a1`）が入り、boss phase の dispatch 枯渇を月次・戦闘・ジャンプ系 owner より先に測り、post-month scan へ `boss_dispatch` を渡してマップを明け渡す。v1557（commit `b87e2b521`）は order_start backoff を実開始まで消費しないようにしている

## 4. 26-2 の証拠が示す staging 状態

### 4.1 staging は gathering まで進み、ready/abandoned ではない

月内 decision counts には `boss_staging_started: 1` がある。一方、`boss_staging_ready` と `boss_staging_abandoned` は counts に存在しない。したがって 26-2 終了時点で少なくとも記録上は `gathering` のままで、ready にも fallback にも到達していない。すなわち、拠点ディランダの将軍一覧に主人公どうしが載ったという実読み確認はなく、abandoned の理由・tick も存在しない。

これはコードとも一致する。

- gathering では `_boss_wave_pick` が `None` を返すので新規ボス波は計画されない
- 仮に旧計画・採用計画に boss order が残っていても、`_ready` と `_relaunch_overdue_boss` が `staging_blocks` で止める
- したがって gathering の間に「月内ボス出撃 0」になるのは異常ではなく、v1483 の直接の設計結果である

### 4.2 主人公を乗せた中継行軍と非ボス移動は実際に進んでいた

証拠 JSON の末尾決定と計画には、少なくとも次の進行がある。

- `A:5f77e647:Mfc34bd` は計画上「ボス出撃の拠点ディランダへアルファルファをアルマムーンからブラックランドへ追従させる」move order
- 同じ step の `sortie_launch_confirmed` は general `どうし` で記録されている。理由は「出撃元の将軍一覧の1ページに出撃した将軍がいないため出撃成立とみなす」で、launch probe の実出撃将軍として主人公どうしが確認されている
- 行軍中一覧には `A:5f77e647:Mfc34bd:en_route(104t)` がある。すなわち主人公を実際に乗せたアルマムーン→ブラックランド方向の行軍が生きている
- さらに `A:5f77e647:M7d03cc:en_route(74t)`、および `A:5f77e647:M2419a2:launched_unconfirmed(20t)` がある
- `M2419a2` はソーピニヨンのアルマムーン→ディランダ move で、末尾 30 決定では order_start、move_general_picked、sortie_departure_requested、order_launched_unconfirmed まで追える

`Mfc34bd` の計画将軍と実出撃将軍が一致しない点は、カーソル/代替選択を伴う出撃として今後の個別解析対象にはなりうる。ただし本件の判定に必要な事実は、**実出撃将軍どうしが relay の第 1 hop へ向かう行軍として確認されている**ことであり、v1483 が何も動かしていない #5448 型ではないことである。

### 4.3 order_start 15 / order_launched 5 とボス出撃 0 は矛盾しない

まず、order_start/order_launched は **月全体**の集計であり、月末 72.3 秒だけの boss phase の集計ではない。本件では通常城の攻略中だった時間と boss phase が混ざっている。

行軍中一覧の年齢からもその構造が見える。月末 tick は 228203 で、boss phase 開始時刻を月内 tick レートへ換算するとおよそ tick 227870 付近である。`A:4540e6dc:M5eb723:en_route(1110t)` はおよそ tick 227093、`A:4540e6dc:Md73af3:en_route(969t)` はおよそ tick 227234 に出ており、どちらも boss phase 開始前の行軍である。詳細は JSON 切断で読めないが、少なくとも boss phase 中のボス dispatch ではない。一方、`Mfc34bd`（104t）、`M7d03cc`（74t）、`M2419a2`（20t）は boss phase 内の短い行軍である。このうち `Mfc34bd` と `M2419a2` は末尾決定・計画から move / staging 関係の行軍と分かるが、`M7d03cc` の目的は公開 JSON の切断範囲外である。

ボス誤分類の有無については、監査実装から次のように切り分けられる。

- `_commit_departure` は `order_launched` に必ず `target=order['target']` を渡す（`flows/map.py` L3004-L3006）
- `_finish_order` は `launched` では purpose/source を自動補完せず、`_deploy_context` も purpose を持たないため、この経路の `order_launched` では purpose は通常欠ける（`strategy/orders.py` L2793-L2820、`flows/deploy.py` L888-L909）。そのため generic `departures=5` には move が混入しうる。これは集計上の注意点である
- ただし `_boss_departures` は target も見る（`month_audit.py` L3210-L3233）。purpose が欠けていても target がボス城なら boss launch として数える。purpose が記録された `move` だけはボス出撃から除外するが、上記の通り launched では purpose 欠落があり得る
- 結果として boss departures / launch_events が 0 である以上、5 件の `order_launched` にボス城を target とする行軍は混入していないと読める。boss phase 前の通常城向け出撃、または自城/中継城を target とする transfer / roster / staging 用 move である
- `order_launched_unconfirmed` は `_boss_departures` の対象外であり、本件末尾の `M2419a2` は目的ディランダの move である

したがって「発射はあるのにボス出撃が 0」は、ボス order の誤分類ではなく、(1) 月前半の非ボス出撃が月集計に残っていること、(2) boss phase 後は staging 用または支援の非ボス移動が先行し、ボス波が意図的に保留されていることで説明できる。

## 5. 26-2 のボスフェーズ覆盖と v1551 のカバー

数値から月内覆盖を計算すると、ボスフェーズは月末に始まったばかりである。

- 月初 first_at: `1795708884.081321`
- boss phase started_at: `1795709334.019797`
- month close / incident created_at: `1795709406.360328`
- 月内 boss phase: `1795709406.360328 - 1795709334.019797 = 約 72.3 秒`
- 月全体: 522.1 秒なので、boss phase は月末の約 14% にすぎない

観測 292 回は密だが、現行 v1551 の閾値は秒数と観測数の両方を要求する。`MIN_BOSS_PHASE_SECONDS=90` 未満なので、同じ入力を現行 main に入れれば `phase_sufficient=False`、`confidence=low`、kind は `boss_month_zero_unverified`、auto_file は false になる。26-2 の high 自動起票は、v1525 にこの fair-window 判定がなかったことによるもので、現行 main ではカバー済みである。

## 6. 26-3 再発コメントの評価

26-3 のコメントには詳細 JSON がないため、staging counts・個別 step までは確認できない。確認できる値は次だけである。

- 判定 `boss_month_zero_sortie` / high
- ボス月次 0 / クーモン
- 確定 0 / 発射 2 / order_start 5
- 月: 観測 806 回 / 186.3 秒 / max gap 0.7 秒
- incident_id `b56932481d0f6056d8b2`

26-3 コメント単体には詳細 JSON がないが、26-2 の証拠 JSON に埋め込まれた after window が 26-3 とほぼ同じ区間を直接覆盖している。

- after window: `1795709406.360328`–`1795709592.8526762`（186.49 秒）。開始は 26-2 の close と同じで、長さは 26-3 の 186.3 秒とほぼ一致する
- after counts: `order_start: 5` / `order_launched: 2`。26-3 コメントの order_start 5 / 発射 2 と一致する
- 同じ after counts には `move_general_picked: 2` / `roster_probe_complete: 2` / `sortie_move_arrival_observed: 1` がある
- 公開証拠全体で `boss_staging_started` 以外の staging イベントはなく、after window にも `boss_staging_ready` / `boss_staging_abandoned` はない

したがって 26-3 の発射 2 件は、ボス attack ではなく move / roster 系の dispatch と読むのが自然である。そして少なくとも after window が覆盖する 26-3 の範囲では、staging は ready にも abandoned にも進んでいない。boss phase tracker は月またぎで継続し（`month_audit.py` L2889-L2924）、26-2 と 26-3 は同じ ch12 クーモンなので、v1525 が同じ episode の zero sortie として再コメントしたこと自体は整合する。ただし 26-3 コメントには boss phase の月内観測数がなく、現行 v1551 基準での sufficiency だけは厳密に再計算できない。

tick 関係も gathering 継続と整合する。

1. 26-2 末の `Mfc34bd` は、実出撃将軍どうしを乗せたアルマムーン→ブラックランドの第 1 hop で、月末 tick 228203 時点の年齢は 104 tick。launch はおよそ tick 228099 である
2. 26-2 では observations 2412 / duration_ticks 2411 なので、この監査では observations-1 が tick 幅に対応する。26-3 の観測 806 回なら tick 幅はおよそ 805、月末はおよそ tick 229008 と見積もれる
3. 主人公の第 1 hop launch から 26-3 末までの経過はおよそ 909 tick にすぎない。設計コメントの実測では 1 hop の launch→roster が 560–5000 tick であり、path はブラックランド→ヘリオポリス→ディランダへさらに 2 hop 残る。3 hop 全ての最小見積もりは 1680 tick で、909 tick はこれを大きく下回る
4. `boss_staging_started` 自体も 26-2 内の記録なので、仮に 26-2 月初に開始していても 26-3 末まで約 3.2k tick であり、`STAGING_MAX_TICKS=6000` 無進捗や `STAGING_MAX_TOTAL=24000` に達しない。relay launch は進捗にも数えられるため、abandon 未発生も設計と矛盾する

これは hard proof ではなく公開値からの再構成だが、after counts の staging イベント不在と独立に一致する。ready 後にボス order だけが失敗したという経路を裏付ける証拠はない。26-3 だけを見ると「fair window があったように見えるのにボス出撃 0」だが、boss_staging ではそもそも ready までボス出撃を出さない。fair-window 判定は画面占有や dispatch の機会を測るもので、staging の意図的な集結期間を免除するものではない。したがって現行 main での再発可否は「監査レコードとしては再発し得る」、しかし「ゲーム挙動としては v1483 の設計内挙動が再現するだけ」と切り分ける。

## 7. 月次処理占有・dispatch 枯渇との関係

26-2 の signature には `monthly_scan_hold` と `monthly_scan_yield` があり、counts にも `order_dispatch_starved: 1`・`post_month_sortie_floor: 1` がある。戦闘系決定も 161 件あり、月次/戦闘が観測を占有していたこと自体は事実である。

ただし本件では次の点で #5681 型の「発射へ復帰できない実欠陥」とは言えない。

- order_start 15 / order_launched 5 と、実際に複数の move が発射まで進んでいる
- 月末の `M2419a2` は order_start から departure request / launched_unconfirmed まで進んでいる
- ボス出撃が 0 なのは dispatch 前に boss order が失われたからではなく、gathering 中は boss order が計画・ready 判定を通らないからである
- v1556 は現行 main で `order_dispatch_starved` を月次 owner より先に測り、post-month scan にマップを明け渡させる。v1557 も order_start backoff を実開始証明へ限定する。これらは残余の占有リスクを下げるが、本件の boss zero の主因は占有ではなく staging なので、これらの有無で結論は変わらない

なお `after.recovered=true` / `departures=2` はボス出撃再開の証拠としては使わない。after counts には `order_launched: 2` があるため dispatch 自体は月またぎ後に再開しているが、26-3 の boss_month では boss launch が 0 のままなので、この 2 件はボス attack ではない。`move_general_picked: 2`、`roster_probe_complete: 2`、`sortie_move_arrival_observed: 1` と併せて、move / roster 系の発射と読むのが自然である。少なくとも月次占有によって dispatch へ戻れない #5681 型の停止ではない。

## 8. #5448 との異同

| 項目 | #5448（v1468、修正前） | #5703（v1525、修正後） |
| --- | --- | --- |
| boss_staging | 未導入 | `boss_staging_started` あり、gathering |
| order_start / order_launched | 21-12 は 0 / 0 | 26-2 は 15 / 5、26-3 は 5 / 2 |
| 欠陥構造 | 暫定 boss order がアルマムーン等から長距離出撃し、吸収・敗北・野営化、overdue と帰還が滞留 | 主人公を乗せた relay 移動と非ボス移動が先行し、ボス波は設計上保留 |
| 行軍中の実体 | boss 向け長距離行軍と帰還/camp churn | boss target ではない行軍。少なくとも 1 件は主人公どうしを乗せた relay 向け move |
| v1483 との関係 | 修正対象の本尊 | 修正機構が動作している観測 |

#5448 の検証記録 `VERIFY-5448.md` でも、v1483 は「主人公を拠点へ先に進め、ready 後は拠点だけがボス出撃を出す」「集結中/拠点以外を抑止する」修正として整理されている。本件はその抑止期間そのものを監査が拾ったもので、同一 fingerprint だからといって同一欠陥の再発ではない。

## 9. 現行 main での再発可否と既存修正のカバー

- **26-2 の high 自動起票**: 現行 main では再現しない。v1551 の `_boss_month_coverage` により約 72.3 秒の月内 phase は `short_boss_phase`、low / unverified となる
- **26-3 と同型の high レコード**: 現行 main でも再現し得る。boss phase が月を跨いで十分な覆盖になり、かつ gathering が続く限り、月内ボス出撃 0 は事実として記録される。現行監査には boss_staging gathering を理由とする免除はない
- **v1483 に対する回帰**: 現行 main で staging 本体は v1525 と同じであり、主人公を乗せた行軍が進行する限り同じ設計挙動になる。v1483 が無効とは言えない
- **関連する後続修正**:
  - v1551 / #5137 / commit `fad137d43`: 月末開始 boss phase の誤発火を low/unverified へ落とす。26-2 をカバー
  - v1556 / #5689 / commit `49b6109a1`: boss dispatch 枯渇を月次占有から回復する。本件に見える占有系の残余リスクを減らすが、staging gathering そのものは変えない
  - v1557 / #5690 / commit `b87e2b521`: order_start backoff を実開始証明まで消費しない。占有中断後の再試行を改善するが、本件結論の主因ではない
  - v1529 / #5623・#5645 / commit `031d0bed3` と v1530 / #5626 / commit `14e2bf483` は owned-target の stale attack クローズ / launched_unconfirmed の bounded reopen という別系修正で、本件の staging 判定を直接カバーしない

## 10. なぜ (a)/(c)/(d) ではないか

- **(a) 実欠陥ではない**: 証拠は本国発ボス波の失控ではなく、v1483 設計の gathering と relay 移動を示す。ready 未到達は経過時間が bounds 未満である範囲で説明できる。ボス order の誤分類や、ready 後に dispatch だけが発火しなかった証拠もない
- **(c) 誤検知ではない**: 月内ボス出撃 0 は実際に起きており、26-3 でも同じ boss episode の継続下で 0 が記録された。26-2 の high 自動起票は短すぎる window による不適切な高確度化だが、監査が検出した事実そのものは虚偽ではない
- **(d) 判定不能ではない**: 証拠 JSON は 24000 字で切れるが、26-2 内の `boss_staging_started` 有無、ready/abandoned 不在、主人公を含む relay 行軍に加え、26-3 とほぼ同じ after window の counts（order_start/order_launched、move/roster 系イベント、staging イベント不在）がある。これらから #5448 と別経路で v1483 設計内と判定できる

## 残件（結論を変えない限定）

- issue 公開 JSON は描画仕様により 24000 字で切れている。`world.sorties` / `launched_orders` の全体、M7d03cc の詳細、26-3 の発射 2 件の exact target は runtime artifacts（`hanjuku_decisions.jsonl` 等）がないと確認できない。26-3 の staging イベント有無は after window の counts でかなり覆盖できるが、厳密な月境界との完全一致までは公開値だけでは証明できない
- `Mfc34bd` の計画将軍アルファルファと実出撃将軍どうしの差は、代替選択の正当な適用か誤選択かを個別に見る価値がある。ただし本件では主人公が意図どおり relay に乗った証拠になるため、boss zero の帰属を変えない
- `order_launched` に purpose が載らないため、generic `departures` は move を純粋な敵城出撃と区別できない。本件では boss target の有無だけは判定できるため結論を変えないが、月次集計の表示精度としては別途検討余地がある
- 現行監査は boss_staging gathering を特別扱いしないため、長い gathering 中は 26-3 型の high レコードが今後も出得る。これをノイズと見なすか、集結遅延の可視化として残すかは製品判断である。本検証では修正 PR を作らない
- fingerprint / occurrence の sha256 独立再計算は、この環境での `python3` / `sha256sum` 実行拒否により未実施。raw 文字列と式は §2 のとおりで、検品ゲートの一致確認と矛盾しない

## 検証メモ

- 実施: issue #5703 の公開本文/コメント/証拠 JSON 確認、`VERIFY-5448.md` 確認、v1525 `05f05c050` と HEAD `5dbcf1af7` の `hanjuku_chart.py` / `strategy/routes.py` / `strategy/replan.py` / `strategy/orders.py` / `month_audit.py` / `orchestrator.py` / `flows/map.py` / `flows/month.py` / `flows/post_month_status.py` 照合、v1551/v1556/v1557 の差分確認
- テスト実行なし（read-only 検証であり、コード変更なし）。git fetch/rebase/push、gh コマンド、issue/Slack 投稿、本番 VM 操作は未実施
- 秘密情報・実値トークン・鍵・パスワードは記載していない
