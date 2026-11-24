# VERIFY-5313 — [半熟英雄] 築城可能なまま月一終了: ch10 17-6（ファルゲ未試行）

- 対象: docich issue #5313 / 監査 `chikujou_available_at_end` (confidence: high)
- ラン: `g1190-ch1v9` / `hanjuku-chart-v1435`、月 ch10 17-6（観測 140 回 / 28.1 秒）
- fingerprint `62d6cbb244f74d86` / occurrence `647fc0db127250fdf70a`
- issue 築城節: 結果 `ended` / 決定 `month_done` / 所持金 9749 / 必要残し 30 / 状態 `done` /
  未試行候補 `['ファルゲ']` / 試行済み `['アルマムーン','カルカル','カロナック','ケントルム','コッカーニュ','トルストリア','ピランドリア','マカリア']` / 増築済み `['ケントルム']`
- 直前の決定: `chikujou_scroll`（4 行の築城一覧を下へ送る）が多数、`chikujou_max_level_seen` が散見
- 検証範囲: read-only（本ファイルの作成以外の編集なし。実装・push・PR・issue コメント・本番 SSH なし）
- 検証ベース: `origin/main` = bc5bed955（`BOT_VERSION v1445`、#5321・#5301 マージ後。行番号はこのコミット基準）。PR #5301 の fold 修正（v1438）と PR #5320（v1444）を含む。issue の観測ランは v1435 で両修正より前。
- `gh` 実行不可（実行環境が `gh`・`python3`/`pytest`・一部の git コマンドを拒否）。issue 本文は
  依頼記載の要点のみを使い、判定は `origin/main` のコードと構造的推論に基づく。正本 handoff:
  `DOCICH_HANDOFF_PATH` 未設定のため未読（AGENTS.md §1 に従い通常検証）。
- 同シグネチャ既検証: VERIFY-5278（ch9 15-5、イデ！半角/全角不一致、(a)、PR #5301 で fold 修正）

## 結論: (a) 実欠陥 — 築城可能な候補が一覧走査で「届かず」終了した。ただし下位機構の一意特定には決定ログ末尾が必要

状態 `done` + upgrades `['ケントルム']` + 未試行 `['ファルゲ']` の組合せから、当月の築城サブフローが
少なくとも 1 城を実測増築した**後**、残候補ファルゲを一度も選択せずに終端したことが構造的に確定する。
設計意図（`_month_chikujou` docstring: 「余剰があれば他の所有城も増築する」、
`month.py` 主版 L1173-1176）に照らせばこれは達成失敗であり、監査の high 起票は正しい。

`done` に至る経路は `_finish_month_sub`（主版 L1767-1796）上 2 系統しかない:

- verified 終端（支払い照合済みの増築直後）: `more` が偽 → `done`（L1784）。
  `more` は `any(c not in tried for c in _chikujou_candidates(mem))` を含む（L1781-1783）ため、
  **その評価時点でファルゲが候補集合から外れていた**（下記の一時除外機構）場合のみ成立。
- declined 終端: `sub.get('declined') and gold == before` かつ upgrades 非空 → `done`（L1785-1794）。
  `declined` は `_chikujou_wait` 上限（`chikujou_held`、L1121-1130）か、候補空の
  `chikujou_declined`（L1526-1528）で立つ。

「到達したが選択されなかった」は構造的に不可能である。可視・可読の行なら
`menu_to`→`here`→`[pad('a')]` で `chosen` が立ち（L1509-1517）、選択後の全経路
（拒否・レベル5・支払い・`castle_selection_unconfirmed` abort）で `chosen`/`rows_tried` が
`chikujou_tried` へ併合され（L1771-1774、`_note_chikujou_tried` L1533-1545）、
監査の `candidates` から除外される（`month_audit.py` L1715-1746）。`イデ！` 型の
「選択不能」のみが「未試行のまま」を作れる。

## 検証観点への回答

### 1. ファルゲが未試行のまま終了した経路 — 「月が先に終わった」は否定、残るは一覧未到達/不一致/一時除外

- **月次処理が先に終わった説は否定できる。** `month_done` は `mem['month_sub']` が残る限り
  `_finish_month_sub` で保留され（主版 `month.py` L606 および L664 のホールド）、sub 解決後にのみ
  `shop['closed']` → `_record('month_done')`（L684-695）へ進む。状態 `done` は sub が
  `_finish_month_sub` 内で評価済みの証跡であり、走査途中で月が切れたのではない。
- **スクロールが届かなかった vs 到達したが選ばれなかった**: 上記のとおり選択不能のまま
  「届いたが選べなかった」経路は存在しない。残る機構は 3 系統:
  - **(i) 行は存在するが語が照合できない**（未読・別表記）: その行が 4 行窓に入ると
    `_chikujou_scroll` の `len(rows) != 4` 不変条件が壊れ `castle_list_unread` →
    `CHIKUJOU_WAIT_LIMIT`（6 観測）→ `declined+aborted` → B（L1147-1151、L1121-1130）。
    #5278 と同型。ただし行が `len(labels)+4 = 15` press または `stalled>=4` に先に達する前の
    打ち切りなので、**その行より下は以後走査不能**。
  - **(ii) 行が一覧にそもそも描画されない**（または別の既知名として描画される）: 全行が既知名なら
    不変条件は保ち、走査が尽きて `castle_scroll_no_progress` → `declined+aborted`（L1155-1162）。
    9 所有城を 4 行窓で走査してもファルゲの行が一度も現れなかった場合に相当。
  - **(iii) 最終評価時点でファルゲが候補集合から外れていた**: `_chikujou_candidates`
    （`economy.py` L1574-1585）は `captured` + home で `garrison[c] == []` の実測空城を除外する。
    当月内に `garrison['ファルゲ'] = []`（出撃ディスパッチ `orders.py:1590`、戦闘全滅 `battle.py:1564`、
    無将軍拒否 `month.py` L1374-1378 等）→ 評価時に候補外 → `names` 空で `chikujou_declined` または
    verified-終端の `more` が偽 → `done`。**その後スナップショットまでに garrison が再スタック/復帰**
    すれば監査は未試行・staffed と読む（`_chikujou_snapshot` は after-state の mem を使う、
    `month_audit.py` L1685-1746）。scroll 記録が多数あるのは、他の未試行候補が残っていた期間とも
    整合するため、この系統を記録だけでは除外できない。
- **短い月（28.1 秒 / 140 観測）の扱い**: `MIN_MONTH_SECONDS = 120` と `MIN_OBSERVATIONS` は
  `_confidence`（`month_audit.py` L2967-2984）で zero_sortie 系にだけ効く。chikujou incident は
  `_build_chikujou_incident`（L1825-1929）が confidence を直接決め（L1851-1861）、
  月の長さを見ない → 短月でも high で起票されるのは仕様どおりであり、欠陥の根拠にはならない
  （むしろ異常系列が自然に短月に収まるだけ）。

### 2. #5278 との異同 — 同じ構造だが、確定した名照合ミス型とは言えない

- 共通: 同じ `chikujou_available_at_end` で、「モデル上 buildable な候補が一覧走査で届かない」構造。
  #5278 の終端（`castle_list_unread` または `castle_scroll_no_progress`）は本件でも同じ。
- 相違 1（名の性質）: #5278 の `イデ！` は画面表記 `イデ!`（ASCII `!`）が実測済みで、
  NFKC fold で一意に説明できた。`ファルゲ` は全角記号を含まない素直なカタカナ名
  （`hanjuku_chart.py` L433-434、`STATUS_NAMES` は空 `readers.py` L1185）であり、
  `unicodedata.normalize('NFKC','ファルゲ') == 'ファルゲ'`。PR #5301（`bc5bed955`、
  `_chikujou_castle_key` `month.py` L1081-1118）が救えるのは**画面が NFKC 互換の別表記
  （半角カナ等）を描いていた場合だけ**で、`イデ！` と同じ確定的な幅不一致の実測は本件にはない。
  別表記・別原因なら #5301 はカバーしない。
- 相違 2（一覧順の制約）: `CASTLE_NAMES[10]` の順ではファルゲは 5 番目/11 城
  （`hanjuku_chart.py` L433-434）で、tried のコッカーニュ〜ケントルムはカタログ順ではその下。
  実一覧順がカタログ順と一致するなら、ファルゲ行の永続的な未読は下側 4 城の試行済みと矛盾する
  （(i) の壁は下側を通せない）→ その場合は (ii) 行未描画、(iii) 一時除外、または間欠的な未読が本命。
  実一覧順が異なりファルゲが最後部なら #5278 と同型。#5278 でイデ！が最後部だった実績から、
  一覧順はゲーム内部順であり断言できない。
- 相違 3（状態）: #5278 は `skipped`（増築ゼロ）、本件は `done`（ケントルム verified 後に終端）。
  増築済みがある点で「最初から届かない」系ではなく「他城は処理できたがファルゲだけ残った」系。
- fingerprint は `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]`（`month_audit.py` `_fingerprint` L3032-3034）で機構そのものを含まないため、ch9 系と ch10 系は別 fingerprint。同一 issue への自動集約も起きない。

### 3. PR #5320（v1444）のカバー確認 — 本件シグネチャを診断も抑制もしない

- `_note_chikujou_skip_unexplained`（主版 `month.py` L703-735、呼出 L685）は
  `shop.get('chikujou') != 'skipped'` で即 return（L712 相当）。**本件は `done` であり記録すら発火しない。**
- 仮に `skipped` 月でも、docstring どおり「diagnostic only: the audit does not treat it as an
  explanation」→ `chikujou_skip_unexplained` の記録を増やすだけで、監査の起票判定には影響しない。
- すなわち `done` + 未試行候補のシグネチャを説明・抑制する機構は現行 main に存在しない。
  本件のような終端理由（`chikujou_held` の deviation_reason や `chikujou_declined`）は
  issue 本文の直前決定列にしか残らず、築城節の構造化情報（`chikujou_event`）には含まれない
  （`month_audit.py` render L3714-3730 は `reason` = 終端 record の理由文だけで、
  `chikujou_held` の deviation_reason を拾わない）。

### 4. 所持金 9749（必要残し 30）で資金は十分 — 資金以外で選ばれなかった経路

`9749 >= 30 + CHIKUJOU_SPARE(1)` で `_chikujou_affordable` は真（`economy.py` L1480-1490）。
予約・実費 decline・`month_chikujou_skipped` 系の保留は成立しない。資金以外の経路は上記 (i)-(iii):

- (i) ファルゲ行が可視だが語が照合不能 → `castle_list_unread`。
- (ii) ファルゲ行が実一覧に現れない → `castle_scroll_no_progress`。
- (iii) 評価時点で `_chikujou_candidates` から一時除外（garrison 空実測等）→ declined/verified-終端
  で `done` 後、監査スナップショットまでに候補復帰。
- いずれも `menu_to`/`_chikujou_screen_label` 経由で `chosen` が立たなかった（または candidates に
  無かった）点で一致し、実費・予約とは無関係。

## 判定の理由

- **(b) でない**: `affordable` + `staffed` + 未試行の所有城を残した月一終了は設計目標の達成失敗。
  打ち切り自体は fail-closed の設計だが、その原因となった「候補が一覧に届かない」状態は欠陥領域。
  監査の降格条件（`held_for` / `menu_unreadable` / `reserve_skipped` / `refusal_covered` /
  `cost_declined`、`month_audit.py` L1754-1794, L1843-1861）は全て偽で、`done` なら
  `_chikujou_hold` 自体が `skipped|None` 限定で評価されない（L1605-1606）。
- **(c) でない**: 監査は flow と同じ `_chikujou_candidates`/`_chikujou_staffed` で内部整合して
  起票。仮にファルゲが実際には施工不能（レベル5/将軍不在）だったとしても、行が読めたなら
  `chikujou_max_level_seen` か拒否で `tried` に入って候補から消える — **未読のまま候補に残る
  こと自体が観測側の失敗**。誤検知というより「検出されるべき実ミス」。
- **(d) でない**: 「実欠陥か設計どおりか」はコード上確定できる（設計どおりではない）。
  下位機構の一意特定に必要な証拠だけ欠けている — 当該月の決定ログ末尾
  （`chikujou_held` の `deviation_reason` と `observed_metric.visible`、`chikujou_declined` の
  reason、`chikujou` 終端 record の metric）と、ch10 一覧にファルゲがどう描画されていたかの
  フレーム。`gh` 不可のため本検証では未取得（issue 本文の 30 件直前決定に入っていれば
  そこで確定できる）。

## 修正方針案（実装はしない）

機構によって修正先が分かれるため、まず診断を強化して次の発生で一意に切り分けるのが先:

1. **done 月にも終端理由を残す診断**: `_note_chikujou_skip_unexplained`（PR #5320）と同型の
   「`done`/`unverified` + 未試行 staffed 候補」記録、または監査側 `chikujou_event` に
   同月最後の `chikujou_held.deviation_reason` / `chikujou_declined.reason` /
   `chikujou` の `observed_metric`（declined/upgrades/payment_verified）を築城節へ載せる。
   これで「unread 壁」「no_progress（行未描画）」「候補なし declined（一時除外）」を
   issue 本文だけで切り分けられる（VERIFY-5278 §6.4 と同趣旨）。
2. **機構別の本修正**:
   - `castle_list_unread`: ファルゲ行の実描画を特定し、幅系なら #5301（v1438、main マージ済み）
     でカバー済みと確認、別表記/未読グリフなら `STATUS_NAMES` alias 追加または
     `_chikujou_castle_key` の fold 拡張（未知タイル許容など）。
   - `castle_scroll_no_progress`（行未描画）: 実一覧に存在しない城が candidates に残る不整合。
     traversal 後も未登場の候補を `chikujou_held` の metric に明示し、ゲームがその城を
     一覧に出さない実条件（英雄所在城の除外等）を調べて候補計算または監査へ反映する。
   - 一時除外（(iii)）: `done`/`check` の評価と監査スナップショットの間で候補集合が変動する
     ギャップ。`_chikujou_candidates` の garrison 実測値が同月内に反転する経路を潰すか、
     「done 後に候補が復帰した月」を監査で別扱いにして一時状態の誤起票を防ぐ。
3. 次の発生時に確認する手順: 当該 runtime の `hanjuku_decisions.jsonl` でその月の
   `chikujou_*` 末尾を読み、終端 `deviation_reason` と `visible` 行を取得 →
   `hanjuku_frames/` の一覧フレームでファルゲ行の実描画を確認 → 上記 (i)-(iii) を確定。

## 検証で使った根拠（抜粋・`origin/main` = bc5bed955 の行番号）

- `src/docich/hanjuku/flows/month.py`: `_chikujou_castle_key`/`_chikujou_name_labels`/
  `_chikujou_list_rows`/`_chikujou_screen_label`/`_chikujou_text_has_castle` L1081-1118
  （PR #5301 `bc5bed955`）、`_chikujou_wait` L1121-1130、`_chikujou_scroll` L1133-1170
  （`castle_list_unread` L1147-1151、`castle_scroll_no_progress` L1155-1162、
  bound = `len(labels)+4`）、`_month_chikujou` L1173+（refused/tried early-exit 等）、
  `_chikujou_visible_maxed` L1273-1291、`_chikujou_continue_list` L1295-1341、
  `_chikujou_step` 選択ループ L1492-1529（`names` 計算・`menu_to` + label fallback・
  scroll・空なら `chikujou_declined`）、`_note_chikujou_tried` L1533-1545、
  `_finish_month_sub` chikujou 終端 L1767-1796（tried 併合・verified→`done`/`check`・
  declined→`done`/`skipped`・else `unverified`）、`month_done` L684-695 と sub ホールド
  L606/L664、`_note_chikujou_skip_unexplained` L685（呼出）/L703-735（skipped 限定）
- `src/docich/hanjuku/os_layer/commands.py` L26-38（`menu_to` exact match）
- `src/docich/hanjuku/os_layer/readers.py` L45-47（`_options`、`_canonical_general` 経由）、
  L1185（`STATUS_NAMES` 空）
- `src/docich/hanjuku/strategy/economy.py` L1480-1490（`_chikujou_affordable`）、
  L1527-1571（`_chikujou_reserve`）、L1574-1585（`_chikujou_candidates`: captured+home、
  `garrison[c]==[]` 除外、home→staffed→unmeasured 順）
- `src/docich/hanjuku/os_layer/world.py` L757-761（`_owned`）
- `src/docich/hanjuku/month_audit.py`: `_chikujou_staffed` L1576-1586、
  `_chikujou_hold` L1595-1621（skipped|None 限定）、`_chikujou_snapshot` L1685-1795、
  `_chikujou_event` L1798-1822、`_build_chikujou_incident` L1825-1929
  （confidence 直接決定 L1851-1861、`auto_file=True` L1928）、`_track_chikujou` L1932-1963、
  `_confidence`（MIN_MONTH_SECONDS/短月は zero_sortie 系のみ）L2967-2984、render 築城節
  L3714-3730、`_fingerprint`（chapter+kind のみ）
- `src/docich/hanjuku_chart.py` L433-434（`CASTLE_NAMES[10]`、ファルゲは 5 番目/11 城）
- `src/docich/hanjuku/strategy/orders.py` L1590・`flows/battle.py` L1564
  （`garrison[c] = []` の経路）
- 前例: VERIFY-5278.md（`イデ!`/`イデ！` の実測差・`castle_list_unread`/`castle_scroll_no_progress`
  の同型終端）、VERIFY-5282.md（検証ドキュメント形式）
- PR #5301 `bc5bed955`（fold、main マージ済み）/ PR #5320 `dd6ed0fbe`（skipped 限定の診断のみ）
- テストの空白: `tests/test_hanjuku_chikujou_scroll.py`・`tests/test_hanjuku_multi_chikujou.py` は
  ch1/ch3/ch5 のカタカナ名のみで、ch10 一覧・行未描画・一時除外のケースは未固定

## 手順上の注記

- `python3`/`pytest` の実行は実行環境が拒否したため未実施（関連テスト:
  `test_hanjuku_chikujou_scroll.py`・`test_hanjuku_multi_chikujou.py`・
  `test_hanjuku_chikujou_skip_note.py`・`test_hanjuku_month_audit.py`）。
- `gh` 不可のため issue #5313 本文・PR 状態は依頼記載の要点のみ使用。推測で補っていない。
- `git checkout -b`/`git branch`/`git add`/`git commit` 等の git 書き込みが実行環境で
  拒否されたため、検証は `origin/main` の blob 参照（`git show origin/main:...`）で実施し、
  本ファイルは worktree ルートに未コミットで置いてある。依頼の
  `verify/5313-chikujou-available-at-end`（origin/main から）でのコミットは
  この環境では実行できなかった — 別環境で `git checkout -b verify/5313-chikujou-available-at-end
  origin/main && git add VERIFY-5313.md && git commit` が必要（行番号は全て origin/main 基準）。
- read-only 検証。VERIFY-5313.md の作成以外のファイル編集・本番 VM 操作・push・PR なし。
