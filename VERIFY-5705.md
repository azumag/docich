# VERIFY-5705 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: クースカン (ch12 ユーフォニア)

- 対象: [docich#5705](https://github.com/azumag/docich/issues/5705) / 監査 `card_miss`（confidence `high`）
- fingerprint `9671bca3d49c9b0f` / occurrence `4c1040fb3937ea20db79`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1525-sortie-cards-eligible`
- ch12 / 月 27-6 / 未使用の携行札: **クースカン**（携行札候補: クースカン）
- 戦闘: ラビオリ vs ラクレット / 城: ユーフォニア / side: `attack` / 時刻 17:27:14Z
- 画面 `egg_battle_menu` / scrolls `3` / cursorY `176` / card_flow `なし` / 表示文言 `こうげきもうこうげき`
- 卵状態: `empty_egg=false` / `summon_egg_unavailable=false` / `enemy_summon_observed=true`
- 検証ベース: `origin/main` = `aca84be44`（`BOT_VERSION` `hanjuku-chart-v1566-egg-attempt-retry`）。観測版は `git show 05f05c050:src/docich/hanjuku/version.py` が `hanjuku-chart-v1525-sortie-cards-eligible` であることを確認した。
- 検証範囲: read-only。本ファイルの追加以外のコード変更なし。本番 SSH・VM 操作・ゲーム入力なし。ラン成果物 `hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/` は VM 側のため直接参照せず、issue 本文・関連 issue/PR・git オブジェクト・現行/観測版コードを照合した。
- `handoff.md` 正本は参照確認コマンドがこの環境で実行できなかったため未読と記録する（AGENTS.md §1）。

## 結論: **(b) 設計どおり・既存修正でカバー済み — v1525 で観測された scroll 枯渇型 / re-arm 猶予中攻撃は PR #5652（v1543）で現行 main に修正済み。追加の修正実装は不要**

本件の証拠形は #5632 と同じく、`scrolls=3`・`card_flow=なし`・`egg_battle_menu`・`cursorY=176`・表示文言 `こうげきもうこうげき` の **scroll 枯渇型** である。v1525 の実装では、切り札行揭示の有界スクロールが上限 `SUMMON_CARD_SCROLL_LIMIT=3` に達した初回枯渇で、`_summon_card_rearm` が `summon_card_exhausted_tick` を置いて `False` を返すだけであり、その同じ観測で attack 分岐へ落ち、`こうげき` 行が実測済みなら `battle_boss_card_missed` を emit して `pad('a')` を返した。したがって v1525 のランで本 issue が作られた経路は実在する。

ただし現行 main には PR #5652（merge commit `58451c3f6`、v1543 `hanjuku-chart-v1543-summon-card-rearm-hold`）が入っている。現行コードは、

1. `scrolls >= SUMMON_CARD_SCROLL_LIMIT` で re-arm 上限が残る間は `rearm_pending=True` とし、`battle_summon_card_exhausted` を記録したうえで `return []` して `こうげき` 確定を保留する（`src/docich/hanjuku/flows/battle_menus.py` L3380-L3423）。
2. `SUMMON_CARD_REARM_TICKS=8` 経過後に `_summon_card_rearm` が `summon_card_scroll=0` へ戻し、有界な `down` スクロールを最大 `SUMMON_CARD_REARM_LIMIT=2` 回だけ再開する（同 L2911-L2941、L3693-L3701）。
3. re-arm 上限まで行が読めなかった最終枯渇は `battle_summon_card_reveal_failed` として別証拠に分離し、`battle_boss_card_missed` の emit 条件から明示的に除外する（同 L3424-L3440、L3593-L3596）。
4. 逆に、枯渇後に `きりふだ` 行が読めたのに `こうげき` を確定した場合は、従来どおり card_miss を残す。監査には `rearms` / `summon_card_scroll_out` / `summon_card_exhausted_tick` も投影される（`src/docich/hanjuku/month_audit.py` L3568-L3574、L4446-L4448）。

この修正は回帰テストでも固定されている。`tests/test_hanjuku_boss_egg_row_log.py` L115-L145 は初回枯渇で `acts == []`・`battle_summon_card_exhausted`・`rearm_pending is True` であり、`battle_boss_card_missed` が出ないこと、猶予経過後に `down` で再開することを確認する。同 L147-L166 は re-arm 上限到達時に `battle_summon_card_reveal_failed` を記録し card_miss を出さないことを確認する。`tests/test_hanjuku_chart_bot.py` L4593-L4618 も dispatcher 経由で同じ状態を固定している。

したがって、issue の観測自体は v1525 の実欠陥に由来するが、現行 main では同じ初回枯渇で攻撃も監査 emit も起きず、最終枯渇も card_miss ではなく揭示失敗へ分離される。重複する修正実装は不要と判定する。

## 1. 証拠形の特定

- `scrolls=3` は上限 `SUMMON_CARD_SCROLL_LIMIT=3` と一致する。切り札行揭示の有界スクロールを使い切った状態である。
- `card_flow=なし` なので、#5564/#5572/#5574/#5577 系の「選択済み・告知待ち `card_flow` が召喚画面で凍結し、揭示を抑止した」型ではない。
- `cursorY=176` は `こうげき` 行の実測カーソルと整合する。emit は未読カーソルや移動途中ではなく、`move == 'here' and label == 'こうげき'` のときだけ行われる。
- `empty_egg=false` は「卵を持っている」実測ではなく、使用済み卵と確定していないだけである。`summon_egg_unavailable=false` も防御側 latch が立っていないだけで、side `attack` の本件では非情報に近い。いずれにせよ、v1525 の `_summon_card_row_hidden` は `egg_battle_menu` で `こうげき` / `もうこうげき` が読め、`きりふだ` が見えない画面を揭示対象とする。
- `enemy_summon_observed=true` と `egg_battle_menu` は、少なくとも emit 時点で敵召喚獣戦文脈として処理されていたことを示す。side は `attack` なので、v1524 以降の「防御戦では切り札を使わない」例外には当たらない。クースカンは BOSS_ONLY ではなく、`_card_usable_in_battle` 上も使用可能な携行札である。

## 2. ラン時版 v1525 と現行 main の差異

### v1525（issue 観測版）

`05f05c050` の `battle_menus.py` では、`_summon_card_rearm` 自体は既に存在した。しかし scroll 枯渇時の外側の分岐は次の構造だった。

- `scrolls >= SUMMON_CARD_SCROLL_LIMIT` かつ `_summon_card_rearm(...)` が真なら `scrolls=0` に戻す。
- 初回枯渇では `_summon_card_rearm` が `summon_card_exhausted_tick` を置いて `False` を返す。
- その場合でも `summon_card_scroll_out` と `battle_summon_card_exhausted` を記録した後、そのまま attack 分岐へ進む。
- `move == 'here' and label == 'こうげき'` なら `battle_boss_card_missed` を emit し、`pad('a')` を返す。
- emit の metric には `scrolls` と `card_flow` 等はあるが、`rearms` / `summon_card_scroll_out` / `summon_card_exhausted_tick` は無く、初回枯渇・re-arm 中・最終枯渇を issue だけで区別できない。

本件の `scrolls=3` / `card_flow=なし` / `cursorY=176` はこの構造と一致する。re-arm が残っているのに初回枯渇の同じ観測で攻撃できる点は、VERIFY-5632 が指摘した残余欠陥と同じである。

### 現行 main（v1566、PR #5652 適用後）

PR #5652 の diff を `git show` で確認した。現行 main では、

- 初回枯渇や re-arm 待ち中は `rearm_pending=True` となり、`return []` で `こうげき` 確定を保留する。
- 待機後の re-arm は `summon_card_scroll=0` に戻して有界 `down` を再開する。
- 最終的に re-arm 上限まで読めなかった場合は `battle_summon_card_reveal_failed` を記録し、その後の攻撃は「切り札を開ける行が読めたのに使わなかった」証拠ではなく揭示失敗として扱う。
- card_miss が残るのは、`きりふだ` 行が読めたのに `こうげき` へ進んだ場合など、実際に選択肢がありながら札を開かなかった場合に限定される。

つまり v1525 で issue を作った経路は、現行 main では初回枯渇・re-arm 中・最終枯渇のいずれでも同じ形の high-confidence card_miss にならない。

## 3. PR #5652 でカバーできるか

カバーできる。理由は次の通り。

1. 本件が **初回枯渇** だった場合: 現行 main では `rearm_pending` により攻撃を保留し、`battle_boss_card_missed` を emit しない。8 tick 後に re-arm して揭示を再試行する。
2. 本件が **re-arm 後の最終枯渇** だった場合: v1525 の issue だけでは rearms が見えないため区別できないが、現行 main では最終枯渇を `battle_summon_card_reveal_failed` へ分離し、card_miss を emit しない。
3. 本件が **行が読めたのに攻撃した真のスキップ** だった場合: 現行 main でも card_miss は残る。ただし本件の表示文言は `こうげきもうこうげき` で `きりふだ` を含まず、`scrolls=3` なので、その証拠形ではない。

したがって、issue 単体で初回枯渇か最終枯渇かは区別できないものの、どちらの亜型でも PR #5652 の変更範囲に入る。この残留不確実性は結論を変えない。

## 4. 既存検証との異同

| 対象 | 観測版・証拠 | 本件との関係 |
| --- | --- | --- |
| #5632（キャトルミュー / ch2 セミヨン） | v1520、`scrolls=3`、card_flow なし、城/side 不明 | 最も近い同型。VERIFY-5632 は「re-arm 猶予を残したまま初回枯渇の同じ観測で攻撃する」実欠陥と判定し、PR #5652 はその指摘を実装した修正。本件は v1525 で、城/side が `ユーフォニア` / `attack` と確定している点だけ異なる |
| #5614（ノリウツール / ch8 おかざき） | v1515、`scrolls=3`、side attack | re-arm 導入前の観測として検証され（issue 検証では PR #5615 merge は発生後）、当時は PR #5652 未マージで残件とされた。本件はそれより後の v1525 だが、PR #5652 前という点では同じ残余経路 |
| #5617（ミックミー / ch3 メガパタ） | v1516、`scrolls=3`、side attack | 同じ scroll 枯渇型。当時の検証でも PR #5652 が直接カバーすると整理されていた。現在は同 PR が main にマージ済み |
| #5577（クースカン / ch12 ワフェルダ） | v1504、`scrolls=不明`、pre-open frozen announce `card_flow` 型 | 同じクースカン・ch12 だが別経路。#5577 は PR #5619（v1520）でカバーされた frozen flow 型。本件は `scrolls=3` / card_flow なしなので、PR #5619 ではなく PR #5652 の対象 |
| #5600（バグストーム） | `scrolls=3`、re-arm 無し | scroll 枯渇系列の起点。PR #5615 が再開機構を追加し、PR #5652 がその初回猶予中に攻撃できる残余を修正した |

## 5. ch12 固有メニューとの関係

ch12 には、五項目コマンドリストを上へ戻して `こうげき` を確定する `human_command_scroll` 経路がある（`battle_menus.py` L3012-L3064）。本件も ch12 なので、この経路の影響を確認した。

- `battle_menu_scroll_attack` の early return は card_miss emit より前に `pad('a')` を返すため、この経路だけでは `battle_boss_card_missed` は作られない。
- `human_command_scroll` が対象外・未開始・解放済みだった場合は、従来の召喚戦カード揭示ロジックへ落ちる。本件は `scrolls=3` が記録されているため、実際にその揭示ロジックの予算を消費している。
- したがって ch12 固有経路の存在は、本件の emit が通常の summon-card reveal 経路から出たという判断を覆さない。仮に human scroll episode が解放されて通常経路へ戻った場合でも、現行 main では PR #5652 の `rearm_pending` / `summon_card_reveal_failed` が同じ場所で効く。

なお、ch12 固有経路が別の携行札使用を省略していないかは本 issue の証拠だけでは判定できない。少なくとも本件の card_miss 証拠形が要求する経路については、現行 main の修正でカバーされる。

## 6. 判定詳細

- **(a) 実欠陥（未修正）ではない**: v1525 では実欠陥だったが、現行 main では同じ初回枯渇で攻撃しない。最終枯渇も card_miss へ混入しない。したがって追加修正が必要な実欠陥とは判定しない。
- **(b) 既存修正でカバー済み**: PR #5652（v1543）が本件と同じ scroll 枯渇型を直接修正し、現行 main v1566 に含まれる。回帰テストも存在する。
- **(c) 誤検知ではない**: v1525 当時、携行クースカンが残り、`こうげき` 行で card_miss emit 条件が成立したことは issue の証拠とコード条件が一致する。監査の静的文言だけに依存した false positive ではない。
- **(d) 要素あり（結論は変わらない）**: v1525 の issue には rearms / scroll_out / exhausted_tick が無く、初回枯渇か re-arm 後の最終枯渇かは区別できない。また scroll が効かなかった直接原因（描画途中、入力抜け、行が実際に灰色など）は VM 側の decision/frame 時系列がなければ確定できない。ただし、いずれの亜型でも PR #5652 の適用範囲なので、現行 main に対する結論は (b) のままである。

## 検証メモ

- 確認した主な根拠: issue #5705 本文、VERIFY-5632、issue #5632/#5614/#5617/#5577 の検証コメント、PR #5652 merge commit `58451c3f6` の diff、v1525 commit `05f05c050` の `battle_menus.py` / `version.py`、現行 main `aca84be44` の `battle_menus.py` / `month_audit.py` / 関連回帰テスト。
- 実行した検証: 静的コード照合、関連 issue/PR の照合、回帰テスト内容の確認。ローカルテスト実行は行わず、既存テストの期待値を根拠にした。
- read-only 検証であり、本番操作・コード修正・VM への接続・ゲーム入力は行っていない。
- 実行環境は VM Devin CLI の smart 非対話モードで、`git add` / `git commit` / `gh` が承認却下される。そのため本ファイルは worktree に作成済みだが、コミットと issue コメント投稿は未実施である。push はもともと行わない。Slack MCP の検索・投稿呼び出しも承認却下/接続失敗のため、Slack 完了報告も未実施である。

## issue #5705 コメント文案（gh が使えないため未投稿）

## 検証結論: **(b) 設計どおり・既存修正でカバー済み — scroll 枯渇型 / re-arm 猶予中攻撃は PR #5652（v1543）で現行 main に修正済み**

検証記録は worktree `verify/5705` の `VERIFY-5705.md` に作成しました。ただし実行環境では git 書き込み操作が承認却下されるため、コミットは未実施です。

要点:

- 本件は `scrolls=3` / `card_flow=なし` / `egg_battle_menu` / `cursorY=176` / 表示文言 `こうげきもうこうげき` で、#5632 と同じ **scroll 枯渇型** です。#5577 系の frozen `card_flow` 型ではありません。
- 観測版 `hanjuku-chart-v1525-sortie-cards-eligible`（commit `05f05c050`）では、初回の scroll 枯渇で `_summon_card_rearm` が `summon_card_exhausted_tick` を置いて `False` を返し、その同じ観測で attack 分岐へ進み、`こうげき` 行の実測時に `battle_boss_card_missed` を emit しました。本件はこの経路と一致します。
- 現行 main `aca84be44`（v1566）には PR #5652（merge commit `58451c3f6`、v1543）が入っています。現行コードは re-arm 猶予中に `return []` で `こうげき` 確定を保留し、8 tick 後に有界 scroll を再開します。re-arm 上限まで読めなかった最終枯渇は `battle_summon_card_reveal_failed` へ分離し、`battle_boss_card_missed` を emit しません。
- 回帰テストでも、初回枯渇では `battle_summon_card_exhausted` のみで card_miss を出さないこと、re-arm 上限では reveal failure へ分離することが固定されています（`tests/test_hanjuku_boss_egg_row_log.py` L115-L166、`tests/test_hanjuku_chart_bot.py` L4593-L4618）。
- v1525 の issue には rearms / scroll_out / exhausted_tick が無く、初回枯渇か最終枯渇かは区別できません。ただしどちらの亜型でも PR #5652 の適用範囲なので、追加の修正実装は不要と判定しました。

したがって、観測当時は実欠陥でしたが、現行 main では既存修正でカバー済みです。重複する修正 PR は不要です。

## Slack 完了報告文案（Slack MCP が使えないため未投稿）

<@U0CCCH69XMW> docich#5705 の read-only 検証を完了しました。

結論: **(b) 設計どおり・既存修正でカバー済み**。`scrolls=3` / `card_flow=なし` / `egg_battle_menu` / `cursorY=176` の証拠は、#5632 と同じ scroll 枯渇型です。観測版 v1525 では初回枯渇の re-arm 猶予中でも attack へ進み、`battle_boss_card_missed` を emit しえましたが、現行 main には PR #5652（v1543）が入っており、re-arm 猶予中は攻撃を保留し、最終枯渇は `battle_summon_card_reveal_failed` へ分離されます。追加の修正実装は不要と判定しました。

検証記録: worktree `verify/5705` の `VERIFY-5705.md`。issue コメントは実行環境で `gh` が承認却下されるため未投稿で、同ファイル末尾に文案を記録しています。コミットも同じ環境制約で未実施です。
