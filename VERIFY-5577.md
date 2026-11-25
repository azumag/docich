# VERIFY-5577 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: クースカン (ch12 ワフェルダ)

- 対象: [docich#5577](https://github.com/azumag/docich/issues/5577) / 監査 `card_miss`（confidence `high`）
- fingerprint `7415788ab1726ebb` / occurrence `550aa81b62deadb5e0ab`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1504-ch10-approach`
- ch12 / 月 13-7 / 未使用の携行札: **クースカン**（携行札候補: クースカン, ミックミー）
- 戦闘: グリッシーニ vs シェーブル / 城: ワフェルダ / side `attack` / 時刻 09:20:35Z
- 画面 `egg_battle_menu` / scrolls `不明` / cursorY `176` / 表示文言 `こうげきもうこうげき`
- 卵状態: `empty_egg=false` / `summon_egg_unavailable=false` / `enemy_summon_observed=true`
- 検証ベース: `origin/main` = `a3fc837765d5bde0d6a68b4e118e25d72c5ece3b`（`BOT_VERSION` `hanjuku-chart-v1527-audit-card-drop`）
- 観測版コード: main 祖先 `64d5112a1`（`BOT_VERSION` `hanjuku-chart-v1504-ch10-approach`）を `git show` で確認した。`fc8f8aa62` 系の同番マーカーと card_miss 関連ファイルに差分はない。
- 検証範囲: read-only。本ファイルの追加以外のコード変更なし。本番 SSH・VM 操作・ゲーム入力なし。ラン成果物 `hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/` は VM 側のため直接参照せず、issue 本文・関連 issue/PR・git オブジェクト・現行/観測版コードを照合した。
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）。

## 結論: **(a) 実欠陥 — #5538/#5553 と同じ「開札前から凍結した選択済み card_flow」型。観測版 v1504 では実欠陥だったが、マージ済み PR #5619（v1520）が本経路をカバーする。追加のコード修正は不要**

`scrolls: 不明` はスクロール未実施かつ召喚箱からの開札もなかったことを意味する。それでも `cards_left` にクースカン・ミックミーが残り、`きりふだ` を含まない `egg_battle_menu` で `こうげき` が確定されているため、v1504 のコード上は `battle['card_flow']` が真で reveal/reopen の両ゲートを塞いでいたことが論理強制される。未選択の menu/down/list flow は attack 分岐先頭で毎観測クリアされるため、残りうるのは選択済みの announce/`selection_planned` flow か防御専用 `read_kit` だけである。本件は side `attack` が記録されており `read_kit` は防御専用なので、実質的には**別札 X を選択入力済み・消費未確定のまま召喚画面へ凍結した flow**と切り分けられる。X はクースカンでもミックミーでもない（選択済み札は `cards_left` から除かれるため）。これは owner ルール「敵召喚獣戦で切り札を携行している場合は切り札を開く」に反する実事象であり、誤検知ではない。

PR #5619（v1520、merge commit `f2409ad08`、2026-11-26 01:52 JST merge）は occurrence（2026-11-25 09:20:35Z）より後の修正だが、まさにこの経路を修正している。凍結した announce/`selection_planned` flow がある間は `こうげき` を確定せず入力を保留し、3観測で `_card_use_unclassified` により `cards_unclassified` へ移して `card_flow = None` へ解放し、同じ観測から残り `cards_left` の reveal scroll へ戻る。防御 `read_kit` も召喚メニューでは進行不能として fail-closed に閉じる。したがって本件は v1504 当時の実欠陥だが、現行 main では既存修正でカバー済みである。

## 1. fingerprint / occurrence の式照合（sha256 再計算済み）

現行 `month_audit.py` と v1504 `64d5112a1` で、card_miss の式は同じである。

- `_fingerprint`（現行 `month_audit.py` L3365-L3367）: `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]`。`_track_card_miss`（同 L3570-L3571）は `[card, str(row.get('castle') or '')]` を渡す。城が記録されているため raw は **`12|card_miss|クースカン+ワフェルダ`**。Python で再計算した sha256 先頭16は **`7415788ab1726ebb`** で issue と一致した。
- occurrence / incident_id（同 L3519-L3521）: `sha256(f"{runtime_id}|{chapter}|{int(float(at))}|{card}")[:20]`。時刻 2026-11-25 09:20:35Z は epoch `1795598435`。raw は **`g1190-ch1v9|12|1795598435|クースカン`**。sha256 先頭20は **`550aa81b62deadb5e0ab`** で issue と一致した。
- 城が不明な系列では末尾が `+` になるが、本件はワフェルダが入るため fingerprint が `12|card_miss|クースカン+` 型ではない点も issue 本文と整合する。

## 2. emit 条件と証拠値の意味

- emit サイトは `egg_battle_step` の `battle_boss_card_missed`。v1504 では `battle_menus.py` L3342-L3375、現行では L3454-L3483。起票条件は `move == 'here' and label == 'こうげき'`、未使用の携行札 `missed` 非空、`cards_left` 非空、`cards_used` 空、1戦闘1件未記録。
- `cursorY=176` は `screen.menu_cursor` の実測で、`こうげき` 行にカーソルがあったことと整合する。未読カーソルや移動途中では emit されない。
- `_carried_card_left` は `planned_cards` または確定携行キットから `{cards_selected, cards_used, cards_unclassified, cards_missing}` を差し引く。クースカン・ミックミーは boss-only 札ではなく、通常の使用対象である。
- issue 本文の「卵なし・奥の手不可のときに」は静的テンプレート文であり、 measured evidence ではない。グリッシーニは `GENERAL_EGGS` では卵持ち `(True, 2)` なので、`empty_egg=false` を「卵が使えた」とも「卵なしだった」とも読めない。`summon_egg_unavailable` の latch は防御側限定なので、side `attack` の本件で false なのも非情報である。ただし §3 の `card_flow` 推理は卵状態に依存しない。
- `enemy_summon_observed=true` は emit metric の合成値で、`enemy_summon_observed` latch または `card_summon_observed` を示す。少なくとも画面は `egg_battle_menu` であり、card_miss 経路の対象画面であることは確かである。

## 3. `scrolls: 不明` からの亜型確定

1. 監査投影は `scrolls` を `_bounded_int(metric.get('scrolls'), 0, 10)` で保持し、int でなければ issue へ `不明` と描画する（現行 `month_audit.py` L3539, L4183）。emit は `battle.get('summon_card_scroll')` をそのまま載せる（現行 `battle_menus.py` L3469）。したがって `不明` はカウンタ未作成、すなわち **reveal scroll も summon 開札時の 0 リセットも一度もなかった**ことを意味する。
2. 召喚開札が成功していれば `battle['summon_card_scroll'] = 0` が書かれ、issue は `scrolls: 0` になる（v1504 `battle_menus.py` L3212、現行 L3324）。本件は不明なので、#5572/#5574/#5564 のような post-open 型ではない。
3. `_summon_card_row_hidden` は v1504 と現行で本文が一致する（v1504 L2822-L2851、現行 L2850-L2879）。先頭で `not cards_left`・`battle.get('card_flow')`・`きりふだ` 可視なら False を返す。本件は `cards_left` 非空、`きりふだ` 非含有である。
4. 卵行・`summon_egg_unavailable` がどちらも証拠上不確かでも、最終分岐 `screen.kind == 'egg_battle_menu' and ('こうげき' in text or 'もうこうげき' in text)` は成立する。したがって `card_flow` がなければ同じ観測で reveal scroll へ進み、`summon_card_scroll` が 1 に書かれて `pad('down')` で早期 return するはずである（v1504 L3179-L3190）。
5. それにもかかわらず `こうげき` が確定され、`scrolls` も未作成だった。scroll 枯渇なら `3` と出るはずなので、残る説明は `battle['card_flow']` が真で `_summon_card_row_hidden` が先頭で False だったことだけである。
6. attack 分岐先頭の `interrupted_flow` 救済（v1504 L3138-L3153）は、`stage in ('menu','down','list')` かつ未選択・非 `read_kit` の flow を `summon_card_resume` へ退避して `card_flow = None` にする。クリアされれば同じ観測で reveal scroll するため、emit に到達しない。 survival flow でも未選択なら同じくクリアされる。
7. よって残る blocker は `stage == 'announce'` / `selection_planned` の選択済み flow か、救済対象外の `read_kit` である。`read_kit` は `_defense_kit_read_window` が `side == 'defense'` を要求する防御専用で、本件は emit に side `attack` が記録されているため、この文脈では成立しない。したがって**選択入力済みの別札 X の flow**が凍結していたと切り分けられる。
8. X はクースカンでもミックミーでもない。札行に A を送った時点で `cards_selected` へ入り、`cards_left` から除外されるため、X がどちらかなら候補から消えるはずだからである。`CARRY_SLOTS = 3` と矛盾しない携行3枚目 X が存在したと推定できるが、X の名前・発射有無は issue 単体では確定できない。

## 4. 凍結 flow の発生元の切り分け

- **summon 開札由来ではない**: summon 開札なら scroll カウンタが 0 に書かれ、`scrolls: 0` になる。本件は不明。
- **未選択の戦術/レア戦術/survival flow ではない**: menu/down/list・未選択は `interrupted_flow` 救済でクリアされ、 blocker として残らない。
- **残るのは選択済み announce flow**: 通常の戦術 flow（`battle.py` で `stage: menu` から開始）、召喚文脈のレア戦術 flow、または survival list からの選択のいずれも、選択後は同じ `stage: announce` / `selection_planned: true` へ進む。画面が `card_list` から `egg_battle_menu` へ置き換わると、announce 待ちは `card_list_step` でしか進行できず凍結する。
- **戦術 flow か survival flow かは issue 単体では判定不能**: 両者とも選択時に `cards_selected`・`selection_planned` を作り、issue には flow 情報が投影されていない（v1504 の emit には `card_flow` がない）。X の札名、`tactic_id` の有無、survival かどうか、実発射の有無を確定するには run `g1190-ch1v9` の `hanjuku_decisions.jsonl` で `battle_card` / `battle_card_selected` / `battle_summon_card_open` / `battle_boss_card_missed` の連続時系列が必要である。v1520 以降は emit に `card_flow` が残るため、同型再発では issue 単体で分かる。

## 5. 関連 issue / PR との異同

| 対象 | 証拠値・型 | 本件との関係 |
| --- | --- | --- |
| #5538 | `scrolls: 不明`、ch8 ノリウツール、城 `いちじょうだに` / side `attack`、同ラン g1190 / v1497 | **同型の初観測**。城・side 既知で pre-open frozen announce flow と確定された。本件は同じランで v1504 まで残存した後発再観測 |
| #5553 | `scrolls: 不明`、ch6 ピッグローラー、城・side 不明、run g1342 / v1500 | 同じ scroll 不明型。場所は不明だが機構の推理は同じで、#5538 と併せて修正対象になった |
| #5560 | `scrolls: 3`、ch10 クースカン、城・side 不明、同ラン g1190 / v1500 | **別型**の scroll 枯渇型。3回 down しても行が読めず、PR #5615 `_summon_card_rearm` の対象。本件はカウンタ未作成で非カバー |
| #5564 | `scrolls: 0`、ch7 ノリウツール、城・side 不明、run g1342 / v1501 | 同じ frozen flow 家族だが post-open 型。開札成功の 0 リセットがあり、本件の pre-open/no-open 型とは発生元の確度が異なる。PR #5619 でカバー |
| #5572 | `scrolls: 0`、ch11 ミックミー、城・side 不明、同ラン g1190 / v1504 | 同ラン・同観測版の post-open frozen flow。本件は scroll 不明で開札前からの blocker であり、城ワフェルダ・side attack が分かる点が異なる |
| #5574 | `scrolls: 0`、ch11 ノリウツール、城・side 不明、同ラン g1190 / v1504 | #5572 と同じ post-open 型。携行候補にクースカンを含むが、本件のクースカン未使用とは fingerprint・戦闘・章が異なる |
| 本件 #5577 | `scrolls: 不明`、ch12 クースカン、城 `ワフェルダ` / side `attack`、同ラン g1190 / v1504 | #5538/#5553 の pre-open frozen flow 型。城・side が分かるため fingerprint に城名が入り、防御専用 read_kit 可能性を排除しやすい |

## 6. 現行 main での変更状況とカバー範囲

- `_summon_card_row_hidden` は v1504 `64d5112a1` と現行 `origin/main` で本文が一致する。reveal 条件そのものは変わっていない。
- `_track_card_miss` の検知条件・`_fingerprint`・incident_id 式は変わっていない。現行の差分は `card_flow` を bounded evidence として保持する1行（`month_audit.py` L3540）と、その描画（同 L4188）だけで、これは PR #5619 で追加された。検知そのものの緩和ではない。
- PR #5521（v1504 より前に適用済み）が `side` / `scrolls` / `cursor` / `screen_text` / `cards_left` を監査へ残したため、本件では城・side が分かり、`scrolls: 不明` の演繹ができる。
- PR #5615（merge commit `8b7fd9232`、v1515 系）: `scrolls=3` の枯渇後に `_summon_card_rearm` で有界再提示する。これは #5560/#5600 型の修正であり、`scrolls: 不明` の本件では reveal ゲートが `card_flow` で止まるため非カバー。
- PR #5619（merge commit `f2409ad08`、v1520）: 本件の経路をカバーする直接修正。現行 `battle_menus.py` L3200-L3246 で `read_kit` を閉じ、announce/`selection_planned` flow を3観測保留後に `_card_use_unclassified` で解放する。`SUMMON_CARD_FLOW_STALL_LIMIT = 3` は L3558。回帰テスト `test_a_frozen_announce_flow_holds_attack_then_releases`（`tests/test_hanjuku_boss_egg_row_log.py` L231）と `test_an_interrupted_defense_kit_read_is_closed_before_reveal`（同 L216）がある。テストは scrolls=0 を直接モデル化しているが、阻断している `card_flow` を解放して reveal scroll へ戻す制御は scroll カウンタ値に依存しないため、`scrolls: 不明` の pre-open 型も同じ経路で防がれる。
- PR #5629（v1522）: 召喚獣戦では希少戦術が無くても携行札を開く方向へ強化した。upstream の開札動機は増えるが、既に凍結した flow の解放機構ではなく、本件カバーの根拠は #5619 である。
- PR #5633（v1524）: 自城・防御戦では切り札を使わず、卵なし召喚獣戦はおくのてを選ぶ。side `attack` の本件には適用されない。将来の defense card_miss 評価では別途照合が必要だが、本件結論には影響しない。
- v1505/v1506/v1507/v1509 等の戦術・HP1 関連変更は card_miss の `_summon_card_row_hidden` / `interrupted_flow` / emit 条件を変えない。現行 main で本経路を変えた中核は #5615 と #5619であり、本件に効くのは #5619 である。

## 判定詳細

- **(a) 実欠陥**: 実測カーソルでの `こうげき` 確定、残り携行札、scroll/open ゼロ、reveal 抑止の論理が揃う。v1504 では残り札を開かず攻撃へ進んだ実事象。
- **(b)「設計どおり」ではないが、既存修正でカバー済み**: 観測版では設計意図に反する欠陥であり、現行 main では PR #5619 / v1520 がカバーする。系列の検証結論と同じく、事象の性質は (a)、追加実装は不要と記録する。
- **(c) ではない**: `scrolls: 不明` は欠損ではなく counter 未作成の証拠であり、`cards_left` 非空・実測 `こうげき` 確定と併せて frozen `card_flow` を論理強制する。静的テンプレート行には依存しない。
- **(d) 要素あり（結論は変わらない）**: X の札名、X が戦術 flow / survival flow / レア戦術 flow のどれで開始したか、X の実発射有無、クースカン・ミックミーの携行経路は issue 単体では確定できない。必要証拠は run `g1190-ch1v9` の `hanjuku_decisions.jsonl` にある当該戦闘前後の `battle_card`、`battle_card_selected`、`battle_card_unclassified`、`battle_summon_card_open`、`battle_summon_card_scroll`、`battle_boss_card_missed` の時系列である。

## 検証メモ

- 実行した検証: issue 本文照合、sha256 再計算、観測版 `64d5112a1` と現行 `a3fc83776` の `git show` / `git diff` による静的照合、`_summon_card_row_hidden` / `interrupted_flow` / `_carried_card_left` / emit / `card_list_step` announce 待ちの経路追跡、#5538/#5553/#5560/#5564/#5572/#5574/#5612、PR #5521/#5615/#5619/#5629/#5633 の確認。
- ドキュメントのみの追加のためテストは実行しない。
- read-only 検証であり、本番操作・コード修正・VM への接続・ゲーム入力は行っていない。
