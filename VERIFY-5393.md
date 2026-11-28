# VERIFY-5393 — [半熟英雄] 卵を残して敗北: ch12 キッシュ

## 対象

- Issue: [#5393](https://github.com/azumag/docich/issues/5393)
- fingerprint: `17db88f7ee083127`
- occurrence: `fc000a630eb1927b23a8`
- run: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1460-password-entered-field`
- chapter/month: ch12 / 18-8
- battle: キッシュ vs マルガリータ、ワフェルダ、attack、loss、ally HP 0 / enemy HP 32
- verification base: main `7a3c45a9667a0602c45ee190fc1ec8f9d019bdff`（v1459）

## 結論: (c) 「卵を残した」という高信頼度の監査主張は stale ledger による誤検知

この occurrence の `battle_result.observed_metric.egg_uses=4` は、敗北時点の実残数を示すレシートではない。現行実装は将軍ごとの最後の観測値をそのまま metric に出しており、戦闘中の召喚・卵選択後に再観測されていない場合でも値が残る。

また、提示された決定列には `battle_survival_unavailable`（screen_kind=`battle_menu`）があり、その後 `battle_menu_rescue_exhausted` へ進んでいる。これは、解析された現在の救済メニューで未試行の選択肢を選べなかったことと整合する。Issue本文にはその決定の `observed_metric.labels` が投影されていないため、卵行が灰色だったのか、OCRで読めなかったのかは区別できない。いずれにせよ、`egg_uses=4` だけでは「使用可能な卵行を見送った」とは証明できない。

### 1. egg_uses は実残数ではなく最終観測値

- `src/docich/hanjuku/flows/battle.py` の `battle_result` は `egg_uses` に `mem['egg_uses'][ally]` の値をそのまま格納する。戦闘中の消費を差し引いた値でも、再読済みであることを示す値でもない。
- `src/docich/hanjuku/strategy/economy.py` の `_egg_recheck` は卵選択時に `dirty_status(... fields=('egg',))` を呼び、将軍を `mem['egg_recheck']` に登録する。これは古い回数を再確認待ちにするラッチであり、選択だけから消費数を推定しない。
- `src/docich/hanjuku/judgment/battle.py` の `_own_egg_positive` は `egg_recheck` 中の将軍について False を返す。したがって古い正数は、戦闘中の選択後にそのまま使えるという証拠にはならない。
- 同ファイルの `_own_egg_empty` も、観測済みの整数0を除外するための判定であり、正数を最新の使用可能数として保証するものではない。

### 2. 救済メニューの記録は「使える卵行を見送った」ことを示さない

`src/docich/hanjuku/flows/battle_menus.py` の `_survival_menu` は、現在の画面から読んだ `labels` に `たまごをつかう` があり、まだ試しておらず、`_own_egg_empty` でもない場合に卵を選ぶ。該当する選択肢がなければ `battle_survival_unavailable` を記録してBで戻る。

本件の公開決定抜粋では、キッシュの敗北step `A:94d33d73:M9e607f` に `battle_survival_unavailable`（battle_menu）と `battle_menu_rescue_exhausted` がある一方、同stepの `battle_survival_select` は示されていない。月全体の `battle_survival_select=7` は別戦闘を含む集計値なので、このキッシュの選択証拠としては使えない。

ただし、`battle_survival_unavailable.observed_metric.labels` は Issue本文に含まれない。したがって卵行が灰色で除外されたのか、白い行のOCR読み取りに失敗したのかは、このIssue本文だけでは確定しない。後者が起きた可能性は別の画面認識調査として残すが、使用可能な行を認識したうえで捨てた証拠はない。

既存テスト `tests/test_hanjuku_survival.py` の `test_no_card_row_uses_readable_egg_despite_learned_pass` と `test_greyed_egg_and_unsafe_cards_are_not_selected` は、読める卵行は選択し、灰色の卵行は選択しない境界を固定している（本検証では再実行していない）。

### 3. monster_menu_choice はキッシュの卵使用証拠に転用できない

`monster_menu_step` は「味方が召喚した召喚獣自身のターン」の技メニューを扱う。しかし、Issue本文に列挙された `monster_menu_choice` の3行は chart_step が `A:94d33d73:M9b416a` であり、キッシュの `battle_result` step `A:94d33d73:M9e607f` とは異なる。

そのため、これらの選択をキッシュの当該戦闘における卵使用の証拠として帰属させない。月全体の `monster_menu_choice=26` も同様に当該戦闘へ帰属できない。

### 4. 切り札と退却

- `cards_selected` / `cards_used` / `cards_remaining` はすべて空で、今回の監査リソースは卵のみ。
- `battle_hero_retreat_open` は退却の可否を確認する記録であり、退却選択の証拠ではない。現行 `_hero_retreat_menu` はオーナー方針により退却を一律禁止し、`battle_retreat_banned` を記録して白兵へ戻す。したがって `retreat_selected=false` はこの実装と整合する。

### 5. 再発 occurrence の留保

Issueコメントの occurrence `a068e8dee9efaa0bc270` は、資源 `['egg']` と将軍エストラゴン等の要約のみで、同じ救済メニューの `labels`、`egg_recheck`、対応するstepの決定列がない。この再発をキッシュの occurrence と同じ根因だと断定しない。

## 判定と次に必要な証拠

主 occurrence は、卵残数の根拠が stale な観測台帳値であるため、`battle_loss_with_resources` の「使用可能な卵を残して敗北」という高信頼度主張は誤検知と判定する。一方、卵行の灰色表示とOCR欠落のどちらだったかは未確定である。

同種の監査を改善するなら、将来の `battle_result` / incident 投影に、少なくとも次を出す必要がある。

- 敗北時点で将軍が `egg_recheck` 中だったか
- 当該戦闘stepの `battle_survival_unavailable.observed_metric.labels`
- 当該stepの `battle_survival_select` / `egg_attempted` の有無

このPRは read-only の検証記録のみで、ゲーム操作・戦闘判断・卵消費ロジックは変更しない。Issue #5393 は、再発 occurrence の証拠と必要ならruntime受入を確認するまで Open のままとする。

## 検証範囲

- main `7a3c45a9667a0602c45ee190fc1ec8f9d019bdff` の該当コードと既存テストを静的に照合。
- pytest / runtime / VM の実行は行っていない。
- VM側の `hanjuku_decisions.jsonl` とフレーム画像は未取得。Issue本文に含まれる決定抜粋・metricのみを証拠として使用。
