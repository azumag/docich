# VERIFY-5026 — [半熟英雄] chikujou_cancelled_with_resources (ch4 8-8)

- Issue: azumag/docich#5026 / 判定 `chikujou_cancelled_with_resources` (ch4 8-8)
- Base: origin/main 3a01ae71 (v1366、worktree、未コミット)
- 検証方法: コード読み。この環境では pytest / gh が使えないため、Issue 本文と
  インシデント行の実測値は未確認。ランタイムデータ (hanjuku_audit.jsonl /
  hanjuku_decisions.jsonl) も worktree 内に無い。

## 監査の発火条件

`month_audit._track_chikujou` → `_chikujou_event` は、現在観測の `records`
(= `state.pop('_records')`、その観測だけのバッチ) の中で
`chikujou_declined` (理由に「候補」を含む終了系) または `chikujou_leftover`
を拾い、`_chikujou_snapshot` が `buildable` (affordable + 将軍在城の未試行候補)
と判定したとき `cancelled` → `chikujou_cancelled_with_resources` を発行する。

`buildable` の「未試行」の再構成ソースは次の2つだけ:

1. `shop['chikujou_tried']` — `_finish_month_sub` が sub 終了時にだけ
   `rows_tried`+`chosen` をマージする台帳。
2. **現在の観測バッチ内**の `chikujou_max_level_seen` /
   `chikujou_castle_unavailable` 行。

## #4989 / #4990 がカバーしないことの確認

- **#4989 (v1363)**: 終了した `deferred_egg` が `_recruit_precedes_cards` 経由で
  ちくじょう/カードを永続ホールドする欠陥の修正。本件の発火経路
  (`chikujou_declined` / `chikujou_leftover`) とは別機構。
- **#4990 (v1365)**: 生存中の chikujou `month_sub` がいる間は
  `_is_chikujou_prompt` 早期Bを抑止する修正。本件の発火に早期Bは必須ではない
  (下記)。早期B経路は残存するが、発火の主因ではない。

## 原因の切り分け (コード読み)

### 欠陥1: 生存中 sub の試行済み一覧が監査に見えない (監査側の盲点)

`_chikujou_step` の「当月未試行で増築候補となる所有城がなくなった」終了判定は
`sub['rows_tried']` を使う。`rows_tried` には sub 開始時の台帳 + そのセッション中に
観測した**各城の拒否** (`chikujou_castle_unavailable`) と**レベル5読み**
(`chikujou_max_level_seen`) が入る。

一方で監査が見る `shop['chikujou_tried']` は sub 終了時にしか更新されず、
`records` バッチは**現在の観測のみ**。城を1つ選ぶ→拒否される、は各1観測を消費するため、
**拒否が2観測以上にまたがる正当な終了**は、監査から見ると直前観測の拒否しか
除外されず「まだ候補があるのにキャンセル」と誤判定される。

ch4 の非ボス城は最大7 (`アルマムーン` `マラコビア` `フヌイヌム` `ネクスドリア`
`ビクトリア` `ミヒラギアン` `アルカ`)。複数城が満級・将軍不在・増築済みで
順に拒否される 8-8 はこの複数観測シナリオと整合する。

### 欠陥2: sub が未完了で捨てられると台帳ごと消える

`month_sub_lost` (map/battle 等の `MONTH_SUB_EXIT_KINDS`、8月は夏季イベントや
防衛戦で起こりうる) と `month_sub_abandoned` (`MONTH_SUB_ABANDON_BACKS`=20) は
`month_sub` を `rows_tried`/`chosen` をマージせずに破棄していた。直後の
`chikujou_leftover` / 次回終了のスナップショットはセッション中に拒否済みの城を
未試行候補として数える → 同じく誤 `cancelled`。

### 欠陥3: 拒否行の無い leftover で `chikujou_refused` が立つ

`month.chikujou_leftover` と orchestrator 早期Bは、どんな残存ちくじょう画面でも
`mem['chikujou_refused']` をセットし、`_month_chikujou` は同一候補集合を
最大2か月スキップする (v1292/#4844)。しかし「どのしろをぞうちくなさいますか?」
だけの**拒否行を含まない城一覧はまだ選択可能**であり、ドロップした sub の
名残を閉じただけで翌2か月を抑止するのは誤帰属。拒否の実測行
(`これいじょう…` / `しょうぐんがおりませなんだ`) があるときだけ抑止すべき。

## 結論

欠陥あり (実装する)。主因は監査が live sub の `rows_tried` と、未完了で
破棄された sub の試行台帳を見られないこと。拒否が複数観測にまたがる正当な
終了を「資源があるのにキャンセル」と誤発行する。補助欠陥として、
拒否行の無い leftover で `chikujou_refused` が立ち翌2か月を抑止する誤帰属も修正。

真の取りこぼし (untracked leftover 自体) の発生条件は Issue 本文の
`chikujou_event.decision` が `chikujou_declined` か `chikujou_leftover` か、
および `tried`/`staffed_candidates` の実測値で確定できる。本修正は両方の
発火経路で、監査の除外集合をフロー自身の除外集合と一致させる。

## 修正 (v1370 `hanjuku-chart-v1370-chikujou-tried-ledger`)

1. `month_audit._chikujou_snapshot`: 同月 (`sub['key'] == shop['key']`) の
   live chikujou `month_sub` の `rows_tried` と `chosen` を `tried` に合流。
   `_chikujou_step` と同じ除外集合を使う。
2. `flows/month._note_chikujou_tried` (新規): `month_sub_lost` と
   `month_sub_abandoned` で、同月 sub の `rows_tried`+`chosen` を
   `shop['chikujou_tried']` へマージしてから破棄 (`_finish_month_sub` と同規則)。
   異なる月の stale sub は書き込まない。
3. `chikujou_refused` は `これいじょう` / `しょうぐんがおりませなんだ` を
   実測した場合のみセット (`month.chikujou_leftover` と orchestrator 早期Bの両方)。
   裸の城一覧を B で閉じても翌月の抑止はしない (v1292 の意図=観測した拒否の
   再オープン抑止を維持)。

## 残課題

- Issue 本文の `chikujou_event.decision`/`tried`/`staffed_candidates` を
  実機の監査行と照合し、本件が `chikujou_declined` 経路か `chikujou_leftover`
  経路かを確定すること。
- `phase_a_escape_step` (同一画面10回でB) が生存 sub を巻き込む経路は
  `sub['presses']` 上限 (CHIKUJOU_SUB_LIMIT=64) と `CHIKUJOU_WAIT_LIMIT`=6 が
  先に効くため、本件の主因とは判断しなかった。
