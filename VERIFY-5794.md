# VERIFY-5794 — [半熟英雄] 出撃失敗: ch11 Mc7fc03

- 対象: azumag/docich#5794（`bug` / `auto-generated`）/ fingerprint `ba9d892e64bf0e45` / occurrence(incident_id) `e8de932deb613b0bfae7`
- 判定: `sortie_failed`（confidence `high`）/ 終端決定 `situation_held` / strategy_variant `attack_retry_exhausted` / reason_code `sortie_retry_exhausted`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1591-clear-sortie-launch-latch`
- 月: ch11 15-10（観測 1140回 / 250.5秒 / max gap 0.8秒 / ticks 168071–169210）
- step: `A:3d1e5d29:Mc7fc03` / issue 表示では将軍・出撃元・対象・目的いずれも不明
- 検証ベース: この worktree の現行 main（`src/docich/hanjuku/version.py` は `hanjuku-chart-v1597-chikujou-defer-reason`）。検証範囲は read-only で、本ファイルの作成以外の編集・git 書き込み・gh・本番操作なし。ラン成果物はローテーション済みのため参照せず、issue 本文の incident JSON と現行 main のコードだけで判定した。
- issue 本文は依頼で gh 実行禁止だったため、公開 GitHub issue ページの webfetch で取得し、incident JSON 全量（`world.launched_orders` / `world.sortie_history` / `decisions.before` / counts）を確認した。

## 結論: **(a) 実欠陥 — ただし欠陥は出撃の同一性（将軍・出撃元・対象・目的）を終端レコードと監査が落とす証拠伝播層。攻撃3敗後の打ち切り自体と `sortie_failed` 起票分類は設計どおり**

`Mc7fc03` は「出撃できなかった」事象ではなく、少なくとも最終試行では **フェデリーニがタタールからクラボニアへ attack 出撃し、突入後の戦闘で敗北した**事象である。incident 自身の `world.launched_orders` に `{general: フェデリーニ, source: タタール, target: クラボニア, purpose: attack}`、`world.sortie_history` に `{general: フェデリーニ, target: クラボニア, outcome: lost, tick: 169210}` が残っている。現行コードでは attack 側敗北ごとに同 step の retry を2回まで許し、3敗目で `failed` にして計画外判断へ渡す。したがって打ち切りと監査種別は契約どおりで誤検知ではない。

一方で、終端 `situation_held` レコードには `general` / `source` / `target` / `purpose` が記録されず、監査側も `world.launched_orders` に残っている同一性を back-fill しない。このため issue が全項目「不明」になり、incident_id の raw でも `general|target` が `None|None` に退化している。これは観測不能ではなく伝播漏れで、現行 main v1597 でも未修正の実欠陥である。既存検証 #5509（VERIFY-5509）と同じ家族の再発である。

## §1 fingerprint / occurrence（incident_id）の式照合 — python3 実行拒否により再計算は未実施

本環境では `python3` 実行が拒否されたため、SHA-256 の再計算はできなかった。検品ゲートが再計算できるよう、現行 main の式と入力値を以下に残す。他種別の式ではなく、`sortie_failed` 系 builder の式である。

- fingerprint: `src/docich/hanjuku/month_audit.py` L3543-3545 の `_fingerprint(chapter, kind, signals)` は
  `sha256(f"{chapter}|{kind}|{'+'.join(signals)}").hexdigest()[:16]`。
  `sortie_failed` incident は L1329 で `_fingerprint(mem.get('chapter'), event['kind'], [event['kind']])` を使う。
  本件の入力は chapter `11`、kind `sortie_failed`、signals `['sortie_failed']` なので、理論 raw は
  **`11|sortie_failed|sortie_failed`**
  であり、期待値は `ba9d892e64bf0e45`。**再計算未実施**。
- occurrence(incident_id): `_build_sortie_incident` L1324-1327 の raw は
  `f"{runtime_id}|{generation}|{kind}|{reason_code}|{step_local}|{general}|{target}|{int(now)}"`。
  issue JSONの値は runtime_id `g1190-ch1v9`、generation `1190`、kind `sortie_failed`、reason_code `sortie_retry_exhausted`、step_local `Mc7fc03`、general/target は監査イベント上 `None`、`created_at=1795821216.7150369` で `int(now)=1795821216`。理論 raw は
  **`g1190-ch1v9|1190|sortie_failed|sortie_retry_exhausted|Mc7fc03|None|None|1795821216`**
  であり、期待値は `e8de932deb613b0bfae7`。**再計算未実施**。
- `None` は Python f-string で文字列 `None` になる。同一性が正しく伝播していれば raw の該当部は `フェデリーニ|クラボニア` となり、occurrence も別値になるはずなので、`None|None` の入力自体が §4 の証拠伝播欠陥を示している。

## §2 現行 main で `attack_retry_exhausted` に至る経路 — 再試行していたのは発射手続きではなく攻城戦

発火点は `src/docich/hanjuku/flows/battle.py` L2042-2082。

1. 条件は `outcome == 'loss'`、`cur['side'] == 'attack'`、step があり、`castle not in captured` であること。つまり戦闘パネル消滅後のHP表示から、未奪取城への攻撃側敗北を実測した場合だけ進む。
2. `retries[step] += 1`。`retries <= 2` なら `orders[step]='pending'` に戻し、実在庫があれば開幕イッテツーン最大2枚の `card_override` を付けて `order_retry`（`retry_with_opening_cards`）を記録し再攻撃する。
3. `retries == 3` なら `orders[step]='failed'` にし、`situation_held` + `strategy_variant='attack_retry_exhausted'` を記録する。コメントにあるとおり、`launched` のまま残すと終了済み出撃が行軍中に見えて後続手順と計画外判断を待たせるため、打ち切って `failed` にするのは意図された終端である。
4. 監査側は `month_audit.py` L1199-1205 で variant `attack_retry_exhausted` / `boss_retry_exhausted` の `situation_held` を `sortie_failed/failed` に写し、L1172-1173 で reason_code `sortie_retry_exhausted` を返す。docs/operations/hanjuku-month-audit.md L112-127 もこの terminal を「戦闘再試行の上限で指示失敗」として `sortie_failed` に分類する契約で、`order_retry` は terminal ではないため対象外と明示している。

したがって、本件で再試行されていたのは出撃確定・部隊▲確認のやり直しではなく、**クラボニアを落とすための実戦闘**である。`misses: None` も矛盾ではない。`misses` は発射未確認巻き戻しの `launch_miss` 系メトリクスであり、戦闘敗北後の retry 終端には存在しない。

## §3 「確定 1 / 発射 1 / order_start 2 / order_launched 1」と failed の整合

これらの数値は step の全履歴ではなく、主に incident 作成時点から遡る直近120秒窓の決定カウントである。`_build_sortie_incident` L1239-1243 は `_read_decisions(runtime, now-120, now)` を読み、L1290-1299 で counts / departures / month_sorties を保存する。`_departures` L3340-3356 は LAUNCH_DECISIONS を step 単位で重複排除し、`_decision_counts` L3385-3389 は全決定名を数える。

このため `order_start 2` / `order_launched 1` / `order_launched_unconfirmed 2` / `launch_interrupted_by_battle 3` は、直近120秒に複数の出撃開始・目標選択中断・1件の実発射があったことを示すにとどまり、「`Mc7fc03` が一度も出撃しなかった」意味ではない。実際、incident の証拠は逆に実出撃と実戦闘を示す。

- `world.launched_orders['A:3d1e5d29:Mc7fc03']`: フェデリーニ / タタール / クラボニア / attack。
- `world.sortie_history` 末尾: フェデリーニ / クラボニア / lost / tick 169210（月終了 tick と同じ）。
- `decisions.before`: `castle_defender_seen`、`battle_card_*`、`battle_melee`、`battle_power` が同 step に並び、終端時刻に `battle_result`・`sortie_outcome`（将軍 フェデリーニ）・`general_location_unclassified`（敗北将軍の所在破棄）が出る。
- `world.captured` にはクラボニアが無いため、battle.py L2042-2043 の未奪取城条件に合致する。
- ch11 の城一覧ではクラボニアは通常城で、ボス城「ボス」（エッグママ）ではない（`src/docich/hanjuku_chart.py` L486-488, L1256-1259）。したがって boss retry ではなく attack retry 側で正しい。

直近120秒に見える `launch_interrupted_by_battle` / `order_launched_unconfirmed` は、目標ピッカーが戦闘などで閉じた別試行ないし再開試行の記録で、最終的な実発射・実戦闘の存在と矛盾しない。`sortie_history` は直近8件だけなので、3敗の全内訳は残っていないが、コード上 `attack_retry_exhausted` は同 step で3回目の attack loss にしか到達しない。

## §4 将軍・出撃元・対象・目的が全て不明になった理由 — 現行 main に残る証拠伝播欠陥

不明化は情報が存在しなかったためではない。incident の `world.launched_orders` に完全な同一性が残っている。落ちる経路は現行 main で再現できる。

1. `battle.py` L2079-2082 の終端 `_record(mem, 'situation_held', ...)` は `chart_step`、`strategy_variant`、`observed_metric={'retries','outcome','enemy_hp','ally_hp'}`、`reason` だけを渡し、`general` / `source` / `target` / `purpose` を渡さない。
2. `month_audit._sortie_event` L1209-1215 は record 本体か `observed_metric` からしか step/general/source/target/purpose を取らない。上記レコードには無いので全て `None` になる。
3. `_track_sortie` L1684-1693 は `order_failed` / `situation_held` について `_context_order(after, step)` から目的と同一性を補おうとするが、`_context_order` L1337-1344 は **現在の `plan.chart_plan.orders` だけ**を検索する。
4. 本件の step は旧計画リビジョン `A:3d1e5d29` のもので、incident 作成時の `plan.chart_plan.request_id` は `53c04353b1719ad7`、現在計画には `A:53c04353:M9ba530` と `A:53c04353:M19334b` だけがある。したがって `_context_order` は空を返す。
5. 一方 `orders._order_for_step` L1021-1037 は「計画差替え後も launched order snapshot を見る」設計で、`world.launched_orders` まで fallback する。実際に incident の `world.launched_orders` にも残っている。つまり battle.py 側では解決可能だった情報を終端レコードへ写さず、監査側も `context.world.launched_orders` を使わないために `None` 化した。

`docs/operations/hanjuku-month-audit.md` L119-120 は出撃失敗recordに将軍・出撃元・対象・目的を保存する契約を示している。L121-123 は少なくとも `order_failed` には補完する旨を明示するが、`situation_held` 終端が未補完のまま残っている。#5509 で指摘されたのと同じ実欠陥であり、v1591 で発生した本件は v1597 でも再発しうる。

この欠陥は起票の誤りではない。`purpose` は `attack` であり、`SORTIE_NON_PURPOSES = {'move','house','repair','recall','defend'}`（month_audit.py L1334）に入らないため、正しく補完されても除外されず `sortie_failed` として起票される。実害は、解析・dedup・incident_id の粒度が落ちることである。

## §5 切り札の携行未確認と v1448 自動携行の関係

issue の「切り札の携行未確認」節は、独立した `sortie_kit_unconfirmed` incident の結果として確定した事実ではない。`month_audit._issue_body` は L4924 で無条件に `sortie_cards = record.get('sortie_event')` とし、L5036-5052 で `sortie_event` がある record に切り札節を描画する。本件の `sortie_event` は card audit event ではなく sortie failure event で、`inventory` / `planned_cards` / `carried_observed` を持たない。その結果として「在庫 None / 携行 未確認 / 計画cards None / 実測レシートなし」という表示になる。したがって、この節だけから携行の有無や計画cardsを推定できない。

むしろ incident の決定列には、同 step の `battle_card_announce_retry`、`battle_cards_before_retreat`、`battle_card_unclassified`、`battle_card_effect_confirmed` があり、counts にも `card_pick 5` / `battle_card_selected 6` / `battle_card_effect_confirmed 4` / `cards_autocarried 2` / `sortie_confirm 2` がある。少なくとも最終戦闘では切り札選択・使用確認の経路に入っており、「非携行だった」事実は成立しない。

v1448（PR #5336、owner 実装の案1）の自動携行は現行 `strategy/kit.py` L108-114, L117-146 にある。計画・override・dropper 等で kit が空のときだけ、在庫にある egg_drop 0 の安全札を1枚追加し、`cards_autocarried` を記録する。v1591 は v1448 より後なので機構自体は観測ランに搭載されていたし、直近120秒窓に `cards_autocarried: 2` があることから、この月のどこかの出撃では実際に発火した。

ただし、counts は step 属性を持たない集計で、直近30行にも `cards_autocarried` の行は残っていない。`Mc7fc03` については、旧計画の `cards`、retry の `card_override`、strong/dropper kit のどれが使われたかが incident だけでは分からない。したがって **v1448 自動携行が本件の出撃そのものに適用されたかどうかは判定不能**であり、「適用された」とも「されていない」とも断定しない。少なくとも最終戦闘で切り札経路に入った以上、本件を cardless 出撃として扱う根拠はない。

## §6 同じ出撃失敗家族との切り分け

- **#5444（sortie_launch_failed / ガスパチョ / VERIFY-5444）**: 発射後の部隊▲・名簿が観測できず、`misses` 上限で巻き戻し失敗化する発射観測層。本件は `misses: None` で、行軍・突入・戦闘敗北まで実測される戦闘結果層。同型ではない。
- **#5365（purpose='move' の order_failed が起票除外をすり抜けた実欠陥）**: 共通するのは purpose 解決ギャップだけ。ただし #5365 では正しく解決すれば move なので起票対象外だった。本件は正しく解決すれば attack で、起票対象のままである。したがって誤起票ではなく、証拠伝播欠陥としてだけ同じ方向性を持つ。
- **#5206（sortie_launch_not_observed、(b) / PR #5305 でカバー済みの発射未観測系）**: コミット済みAが部隊を生まない事象。本件は部隊が出て戦闘した後の再試行上限で、発射未観測ではない。該当しない。
- **#5317（計画が空の明示的 cardless order、(b)）**: あれは `planned_cards=[]` が確認でき、cardless order がそのまま実行された事象。本件は旧計画が差し替わって planned_cards が読めないだけで、cardless 指示の証拠はない。さらに最終戦闘で切り札経路に入っている。別型。
- **#5226（監査が実測携行を読めず非携行と誤検知した (c)）**: 本件の主判定は `sortie_without_cards` ではなく `sortie_failed` であり、非携行を断定して自動Issue化していない。切り札節の「未確認」表示は主判定の誤検知を意味しない。#5226 型ではない。
- **#5509（attack_retry_exhausted で全項目不明、(a)）**: 本件と同じ終端型・同じ不明化機構。VERIFY-5509 が指摘した「終端 situation_held が `_order_for_step` / `cur` から得られる同一性を落とす」欠陥が、v1591 でも再現し、現行 v1597 でも残っている。本件はこの既知家族の再発として最も近い。

## §7 残る未決事項

- SHA-256 の再計算は python3 実行拒否のため未実施。§1 の raw 文字列と期待値を検品ゲートで照合すること。
- 3敗それぞれの将軍・HP・切り札効果・敗北の妥当性は、ローテーション済みの decision log がないため判定できない。ただし `attack_retry_exhausted` に到達した以上、現行コードの構造上は同 step で3回の attack loss が記録されたはずであり、打ち切り機構そのものは設計どおりである。
- v1448 自動携行が `Mc7fc03` 自身に適用されたかは、step 単位の `cards_autocarried` 行が incident に残らないため判定不能。自動携行機構が同窓内で2回発火したことまでは確認できる。
- イカリア/クラボニアのような目標選択が戦略的に妥当だったか（強すぎる敵への再攻撃だったか）は、この issue の範囲だけでは決着しない。本検証の (a) はあくまで証拠伝播・incident品質の実欠陥であり、攻城リトライ上限の存在自体を欠陥とする意味ではない。

## 検品ゲート確認（Muse、2026-11-28 15:05 JST）

- fingerprint / occurrence を検品ゲートで再計算した。SHA-256("11|sortie_failed|sortie_failed")[:16] = ba9d892e64bf0e45、SHA-256("g1190-ch1v9|1190|sortie_failed|sortie_retry_exhausted|Mc7fc03|None|None|1795821216")[:20] = e8de932deb613b0bfae7。いずれも issue の値と一致する。
- 結論 (a) 実欠陥は、攻撃3敗後の打ち切りと `sortie_failed` 起票分類自体は設計どおりである一方で、終端 `situation_held` レコードが将軍・出撃元・対象・目的を落とし、監査側も `world.launched_orders` から back-fill しないため issue が全項目「不明」になり、incident_id の raw でも `general|target` が `None|None` に退化していることで裏付けられている。#5509 と同じ家族の再発で、現行 main v1597 でも未修正である。
