# VERIFY-5365 — [半熟英雄] 出撃失敗: ch3 328a9db8

- 対象: docich#5365（監査自動起票、`sortie_failed` / confidence: high）/
  fingerprint `2811e2cc01ede86e`、ch3 / 月 6-2
- 観測: step `I:743437:328a9db8`（アテナ、パタゴーヌ→アルマムーン）で `order_failed`、
  理由 `sortie_no_spare`（移動元に残す将軍がいない）
- ラン: `g1342-1fe0734c` / `v1454`
- 検証範囲: read-only（本ファイルの作成以外の編集なし）
- 検証ベース: worktree `verify/5365-sortie-failed` @ `46d17f1a5`（main 先頭、
  v1455 = `hanjuku-chart-v1455-password-in-game-retry`、`src/docich/hanjuku/version.py:7`
  を実ファイルで確認）。行番号はこのツリー（＝現行 main）のもの。
- gh はこの環境で利用不可（実行が却下）。issue 本文は依頼記載の要点に基づく。
  関連テストの実行も却下されたため未実施。

## 結論: (a) 実欠陥 — ただし欠陥は出撃実行層ではなく監査起票層

`移動元に残す将軍がいない` での取消自体は、実一覧の証拠に基づくオーナー規定どおりの
正しい挙動（城を空にしない、ゲーム自体も1将軍城からの出撃を拒否する）。
一方で、この注文は `purpose='move'`（将軍移動）であり、`order_failed` を
`sortie_failed` として起票しないために v1333（#4931）で導入された
`SORTIE_NON_PURPOSES` 除外が **interim 注文では構造的に効かず**、
起票対象外のはずの移動失敗が `sortie_failed`（high）として issue 化された。
これは監査パイプラインの purpose 解決ギャップという実欠陥である
（§検証観点4、修正案は §6）。

## 検証観点1: `sortie_no_spare` の経緯（現行 main v1455 での切り分け）

### step の由来 — interim の将軍移動注文

- `I:` は `chart_adjust.INTERIM_PREFIX`（`src/docich/hanjuku_chart_adjust.py:28`）。
  `I:{rid[:6]}:{digest}` の形式は `_mechanical_interim` 内の `stable_step`
  （`strategy/replan.py:2240-2255`）が名付ける暫定注文 id で、`328a9db8` は
  `(purpose, source, target, general)` 内容のハッシュ。
- ch3 は road 章（`strategy/routes.py:21` `ROAD_CHAPTERS = frozenset({3, 9})`）で、
  `interim_candidates`（`replan.py:1026-`）の中継移動ブロック
  （`replan.py:1465-1509`）が `purpose='move'`・`cards=[]` の注文を作る。
  名指し将軍（ここではアテナ）が付くのは、出撃元の記録上の空き
  `free` が `chart.sender_min(chapter)=2`（`hanjuku_chart.py:1303-1305`、
  guard_free は第9話のみ `:1308-1318`）以上ある時だけ
  （`replan.py:1488-1505`；未達なら `MOVE_ANY_GENERAL`＋`source_probe`）。
  他の移動生成経路（`_defense_rebalance` `replan.py:702`、`_garrison_rotation`
  `replan.py:800-802`、空城補充 `replan.py:1528-1539`）も同じ「記録上2名以上」条件。

  → **計画時点では、パタゴーヌの駐留記録（`world.garrison` または部分一覧
  `garrison_partial`、`_known_residents` `replan.py:448-458`）が「空き2名以上」を
  示していた**、というのがコードから言える確定事項。

### 実行時の取消 — `_move_general_pick`（実一覧で再判定）

- deploy フローは `kind == 'general_list'` で
  `_observe_garrison`（`flows/deploy.py:1529-`、`:2323` で実一覧を先に駐留記録へ反映）
  → 各種ガード → `order.get('purpose') == 'move'` で `_move_general_pick`
  （`deploy.py:2361-2362`）。
- `_move_general_pick`（`deploy.py:923-973`）は **実画面の将軍一覧**
  `_listed_generals`（`:1483-1491`）を読み、`busy = _en_route(mem)[0]`
  （`os_layer/world.py:453-471`、在 flight の sortie 記録を持つ将軍）を除いた
  `spare` を作る。
- `deploy.py:944-948`:
  `(len(present) < 2 and not road_walk) or not spare` なら
  `_finish_order(mem, 'failed', deviation_reason='移動元に残す将軍がいない', …)`
  を呼び `[B, B]` で取消。`road_walk = chart.guard_free(chapter)` は第9話のみなので
  ch3 では `len(present) < 2` が無条件で効く。
- この文言を出す箇所はコードベース全体で **この1箇所のみ**
  （`sortie_held_source_defender` 系は別文言・別 decision）。よって
  本 step は `purpose='move'` 確定。

### order_failed → sortie_failed / sortie_no_spare の監査変換

- `_finish_order` は `orders.py:2521` で `order_failed` decision を記録する
  （`orders.py:2502-2558`）。
- 監査側 `_sortie_event`（`src/docich/hanjuku/month_audit.py:958-996`）は
  `SORTIE_TERMINAL`（`:921-925`）で `order_failed → sortie_failed/failed` に写像し、
  `_sortie_reason_code`（`:928-955`）が deviation 文言 `'移動元に残す将軍がいない'`
  を `sortie_no_spare` に分類する（`:947-948`）。
- `_track_sortie`（`:1389-1435`）で incident 化。confidence は
  `sortie_order_unaccepted` 以外の降格規則が無いため `high`（`:1036-1041`）。
  fingerprint は `_fingerprint(chapter, kind, signals)`（`:3032-3034`、
  `sha256("3|sortie_failed|…")[:16]`）で issue の `2811e2cc01ede86e` と整合。

### 実一覧が「1名以下／全員 busy」になり得る経路

- `len(present) < 2`：駐留記録が古い（最後の一覧読み以降に将軍が出撃・戦死・
  所在不明化）か、mint→実行の間に2人目が出撃した競合。記録と実一覧の食い違いは
  一覧を開くことでしか確かめられず、**開いて実一覧で取消すのがこの機構の設計**
  （`_observe_garrison` が先に実一覧を記録するため同じ mint は繰り返さない。
  失敗 step は `stable_step`＋`orders[step]='failed'` で同一 rid 内では再発行しない:
  `replan.py:2265-2271`、`orders.py:2527` の `failed_local_step` 抑制）。
- `not spare`：一覧にいる将軍が全員 `en_route` 記録中（例: パタゴーヌへ到着済みだが
  到着 reconcile が未済で `march_arrival_limit` 内の行軍扱い）。`_en_route` は
  到着上限超過で自動失効（`world.py:462-468`）するため窓は限られる。
- どちらが発火したかは incident `sortie_event`/`observed_metric`（`present[:8]` が
  そのまま入る）と `decisions.before` を見れば分かるが、gh 不可のため未確認。
  いずれにせよ取消自体は fail-closed の規定動作（オーナー 2026-11-06「城を空に
  しない」、`orders.py:798-799` コメント、b87d0215d）であり、同一内容の
  即時再発行ループも無い。

### 「計画が空（自動携行の対象・v1448 以降）」との関係 — 無関係

- その文言は `sortie_cards`（非携行）incident の描画（`month_audit.py:3756-3758`、
  v1454 #5364 で追加）であり、`sortie_failed` incident の様式ではない。
- 移動注文は `cards: []` で `card_select` ステージに入らない。
  `_auto_carry_safe_card`（`strategy/kit.py:108-146`、v1448 #5336）は
  `_deploy_cards`（`kit.py:51-114`）経由の出撃携行札だけを扱い、
  本件の失敗は将軍一覧ステージ（カード画面より前）で起きている。

## 検証観点2: PR #5359（#5353 空計画札の出撃拒否）との関係

- PR #5359 に相当するブランチコミット列（`818dc3460` deploy.py、
  `57ac10a9c` regions.py、`65d0fea80`、`47458ef52` collect_diagnostics.py、
  `19b005af7` version.py、`53fd76dc7`、`9ba8eddb9`、
  `bd9aeec07`/`3afd9fd19`/`21cad70ad`）は v1451（`2cbe3542f`）を親とし、
  `BOT_VERSION='hanjuku-chart-v1454-empty-planned-kit-guard'` を設定する。
- **このガードは現行 main に存在しない**: HEAD（v1455）のツリーに
  `_reject_empty_planned_kit` / `sortie_empty_planned_kit_rejected` /
  `EMPTY_PLANNED_KIT_RETRY_LIMIT` / `_requires_nonempty_sortie_kit` は無く、
  `git log HEAD -S` でも出ない。main 上の v1454 は
  `hanjuku-chart-v1454-sortie-card-type`（`cf7f1a3c6` #5364、監査の「型」表示のみ）。
  つまり v1454 の版番が二系統に重複しており、観測ランが mainline v1454
  （sortie-card-type）ならガード非搭載、ブランチビルドなら搭載 —
  incident の `evidence.bot_version` で判別できるが gh 不可で未確認。
- **いずれでも #5359 は本件をカバーしない**: ブランチ側の
  `_requires_nonempty_sortie_kit`（`818dc3460` の差分）は
  `order.get('purpose') == 'move'` を明示的に除外しており、
  `sortie_no_spare` はそもそも将軍一覧ステージ（出撃確認画面より前）の失敗。
- 修正前後の整理: 観測ラン v1454 は v1451 を含むため #5324 修正は搭載済み。
  #5359 のガードに対しては、mainline 基準では「未導入」だが
  本件の失敗様式とは無関係。

## 検証観点3: #5324（ch3 4-11、v1451 修正済み）との異同

- #5324 の機構: `_defense_gate`（`replan.py:2122-2186`）が roster_probe だけの
  候補で `defense_wait` を解除し、待機が月次 cycles を積めず出撃ゼロが続いた。
  v1451（`2cbe3542f` #5343）は probe-only 候補で待機を解除しない
  （`replan.py:2145-2159, 2180-2183`）＋攻撃ルートの無い城も読み取り専用
  プローブ対象にした（`replan.py:1955-1995` の `no_attack_leg` 処理。
  コメント中に第3話アルマムーン＝本件の移動先と同じ城への言及あり）。
- 本件との異同: 同じ ch3 の「各城が1将軍しか持たない飢餓状態」
  （`_sole_starved` `replan.py:2099-2114`）を背景にするが、機構は別。
  #5324 は出撃が一切発行されない停滞の欠陥、本件は発行された移動注文が
  実一覧で規定どおり取消された事象。v1451 修正は v1454 ランに搭載済みで、
  本件はその修正の対象機構（待機解除）では起きていない。
- `defense_wait` / probe 経路との関係: `_sole_starved` 成立時は gate='hold' で
  `_off_chart` が早期 return し注文自体が発行されない（`replan.py:3101-3103`）。
  本件で移動が発行できた時点で、記録上はパタゴーヌに空き2名以上があった
  （＝`_defense_gate` の評価対象としても非飢餓扱い）。defense_wait は
  `DEFENSE_WAIT_MAX_CYCLES=2`（`replan.py:2096`）経過で 'break' になるが、
  b87d0215d（#3566、オーナー 2026-11-06）で1将軍出撃の例外は廃止済みのため
  `gate == 'break'` でも `allow_sole=False`（`replan.py:3063-3064, 3170-3171`；
  `allow_sole=(gate=='break')` が残るのは採用済みプラン経路 `:3168` のみ）。
  なお `defense_break_sources` への書き込みは現行コードに存在せず、
  `_defense_break_allowed`（`orders.py:2233-2256`）は実質 dead path。
- 「戦闘の長引き」との関係: 無関係。本件は戦闘に到達せず出撃元の一覧で終了。

## 検証観点4: 起票経路の欠陥（今回の実欠陥）

- 設計: `SORTIE_NON_PURPOSES = {move, house, repair, recall, defend}`
  （`month_audit.py:1097`）。`_track_sortie` は `order_failed`/`situation_held`
  の purpose がこれに含まれる場合 **起票しない**（`:1395-1399`）。
  v1333（#4931）のコミットメッセージ「exclude move/house order failures from
  sortie issues」が意図を明示。テスト `test_sortie_order_failure_skips_move_orders`
  （`tests/test_hanjuku_month_audit.py:772-779`）もこの除外を確認する。
- ギャップ: purpose 解決は `event.get('purpose') or _context_order(after, step)
  .get('purpose')`（`:1397`）の2系統しかなく、両方とも本件では空振り:
  1. `deploy.py:945-948` の `_finish_order` 呼出は `purpose`/`general`/`target`
     を記録しない（`observed_metric` は `present[:8]` の **list** で、
     `_sortie_event` の `metric.get('purpose')` も取れない `:972-978`）。
  2. `_context_order`（`:1100-1107`）は `context.plan.chart_plan.orders` だけを
     検索する。interim 注文は `chart_adjust.interim_order`（PLAN region、
     `state/regions.py:219`）に置かれ、`_summarize_plan`（`:359-382`）は
     `active`/`orders`/`chart_plan` しか写さないため、監査コンテキストに
     interim 注文は現れない。
- 結果: `purpose='move'` の `order_failed` が `SORTIE_NON_PURPOSES` を潜り抜け、
  設計上起票対象外の移動取消が `sortie_failed`（high）で起票された。
  本件の issue はまさにこのギャップの産物。同じギャップは no_spare に限らず、
  interim 移動注文の全ての `order_failed`（城メニュー不成立等）に効く。
- 実行層の挙動（取消・一覧記録・step 失敗化・再発行抑制）は設計どおりで
  修正不要と判断する。欠陥は起票層の purpose 未解決のみ。

## §6 修正案（実装は別タスク）

1. **根本解決（推奨）**: `orders.py:_finish_order`（`:2502-2558`）で `order_failed`
   記録に注文の `purpose`・`general`・`target`（`source` は既に各所で付与）を
   付ける。`:2529` で goals 用に `_order_for_step` を既に解決しているため、
   同じ order から fields へ写すだけで全 `order_failed` が record 側で
   purpose を持ち、`_track_sortie` の `event.get('purpose')` が効く。
   監査コンテキスト欠落（`context_missing`）でも除外が働く点で頑健。
   基準チャート注文は `purpose` を持たない（`hanjuku_chart.py` に該当フィールド
   無し）ため、未指定は従来どおり攻撃扱いで起票される挙動も変わらない。
2. 補助: `month_audit._context_order`（`:1100-1107`）を
   `chart_adjust.interim_order`、`world.launched_orders`、`world.sorties` まで
   検索対象に拡張する（`_summarize_plan`/`_summarize_world` に interim/sortie の
   purpose 項目を足す必要あり）。ただしコンテキスト欠落時は無力なので
   単独では 1 より弱い。
3. 最小修正案: `deploy.py:945-948` の `_finish_order` 呼出に
   `purpose='move'`（と `general`/`target`）を渡す。ただし他の移動失敗経路や
   house/repair/recall 系の order_failed はカバーしないため部分対応。
4. テスト: `test_sortie_order_failure_skips_move_orders` と同型で、
   `chart_plan` に注文が無い状態（`chart_adjust.interim_order` のみ）＋
   record に `purpose='move'` が付いた `order_failed` が起票されないことを
   確認する回帰テストを足す。

## 手順上の注記

- read-only 検証。VERIFY-5365.md の作成以外のファイル編集・本番 VM 操作・
  push・PR・issue コメントは実施しない。
- gh はこの環境で利用不可（コマンド実行が却下）。issue #5365/#5353/#5324 本文、
  PR #5359/#5364 の状態は依頼記載とローカル git 履歴に基づく。
  観測ランが mainline v1454（sortie-card-type）かブランチビルド
  （empty-planned-kit-guard）かは `evidence.bot_version` で判別可能だが未取得。
  どちらでも結論は変わらない（`_requires_nonempty_sortie_kit` が move を除外）。
- 関連テスト（`tests/test_hanjuku_month_audit.py`、`test_hanjuku_defense_wait.py`、
  `test_hanjuku_defense_wait_probe.py`）は実行が却下されたため未実施。
  静的確認では `test_hanjuku_month_audit.py:772-779` が「`chart_plan` 上の move
  注文の order_failed は起票しない」を確認している一方、record 側 purpose や
  interim 注文の経路を試すテストは存在しない（§6-4）。
- 監査ファイルのパス: `month_audit.py` は `src/docich/hanjuku/month_audit.py`、
  `execution.py` は `src/docich/hanjuku/strategy/execution.py`（いずれも
  `flows/` 配下ではない）。
- `observed_metric` の実一覧内容（`present` が1名だったか、2名以上で全員
  en_route だったか）の区別、および駐留記録が古くなった直接原因の特定には
  incident record（`sortie_event`/`decisions.before`）が必要。
  VM へは SSH しない方針のため未取得。ただし §検証観点4 の起票ギャップは
  この区別に依らず成立する。
