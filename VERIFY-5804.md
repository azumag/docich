# VERIFY-5804 — [半熟英雄] ボスフェーズでボス戦闘ゼロ: ch12 クーモン

- 対象: docich#5804 / 判定 `boss_phase_no_battle`（confidence `high`）
- fingerprint `b4e810c24409f219` / occurrence(incident_id) `8c051f7b140978641c88` / key `b4e810c24409f219/16-12/201993`
- ラン: `g1190-ch1v9` / generation `1190` / 観測版 `hanjuku-chart-v1593-unreadable-card-source`
- 月: ch12 `16-12` / ボスフェーズ 600.047 秒・2465 観測 / ボス戦闘 0 / battle_start 全体 6
- 検証範囲: read-only。issue 本文の embedded incident JSON と、worktree の現行コードだけを使った。本番 decision log はローテーション済みのため探していない。コード修正・commit・push・PR・gh コマンドはなし。
- 検証基盤: worktree branch `verify/5804`。git metadata 上は `origin/main` と同じ `5ead25262e4df0d14ef3478c582e51915fef7c25`、コード上の `BOT_VERSION` は `hanjuku-chart-v1605-retreat-card-flow`。
- `gh issue view` は依頼内の後段で禁止されていたため実行せず、issue #5804/#5744/#5749/PR #5753 の公開 GitHub ページを read-only に照合した。`DOCICH_HANDOFF_PATH` 確認を含む shell/python 実行は承認されず、handoff 正本は未読。

## 結論: **(b) 設計どおり**

ボス戦闘 0 は事実であり、(c) 誤検知ではない。ただし現行コードでも、第12話では boss staging の gathering 中はボス攻撃を意図的に出さない。issue の終端でも主人公は拠点ディランダへ到着済みと確認できず、ブラックランドからヘリオポリスへ進む relay の途中だった。したがって 600 秒以内にボス戦闘が無かった主因は、ボス出撃を組めない実装欠陥ではなく、オーナー承認済みの集結設計である。

監査側は gathering を免除しないため、同じ状況では今後も high の `boss_phase_no_battle` が起票され得る。これは #5744 の検証でも残件として明示されていた製品判断の領域であり、今回の occurrence から新規の実欠陥経路は確認できなかった。

## fingerprint / occurrence の式と再計算結果

現行 `src/docich/hanjuku/month_audit.py` の boss_phase_no_battle 系 builder を読んだ。他種別の式ではなく、この種別専用の箇所は次のとおり。

### fingerprint

- `_build_boss_incident()` は `record['fingerprint'] = _fingerprint(current.get('chapter'), 'boss_phase_no_battle', ['boss_no_battle'])` を使う（`month_audit.py` L936-L939）。
- `_fingerprint(chapter, kind, signals)` は `raw = f"{chapter}|{kind}|{'+'.join(signals)}"` を UTF-8 sha256 し、先頭 16 hex を取る（同 L3545-L3547）。
- したがって本件の入力は **`12|boss_phase_no_battle|boss_no_battle`**、期待値は issue の **`b4e810c24409f219`**。

### occurrence / incident_id

- `_boss_incident_id(identity, current)` は `raw = f"{identity.get('runtime_id')}|{identity.get('generation')}|boss|{current.get('chapter')}|{current.get('started_at')}"` を UTF-8 sha256 し、先頭 20 hex を取る（同 L805-L808）。
- issue embedded JSON の値を入れると入力は **`g1190-ch1v9|1190|boss|12|1795828214.3624802`**、期待値は issue の **`8c051f7b140978641c88`**。

### 再計算の成否

`python3` の実行が承認却下されたため、sha256 の独立再計算は **未実施**である。式と入力値は上記のとおり特定できたので、検品ゲート側で再計算可能。なお #5744 の公開再検証コメントには、同じ fingerprint raw `12|boss_phase_no_battle|boss_no_battle` から `b4e810c24409f219` が得られたと既に記録されているが、これは本件の独立再計算の代替にはしない。

## 600 秒間にボス戦闘へ至らなかった経路

### 1. ch12 の boss staging がボス攻撃を合法的に止めていた

現行コードの構造:

- ch12 の encoded chart は、主人公 relay が `アルマムーン → ブラックランド → ヘリオポリス → ディランダ`、ボス注文 12-B1 の source が `ディランダ`である（`src/docich/hanjuku_chart.py` L1111-L1135）。`boss_staging(12)` はこの encoded chart から `{'castle': ディランダ, 'path': (...)}` を導く（同 L1368-L1392）。
- `routes.staging_blocks()` は、target がボスで `purpose != 'move'` の注文について、phase `gathering` では全城からのボス出撃を止め、`ready` 後もディランダ以外からの出撃を止める（`src/docich/hanjuku/strategy/routes.py` L476-L489）。
- `ready` へ進む条件は、ディランダの将軍一覧へ主人公 `どうし` を実読みすることである（同 L419-L439）。
- mechanical planner の `_boss_wave_pick()` も、phase `gathering` では候補を返さず、`ready` では source をディランダだけに制限する（`src/docich/hanjuku/strategy/replan.py` L1430-L1438）。

issue embedded JSON の終端状態:

- 奪取済みにはディランダを含む通常城が並び、残りはボスだけだが、ディランダ在城は `ブリー` / `カシス` のみで主人公はいない。
- ブラックランド在城には `どうし` がある。
- 現在計画の active は `A:5f77e647:Ma5a7a3` で、note は「ボス出撃の拠点ディランダへ主人公をブラックランドからヘリオポリスへ進める」、purpose `move`、source `ブラックランド`、target `ヘリオポリス`。
- `boss_entry` は null、boss HP 履歴 0、`boss_staging_ready` / `boss_staging_abandoned` の記録は窓内にない。

つまり主人公はまだ relay の 2 段目（ブラックランド→ヘリオポリス）に入ったところで、最終拠点ディランダの roster に未到達である。現行コードでもこの状態でボス攻撃が出ないのは設計どおりである。

### 2. 発射 4 / order_start 9 はボス攻撃ではなく自城間 move・集結/厚塗り系だった

issue の識別できる発射先は、少なくとも `world.launched_orders` に残る次の 3 件で、いずれも `purpose: move` の自城間転送である。

- `A:5f77e647:M2419a2`: ガーラント、アルマムーン → ディランダ
- `A:5f77e647:M5eb723`: フェットチーネ、アルマムーン → ファンゴル
- `A:5f77e647:Md73af3`: オレガノ、アルマムーン → ヘリオポリス（終端では status `arrived`）

4 件目は完全な decision log が無いため直接同定できないが、`sortie_march_overdue` の step `M3b4529`、終端計画の同 step `ヘリオポリス → ディランダ`、終端のディランダ在城 `カシス` から、カシスのヘリオポリス→ディランダ移動だった可能性が高い。いずれにせよ issue JSON にボス target の launched order / live sortie はなく、`world.sorties` に残る行軍も自城宛て `move` だけである。

現行監査は live な boss attack march があれば 600 秒発火を defer する（`month_audit.py` L3003-L3037, L3089-L3093）。それでも発火したことは、少なくとも発火時点でボスへ向かう測定可能な行軍が無かったことを意味する。6 件の `battle_start` はボス名でもボス城への entry でもない通常戦であり、ボス HP も未観測だった。

補足: incident の `sorties.departures=4` と `sorties.moves=0` は見かけ上ずれる。`month_audit._departures()` は `purpose == 'move'` の行を除外する設計だが、`order_launched` 行に purpose が投影されていないと move を除外できない。今回は world/launched_orders 側で自城 move と分かるため、結論は変わらないが、issue 表の「発射 4」を敵城攻撃 4 回と読んではいけない。

### 3. `plan_step_released` / `chart_adjust_applied` の連鎖は主因ではない

- `plan_step_released` は、再計画で同じ step が出たとき、行軍中でない（または到着上限を超えた）過去の `launched` 状態を pending へ戻す台帳整理である。現行コードは `_arrival_limit` 内の `en_route` / `launched_unconfirmed` を保持し、その行軍を解放しない（`replan.py` L3303-L3361）。
- `chart_adjust_applied` は mechanical な指示列を adopt した記録であり、本件末尾では直後に主人公の relay move（ブラックランド→ヘリオポリス）が `order_start` へ進んでいる。つまり集結を止める方向ではなく、再開させる方向に働いている。
- 末尾で release された `M5eb723` はアルマムーン→ファンゴルの厚塗り move であり、ボス出撃そのものではない。release/re-plan が relay の到着を遅らせた可能性は残るが、ボス攻撃を禁止していた上位条件（gathering）を作った経路ではない。

したがって、この連鎖は状態台帳の再同期と転送再計画の表れであり、ボス戦闘 0 の根因ではない。月次処理や通常戦が時間を使ったことも counts から読めるが、`post_month_boss_dispatch_floor` / `order_dispatch_starved` は既存の回復機構が発火した痕跡であり、dispatch 以前の staging 抑止を覆さない。

### 4. 600 秒は ch12 staging の完了を期待するには短い

`routes.py` の staging 実装は、1 hop の launch→roster 実測が 560..5000 tick あることを前提に、`STAGING_MAX_TICKS=6000`、総上限 `STAGING_MAX_TOTAL=24000` を置く（L360-L364）。本件の 600 秒は約 2464 tick（first tick 199850 → last tick 202314）で、主人公はブラックランド→ヘリオポリス→ディランダの 2 hop をまだ完了していない。集結完了を待つ設計のままでは、600 秒でボス戦闘が始まらないことは当然に起こる。

## #5744 / #5749 / PR #5753 との切り分け

### #5744

#5744 は同じ fingerprint・同じ ch12 クーモンだが、月 `24-8` / incident `fe08858667e071c5add6` / 観測版 v1575 の別 occurrence である。公開 issue コメントの一次・独立再検証はともに **(b) 設計どおり / 既存修正でカバー済み** であり、(d) 判定不能ではない。主因は同じく ch12 boss_staging gathering で、あちらは主人公が既に `ヘリオポリス → ディランダ` の最終 relay を行軍中だった。本件はそれより手前の `ブラックランド → ヘリオポリス` なので、むしろ staging 未完がより明確な同型である。

#5744 で実際に参照されていた既存修正は、v1483 / PR #5451（ch12 boss staging 本体）と、v1556 / PR #5689（boss dispatch starvation を月次占有から回復する経路）である。本件にも `order_dispatch_starved` 3 / `post_month_boss_dispatch_floor` 1 があり、副次経路は同じ回復機構の範囲に読める。

### #5749 / PR #5753 は本件と無関係

依頼文の「#5744 のフォローアップ #5749・修正 PR #5753」という前提は公開 issue/PR と一致しない。#5749 は #5747 の `battle_end` card_miss 判定不能を受けた証拠追加 issue で、PR #5753 は `battle_boss_card_missed` の emit 証拠と監査 confidence tier を修正したものである。boss staging、`boss_phase_no_battle`、出撃 relay の制御を変更していない。

したがって本件が「#5749 の修正でカバー済みか」という問いには、**カバー対象外**と答えるのが正しい。ただしそれは未修正の実欠陥を意味しない。本件のボス不出撃は、そもそも #5749/PR #5753 が触らない ch12 staging の意図的抑止で説明できる。

### v1593 以降の現行 main の関連修正

現行 main には v1593 以降の修正が入っているが、結論を変えない。

- PR #5798 / #5775（boss-only の非ボス plan 保留を越えて boss 候補生成を再評価する v1595 系）は、`order_start 0` 型の候補生成停滞を対象にする。現行コードでも `_boss_wave_pick()` は gathering 中に `None` を返すため、ch12 staging を無視して boss wave を作らない。本件のような relay move が進んでいる状態をカバーする修正ではない。
- PR #5819（v1602 の sortie identity propagation）は終端レコードと監査へ general/source/target/purpose を伝える証拠層の修正であり、move 自体や staging の優先順位を変えない。
- 現行 `month_audit.py` は 600 秒、`boss_entry` 120 秒猶予、live boss attack march の defer はあるが、ch12 staging gathering を exempt する条件はない（L51-L57, L3003-L3113）。したがって同じ集結遅延では今後も同型起票が起こり得る。

## 残りの未決・制約

- 本番 `hanjuku_decisions.jsonl` はローテーション済みで、issue embedded JSON の直近 30 行では 9 件の `order_start` 全件の step/target を直接列挙できない。特に 4 件目 launch の target 特定は終端状態からの推定である。ただし、staging gathering 中に boss attack が生成されないコード経路と、終端計画/世界状態から、結論 (b) を覆すほどの不確実性ではないと判断した。
- sha256 の独立再計算は python3 実行却下のため未実施。式と入力値だけを上記に残した。
- gathering 中の high 起票を「可視化として残すか、ノイズとして抑制するか」は #5744 と同じく未解決の製品判断である。今回の read-only 検証では変更しない。

## 検品ゲート確認（Muse、2026-11-28 18:02 JST、現行 main 5ead25262）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("12|boss_phase_no_battle|boss_no_battle")[:16] = b4e810c24409f219、SHA-256("g1190-ch1v9|1190|boss|12|1795828214.3624802")[:20] = 8c051f7b140978641c88。いずれも issue の値と一致する。
- 結論 (b) 設計どおりを維持する。現行 main のコードで次の3点を直接確認した。
  - ch12 の encoded chart は主人公 relay が アルマムーン→ブラックランド→ヘリオポリス→ディランダ、ボス注文 12-B1 の source が ディランダである。
  - `staging_blocks` は phase `gathering` ではボス出撃を全城から止め、`ready` 後もディランダ以外からの出撃を止める。`ready` へ進む条件はディランダの将軍一覧へ主人公を実読みすることである。
  - mechanical planner の `_boss_wave_pick` も phase `gathering` では候補を返さず、`ready` では source をディランダだけに制限する。
- issue の終端状態（主人公はブラックランド在城でヘリオポリスへの relay 途中、ディランダ在城はブリー / カシスのみで主人公なし）は gathering 中であり、現行コードでもこの状態でボス攻撃が出ないのは設計どおりである。
- 監査側は gathering を免除しないため、同じ状況では今後も同型の起票が起こり得る。これは #5744 と同じく製品判断の領域であり、本検証では変更しない。修正 PR は割り当てない。
