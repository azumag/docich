# VERIFY-5538 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: ノリウツール (ch8 いちじょうだに)

- 対象: docich#5538（`bug` / `auto-generated` の監査自動起票）/ 監査 `card_miss`（confidence `high`）
- fingerprint `825381c9ddde154b` / occurrence `f50e042ebe5144698ee5`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1497-boss-audit-live-sortie-defer`
- ch8 / 月 7-10 / 未使用の携行札: **ノリウツール**（携行札候補: ノリウツール, ノリウツール）
- 戦闘: デーメーテール vs ヴィーナス / 城: いちじょうだに / side `attack` / 時刻 04:22:52Z
- 画面 `egg_battle_menu` / scrolls `不明` / cursorY `176` / 表示文言 `こうげきもうこうげき`
- 卵状態: `empty_egg=false` / `summon_egg_unavailable=false` / `enemy_summon_observed=true`
- 理由: 敵召喚獣戦で携行切り札を開けず、こうげきへ進んだ
- 検証ベース: `origin/main` = `757f24fcd`（`BOT_VERSION` `hanjuku-chart-v1509-card-at-hp1-audit`）
- 検証範囲: read-only。本ファイルの追加以外のコード変更なし。本番 SSH・VM 操作・ゲーム入力なし。ラン成果物 `hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/` は VM 側のため直接参照せず、issue 本文・git オブジェクト・現行/観測版コードを照合した。
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）。
- 環境制約: 本環境では `python3` 等のハッシュ計算が実行拒否される（VERIFY-5444/5476/5509 と同制約）。fingerprint / occurrence の sha256 再計算は未実施で、式と入力値の照合のみ記録する。

## 結論: **(a) 実欠陥 — #5259 家族の新亜型。敵召喚で凍結した「選択済み・消費未確定」の `card_flow` が揭示スクロールと再オープンの両ゲートを抑止し、残りの携行札（ノリウツール×2）が一度も揭示されないまま `こうげき` が確定された。v1497 と現行 main の双方に生存**

これまでの系列（#5259…#5476）は**卵行 OCR 欠落型**（揭示ゲートの前提「卵行が読める or 卵0実測」が成り立たず scroll しない）だったが、それは PR #5472 の `egg_battle_menu` フォールバック分岐で修正済みであり、観測版 v1497 にも入っている。本件はそのフォールバックが**成立していたにもかかわらず** scroll されなかった型で、`scrolls: 不明`（カウンタ未作成）と `cards_left` 非空・`きりふだ` 不含有から、`battle['card_flow']` が真であったことが**論理強制**される（§3）。`interrupted_flow` 救済（`battle_menus.py:3151-3166`）が選別して残るのは announce 系の flow だけなので、**本戦闘中に別札 X（≠ノリウツール）が選択入力済み・消費未確定のまま召喚画面へ凍結**し、その真値が残り札の揭示を抑止した。emit の前提（確定携行・未使用・`こうげき` 実確定）は静的に確認でき、実事象の起票であり誤検知ではない。

## 1. fingerprint / occurrence の式照合（再計算は環境拒否のため未実施）

現行 `month_audit.py`（v1497 と card_miss 経路の差分なし、§6）で式を確認した。

- `_fingerprint`（`month_audit.py:3312-3314`）: `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]`。`_track_card_miss`（同 3516-3517）の呼出引数は `[card, str(row.get('castle') or '')]`。#5472 以降の emit は `castle` を渡すため第3要素は城名。期待 raw = **`8|card_miss|ノリウツール+いちじょうだに`** → 期待値 `825381c9ddde154b`。
- occurrence（`month_audit.py:3465-3468`）: `sha256(f"{runtime_id}|{chapter}|{int(float(at))}|{card}")[:20]`（月次 incident の `_incident_id` 式とは別の、card_miss 専用 per-occurrence 式）。時刻 04:22:52Z（2026-11-25）は epoch `1795580572`（2026-01-01 = 1767225600 + 経過 328 日 + 15772 秒）。期待 raw = **`g1190-ch1v9|8|1795580572|ノリウツール`** → 期待値 `f50e042ebe5144698ee5`。
- 再計算は本環境の実行拒否（`python3` 系コマンドがユーザー拒否）により未実施。形式（16/20 hex）・構成要素（章・kind・札・城・ラン・時刻）はいずれも整合し、同族 issue の実績式と同型。手段のある環境での照合を残件とする。

## 2. 発火機構（何が「切り札を開くべきだった」と判定したか）

- emit サイトは `egg_battle_step`（`src/docich/hanjuku/flows/battle_menus.py:3342-3375`）の1箇所のみ。`orchestrator` が `screen.kind == 'egg_battle_menu'` で振り分ける汎用パス。
- 起票条件（同 3342-3353）: `move == 'here' and label == 'こうげき'`（カーソルが実測 `こうげき` 行にある。`_battle_menu_to` は `screen.menu_cursor` が行 Y と一致する場合だけ 'here' を返す — `os_layer/commands.py:395-404`。issue の cursorY 176 と整合）かつ `missed = _carried_card_left(mem, battle)` が非空、かつ `cards_left` 非空、かつ `not battle.get('cards_used')`、かつ `not battle.get('boss_card_missed_noted')`（1戦闘1件）。
- `_carried_card_left`（同 1268-）: `planned_cards` 優先、空なら `_confirmed_carried_kit`（`judgment/battle.py:1007`、出撃確定帳簿 − 消費）から、`{cards_selected, cards_used, cards_unclassified, cards_missing}` を引き、`_card_usable_in_battle`（同 1263、`BOSS_ONLY_CARDS = ('バルムンク',)` を通常戦から除外するのみ）で絞る。ノリウツールは使用可能札なので miss 対象となるのは契約どおり。
- 「開くべきだった」の根拠は owner 指示: `_summon_card_row_hidden` docstring（同 2838-2842、2026-11-22/25: 敵召喚獣戦で卵が使えず携行札があれば `きりふだ` を開く、2枚目以降も開く — `docs/operations/hanjuku-summon-card-20261125.md` も参照）。emit はその違反のアラームであり、`enemy_summon_observed` / 卵状態は**起票条件ではなく証拠フィールド**である。
- `enemy_summon_observed=true` は `battle.get('enemy_summon_observed') or battle.get('card_summon_observed')`（同 3370-3372）で、後者は egg_battle_step の毎観測で立つためほぼ重言的。ただし `GENERAL_EGGS['デーメーテール'] = (False, 1)`（`hanjuku_egg_reference.py:80`、卵なし・思考型1）で味方将軍は静的に卵を持たないため、観測された召喚は敵側（ヴィーナス）のものと確定でき、**実際に敵召喚獣戦**である。
- `empty_egg=false`: `_screen_egg_empty`（`battle_menus.py:3002`）は実測 0 等の限定条件でのみ立つ。卵を持たない将軍には測定対象がなく False となる（fail-closed）。`summon_egg_unavailable=false`: ラッチは `side == 'defense'` 限定（同 3268-3271）で、本件は attack 側のため立たない。両者とも矛盾しない。

## 3. 切り札行の表示・選択機構 — `scrolls: 不明` による亜型の確定

issue の証拠（#5521 で保持、§5）から次の逐次推理が**強制**される。

1. **`scrolls: 不明` ⟺ `battle['summon_card_scroll']` が None**。監査側は `_bounded_int(metric.get('scrolls'), 0, 10)`（`month_audit.py:3486`）を `不明` と描画し（`_render_card_miss` 4120）、emit は metric に `battle.get('summon_card_scroll')` をそのまま載せる（`battle_menus.py:3366`）。このカウンタは揭示スクロール実行時（同 3194-3196、`+1`）と召喚箱からの開札時（同 3225、`0` リセット）にのみ書かれる。よって **有界スクロールが1度も走らず、召喚箱からの開札も1度もなかった**。
2. それにもかかわらず `_summon_card_row_hidden`（同 2835-2864）の True 側条件は成立していた: `'たまごをつかう' in text or empty_egg` → False（卵なし将軍、表示文言にも非含有）、`summon_egg_unavailable` → False（attack 側）、最後の **`screen.kind == 'egg_battle_menu' and ('こうげき' in text or 'もうこうげき' in text)` → True**（kind と表示文言 `こうげきもうこうげき` が完全一致）。#5472 の卵行欠落フォールバックが効く画面だった。
3. ゆえに hidden が False となった経路は、関数先頭の `if not cards_left or battle.get('card_flow') or 'きりふだ' in (screen.text or ''): return False`（同 2857）の3肢に限られる。`cards_left` は非空（携行札候補2枚・emit 前提）、`きりふだ` は表示文言に非含有 → **`battle['card_flow']` が真**。
4. その flow の正体も絞れる。`interrupted_flow` 救済（同 3151-3166）は attack 分岐の先頭で **`stage in ('menu','down','list')` かつ `selection_planned`/`read_kit` を持たない** flow を毎観測クリアする（`summon_card_resume` へ退避）。クリアされれば同じ観測内で hidden が True となり scroll →早期 return するので、emit 時点で残っていた flow は **`stage == 'announce'`、または announce から戻った stage に `selection_planned=True` が残ったもの**のいずれか（`read_kit` は `_defense_kit_read_open` 経由で `side == 'defense'` 専用、本件は attack で不成立）。`selection_planned` は札行に A を送った時点（`card_list_step`、同 2213-2224）で `cards_selected` へ登録される。
5. したがって **この戦闘で別札 X（≠ノリウツール）の選択入力が済んでいた**が、その消費は最後まで未確定（`cards_used` 空）。X がノリウツールなら `cards_left` から除かれて emit 自体が不成立のため、X ≠ ノリウツールは強制。announce 待ちは `kind == 'card_list'` の `card_list_step`（同 2237-2316）でしか進行しないため、敵召喚が `card_list` / 通常メニューを `egg_battle_menu` へ置き換えた時点で flow は凍結し、この画面系では二度と進行しない。
6. 凍結 flow の真値は2つのゲートを同時に抑止する: 揭示ゲート（2857 の `battle.get('card_flow')`）と再オープンゲート（3213 の `and not battle.get('card_flow')`）。後者は `きりふだ` が既に可視の場合すら開かせない。結果、ノリウツール×2 は**画面に存在したが一度も揭示されず、選択もされなかった**まま `こうげき` が確定された。
7. 排他確認: scroll 枯渇型（scrolls≥3）は「不明」ではなく `3` と描画されるため不成立。`summon_card_opens >= 2`（同 3204）は `cards_left=[]` にして emit を消すため不成立。オープン経路（3212-3238）は `きりふだ` 不在のため未到達。emit 観測直前に救済が走るケースも上記のとおり scroll へ分岐し emit に届かない。
8. `cursorY=176` は `screen.menu_cursor` の実測値で、`_battle_menu_to(screen,'こうげき')` が 'here' を返したことと整合（箱先頭ページの `こうげき` 行）。`label == 'こうげき'` は `fierce`（同 3257-3261）が `not cards_left` を要求するため自動的に成立 — 携行札が残っている限り `もうこうげき` には進まない設計どおり。

## 4. #5259 家族・PR #5347 との異同

- 同一性: emit サイト・起票契約・「召喚戦の切り札行は箱下部にあり揭示が必要」という機構クラスは系列と同一。fingerprint が新しいのは章・札・城の組合せが初出だからで、別機構の証拠ではない。
- 相違: 系列の既発火（#5259/#5322/#5328/#5331/#5352/#5362/#5373/#5381/#5398/#5433/#5458/#5466/#5469/#5476）はすべて**卵行 OCR 欠落型**（旧ゲートの前提不成立 → scroll 不発）。本件は v1497 で既に #5472 修正後のゲートを使っており、フォールバック自体は True だった。**阻害要因が卵行条件ではなく `card_flow` 抑止である点で別亜型** — VERIFY-5331 が残存亜型として挙げていた「`card_flow` 残存 skip」の系列初の実観測である。
- PR #5347（家族の旧修正車両、CONFLICTING・未マージ、#5472 で superseded）: `own_attack_menu` フォールバックは `not card_flow` を前提に残しており、**本亜型はカバーしない**。仮にマージされていたとしても救えない型。
- なお本件は #5472 以降に発火した**系列初の card_miss issue**（観測版が #5472 込みの v1497）。

## 5. PR #5521（証拠保持）の適用状況

- `dae1516e4`「fix(audit): retain bounded card-miss evidence (#5519) (#5521)」は 2026-11-25 02:51:34Z に merge、観測版 v1497（`2fe1b66f8`、同日 02:59:39Z merge。occurrence 04:22:52Z より前）に**含まれる**。
- その内容（diff 実測）: `_track_card_miss` の `battle` 投影へ `side` / `screen_text` / `scrolls` / `cursor` / `empty_egg` / `summon_egg_unavailable` / `enemy_summon_observed` / `cards_left` を追加し、`_render_card_miss` で描画。issue に `side: attack`・`scrolls: 不明`・`cursorY: 176`・`表示文言`・`卵状態`・`携行札候補` がすべて残っているのは本 PR の成果。
- **`scrolls: 不明` は「証拠が欠落した」のではなく「`None` を忠実に保持した」値**で、カウンタが一度も書かれなかったこと（scroll・open ともにゼロ回）の確実な証拠になった。これが §3 の演繹を可能にした決定的な手掛かりで、根本原因の特定（亜型の確定）には十分機能した。一方、抑制していた `card_flow` の内容（札・stage・`selection_planned`）は記録されず、亜型の確定は演繹頼み — emit の `observed_metric` への追加を残件として提案する（§6）。

## 6. 現行 main（v1509 以降）での変更状況

- `git diff 2fe1b66f8 origin/main -- src/docich/hanjuku/flows/battle_menus.py`: `_hp1_resource_hold` 追加（v1506 `4e47662f3`、ボス戦以外の敵HP1で資源を使わない — announce 中 flow は意図的に温存される設計が明示される）と survival 一覧の unclassified 帳簿化（`5e41759ff`、#5198）のみ。**`_summon_card_row_hidden` / `interrupted_flow` / emit / `card_list_step` の announce 待ちは v1497 と現行 main で無差分**。
- `git diff` on `month_audit.py`: `battle_card_at_hp1` トラッカー追加（v1509 `cd6d7a24d`）と `_render_card_at_hp1` のみ。`_track_card_miss` / `_render_card_miss` / `_fingerprint` / card_miss incident_id は無差分。
- v1505（`3f037d7ed`、卵なし・敵卵あり→切り札で押し切る）は本ゲート・経路に触れず、occurrence 後の merge。
- したがって**本亜型の欠陥は現行 main に生存**する。カバーする PR・版は存在しない。
- 修正の方向（§3 の機構に対応）:
  1. `egg_battle_step` の `interrupted_flow` 救済を announce 系へ拡張する。`stage == 'announce'`（または `selection_planned` が残った後続 stage）の flow はこの画面系では進行不能なので、召喚画面での滞留が有界回数を超えたら `_card_use_unclassified`（`judgment/battle.py:152`）相当の帳簿処理（`cards_unclassified` へ移し `card_flow = None`）で解放する。一覧は既に閉じているため B は送らない。解放後は残り `cards_left` に対して揭示ゲートが通常どおり働き、ノリウツールを開ける。unclassified は「未使用」でも「使用済み」でもない保守的分類であり、`cards_used` ガードや監査の `unconfirmed` 意味論と矛盾しない。
  2. 代替: `_summon_card_row_hidden` / 再オープンゲートの `card_flow` 条件を「この画面で進行可能な flow のみ抑止」に絞る。ただし stale flow を残さない 1. が本筋。
  3. emit の `observed_metric` に `card_flow` の `{card, stage, selection_planned}` を追加し、本亜型を issue 単体で識別可能にする。

## 判定詳細

- **(a) 実欠陥**: emit の全前提（確定携行・`cards_left` 非空・`cards_used` 空・`こうげき` 実確定・未起票）が静的に確認でき、「携行ノリウツールを開かず攻撃を確定した」実事象。それを招いた flow 凍結→ゲート抑止は現行 main にも残る機構的欠陥。
- **(b) ではない**: #5472 は卵行欠落型のみカバー。`card_flow` 抑止の解除経路（救済）は announce 系を対象外としており、現行まで無変更。
- **(c) ではない**: X が実際に発射していようがいまいが（未確定）、ノリウツールが未開封のまま攻撃確定された事実は不変で、owner ルール（2枚目以降も開く）に反する。幻影の planned_cards 説を支持する証拠もない（別札 X の実一覧選択が行われている = 実キットは存在）。
- **(d) 要素あり（結論は変わらない）**: 凍結 flow の発生元（`battle.py:1148` の戦術 flow / `battle_menus.py:858` の survival flow / 同 1478 のレア戦術）、X の札名、X の発射有無、ノリウツールの携行経路（ch8 通常城へノリウツール×2を積んだ計画・調整経路）は issue 単体では確定不能。確定には `g1190-ch1v9` の `hanjuku_decisions.jsonl`（当該戦闘の `battle_card` / `battle_card_selected` / `battle_boss_card_missed` 周辺）が要る。

## 同型家族との照合

| 記録 | 章/札 | 判定 | 本件との関係 |
| --- | --- | --- | --- |
| #5259 家族（#5259…#5476） | ch1-12 各種 | (a) | 同一 emit・同一機構クラス。全件が卵行 OCR 欠落型（#5472 前） |
| VERIFY-5331 | card_miss 家族整理 | (a) | 「`card_flow` 残存 skip」を残存亜型として記録 — 本件がその初観測 |
| VERIFY-5476（#5476） | ch2 ダイチスイム v1486 | (a) | 卵行欠落型・#5472 カバー。本件はその次の亜型で、#5472 非カバー |
| PR #5347 | 未マージ | — | `not card_flow` 前提のため本亜型非カバー（#5472 で superseded） |
| PR #5521 | 証拠保持 | — | v1497 込み。`scrolls: 不明` の忠実保持が亜型確定の決め手 |
| 本件 #5538 | ch8 ノリウツール v1497 | (a) | 系列初の `card_flow` 凍結亜型。v1497・現行 main 双方に生存 |

## 検証メモ

- 実行した検証: issue 本文の照合、`git show`/`git diff` による観測版 `2fe1b66f8`（v1497）と現行 main `757f24fcd`（v1509）の静的照合、`interrupted_flow`・`_summon_card_row_hidden`・emit・`card_list_step` announce 待ち・`_carried_card_left` の経路追跡、`GENERAL_EGGS`・ch8 城一覧（いちじょうだに = 通常城）の確認、系列 VERIFY・`docs/operations/hanjuku-summon-card-20261125.md` との照合。
- fingerprint / occurrence の sha256 再計算は本環境の実行拒否（ハッシュ計算コマンドが拒否される）により未実施。期待 raw を §1 に記載。
- ドキュメントのみの追加のためテストは実行しない。
- 本ファイルは worktree ルートに**未コミット**で置いてある。`git switch -c` / `git checkout -b` / `git add` 等の書き込み系 git 操作を環境が拒否したため、`verify/5538-card-miss` ブランチへのコミットは別環境で `git checkout -b verify/5538-card-miss && git add VERIFY-5538.md && git commit` が必要（VERIFY-5476/5381 と同じ制約パターン）。push・PR 作成・issue コメントは依頼どおり実施しない。
- read-only 検証であり、本番操作・コード修正・VM への接続は行っていない。
