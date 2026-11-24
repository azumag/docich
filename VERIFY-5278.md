# VERIFY-5278 — [半熟英雄] 築城可能なまま月一終了: ch9 15-5（イデ！未試行）

- 対象: docich issue #5278 / 監査 `chikujou_available_at_end` (confidence: high)
- ラン: g1190-ch1v9 / hanjuku-chart-v1431-boss-companion-timeout、月 ch9 15-5（観測 54 回 / 10.4 秒 / max gap 0.2 秒）
- issue 築城節: 結果 `ended` / 決定 `month_done` / 所持金 9975 / 必要残し 30 / 状態 `skipped` /
  未試行候補 `['イデ！']` / 試行済み `['イソ','オオキク','オシロガ','カラ','サヌト','シマウ','タオ','ドンドン']` / 増築済み `[]`
- 直前の決定: `chikujou_scroll` と `chikujou_max_level_seen` の繰り返し（step A:3814542a:M80dfbb）
- 再発: ch9 15-6（gold 9975、候補 `['イデ！']`）、ch9 13-8（gold 4698、候補 `['イデ！']`）
- 検証ブランチ: `verify/5278-chikujou`（read-only。実装・push・PR・issue コメント・本番 SSH なし）
- 正本 handoff: `DOCICH_HANDOFF_PATH` 未設定のため未読（AGENTS.md §1 に従い未読と明記し通常検証）。
  `gh` 実行不可・GitHub リポジトリは private のため、issue 本文・PR #5274 の状態は依頼記載の
  要約とローカルブランチ `origin/fix/5274-chikujou-event-text-wait-v1431` の実差分で検証した。
- 同シグネチャ既検証: VERIFY-5163（ch9 73-8、(a)・PR #5167 でカバー）、VERIFY-5186（ch2 21-9、(a)・別機構）

## 結論: (a) 実欠陥 — 築城一覧の城名照合が幅非依存でなく、画面表記 `イデ!` と候補名 `イデ！` が永久に一致しない

根本原因は **半角/全角の城名ミスマッチ**であり、#5163（盲操作の誤確定）・#5186（一発 skip ゲート）とは
**別機構**である。画面（ゲームフォント）は `イデ!`（ASCII `!`）を描くのに対し、チャート
`CASTLE_NAMES[9]` と `_chikujou_candidates` が返す候補名は `イデ！`（全角 `！`）。築城一覧の選択・
行判定・レベル読みの 3 箇所すべてが**生文字列の完全一致**で比較するため、`イデ！` は

1. `menu_to` で絶対に選択できず（`os_layer/commands.py:30` `w == label` の exact match）、
2. 一覧に現れた瞬間に「既知城 4 行」不変条件を壊して `castle_list_unread` → 有限待ち → 中止し
   （`month.py:1063-1070` → `:1042-1051`）、
3. 仮にレベル5でも `_chikujou_visible_maxed` の known 集合から外れ maxed 検出も効かない
   （`month.py:1194-1207`）。

これはコード上**既に2箇所で同じ障害として実測・修正済み**の既知の差である。地図系だけが折りたたみ
比較を持ち、築城経路には移植されていない:

- `flows/map.py:1360-1368` `_castle_key` —「The game's しろ list draws イデ！ with a half-width '!'
  while the chart ... hold the full-width '！'」（g1190 2026-11-15 72-2 castle_missing を修正）
- `flows/map_registration.py:55-67` `_fold` — 実測「native status panel reads
  `しゅつげきイデ!じょう...`（ASCII `!`）while the source catalog spells `イデ！`」（v999/#4433）
- `flows/deploy.py:1624-1627, 1661-1662` — 出撃元確認も `_castle_key` 折りたたみ比較
- `os_layer/readers.py:45-47` `_options` は `_canonical_general`（将軍名の濁点 fold、`readers.py:1312`）
  を通すのみで、城名の幅正規化はない。フォント読みは `os_layer/glyphs.py:8` で `!`（ASCII）を返す

## 検証観点への回答

### 1. イデ！が未試行のまま month_done になった原因

`month.py:1420-1431` の候補ループを追う:

```python
names = [c for c in _chikujou_candidates(mem) if c not in tried]   # ['イデ！']
for name in names:
    move = menu_to(screen, name)      # 'イデ！' != 画面の 'イデ!' → 常に None
    if move is None: continue
if names:
    return _chikujou_scroll(...)      # 毎フレーム down（chikujou_scroll 記録）
```

- `menu_to`（`commands.py:26-38`）は `_options` の語と `label` の完全一致のみを見る。
  `_chikujou_candidates`（`economy.py:1574-1585`）は `STATUS_NAMES` 経由だが同マップは空
  （`readers.py:1185`）で `'イデ！'` がそのまま返る。画面行は `イデ!` であり、**スクロールで
  視野に入っても選択不能** — 「到達したが省いた」のではなく「行を見ても名前が一致しない」。
- ループの終端: 一覧が下へ進んで `イデ!` の行が 4 行窓に入ると、`_chikujou_scroll` の
  `rows = [w in known]`（`month.py:1064`、known は `CASTLE_NAMES` 生名＝`イデ！` 綴り）が
  その行を落とし `len(rows) != 4` → `_chikujou_wait('castle_list_unread')` →
  `CHIKUJOU_WAIT_LIMIT=6` で `declined+aborted` → B（`month.py:1042-1051`）。
  （代替終端として `stalled>=4` / `presses>=len(known)+4=17` の `castle_scroll_no_progress` でも
  同じ `declined+aborted` に到る — どちらでも `イデ！` は選択されない。）
- 月メニュー復帰で `_finish_month_sub` が `declined かつ gold==gold_before`（未払い）を評価し、
  `upgrades` 空のため `shop['chikujou']='skipped'`（`month.py:1691-1700`）— issue の
  「状態 `skipped`」と一致。`rows_tried`（8城＝拒否/レベル5）は `_note_chikujou_tried`
  （`month.py:1439-1453`、#5026 の台帳）で `chikujou_tried` へ併合され、監査の
  `candidates` から `イデ！` だけが残る（`month_audit.py:1705-1708`）。
- `chikujou_scroll` と `chikujou_max_level_seen` が繰り返されるのは仕様どおりの観測である:
  下へ送るたび新たに見えたレベル5行を `tried` へ足し（`month.py:1411-1419`）、残候補は常に
  `['イデ！']` のまま down を返す。レベル5誤排除ではない — `maxed` は「画面に見えた行のレベル値
  が '5'」のときだけ追加し（`month.py:1200-1207`）、`イデ！` はそもそも known 不一致で
  maxed にも計上されない。

### 2. 月が 54 観測 / 10.4 秒である点

「走査の進行中に month_done が発火した」説は構造的に否定できる。`month_done` は
`month_step` 内で `mem['month_sub']` が残る限り `_finish_month_sub` の完了を待ち
（`month.py:663-667`、偽なら `[]` で保留）、sub 解決後にのみ記録される（`month.py:682-690`）。
issue の `skipped` は `_finish_month_sub`（または abandon `month.py:1994-2010`）が sub を
**解決済み**であることの証跡なので、記録時点で走査は終わっている。

10.4 秒は欠陥の**結果**であり原因ではない。フレーム勘定: extras ゲート通過 → ちくじょう開放
→ スクロール約 6〜10 観測（所持 9〜10 城の 4 行窓で最下行の `イデ！` まで数回の down）→
`castle_list_unread` 6 観測 → B・finish → `month_done` → `も〜おしまい!` ナビ。sub 内で
完結する 20〜30 観測の異常系列が月全体 54 観測に自然に収まる。

監査の high 起票は**正しい**。`_build_chikujou_incident` の降格条件
（`held_for` / `menu_unreadable` / `reserve_skipped` / `refusal_covered` / `cost_declined`、
`month_audit.py:1719-1757, 1814-1824`）はいずれも設計上の保留であり、本件の
「affordable（9975 ≥ 30+`CHIKUJOU_SPARE`）かつ staffed な未試行候補を名不一致で逃した」は
実ミスとして high で起票される設計どおりの扱い。`staffed_candidates` 非空が起票の要件であり
（`month_audit.py:1756`）、`イデ！` が staffed と判定されていること自体も監査データの内部整合を
裏付ける（ch9 では `イデ！` がボス集結城で将軍が常駐する。`flows/boss_pursuit.py:253`、
`strategy/corridor.py:1401-1403`）。

### 3. PR #5274（fix(hanjuku): keep event text from aborting chikujou）は本件をカバーするか

ローカルブランチ `origin/fix/5274-chikujou-event-text-v1431`（HEAD 67fdb2e2e、未マージ・
CONFLICTING）の実差分を精査した。#5274 が直すのは:

- `_chikujou_menu_text_owned`（`orchestrator.py` 追加、同ブランチ 0e297ac85）で築城 sub が
  所有する text 画面（一覧・確認・結果・資金不足）を**汎用イベント文送りの A から保護**し、
- 無関係なイベント会話に A を送った場合は `sub['build_wait']` をリセットする
  （会話進行を築城の停滞として数えない）。
- 同梱ドキュメント `docs/operations/hanjuku-chikujou-event-text-advance-20261124.md` は
  「章9 13-1 で増築済みの城を照合した後、未試行のイデ！が一覧外に残っていた。築城サブフローが
  一覧の再観測を待っている間に、全体のイベント文送りが同じ text 画面へ A を送り、直後に築城側の
  有限待ちが尽きて月を終了していた」と別機構（イベント A による build_wait 消費）を名指しし、
  「実機でイデ！が増築されることは別途受入確認が必要」と留保している。

**判定: #5274 は本件の機構をカバーしない。** イベント文の割り込みを防いでも、`menu_to` の
`イデ！`/`イデ!` 不一致は無傷で残る。待ちが保護されると逆に `castle_list_unread` の 6 待ちか
スクロール上限 `len(known)+4` のいずれかで**より遅く**失敗するだけで、`イデ！` の選択不能は
変わらず、month_done→skipped→high 起票の結末は同じである。本件の記録列（一覧が生きたまま
`chikujou_scroll`/`chikujou_max_level_seen` が交互に立つ）はイベント A による early abort より、
名不一致による終端の方が素直に説明できる — ただし仮に #5274 型の割り込みが終端でも、
`イデ！` はどのみち選択不能なので (a) の判定は不変。

### 4. #5163 / #5049 との異同

- **#5163（VERIFY-5163, commit 72859267e）**: 月メニュー盲操作バッチが移動と確定 `a` を同一
  バッチで送り `しょうぐんかいこ` を誤確定 → `discharge_menu` で sub が落ちた機構。
  **本件とは別機構**（本件は城一覧内の名照合）。PR #5167 は本件をカバーしない。
- **#5049**: 本文は本環境から取得不能。ただし fingerprint は
  `sha256(f"{chapter}|{kind}|{kind}")` のみ（`month_audit.py:2993-2995`）で機構を含まないため、
  同 fingerprint 共有は「同じ章9・同じシグネチャ」の意味しか持たない。#5049/#5163 が CLOSED で
  本件が再発している事実と、`イデ！` 限定の再発パターンは、名照合欠陥が従来の修正をすべて
  すり抜けて残っていることと整合する。
- VERIFY-5163 以来の一連の修正（盲操作分離・hold/menu_unreadable/reserve 降格・台帳一致）で
  `skipped`+high の組合せは「設計内保留」の可能性をほぼ排除済みであり、本件の残る説明は
  名不一致で一意に近い。

### 5. イデ！が 3 再発すべてで未試行に残る点

**城固有の要因が存在し、それが決定的である。** `CASTLE_NAMES` 全 12 章を走査すると
`！`（あるいは句読点・全角記号）を含む城名は `イデ！` のみ（`hanjuku_chart.py:411-441`）。
`イデ！` は第9話のボス攻略で最前線の集結城（`corridor.py:1401`、`boss_pursuit.py:98`）として
毎月 owned+staffed を満たし `_chikujou_candidates` に常時載る一方、一覧表記 `イデ!` は
候補名 `イデ！` と構造的に永久不一致で、さらに一覧順では最後に来るため「全他城を処理した後に
残る唯一の城」として可視化される。イベント abort のような確率的事象なら残る城は毎回ばらけるはずで、
3 回連続 `['イデ！']` 単独は確定的な名照合欠陥を強く支持する。

## 修正方針案（実装はしない）

築城一覧経路の城名照合に、地図系と同じ幅非依存の fold を適用する:

1. `month.py` の chikujou 系で使う `known` 集合・`menu_to` 呼出・`_chikujou_visible_maxed` の
   行名比較・`_chikujou_continue_list` の行抽出を `map._castle_key`（または同等の
   `NFKC + '！'→'!'` 正規化）で比較する。候補名・台帳名はカタログ綴り `イデ！` のまま保持し、
   照合だけ fold する（`map_registration._fold` と同じ方針）。
2. `menu_to` は exact 比較のみなので、一覧用に「画面行→候補名」の fold 済みマップを作り、
   行の描画名ではなく候補名を `chosen` に入れる経路にする（`menu_to` の `label` を画面表記に
   合わせてしまうと `sub['chosen']` や `rows_tried` が `イデ!` 綴りで汚染され、監査の
   tried/candidates 比較が再びずれる）。
3. 回帰テスト: `test_hanjuku_chikujou_scroll.py` 系に ch9 フィクスチャ（行 `イデ!` + 候補
   `イデ！`）を追加し、スクロール到達後に `[pad('a')]` で `chosen == 'イデ！'` となること、
   および `イデ!` 行が見えても `castle_list_unread` で abort しないことを固定する。
4. （監査側・任意）`castle_list_unread`/`castle_scroll_no_progress` を含む `chikujou_held`
   記録を issue 本文へ載せると、名不一致終端とイベント abort 終端の切り分けが issue だけで
   可能になる。起票 confidence は実ミスなので high のままが正しい。

## 判定: (a) 実欠陥。#5274 ではカバーされず、城名 fold の修正が必要

- (b) でない: `skipped`+`affordable`+staffed 未試行の組合せを説明する設計内経路は、監査の
  downgrade 対象がすべて falsy である以上残らない。「候補が全部試行済み」以外で `skipped` に
  なる設計内経路（拒否枠・hold・実費見送り）は全て記録を残し、本件は記録列が scroll/
  max_level_seen（＝sub が一覧を操作中に終了）である点とも矛盾しない。
- (c) でない: `イデ！` は実際に建造可能な状態（staffed・未レベル5確認・予算内）で未施工。
  監査の buildable/high は正しい。
- (d) でない: 名不一致はコード上の完全一致比較と、別経路で実測済みの画面表記 `イデ!` の
  2 点から論理確定できる。issue の決定列そのものは未取得だが、提示された事実（繰り返し記録・
  `skipped`・`['イデ！']` 単独残留）はこの機構と一意に整合する。

## 検証で使った根拠（抜粋）

- `src/docich/hanjuku/flows/month.py`: `_chikujou_scroll` L1054-1089（known 生名・4行不変条件）、
  `_month_chikujou` L1092-1188、`_chikujou_visible_maxed` L1191-1207、`_chikujou_step`
  L1404-1436（`menu_to` → scroll → `names` 空で declined）、`_chikujou_wait` L1042-1051、
  `_finish_month_sub` の chikujou 終端 L1673-1714（`declined`+`gold==before`→`'skipped'`）、
  `month_done` L663-690、`_note_chikujou_tried` L1439-1453
- `src/docich/hanjuku/os_layer/commands.py` L26-38（`menu_to` exact match）、
  `os_layer/readers.py` L45-47・L1185・L1299-1313（`_options`/`STATUS_NAMES` 空/`_canonical_general`）、
  `os_layer/glyphs.py` L8（`!` の字形→ASCII `!`）
- `src/docich/hanjuku/strategy/economy.py` L1574-1585（`_chikujou_candidates` 生名）
- `src/docich/hanjuku/flows/map.py` L1360-1368（`_castle_key`、画面表記 `イデ!` の実測コメント）、
  `flows/map_registration.py` L55-67（`_fold`、native `しゅつげきイデ!` の実測コメント）、
  `flows/deploy.py` L1624-1627, L1660-1662
- `src/docich/hanjuku_chart.py` L411-441（`CASTLE_NAMES`、全角 `！` を含むのは `イデ！` のみ）
- `src/docich/hanjuku/month_audit.py` L1648-1758（snapshot/candidates/buildable）、L1761-1785、
  L1788-1892（confidence 降格条件）、L2993-2995（fingerprint は chapter+kind のみ）
- `src/docich/hanjuku/orchestrator.py` L2041-2042, L2177（month_sub の所有条件）
- PR #5274: `origin/fix/5274-chikujou-event-text-wait-v1431`（0e297ac85, 67fdb2e2e）
  `_chikujou_menu_text_owned`・`build_wait` リセット・付随ドキュメントの機構記述
- 前例: VERIFY-5163（commit 72859267e）、VERIFY-5186（commit ba37f9a83）
- テストの空白: `tests/test_hanjuku_chikujou_scroll.py`・`test_hanjuku_chikujou_visible_levels.py`
  の NAMES は全て ch1/ch5 のカタカナ名で、句読点を含む城名の一覧ケースは未固定
