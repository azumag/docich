# VERIFY-5319 — [半熟英雄] 切り札在庫があるのに非携行で出撃: ch11 カシュー

- 対象: azumag/docich issue #5319 / fingerprint `4dfd4ad0f6d10426` / occurrence `4d6f23e75de03f9b9ffc`
- 観測: ch11 17-11、step `A:e6c9732e:Mc75d8c`（将軍 カシュー / purpose attack / アルマムーン→ゴンダル）、
  計画cards `['クースカン', 'ミックミー', 'ノリウツール']`、実測携行 空、在庫12種全て正数
  （計画札は クースカン94 / ミックミー93 / ノリウツール97）、order_start 0 / order_launched 2 / 確定 0
- ラン: `g1190-ch1v9` / `hanjuku-chart-v1435`
- 検証範囲: read-only（本ファイルの作成以外の編集なし）
- 検証ベース: origin/main `23c108d62`（v1447 = `hanjuku-chart-v1447-password-measured-order`、
  `src/docich/hanjuku/version.py:7` を `git show origin/main:` で確認）。本 checkout は
  `verify/5319-sortie-without-cards` @ `dd6ed0fbe`（v1444）。v1435→v1447 でカード選択経路の変更は
  boss companion wait 上限（#5310/#5308、非ボス出撃に無関係）のみで、引用行番号は origin/main のもの。
- gh は利用不可（実行環境で却下）。issue 本文は依頼記載の要点に基づく。関連テストも実行却下のため未実施。

## 結論: (a) 実欠陥 — ただし #5282 とは別機構。計画した在庫札が採用層ではなく出撃実行層で失われた

`order_launched` は送信日誌A受領＋ピッカー閉鎖の実測裏付けのある確定出撃であり、
出撃確認で読まれた携行kitは空（または blind approve で未読）として実測された。計画された
クースカン・ミックミー・ノリウツールの3枚が在庫にありながら1枚も携行せず
ゴンダル（非ボス城、エッグママがボスの章の通常 attack）へ出撃したのは、#4685/#4895 で
オーナーが「欠陥」と判断したのと同じクラス（attack・在庫放置・空携行）の挙動である。

**ただし原因箇所は #5282 とは異なる。** #5282 の欠陥は「チャートが在庫無しの札を計画 →
`_adopt_plan` が verbatim 採用 → `_stock_cards` フォールバックが届かない」という
**採用層**のギャップだった。本件は計画3枚が全て在庫正数であり、採用時に `_stock_cards`
を通しても同じ3枚が返るだけ（`replan.py:889-894` の held 経路）なので、
採用層のフォールバック欠如は本件を説明しない。未マージの修正 PR #5304（v1442、OPEN・CONFLICTING、
gh 未確認で差分は未検証）が採用層のフォールバック追加であっても、本件の携行結果は変わらない。
本件の喪失は採用後の**出撃実行層**（card_select〜sortie_confirm）で起きた。

## 根拠となるコード箇所（origin/main v1447）

### 検証観点1: #5282 機構は本件に適用されない

- `src/docich/hanjuku/strategy/replan.py:2901-2905` — `_adopt_plan` は `o['cards']` を verbatim で
  `'cards': list(o['cards'])` として採用。#5282 と同じ構造のまま（main で不変）。
- `src/docich/hanjuku/strategy/replan.py:862-908` — `_stock_cards` の適用箇所は暫定（interim）
  計画のみ（`:1378`、`:1704`）。`wanted` が全て在庫正数なら held 経路でそのまま返る（`:889-894`）。
  本件の `wanted=['クースカン','ミックミー','ノリウツール']` は全て在庫正数 → 採用時に
  `_stock_cards` を通しても出力は同一。**よって #5304 型の採用層修正は本件を救えない。**
- `src/docich/hanjuku_chart_adjust.py:279-282` — `_order` は `cards ∈ CARD_NAMES` と枚数のみ検査。
  今回モデルはプロンプト（`hanjuku_chart_worker.py:110-113`「在庫1以上と実測された札だけ」）を
  遵守しており、#5282 のモデル未遵守とは状況が違う。
- `src/docich/hanjuku/strategy/kit.py:25-34` — `_cap_card_ids` は ID 合計 <48 の範囲で計画順に残す。
  クースカン(13)+ミックミー(17)+ノリウツール(11)=41 <48 なので全3枚が残り、ID cap も原因でない。

### 検証観点2: 検出は実測に基づき仕様どおり（#5258 の修正は正常動作）

- `src/docich/hanjuku/month_audit.py:2277-2281` — `_positive_card_stock` は `card_stock` の正数のみ。
  issue の在庫12種は全て正数で検出条件を満たす。
- `src/docich/hanjuku/month_audit.py:2309-2353` — `_sortie_carried_cards` はレア/強/卵落キット、
  発射record、`order_context[step].observed_metric.cards`、
  `world.launched_orders[step].departure_binding.cards`、同一stepの `sortie_confirm` record の
  `cards` を実測kitとして読む。全て空/欠落 → `carried=[]` は実測に基づく正しい判定。
- `src/docich/hanjuku/month_audit.py:2356-2385` — `_sortie_cards_event` は attack/retake/boss で
  在庫正数・実測携行空の launch record に発火。`planned_cards`（`:2382`）は
  `chart_plan.orders[].cards` の計画表示であり、実携行の証拠には使わない
  （`docs/operations/hanjuku-month-audit.md:283-286`: binding を持たない snapshot の `cards`
  は計画値の可能性があるため証拠にしない。実測kitが空**または未読**(blind approve等)の出撃だけを
  issue にする）。blind 承認で未読だった場合でも仕様どおり発火する。
- 確定 0 / 発射 2 / order_start 0 の読み方（VERIFY-5282 と同じ）:
  `_build_sortie_cards_incident`（`month_audit.py:2390`）は `_read_decisions` で**直前120秒**の
  決定行だけを集計するため `order_start 0` は窓外の先行記録で、「確定」は当月の
  `month_sorties.launches`（`month_audit.py:2396`、roster 読みでの発射確定数
  `flows/map.py:3153-3157`）であり未確定を意味しない。`order_launched 2` は窓内の2件の発射記録
  （Mc75d8c を含む実体。`flows/map.py:2910-2932` `_commit_departure` が receipt 裏付けのみで
  `order_launched` を出す）。

### 検証観点3: 計画3枚が実行層で失われた候補経路（どれが発火したかはランの決定ログが必要）

card_select で `wanted = _deploy_cards(order, mem)`（`strategy/kit.py:51-108`）が空になるか、
非空でもパネル処理が escape すると、1枚も選ばず `sortie_confirm` へ抜けて発射できる。
本件の step は `A:` 調整チャートの非ボス attack（`chart_adjust.py:117` `PLAN_PREFIX 'A:'`、
`kit.py:37-48` `_strict_boss_cards` は基準チャートstep限定で非適用、
`deploy.py:1412-1416` `_cursor_order_cards` は `GUARD_FREE_CHAPTERS={9}` の9話限定で非適用）。

1. **`card_zero_at_start` フィルタ（最有力の構造的ギャップ）** —
   `flows/map.py:2641-2642` が order_start 時に「台帳で count==0」の札を記録し、
   `kit.py:91-96` が計画札から「`live_stock.get(c) or 0` ≤0 のもの」を除外する。
   **`live_stock` にキーが無い（CARD_STOCK_LIMIT=24 の FIFO 追い出し等）場合も 0 扱いで
   フィルタは外れない**（`kit.py:93-95`）。パネル窓がその札を1度でも正数で読めば
   `_observe_card_stock` の revived 機構（`kit.py:264-269`）で即解除されるが、
   `wanted` が空になると deploy.py:2615-2616 が**最初の観測で即 B を押して一覧を抜ける**
   （4行窓の初回表示だけで終了し、filtered 札を探すスクロールは走らない）。
   つまり order_start 時の台帳-0 が stale だった場合、検証なしに全計画札が落ち、
   その後の別の読み（フィールド一覧・他出撃のパネル）で台帳が正数へ更新されれば
   incident は「在庫正数・携行空・計画あり」を正確に報告する — 本件のシグネチャと一致する。
   発火には order_start 時に3枚が台帳-0 だったことが前提で、実在庫と矛盾する
   誤0証明（`_observe_card_stock` の between-rows/complete-panel 証明 `kit.py:255-260`）が
   必要であり、決定ログなしには確定できない。
2. **`card_drop` の累積（通常注文に復元経路なし）** —
   `deploy.py:555-590` `_drop_card` は選択不能が `CARD_MISS_LIMIT=5` 読み続いた計画札を外す
   （`CARD_SCROLL_LIMIT=32`、12種在庫では全一覧を探索可能な設計）。`card_miss`/`card_drop` は
   chapter 期間持続する FLOW 状態（`state/regions.py:303-304`）で order_start でも
   `card_unreadable`/`card_skip_departure` 等と違いリセットされない
   （`map.py:2633-2636` のリセット対象外）。ボス注文には `note_card_purchase` による
   drop 復元があるが（`strategy/boss_cards.py:193-200`）、通常注文には無い。全3枚が
   先行試行で drop 済みなら `kit.py:78-80` で `_deploy_cards`→[] → 即B → 空kit確定 → 発射。
   実在庫の札が「選択不能」になるにはパネル行の読み違い（行不在・数量0誤読）が必要。
3. **`card_list_unreadable_depart` + 確認 escape（オーナー承認の退避）** —
   `_measured_card_select` が `NORMAL_CARD_UNREADABLE_LIMIT=3` 観測連続で None を返すと
   `deploy.py:2554-2584` が `card_list_unreadable_depart` を記録し attempt0 で B 1回のみ
   （picked=[] のまま一覧を閉じて確認へ。attempt1-3 のA押下は強調行の札を取るため
   非空kitになり得る）。確認は readable 空kit なら `sortie_kit_mismatch`
   （`deploy.py:2769-2775`、SORTIE_CONFIRM_LIMIT=8 後に読めたkit=空を承認）、
   読めなければ `sortie_confirm_blind_approve`（`:2753-2763`、非ボスでは
   SORTIE_CONFIRM_UNREADABLE_LIMIT=3 後に既定Aで発射）へ進む。
   これらは 2026-11-10 オーナー承認の「取り消すより出撃を優先する」bounded escape で、
   発火はパネル測定失敗を意味する。blind approve は binding を作らず、
   `departure_snapshot` fallback（`execution.py:123-136`）の snapshot.cards は計画値として
   監査に無視される（`month_audit.py:2340-2343` コメント）。
4. **bound execution の空kit 継承** —
   確定時に `confirm_departure_order`（`execution.py:72-91`）は**実測kit**を
   `departure_binding.cards` に固定し `order.cards` へも写す。一度空kitで確定すると、
   同一 attempt/sortie が有効な間は `bound_fields`（`execution.py:117-120`）→
   `_deploy_cards`（`kit.py:53-61`）が `bound['cards']=[]` を返し、再実行も空のままになる。
   初回の空確定そのものの説明にはならないが、窓内 `order_launched 2` に同一stepの
   再発射が含まれる場合の増幅機構としてあり得る。

いずれの経路も「在庫のある計画札を意図的に置いていく」設計ではなく、測定失敗の退避か
台帳の stale 値の無検証信用による喪失であり、オーナーの欠陥判断クラス（attack・在庫放置・
空携行）に一致する。**どの経路が発火したかは、ランの `hanjuku_decisions.jsonl` 上の
`card_zero_at_start` 関連/`card_dropped`/`card_list_unreadable_depart`/`sortie_kit_mismatch`/
`sortie_confirm_blind_approve`/`card_scroll` の記録が必要であり、この環境（VM へ SSH 不可）
からは取得できない。** よって (a) の結論は「欠陥クラスの出撃が実測で発生し、採用層は無実、
実行層に複数の既知の喪失経路が残っている」構造的判定であり、特定経路の確定ではない。

### 検証観点4: #5282 / #5317 との異同（集約判断点の分離）

- **#5282（(a)、計画札が在庫になくフォールバックも届かず空）**: 本件とは**別機構**。
  #5282 はモデルが在庫無しの札を計画 → `_adopt_plan` が verbatim 採用 → 一覧で全て選択不能 →
  drop → 空、という採用層ギャップ。本件は計画3枚が全て在庫正数であり採用は正常、
  喪失は実行層。#5304 がマージされても本件は再現し得る。
- **#5317（(b)、cardless order、計画cards=[]）**: 本件とは**別機構**。#5317 はチャートが
  cards を計画しない出撃であり、オーナー設計判断「チャートがカードを計画していない出撃で、
  在庫があるのに自動で1枚携行させるか」の対象。**本件はチャートが3枚を計画していたため、
  その設計判断は適用されない。** #5317 が本 issue へ集約されたのは同じ
  `sortie_without_cards` シグネチャだからで、機構の同一性を意味しない — 検証結論とは
  分離して記録する。
- fingerprint は `"{chapter}|{kind}|{signals}"`（`month_audit.py:2452-2453` 周辺の
  `_fingerprint` 呼出、`4dfd4ad0f6d10426` = ch11|sortie_without_cards|…）で章・kind が
  同じなら全 occurrence が同じ fingerprint を持つ。同一 issue への occurrence 集約は仕様。

## 修正案（実装は別タスク）

1. `card_zero_at_start` フィルタの補強（`kit.py:91-96`）: `live_stock` にキーが無い場合は
   0 扱いせず「不明」として探索対象に戻す。または計画kit**全体**がフィルタで空になるときは
   即Bせず、filtered 札を一覧スクロールで1度だけ再検証してから退避する
   （stale zero をパネルで再証明する経路が今は無い）。
2. `card_drop` の復元を通常注文にも: 台帳が drop 済みの札を正数で再証明したら
   `card_drop[step]` から戻す（`boss_cards.note_card_purchase` の boss-kit 復元
   `:193-200` と対称）。少なくとも order_start で台帳正数の drop を再評価する。
3. `card_list_unreadable_depart`/`sortie_confirm_blind_approve` の発射前に、台帳で
   計画札が正数なら「読めないまま空で出撃する」か「取消して再読を待つ」かを
   オーナー判断で決める（2026-11-10 の「取消より出撃優先」と衝突し得るため設計判断）。
4. order_start（`map.py:2641` 付近）で計画札が台帳-0/欠落でも他の正数在庫があるとき、
   暫定指示と同じ `_stock_cards` 相当の補填を検討する（採用層の #5304 とは別の実行層対策）。
5. 監査側の再判定を変える必要はない — 実測kitが空/未読で在庫正数の発射を拾うのは仕様どおり。
   ただし対応方針として経路別のシグナル分離（drop 由来か unreadable 由来か）を
   incident の `decisions.counts` から issue 本文へ出すと次の検証が速い。

## 手順上の注記

- read-only 検証。VERIFY-5319.md の作成以外のファイル編集・本番 VM 操作・push・PR・
  issue コメントは実施しない。
- gh はこの環境で利用不可（コマンド実行が却下）。issue #5319/#5317/#5282 の本文・
  #5304 の差分・状態は依頼記載に依る。#5304 が採用層フォールバック以外を含むかは未検証。
- 関連テスト（`tests/test_hanjuku_stock_cards.py`、`test_hanjuku_card_zero_live_stock.py`、
  `test_hanjuku_sortie_evidence.py`、`test_hanjuku_month_audit.py`）は実行が却下されたため
  未実施。静的確認では `test_hanjuku_month_audit.py:1744-1780` が「実測空kitは起票・
  計画値のみの snapshot は証拠にしない」を、`test_hanjuku_card_zero_live_stock.py:55-85` が
  「live正数で zero フィルタ解除・同一観測で再計算される」ことを、それぞれ現行挙動として
  裏付けている。
- 検品ゲート（2026-11-24 12:1x、Muse）：現行 origin/main（`23c108d62`、v1447）
  で主要な引用を直接確認（`_adopt_plan` verbatim、`_stock_cards` held 経路・
  呼出箇所、`_cap_card_ids` の ID 合計 41<48、`card_zero_at_start` フィルタの
  キー欠落=0 扱い・revived 機構、即B・`card_drop`・unreadable 退避・blind
  approve・kit mismatch、`departure_binding` の実測kit固定・bound 継承、
  `_sortie_carried_cards` / `_sortie_cards_event` の実測主義・計画値除外、
  120秒窓・fingerprint 式、boss-kit 復元、card_drop の FLOW/chapter 寿命、
  `PLAN_PREFIX 'A:'`・`_order` 検証・チャートワーカーの在庫実測指示、
  監査ドキュメントの計画値除外）。`kit.py`・`deploy.py`・`execution.py`・
  `replan.py`・`boss_cards.py` は worktree base `dd6ed0fbe` と origin/main で同一。
  `map.py` の差分は城ジャンプ停滞カウンタ（#5308）のみ、`month_audit.py` は
  battle_start 計数修正のみで、カード選択経路の変更はない。関連テスト 4件
  （stock_cards / card_zero_live_stock / sortie_evidence / month_audit）は
  321件 green。秘密情報スキャン clean。
  補足：`execution.py` は `src/docich/hanjuku/strategy/execution.py`、
  `month_audit.py` は `src/docich/hanjuku/month_audit.py`（いずれも `flows/`
  配下ではない）。本文中の裸のファイル名の引用は行番号とも正しい。
- 発火経路の確定には g1190-ch1v9 の `hanjuku_decisions.jsonl`（`card_dropped` /
  `card_list_unreadable_depart` / `sortie_kit_mismatch` / `sortie_confirm_blind_approve` /
  `card_scroll` / `ledger_drift` の有無）が必要。VM へは SSH しない方針のため未取得。
