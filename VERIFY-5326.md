# VERIFY-5326 — [半熟英雄] 築城可能なまま月一終了: ch11 17-11（タタール未試行）

- 対象: docich issue #5326 / 監査 `chikujou_available_at_end` (confidence: high)
- ラン: `g1190-ch1v9` / `hanjuku-chart-v1446`、月 ch11 17-11（観測 135 回 / 24.4 秒）
- fingerprint `0b6b69f4168c8ef4` / occurrence `f304f29efc35b599212f`
- issue 築城節（依頼記載の要点）: 結果 `ended` / 決定 `month_done` / 所持金 9777 / 必要残し 1380 /
  状態 `skipped` / 未試行候補 `タタール` / 試行済み `[]` / 増築済み `[]`
- 出撃: 確定 2 / 発射 0 / order_start 0 / order_launched 0（築城判定とは別系列の文脈情報）
- 検証範囲: read-only（本ファイルの作成以外の編集なし。実装・push・PR・issue コメント・本番 SSH なし）
- 検証ベース: `origin/main` = 2cbe3542f（`BOT_VERSION v1451`、#5343 マージ済み。
  行番号はこのコミット基準）。観測ランの v1446 は #5301（fold, v1438）と #5320（skip 診断,
  v1444）を含み、#5338（v1449, held 理由の名指し）を含まない。
- `gh` 実行不可（実行環境が `gh`・`python3`/`pytest`・一部の git コマンドを拒否）。issue 本文は
  依頼記載の要点のみを使い、判定は `origin/main` のコードと構造的推論に基づく。正本 handoff:
  `DOCICH_HANDOFF_PATH` 未設定のため未読（AGENTS.md §1 に従い通常検証）。
- 同シグネチャ既検証: VERIFY-5278（ch9 15-5、イデ！半角/全角、(a)、#5301 で fold 修正済み）、
  VERIFY-5313（ch10 17-6、ファルゲ未試行、state `done`、(a)）

## 結論: (a) 実欠陥 — 築城 sub は起動したが、最初の城選択に至る前に有限待ち（`chikujou_held`）で断念し `skipped` で月を終えた

状態 `skipped` + tried `[]` + upgrades `[]` + staffed・affordable な未試行候補タタールの組合せから、
当月の築城 sub が `month_sub_open` 後、**いかなる城の選択・拒否・レベル確認にも至らず**
有限待ちの上限で断念したことが構造的に確定する:

- `chikujou_held` 記録が存在したことは、PR #5338（`cc2b2d969`, v1449）が
  `_note_chikujou_skip_unexplained` に `chikujou_held` 記録を辿る分岐を追加する際に
  「docich#5326: chikujou_held -> skipped」と本 issue を直接引用しており確定的
  （`month.py` L728-738 のコメント、および `tests/test_hanjuku_chikujou_skip_note.py` の
  `test_a_hold_that_gave_up_names_the_hold_not_unexplained`）。
- `chikujou_held` は `_chikujou_wait`（`month.py` L1142-1151）または
  `castle_scroll_no_progress`（L1177-1185）で `declined+aborted` を立てて記録される。
  月メニュー復帰後 `_finish_month_sub` が `declined && gold==before` を評価し、
  `cost_declined` が無いため `more` は偽 → upgrades 空で `shop['chikujou']='skipped'`
  （L1806-1815）。issue の `skipped` と一致。
- tried が空なのは、`chosen` も `rows_tried` も無いまま終端したためである。
  `_finish_month_sub` は終端時に `rows_tried + chosen` を `chikujou_tried` へ必ず併合する
  （L1791-1795）ため、tried 空 ⟹ sub が**選択直前までのどこか**で止まった、つまり
  `chosen` を立てる前の wait 系に限られる。
- 監査が high で起票した事実から、`held_for`（recruit/egg 保留）・`menu_unreadable`・
  `reserve_skipped` の降格条件は全て偽（`month_audit.py` L1854-1863）。特に `held_for`
  が偽（= `_chikujou_hold` が None、`month_audit.py` L1607-1618）は、月終了時点で
  `shop['chikujou_held']` フラグが立っていなかったことを示し、本件は
  `_hold_month_cards` 由来の保留未解放（`month.py` L919-920 → L544 で解放）ではなく、
  sub 内の有限待ち断念（記録側の `chikujou_held`）であることが確定する。

## 検証観点への回答

### 1. 実欠陥か設計どおりか、#5313 との異同

- **実欠陥。** `gold 9777 >= reserve 1380 + CHIKUJOU_SPARE` で `_chikujou_affordable` は真
  （`economy.py` L1480-1490）であり、staffed な未試行候補タタールを残して月を終えたのは
  `_month_chikujou` の設計目標（「余剰があれば所有城も増築する」、`month.py` L1194 付近の
  docstring）の達成失敗。有限待ち → `declined` → `skipped` の終端そのものは fail-closed の
  設計どおりだが、その原因である「築城画面が規定回数内に読めなかった」状態は欠陥領域。
  監査がこの系列を降格せず high で起票するのも意図どおり（#5338 のコメントも
  「the audit still files, but the log now points at the real failure」と明記）。
- **#5313 との異同**:
  - 共通: 同じ `chikujou_available_at_end`・high。画面側の何かが読めず、未試行候補を残して
    終了する構造。
  - 相違 1（終了位置）: #5313 は `done`（ケントルム verified + tried 8 城の**後**に残候補
    ファルゲだけが届かなかった＝走査末尾での未到達）。本件は `skipped` で tried/upgraded
    ともに空（**sub 起動直後〜最初の選択までのどこか**で全滅）。同じシグネチャでも
    障害ステージが異なる。
  - 相違 2（話数・候補）: ch10 ファルゲ vs ch11 タタール。fingerprint は
    `sha256(chapter|kind|signals)` 系（`month_audit.py` `_fingerprint`）のため別 fingerprint、
    別 issue になるのは仕様どおり。
  - 相違 3（診断済み度）: 本件は #5338 が実例として挙げた `chikujou_held -> skipped` 系であり、
    次回以降は `chikujou_skip_after_hold.held_reason` で終端理由が issue から読める
    （v1449 以降）。#5313（`done`）は skip 診断の対象外。

### 2. PR #5320 の skip 診断は発火したか — v1446 では「発火したが原因名は付かない」

- 観測ランは v1446 で、#5320（v1444, `dd6ed0fbe`）の `_note_chikujou_skip_unexplained`
  は含まれる。月終了ブロック（`month.py` L684-685）で `shop['closed']=True` 直後に必ず呼ばれる。
- v1446 時点の同関数は 4 つの early-return を持つ:
  1. `shop['chikujou'] != 'skipped'` — 本件は `skipped` なので通過。
  2. `shop['chikujou_held']` フラグ — §結論のとおり監査 high によりフラグ非成立が確定 → 通過。
  3. `chikujou_last_eval.affordable is False` — sub は `_chikujou_affordable` 真の評価
     （`{'affordable': True}` を L1245-1246 で記録）の直後に開くため偽 → 通過。
  4. `chikujou_refused` が全未試行候補を覆う — 監査の `buildable`（`not refusal_covered` を
     含む、`month_audit.py` L1795-1796）が真で起票済み → 通過。
- したがって **v1446 では `chikujou_skip_unexplained` が月 17-11 に記録されたはず**
  （「拒否・保留・読取不可・予約不足の記録が無い」という理由文で）。ただし実際には
  `chikujou_held` 記録が存在したため、この「unexplained」ラベルは**誤記**であり、
  それを正すのが #5338（v1449）の `chikujou_skip_after_hold`（`held_reason` を名指し）。
- 確認の留保（検品ゲートで解消）: issue #5326 本文をゲートで取得し、`chikujou_skip_unexplained`
  の記録が決定列に存在することを確認した（02:05:29Z、reason「築城を skipped のまま月を終えたが、
  拒否・保留・読取不可・予約不足の記録が無い」）。v1446 での発火は構造推論ではなく実測で確定。
  ただしこのラベル自体は誤記であり（`chikujou_held` 記録が存在した）、#5338（v1449）が
  `chikujou_skip_after_hold` で正している。
- **#5278 との異同**:
  - 共通: 同じ `skipped` 終端（scroll 走査→`declined+aborted`→skipped）。#5278 も
    `castle_list_unread`/`castle_scroll_no_progress` の有限待ちで終わった。
  - 相違: #5278 の根因は `イデ!`（ASCII `!`）vs `イデ！`（全角）の半角/全角不一致であり、
    #5301（v1438, fold）で修正済み。本件はその fold **を含む** v1446 で発生しており、
    かつタタールは半角/全角バリアントを持たない純粋カタカナ名
    （`unicodedata.normalize('NFKC','タタール') == 'タタール'`）。
    よって幅不一致では説明できず、#5301 ではカバーされない別の unread 原因。

### 3. #5313 の (i)/(ii)/(iii) のどれに当たるか

- **(iii) 一時的な候補除外は構造的に除外できる。** 候補が一時的に空になった場合は
  `_chikujou_step` L1530 の `names` が空 → `chikujou_declined`（「増築候補となる所有城が
  なくなった」L1547-1549）になり、`chikujou_held` 記録は残らない。本件は #5338 の実例引用
  により `chikujou_held` 記録の存在が確定。また `_chikujou_candidates`（`economy.py`
  L1574-1585）の garrison 変動除外は監査 snapshot と同じロジックで評価され、
  終了時に候補が残っている以上、(iii) 経路なら起票されない。
- **(ii) `castle_scroll_no_progress` は issue 本文の決定列で除外できる。** この経路は
  hold に至る前に成功した scroll ごとに `chikujou_scroll` を記録する（L1187-1192）。
  issue #5326 の決定列に `chikujou_scroll` は 0 件であり、走査が一度も成功していない。
  よって (ii) は成立しない。
- **(iv) `build_screen_unclassified` が最も有力（検品ゲートでの切り分け）。**
  issue #5326 の `chikujou_held` 記録は `screen_kind: "discharge_menu"` で、sub 起動
  （02:05:27 `month_sub_open`、直前に `month_blind_nav` で月メニュー盲操作）から
  1 秒後の hold である。`_chikujou_step` は `'ぞうちくなさいます'` が画面文面に無いと
  一覧分岐（L1514）に入らず、L1551 の `build_screen_unclassified` へフォールスルーする。
  discharge_menu 画面には当該プロンプトが無いため、6 回の待ちはすべて (iv) 経路で
  消費されたと読める。**注意：** 本 VERIFY 初版で (i) を「最も有力」とした根拠は、
  #5338 のテスト `test_a_hold_that_gave_up_names_the_hold_not_unexplained` が
  `deviation_reason='castle_list_unread'` を**例示値**として使っていることだったが、
  これは本件の実測値ではない。本件の `deviation_reason` 自体は v1446 の issue 書き出しに
  含まれておらず（`reason`/`screen_kind` のみ）、(i) の成立には築城一覧画面が表示されて
  いたことが必要だが、記録上の画面は discharge_menu である。よって (i) は後退し、
  (iv) が最有力となる。残る (i) の可能性は、6 回の待ちの途中で画面が一覧へ遷移し、
  そこで unread になった場合に限られるが、その場合は `chikujou_scroll` または
  `chikujou_max_level_seen` の記録が残るはずで、決定列には存在しない。
- **(i) `castle_list_unread`**（一覧は描画されたが語/行構造が合わない）:
  上記のとおり、本件の記録では後退する。成立には築城一覧画面の表示が必要で、
  記録上の画面は discharge_menu である。#5338 のテストが本 issue を例として
  `deviation_reason='castle_list_unread'` を使っているのは例示値であり、本件の
  実測値ではない。
- **一意特定に不足している証拠（検品ゲートでの更新）**: issue #5326 本文はゲートで
  取得済みで、上記の切り分け（(ii) 除外、(iv) 最有力）は本文の決定列に基づく。
  残る不確定は `deviation_reason` そのものの値で、v1446 の issue 書き出しには
  含まれない。v1449 以降のランでは `chikujou_skip_after_hold.held_reason` が
  直接示すため、次回以降の同系 issue は本文だけで機構確定できる。本件自身の
  確定にはランの `hanjuku_decisions.jsonl`（`deviation_reason`・`observed_metric.text`
  を含む完全版）の回収が必要。なお (iv) の場合、直前の recruit sub 終了時の
  `menu_nav_stuck` → `month_blind_nav`（月メニュー盲操作）が誤行へ進み、
  discharge_menu を開いた可能性があり、盲操作と画面遷移の連鎖も修正方針の検討対象。

## 判定の理由

- **(b) でない**: fail-closed の `skipped` 終端は設計だが、staffed+affordable な候補を残す
  根本原因（画面が読めない/候補行が届かない）は欠陥。#5338 は終端理由を**名指しする診断**
  であって unread 自体の修正ではない。#5301（fold）・#5325 等の既存修正は v1446 に
  含まれたまま発生しているためカバー外。
- **(c) でない**: 監査は flow と同じ `_chikujou_candidates`/`_chikujou_staffed` で内部整合し、
  `buildable`（affordable・staffed・非 refusal_covered・非 cost_declined）真でのみ起票
  （`month_audit.py` L1795-1796, L1810）。起票条件は全て実測値で満たされる。
- **(d) でない**: 「実欠陥か設計どおりか」は確定できる（設計目標の達成失敗＝実欠陥）。
  下位機構（unread の具体的行/語）の一意特定だけが決定ログ末尾依存で、それは (a) の中の
  修正方針の選択問題に留まる（VERIFY-5313 と同じ構造）。

## 修正方針案（実装はしない）

1. **次の発生で機構を一意に切る**（既に #5338 で前進済み）: v1449 以降のランでは
   `chikujou_skip_after_hold.held_reason` が `castle_list_unread` /
   `castle_scroll_no_progress` / `build_screen_unclassified` 等を直接示す。本件以後の
   同系 issue は本文だけで機構確定できるはず。本件自身は `chikujou_held` の
   `deviation_reason`/`observed_metric.text` をランの `hanjuku_decisions.jsonl` と
   `hanjuku_frames/` の一覧フレームから回収して確定させる。
2. **機構別の本修正（検品ゲートでの順序変更：(iv) が最有力のため先頭）**:
   - `build_screen_unclassified`: sub 起動直後に別画面が居座る経路（直前 recruit sub 終了時の
     `menu_nav_stuck` → `month_blind_nav` による誤行、discharge_menu の居座り）をフレームで特定し、
     wait 中の回復操作または sub の翌月持ち越しを検討。盲操作と画面遷移の連鎖も対象。
   - `castle_list_unread`: 読めなかった可視行をフレームで特定。別表記なら `STATUS_NAMES`
     alias または `_chikujou_castle_key`（`month.py` L1102-1105）の fold 拡張。
     `len(rows)!=4` が所有城 4 未満や非城行の混入に起因するなら、不変条件を
     「既知城の行だけ検査し、未知行は行として数えない」形へ緩和する方向を検討
     （未知タイル許容の方針は要オーナー確認）。
   - `castle_scroll_no_progress`: 実一覧にタタールが現れない実条件（例: 特定の城が
     一覧から除外される章/状況）を調べ、候補計算または走査モデルへ反映。
     （本件では決定列の `chikujou_scroll` 0 件で既に除外済み。）
3. いずれの場合も、短い月（24.4 秒/135 観測、CHIKUJOU_WAIT_LIMIT=6 で実質 ~1.1 秒の猶予）
   で wait が尽きやすい点は、機構確定後に限界値の再評価対象として残してよい。

## 検証で使った根拠（抜粋・`origin/main` = 2cbe3542f の行番号）

- `src/docich/hanjuku/flows/month.py`:
  - 保留解放 L544（`pop('chikujou_held')` → `check`）
  - 月終了ブロック L684-695（L685 で `_note_chikujou_skip_unexplained` 呼出、`month_done` L687）
  - `_note_chikujou_skip_unexplained` L703-758: skipped ゲート L713、`chikujou_held`
    フラグゲート L715、`last_eval.affordable is False` ゲート L718-722、refused ゲート
    L724-727、`chikujou_held` 記録走査 L728-738（コメントに docich#5326 引用）、
    重複抑止 L739-742、`chikujou_skip_after_hold` L744-751（#5338/v1449）、
    `chikujou_skip_unexplained` L752-758（#5320/v1444）
  - `_hold_month_cards` L913-925（`skipped`+`chikujou_held` フラグ L919-920）
  - `CHIKUJOU_WAIT_LIMIT=6` L1068
  - `_chikujou_castle_key`（NFKC fold）L1102-1105、`_chikujou_name_labels` L1107-1116、
    `_chikujou_list_rows` L1119-1126、`_chikujou_screen_label` L1128-1136
  - `_chikujou_wait` L1142-1151（bound → `declined`+`aborted` → `chikujou_held` L1148）
  - `_chikujou_scroll` L1154-1192（`castle_list_unread` L1165/1172、
    `castle_scroll_no_progress` L1177-1185、bound=`stalled>=MENU_NAV_PRESS_LIMIT` または
    `presses>=len(labels)+4`、成功時 `chikujou_scroll` L1187+）
  - `_month_chikujou` L1194+（refused-set skip L1213-1218、全試行済み → done/skipped
    L1219-1222、unaffordable 評価 L1225-1244、`last_eval={affordable:True}` L1245-1246、
    `chikujou_menu_unreadable` → skipped L1261-1278、sub 起動 `month_sub_open` L1287）
  - `_chikujou_step` L1364+（一覧分岐 L1514、`castle_selection_unconfirmed` L1519、
    `names` L1530、scroll 呼出 L1545-1546、names 空 → `chikujou_declined` L1547-1549、
    `build_screen_unclassified` L1551）
  - `_finish_month_sub` L1632+、chikujou 終端 L1789-1828（tried 併合 L1791-1795、
    verified → done/check L1797-1805、declined → check/done/skipped L1806-1815）
- `src/docich/hanjuku/strategy/economy.py`: `_chikujou_affordable` L1480-1490、
  `_chikujou_reserve` L1527+、`_chikujou_candidates` L1574-1585（captured+home、
  `garrison[c]==[]` 除外、home→staffed→unmeasured 順）
- `src/docich/hanjuku/month_audit.py`:
  - `_chikujou_hold` L1597-1621（`skipped|None` 限定、flag or `month_cards_held_for_recruit`
    記録 → `egg_unverified`/`recruit_pending`）
  - `_chikujou_cost_declined` L1629-1665、`_chikujou_menu_unreadable` L1668-1684
  - `_chikujou_snapshot` L1687-1797（live sub の tried 併合 L1722-1728、maxed/unavailable
    L1735-1742、`refusal_covered` L1754-1757、`reserve_skipped` L1768-1771、
    `buildable` L1795-1796）
  - `_chikujou_event` L1800-1824（`buildable` ゲート L1810、month_done→`ended`）
  - `_build_chikujou_incident` L1827+（high 既定、held_for/menu_unreadable/reserve_skipped
    のみ medium 降格 L1854-1863）、`_track_chikujou` L1934+
- `src/docich/hanjuku_chart.py` L436-440: `CASTLE_NAMES[11]`（11城+ボス、タタールは7番目）
- `tests/test_hanjuku_chikujou_skip_note.py`: `test_a_hold_that_gave_up_names_the_hold_not_unexplained`
  が docich#5326 を引用し `deviation_reason='castle_list_unread'` を例示
- `src/docich/hanjuku/version.py`: `BOT_VERSION='hanjuku-chart-v1451-defense-wait-probe'`
- 前例: VERIFY-5278.md（`イデ!`/`イデ！`、同じ skipped 終端、#5301 で fold 修正）、
  VERIFY-5313.md（`done` 終端、(a)）、PR #5301 `bc5bed955`（fold、v1446 に含有）、
  PR #5320 `dd6ed0fbe`（v1444、skip 診断）、PR #5338 `cc2b2d969`（v1449、held 理由名指し、
  コメントとテストで本 issue を引用）

## 手順上の注記

- `python3`/`pytest` の実行は実行環境が拒否したため**未実施**（関連テスト:
  `test_hanjuku_chikujou_skip_note.py`・`test_hanjuku_chikujou_scroll.py`・
  `test_hanjuku_multi_chikujou.py`・`test_hanjuku_month_audit.py`）。
  → **検品ゲートで実行済み：現行 main（2cbe3542f）で上記4ファイル 201件 green。**
- `gh` 不可のため issue #5326 本文（直前決定列・`chikujou_held` の deviation_reason）は
  依頼記載の要点のみ使用。推測で補っていない。§3 の (i)/(ii)/(iv) の一意切り分けは
  決定ログ末尾の確認が必要（上記の不足証拠）。
  → **検品ゲートで issue 本文を取得済み。§3 の切り分けを更新：(ii) は決定列の
  `chikujou_scroll` 0 件で除外、(iv) `build_screen_unclassified` が最有力
  （`screen_kind: "discharge_menu"`、sub 起動から 1 秒後の hold、L1551 フォールスルー）。
  `deviation_reason` そのものの値は v1446 の書き出しに無く、完全版は
  `hanjuku_decisions.jsonl` の回収が必要。**
- `git merge --ff-only`/`git add`/`git commit` 等の git 書き込みが実行環境で拒否されたため、
  本ファイルは branch `verify/5326-chikujou-available-at-end`（基点 `bc5bed955`、
  origin/main の祖先）の worktree ルートに**未コミット**で置いてある。依頼のコミットは
  この環境では実行できなかった — 別環境で
  `git checkout verify/5326-chikujou-available-at-end && git merge --ff-only origin/main &&
   git add VERIFY-5326.md && git commit` が必要（行番号は全て `origin/main` =
  2cbe3542f 基準で、rebase 後も変わらない）。
  → **検品ゲートで上記手順を実行し、コミット・push・PR 化まで完了。**
- read-only 検証。VERIFY-5326.md の作成以外のファイル編集・本番 VM 操作・push・PR なし。
