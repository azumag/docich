# VERIFY-5769 — [半熟英雄] ボスフェーズでボス戦闘ゼロ: ch2 にせヒーロー

検証日: 2026-11-27

## 対象と証拠範囲

- issue: azumag/docich#5769
- 判定: `boss_phase_no_battle`（confidence `high`）
- fingerprint: `278adb973c40ad57` / occurrence（incident_id）: `a87f3a8b8897759e5e57`
- ラン: `g1373-b4c4126f` / generation `1373` / 観測版 `hanjuku-chart-v1584-stepless-card-ledger`
- 現行 main の version marker: `hanjuku-chart-v1586-announce-interrupt-settle`
- 月: ch2 `6-3`（観測 611 回 / 870.5 秒 / max gap 4.1 秒）
- ボスフェーズ: にせヒーロー / 対象 `ボス` / 600.3 秒 / 433 観測 / max gap 3.2 秒 / ボス戦闘 0 / `battle_start` 全体 3

### 証拠制約

- 依頼どおり read-only。コード修正、テスト実行、本番 SSH、git 書き込み操作、commit、push、PR 化、issue/Slack 投稿は行っていない。
- `gh` コマンドは実行していない。issue 本文は公開 GitHub ページの read-only 取得で確認した。
- 本番 decision log にはアクセスできないため、issue 本文に埋め込まれた監査 JSON と現行 main のコードだけを根拠にした。存在しない経路でのログ探索はしていない。
- `python3` による sha256 one-liner の実行承認は拒否された。したがって digest の独立再計算は未実施で、式と入力値を §1 に残す。検品ゲート側での再計算が必要。
- `DOCICH_HANDOFF_PATH` 参照コマンドも拒否され、handoff 正本は未読。未設定とは断定しない。

## 結論: **(a) 実欠陥 — ch2 ボスフェーズで sole-starved 膠着へ復帰手段がなく、ボス注文が発射へ到達しない**

監査の発火自体は契約どおりで、ボス戦闘が本当に観測されていない。誤検知ではない。問題は下層の実行経路で、現行 main でも次の構造が残っている。

1. issue の終了時点では、全城の在城が 0 または 1 人で、`no_source` signal どおり出撃元に余剰がない。具体的には `アルマムーン: どうし`、`スペランザ: ヘパイストス`、他城も 1 人か空であり、唯一の空城 `ウラノポリス` へはセミヨンの move が行軍中だった。
2. 機械ボス計画は `M:0c8ccd07315e73b2:アルマムーン>スペランザ`（主人公の移動）と `2-B1`（スペランザ→ボス）だけを pending として立てている。しかし主人公はアルマムーンに 1 人だけなので、その移動はアルマムーンを空にする。`2-B1` もスペランザに 1 人だけでは、出すとスペランザが空になる。
3. 現行の `_source_spare_missing` は、active 化前の注文について source の自由将軍が 2 人未満なら開始を拒否する。これは owner 指定の「城を空にしない」防衛ループ防止ではあるが、この状態では hero move も boss attack も開始できない。
4. boss phase では `_sole_starved` の先頭条件が「未保有の通常城がある」ことを要求するため、通常フェーズ用の defense-wait break はボスだけが残った局面では発火しない。全城確保後に全城 1 人へ偏った場合、読取 probe はできても、誰かを集約する合法手が生成されない。
5. owner コメント上のボス設計は「ボス城へ余剰兵力を連続投入」であり、`_boss_wave_pick` もソースに 2 人必要と明記する。今回の状態はその前提を恒久的に満たせず、600 秒どころか新規加入や敵の攻城で局面が壊れるまで復帰不能になりうる。したがって設計どおりの安全待ちではなく、ボス進行と一城残留ルールの間にある実装ギャップと判定する。

この結論は、order_start の全内訳が decision log 不在で分からない点を残件にしても変わらない。なぜなら、終了時点で `2-B1` は pending、boss live sortie はなく、boss entry は null、ボスHP履歴も 0 であり、少なくともボス戦闘へ到達した証拠がないためである。

## 1. fingerprint / occurrence の生成式と再計算結果

### 1.1 現行 main の式

現行 `src/docich/hanjuku/month_audit.py` では次の通り。

- `_fingerprint(chapter, kind, signals)`（L3521-L3523）:
  `sha256(f"{chapter}|{kind}|{'+'.join(signals)}".encode('utf-8')).hexdigest()[:16]`
- `_boss_incident_id(identity, current)`（L805-L808）:
  `sha256(f"{identity.get('runtime_id')}|{identity.get('generation')}|boss|{current.get('chapter')}|{current.get('started_at')}".encode('utf-8')).hexdigest()[:20]`
- `_build_boss_incident` は fingerprint に全 signals ではなく `['boss_no_battle']` だけを渡す（L936-L939）。

したがって本件の入力文字列は次の通り。

- fingerprint raw: **`2|boss_phase_no_battle|boss_no_battle`**
- occurrence raw: **`g1373-b4c4126f|1373|boss|2|1795802027.5511885`**

期待される digest は issue 記載値:

- fingerprint: `278adb973c40ad57`（先頭 16 hex）
- occurrence: `a87f3a8b8897759e5e57`（先頭 20 hex）

### 1.2 再計算の成否

**この環境では独立再計算は未実施。** `python3` one-liner の実行承認が拒否されたため、sha256 を新規計算していない。検品ゲートが上記 raw 文字列から再計算する必要がある。式と入力値の対応は現行 main の該当箇所から確認済みで、chikujou 系や zero_sortie 系の式は使っていない。

## 2. `boss_phase_no_battle` の起票条件との照合

現行 main の監査条件は次の通り。

- `_boss_phase`（`month_audit.py` L603-L637）は、通常城がすべて自軍で、boss site が少なくとも 1 つ残り、boss 未撃破のときだけ boss phase とする。ch2 では `ボス` が boss site。
- `_track_boss`（L3016-L3089）は phase を 600 秒以上観測し、boss 戦闘証拠がなく、live boss attack sortie がなく、boss entry grace も外れた場合に 1 回だけ記録する。
- `_boss_battle_seen` / `_rows_boss_battle`（L640-L710）の証拠は、章ボス名・`BOSS_GENERALS` に一致する `battle_start`、boss site の `sortie_outcome`、boss 撃破、boss HP 観測である。`attack_observed` の実測突入は戦闘成立扱いにせず、120 秒の entry grace だけ延期する（L713-L759, L799-L802, docs/operations/hanjuku-month-audit.md L45-L73）。
- `_has_live_boss_attack_sortie`（L2979-L3013）は、boss site へ向かう `en_route` / `launched` / `launched_unconfirmed` で purpose が `attack` または不明の行軍がある間だけ起票を延期する。

本件は次を満たす。

- boss phase 600.3 秒 / 433 観測 / max gap 3.2 秒。600 秒閾値と観測密度を満たす。
- `boss_entry: null`。実測突入 grace は発動しない。
- `boss_hp_seen: null` / `boss_hp_history_count: 0`。
- world の live sorties は `A:ee42e9aa:M2458d8`（セミヨン、アルマムーン→ウラノポリス、purpose `move`、en_route）と `A:fe7acb8c:Mb51fe8`（ヴィーナス、アルマムーン→フーリック、purpose `move`、arrived）だけで、boss-target attack はない。
- `sortie_history` にも boss target の行はなく、最後はヘパイストス won スペランザ（tick 32769 = boss phase first tick）である。
- したがって live-sortie defer は発動せず、監査は契約どおり発火する。`confidence_reasons` が空なので high も契約どおり。

### 補助 signal の意味

- `boss_hp_missing`: 正しい。ボスHPパネル未観測。
- `boss_cell_missing` は付いていない。`boss_cell` は cell `[530,189]` / chapter 2 / readings 3 / tick 33060 と測定済み。
- `boss_pursuit_idle` は ch2 では弱い補助 evidence である。`flows/boss_pursuit.py` L627-L630 で pursuit step は chapter 9 以外即 `None` を返すため、ch2 に追跡 loop が動かないのは仕様上当然である。主判定には使えない。
- `no_source` は issue の world.garrison と一致する。全城の在城が 0/1 人で、`_signals` の `max(staffed) <= 1` 条件（`month_audit.py` L3431-L3434）を満たす。

## 3. order_start 2 / order_launched 1 / battle_start 3 の内訳

### 3.1 boss attack の開始・発射

本番 decision log がないため、**2 件の `order_start` の step 完全一致は確認できない**。ただし issue 本文から次は確かめられる。

- 終了時の `plan.chart_plan` は `M:0c8ccd07315e73b2:アルマムーン>スペランザ` と `2-B1` の 2 件で、両方とも未完了。`2-B1` は `pending`。
- world の live sortie / launched_orders に boss target はない。
- boss entry は null、boss site の `sortie_outcome` もない。
- 月内 counts に `boss_companion_required` / `boss_companion_timeout` / `boss_companion_source_wait` / `boss_companion_source_timeout` / `order_failed` がなく、少なくとも ch4 型の companion wait bounded cycle に入った形跡はない。

したがって、`order_launched` 1 件が boss attack だったと断定できる証拠はなく、**boss attack が発射・到達・突入した証拠はない**と読むべきである。`sortie_launch_confirmed` 1 と picker 再開系（`launch_interrupted_by_battle` 2 / `sortie_unverified_rearmed` 1 / `sortie_target_rejump` 1 / `sortie_target_resumed` 1）から、少なくとも 1 件の非ボス logistics 出撃が戦闘割り込みを挟んで発射確認された可能性が高い。最終 world に残る `A:ee42e9aa:M2458d8` は purpose `move` / target `ウラノポリス` であり、少なくともその行軍はボス攻撃ではない。

なお `_commit_departure` は `order_launched` に target/general は渡すが、`_deploy_context` には purpose がなく、`_finish_order` が purpose を補うのは failed の場合だけである（`flows/map.py` L3033-L3049、`flows/deploy.py` L910-L931、`strategy/orders.py` L3052-L3082）。したがって incident の `moves: 0` は「purpose が move と記録された order_launched が 0」という意味にすぎず、出撃が本当に enemy-directed だったことを強く証明しない。この点は集計の既知の弱さとして扱う。

### 3.2 battle_start 3 は防御戦系列

`battle_start` 3 と `defense_observed` 3、shown timeline の先頭が一致する。

- `defense_observed`（reason `せめこまれました表示`）の直後に `battle_start` が記録される。
- その後 `battle_egg_before_retreat`（ヘパイストス）、`battle_retreat_banned`、`battle_survival_select`、敵の卵変身、`monster_menu_choice`、`enemy_general_menu_wait`、`battle_result` が続く。
- この系列は boss name を持たない通常戦闘であり、監査側も `boss_battles: 0` と判定している。`battle_start` の enemy は `_decision_rows` の出力項目に含まれないため issue の簡略表では見えないが、監査内部では full row を見て boss 名でないことを確認している（`month_audit.py` L825-L827）。

したがって `battle_start` 3 はボス戦ではなく、boss phase 中の counterattack / defense 戦闘系列と判定する。ボス戦闘 0 の事実は変わらない。

## 4. 実行経路の確認

### 4.1 機械ボス計画は hero move + 2-B1 だけを組む

`_mechanical_boss_plan`（`strategy/replan.py` L2778-L2917）は、通常城確保後に base chart の boss order を選び、非 site chapter では hero の現在地から boss source への move を先に立てる。issue の plan はこの実装どおりで、`アルマムーン→スペランザ` move の後に `2-B1` がある。

ch2 base chart の `2-B1` は `hanjuku_chart.py` L676-L679 で、`どうし` / source `スペランザ` / target `ボス` / cards `クースカン, イッテツーン, ノリウツール` である。ただし boss attack の実際の actor は companion substitution が効く設計で、主人公本人をボスへ送る必要はない。それでも source を空にしない守備者は必要である。

### 4.2 companion substitution はあっても、一城残留ルールを越えられない

`_order_hero_alternatives`（`strategy/orders.py` L53-L121）は主人公以外の代役を許し、弱くても fit な将軍を boss に送る owner ルールを持つ。`_boss_companion_source`（L170-L216）は source を含む全自城から適格 source を探す。

一方、`next_order`（L2369-L2390）は `_source_spare_missing` を通った注文だけ返す。`_source_spare_missing`（L2772-L2828）では、active 化前で source の自由将軍が 2 人未満なら開始しない。これは「pre-flight only: never reopen a plan for the last defender」と明記されている。

今回の終端状態では:

- `スペランザ` は `ヘパイストス` 1 人だけ。彼が boss に出れば城は空になる。
- `アルマムーン` は `どうし` 1 人だけ。彼がスペランザへ移れば本城は空になる。
- 他城も 1 人または空で、どこからも守備者を供給できない。
- 空城 `ウラノポリス` へ向かうセミヨンは、到着してもその城を 1 人にするだけで、スペランザやアルマムーンの 2 人化には直結しない。

したがって、`_boss_companion_source` が仮に適格な卵持ちを見つけても、pre-flight の source guard が boss order の開始を止める。実際の game が 1 人将軍城からの出撃を拒否するというコメントも `_boss_wave_pick` にあり、boss wave は source に 2 人必要とされる（`replan.py` L1441-L1445）。

### 4.3 boss phase では sole-starved break が働かない

`_sole_starved`（`replan.py` L2399-L2414）は「未保有の通常城がある」場合だけ true を返す。boss phase では通常城は全て保有済みなので、この関数は false になり、`_defense_gate`（L2422-L2486）の hold/break 評価に入らない。通常フェーズの「1 将軍でも出す」例外が、ボスだけが残った局面には無い。

同時に、owner 設計として boss chain 確保後は boss を targets 先頭へ置き、「余剰兵力を連続投入」すると明示されている（`replan.py` L1339-L1363）。今回は余剰が構造的に作れないままなので、防衛ルールそのものは意図どおりでも、**boss 進行を再開するための人員集約または bounded 例外が欠けている**という実装ギャップになる。

### 4.4 roster probe は証拠収集にすぎない

counts に `roster_probe_complete` 2 / `roster_resident_confirmed` 1 がある。現行コードには stale/unknown roster を読み直す probe 機構があり、全城 1 人に見える状態を放置しない工夫はある。しかし probe は将軍を生成しない。実際に全城 1 人なら、読み直し後も合法手は増えない。issue の最終 garrison がその状態であり、probe の存在は結論を変えない。

## 5. 同一ラン #5765 / stale launch latch との関係

同じラン `g1373-b4c4126f` では、直前月 ch2 5-11 の VERIFY-5765 が `A:59dc871a:Md3216a`（グロン retake）に残った stale launch latch を (a) 実欠陥と判定している。本件 counts にも `order_dispatch_starved` 2 と picker 再開系があり、同じ run の出撃確認まわりの混乱は背景証拠として一致する。

ただし #5765 の latch は主として house/chikujou などの housekeeping を退避させる仕組みであり、本件で `スペランザ` / `アルマムーン` が 1 人ずつしか持たない source guard を直接作るものではない。したがって stale latch は時間消費・order 混乱の副次因になりうるが、本件の主因は boss phase 終端に実際に残った sole-starved 膠着と判定する。現行 main は `hanjuku-chart-v1586` で、#5765 の v1587 修正はまだ main には入っていない。

## 6. #5758（boss_month_zero_sortie、結論 (b)）との異同

VERIFY-5758 は ch4 四季王の `boss_month_zero_sortie` で、結論は (b) 設計どおりだった。主な根拠は、四季王サイトでは卵なし・不安定な将軍や不在の主人公を送らない companion 安全設計があり、source/deploy の wait がともに 200 tick で bounded、timeout、`order_failed`、site step rearm の counts が揃っていたことである。

本件とは次が違う。

- #5758: ch4 site order が実際に `order_start` へ進み、companion gate の bounded wait / timeout / failed / rearm cycle が counts に見える。少なくとも復帰機構が動いていた。
- #5769: ch2 の `2-B1` は pending のまま。companion wait/timeout/failed/rearm の counts はなく、boss live sortie も boss entry もない。復帰 cycle ではなく、source guard と boss-phase sole-starved 除外の組合せで開始条件自体が立たない。
- #5758: 既存サイト行軍・戦闘が companion 資源を占有していた可能性があり、短期間の 257 秒月で出撃 0 だった。
- #5769: 600 秒フェーズの終端で全城 0/1 人の膠着が直接見え、空城 refill move はあっても boss source の 2 人化につながらない。
- 共通点は「監査がゼロ出撃/ゼロ戦闘を正しく記録した」ことだけである。下層の原因は別で、本件は #5758 の bounded companion 待ち型ではなく、#5372 で実欠陥と判定された upstream sole-starved クラスに近い。

## 7. 判定

- **(a) 実欠陥**: boss phase で全城が 0/1 人になると、hero move も boss attack も一城残留ルールに阻まれ、boss phase では defense break が働かず、機械ボス計画にも別 source への集約手がない。owner 設計の連続 boss wave と矛盾し、新規加入などの外部イベントを待たずに復帰する能動的手段がない。
- **(b) 設計どおりではない**: 「城を空にしない」個別ルールは設計どおりだが、その結果として boss 進行が恒久的に止まる経路まで設計意図とは読めない。既存コードの owner コメントは boss への連続投入を要求する。
- **(c) 誤検知ではない**: boss phase は実在し、600 秒条件を満たし、boss battle evidence、boss HP、boss entry、live boss sortie はいずれもない。監査の発火は正しい。
- **(d) 判定不能ではない**: `order_start` 2 件の完全な step 内訳は不明だが、boss 戦闘へ到達しなかった事実と、終端の sole-starved 膠着、その復帰不能なコード経路は特定できる。

## 残件（結論を変えない限定）

1. fingerprint / occurrence の独立 sha256 再計算は、`python3` 実行承認拒否のため未実施。入力 raw は §1.1 の通り。
2. `order_start` 2 件の全 step/purpose、`sortie_launch_confirmed` の対象 step、picker interruption が同じ step だったかは decision log 不在で未確認。ただし boss launch/entry/battle の証拠がないことと、終端膠着の判定には影響しない。
3. ヘパイストスが卵を保持していたか、boss companion として fit だったかは issue 本文だけでは確定しない。仮に適格でも source が 1 人では pre-flight guard を越えられないため結論は変わらない。
4. `boss_pursuit_idle` signal は ch2 では構造的に常時発火しうる補助ノイズ。主判定の根拠にはしていない。
