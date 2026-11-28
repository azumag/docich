# VERIFY-5632 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: キャトルミュー (ch2 セミヨン)

- 対象: [docich#5632](https://github.com/azumag/docich/issues/5632) / 監査 `card_miss`（confidence `high`）
- fingerprint `2cece4f3f5b7a65b` / occurrence `6317740c23d255c9f469`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1520-summon-card-flow-hold`
- ch2 / 月 2-9 / 未使用の携行札: **キャトルミュー**（携行札候補: キャトルミュー）
- 戦闘: クランベリー vs セミヨン / 城: 不明 / side: 不明 / 時刻 18:11:59Z
- 画面 `egg_battle_menu` / scrolls `3` / cursorY `176` / card_flow `なし` / 表示文言 `こうげきもうこうげき`
- 卵状態: `empty_egg=false` / `summon_egg_unavailable=false` / `enemy_summon_observed=true`
- 検証ベース: `origin/main` = `1eb90d221f81a1cd0f0602a9dc1aeefc2991e495`（`BOT_VERSION` `hanjuku-chart-v1528-card-list-unconfirmed-evidence`）
- 観測版コード: PR #5619 の merge commit `f2409ad08fd74c0e698396a8b7890f69c7b10d19` を `git show` で確認した。この tree の `BOT_VERSION` は issue 本文どおり `hanjuku-chart-v1520-summon-card-flow-hold`。
- 検証範囲: read-only。本ファイルの追加以外のコード変更なし。本番 SSH・VM 操作・ゲーム入力なし。ラン成果物 `hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/` は VM 側のため直接参照せず、issue 本文・関連 issue/PR・git オブジェクト・現行/観測版コードを照合した。
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）。

## 結論: **(a) 実欠陥 — scroll 枯渇後の re-arm 猶予を残したまま初回枯渇の同じ観測で `こうげき` を確定する残余欠陥。PR #5615 は恒久開封不能を緩和するが本件の emit を防がない。PR #5619 の frozen `card_flow` 型ではない。現行 main v1528 にも同じ経路が残る**

本件は `scrolls=3` かつ `card_flow=なし` なので、#5564/#5572/#5574 系の「選択済み flow が凍結して揭示を抑止した」型ではない。v1520 には PR #5615 の `_summon_card_rearm` が既に入っているが、実装は **初回 `SUMMON_CARD_SCROLL_LIMIT=3` の枯渇では re-arm を実行せず、`summon_card_exhausted_tick` を置いてそのまま攻撃へ進む**。この挙動は回帰テストでも明示的に固定されており、`test_a_fresh_scroll_exhaustion_marks_evidence_before_attacking` は `acts == [pad('a')]` と `battle_boss_card_missed` の両方を期待する（`tests/test_hanjuku_boss_egg_row_log.py` L115-L127）。

したがって v1520 で本件が起きた直接理由は、re-arm 未適用ではなく、**re-arm の初回待ちを開始した観測で攻撃と監査 emit が先に走る構造**である。re-arm は次の召喚メニュー観測で `SUMMON_CARD_REARM_TICKS=8` 経過後に初めて有効になる。issue だけではその後にキャトルミューを開けたかどうかは分からないが、「携行札が残る状態で `こうげき` を確定した」という監査事実そのものは実在し、owner ルール（敵召喚獣戦で卵が使えず切り札を持つ場合は切り札を使う）に反する。re-arm が後で成功した可能性をもって誤検知とも断定できない一方、少なくとも「re-arm 回復前に攻撃できる」制御欠陥はコード上確定している。

## 1. fingerprint / occurrence の式照合（sha256 再計算できた）

現行 `month_audit.py` と v1520 で、card_miss の式は同じである。

- `_fingerprint`（現行 `month_audit.py` L3367-L3369）: `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]`。`_track_card_miss`（同 L3572-L3573）は `[card, str(row.get('castle') or '')]` を渡す。城不明なので raw は **`2|card_miss|キャトルミュー+`**。Python で再計算した sha256 先頭16は **`2cece4f3f5b7a65b`** で issue と一致した。
- occurrence / incident_id（同 L3520-L3523）: `sha256(f"{runtime_id}|{chapter}|{int(float(at))}|{card}")[:20]`。時刻 2026-11-25 18:11:59Z は epoch `1795630319`。raw は **`g1190-ch1v9|2|1795630319|キャトルミュー`**。sha256 先頭20は **`6317740c23d255c9f469`** で issue と一致した。
- issue の再発コメント `aa666b149e3cc70e7295` も、時刻 2026-11-25 20:37:48Z = epoch `1795639068`、raw `g1190-ch1v9|2|1795639068|キャトルミュー` で再計算一致した。ただし再発コメントには scrolls / card_flow が無いため、本件と同じ scroll 枯渇亜型とは断定しない。

## 2. emit 条件と証拠値の意味

- emit サイトは v1520 `battle_menus.py` L3443 の `battle_boss_card_missed`。実測カーソルが `こうげき` 行にあること（`move == 'here' and label == 'こうげき'`）、未使用の携行札 `missed` 非空、`cards_left` 非空、`cards_used` 空、1戦闘1件未記録が条件である。
- `cursorY=176` は `screen.menu_cursor` の実測で、`こうげき` 行への移動済みであることと整合する。未読カーソルや移動途中では emit されない。
- `_carried_card_left` は `planned_cards` または確定携行キットから `{cards_selected, cards_used, cards_unclassified, cards_missing}` を差し引く。キャトルミューは BOSS_ONLY ではなく、本件の敵セミヨンは ch2 ボス `にせヒーロー` ではないが、敵召喚獣戦では通常使用対象である。
- issue 本文の「卵なし・奥の手不可のときに」は静的テンプレート文であり、measured evidence ではない。`empty_egg=false` は卵所持を肯定する意味ではなく、未観測と使用可能を区別しない。`summon_egg_unavailable` の latch は防御側限定なので、side 不明の attack 的可能性では false が非情報になる。
- `enemy_summon_observed=true` は emit metric の合成値で、少なくともコード上は敵召喚文脈の証拠として記録されている。画面も `egg_battle_menu` であり、card_miss 経路の対象画面である。

## 3. `scrolls=3` / `card_flow=なし` から確定する亜型

1. 監査投影は `scrolls` を `_bounded_int(metric.get('scrolls'), 0, 10)` で保持する（現行 `month_audit.py` L3541）。本件は `不明` ではなく int `3` なので、reveal scroll カウンタが上限 `SUMMON_CARD_SCROLL_LIMIT=3` まで進んでいた。
2. `_summon_card_row_hidden` は v1520 L2835-L2854。`cards_left` 非空・`card_flow` なし・`きりふだ` 非含有で、`screen.kind == 'egg_battle_menu'` かつ `こうげき` / `もうこうげき` が見える本件では hidden 条件が成立する。
3. したがって本来は `pad('down')` の reveal scroll に入る。実際に `scrolls=3` なので、少なくとも直近の有界予算は使い切った。描画途中・入力抜けで行が読めなかったのか、行が本当に灰色/不存在だったのかは issue 単体では分からない。
4. v1520 には PR #5615 の `_summon_card_rearm` がある（v1520 L2867-L2896、merge commit `8b7fd9232` は `f2409ad08` の祖先）。しかし `summon_card_exhausted_tick` が未設定の初回は、tick を書いて `False` を返すだけである（L2883-L2886）。
5. その場合、外側の scroll 分岐（v1520 L3257-L3287）は `summon_card_scroll_out` を記録した後、reveal 処理を抜ける。そのまま attack 行の選択へ進み、emit と `pad('a')` が起きる。re-arm は `SUMMON_CARD_REARM_TICKS=8`（約12秒）経過後の別観測で初めて `summon_card_scroll=0` に戻す（定数は v1520 L3527-L3535）。
6. つまり **scrolls=3 の初回枯渇だけで、本件と同じ証拠形（scrolls=3 / card_flow なし / `こうげき` emit）が必然的に作られる**。追加の frozen flow や OCR 完全不能を仮定する必要はない。
7. 逆に、1回または2回の re-arm 後に再び `scrolls=3` へ達していても、監査 issue 上の見え方は同じ `3` である。`rearms` / `summon_card_scroll_out` / `battle_summon_card_exhausted` は `battle_boss_card_missed` の metric に投影されていないため、初回枯渇か最終枯渇かは issue 単体では区別できない。これは判定不能な残件だが、初回枯渇で攻撃できる制御欠陥の存在は変わらない。
8. `card_flow` は v1520 の emit で `{card, stage, selection_planned}` を保持するようになり、監査も `_bounded_card_flow` で投影する（現行 `month_audit.py` L313-L319, L3542, L4118-L4124）。本件が `なし` なのは投影欠落ではなく、emit 時に dict 型の flow がなかったことを意味する。したがって announce/`selection_planned` flow が揭示を抑止した型は否定できる。
9. キャトルミュー固有の rare tactic 経路はこの結論を変えない。v1520 の `egg_battle_step` は `_available_rare_tactic` があっても最初に `rare_egg_command_return` を立てて `pad('b')` で「通常コマンドへ一度戻る」だけで、その場では開札しない（v1520 `battle_menus.py` L3103-L3108）。実際の rare 開幕使用は通常 battle menu に `きりふだ` が見えた場合だけ進む（同 L1465-L1486）。本件の emit 画面は `egg_battle_menu` で `きりふだ` を含まず、card_flow もないため、少なくともその経路で使用完了へ進まなかったことは確かである。`_available_rare_tactic` は step 一致を要求する（`judgment/battle.py` L2633-L2637）が、本件は side/castle/step の文脈が不明で、rare tactic オブジェクトが生成されていたか、B 後の通常メニューで行が読めなかったかは issue 単体では分からない。いずれにしても最終的な阻止形は generic な reveal scroll 枯渇である。

## 4. scrolls=3 系列との異同

| 対象 | 観測版・証拠 | 本件との関係 |
| --- | --- | --- |
| #5600（バグストーム / ch2 ウラノポリス） | v1513、`scrolls=3`、城・side は attack と記録 | 系列初の scroll 枯渇型。当時は re-arm がなく、1回の入力抜けでその戦闘中は開封不能だった。PR #5615 はこの恒久開封不能を緩和した。本件は re-arm 導入後だが、初回枯渇の攻撃 emit が残った後発観測 |
| #5614（ノリウツール / ch8 おかざき） | v1515、`scrolls=3`、文言に `たまごをつかう` を含む | re-arm 導入前の同外型。sibling だが v1520 の本件とは修正適用状況が異なる |
| #5617（ミックミー / ch3 メガパタ） | v1516、`scrolls=3`、城・side は attack と記録 | 同じく re-arm 導入前。本件は v1520なので PR #5615 の有無で切り分ける必要がある |
| 本件 #5632（キャトルミュー / ch2 セミヨン） | v1520、`scrolls=3`、城・side 不明、card_flow なし | re-arm 済み版での再観測。恒久開封不能かは不明だが、初回枯渇で攻撃・起票する残余経路は確定 |

PR #5615（merge commit `8b7fd9232`、v1519 tree に導入済み）は「scroll 枯渇後にもう一度有界で見る」ことはカバーするが、「初回枯渇の観測で攻撃しない」ことはカバーしない。したがって本件の根本原因を完全にはカバーしていない。特に `test_a_fresh_scroll_exhaustion_marks_evidence_before_attacking` が示すように、現在の仕様実装は初回枯渇で `battle_boss_card_missed` を出すことを明示的に許容している。

## 5. announce 凍結型・城/side 記録との違い

| 対象 | 証拠形 | 本件との違い |
| --- | --- | --- |
| #5560（クースカン / ch10） | v1500、`scrolls=3`、card_flow 投影なし | 外形は scroll 枯渇だが、v1500 の emit には card_flow が無く、凍結 flow の併存を issue 単体で排除できない。本件は v1520 で card_flow が明示投影され `なし` なので、flow 凍結を否定できる |
| #5564（ノリウツール / ch7） | v1501、`scrolls=0` | post-open frozen flow 型。少なくとも一度開札してカウンタが 0 にリセットされた型で、本件の 3 とは反対の証拠形 |
| #5572（ミックミー / ch11） | v1504、`scrolls=0` | 同じく post-open frozen flow 型。PR #5619 が直接カバーする型 |
| #5574（ノリウツール / ch11） | v1504、`scrolls=0` | #5572 と同型。本件は開札後ではなく reveal scroll 枯渇後 |
| #5577（クースカン / ch12 ワフェルダ） | v1504、`scrolls=不明`、城 `ワフェルダ` / side `attack` | pre-open/no-open frozen flow 型。城・side が記録されているため fingerprint に城名が入り、防御専用 `read_kit` の可能性を排除しやすい。本件は城・side 不明で fingerprint 末尾が空になる |

PR #5619（merge commit `f2409ad08`、v1520）は、announce/`selection_planned` flow が召喚メニューで凍結したとき、解放前の攻撃を保留し、3観測で `_card_use_unclassified` へ解放する。これは #5564/#5572/#5574/#5577 系には有効だが、本件は emit 時 `card_flow=なし` なので適用経路ではない。PR #5619 の価値は、むしろ本件で flow 不在を確認できる証拠を追加した点にある。

城・side については、v1520 の emit は `ally/enemy/castle/side` を top-level へ渡している。したがって本件の「不明」は v1488 以前のような投影欠落ではなく、battle context 自体が `castle=None` / `side=None` だったことを意味する。`_battle_context` は attack message が一致した場合だけ城と side を確定し、en-route の一意候補だけでは `unclassified_location` として両方を None にする。v1520 から現行 main までこの構造に差分はない。野外遭遇または attack message 未捕捉の可能性があり、#5577 のように城攻撃が確定した事案とは証拠品質が異なる。

この unknown は v1524（PR #5633）以降に重要になる。PR #5633 は `side == 'defense'` なら切り札を使えない owner ルールを追加したが、`_card_usable_here` は `side` が None の場合に真を返す。したがって side 不明の本件型は、実際には防御戦でも攻撃戦として切り札使用を試みる可能性を残す。本件の観測版 v1520 は v1524 より前なので結論を左右はしないが、後続修正では context evidence の保持が必要である。

## 6. 現行 main と修正の方向

現行 main `1eb90d221f`（v1528）でも、v1520 から `_summon_card_row_hidden` / `_summon_card_rearm` / scroll 枯渇後の fall-through / `battle_boss_card_missed` emit に関係する行は変わっていない（`git diff f2409ad08..origin/main -- battle_menus.py` で該当シンボルの差分なし）。v1522（PR #5629）は `battle_menu_step` で希少戦術が無くても携行札を開く拡張であり、キャトルミューは既に rare tactic 経路を持つため本件のscroll枯渇経路を変えない。v1528（PR #5643）は選択済み札の未確認証拠を監査へ残す変更で、scroll/re-arm 制御は変えない。

修正の方向は次の通り。

1. **re-arm 猶予中は攻撃を保留する**: `scrolls >= SUMMON_CARD_SCROLL_LIMIT` かつ `rearms < SUMMON_CARD_REARM_LIMIT` で、まだ re-arm 待ちが残る間は `pad('a')` の `こうげき` 確定へ進めず、有界に入力保留または次観測待ちにする。`egg_battle_menu` はコマンド入力待ちなので、切り札使用を優先する owner ルールに合わせて、回復余地がある間に白兵を確定しない。
2. **監査 emit を最終失敗と分ける**: re-arm 回復前の初回枯渇で `battle_boss_card_missed` を high confidence 起票しない。少なくとも emit の `observed_metric` に `rearms` / `summon_card_scroll_out` / `summon_card_exhausted_tick`（または直近の `battle_summon_card_exhausted` 有無）を入れ、month audit へ投影し、初回枯渇・re-arm 中・最終枯渇を区別できるようにする。最終的に `SUMMON_CARD_REARM_LIMIT=2` まで失敗した場合は、単なる card_miss ではなく「切り札行揭示失敗」の別証拠として扱う方が原因を誤読しにくい。
3. **スクロール入力の進行確認**: 現行は観測ごとにカウンタを増やすだけで、down が実際にカーソル/表示へ効いたかを見ていない。cursorY・visible rows・同一 frame を bounded evidence として、入力不成立時は予算消費を延期または明示記録する。
4. **side/castle 不明を残さない**: emit に `_battle_context` の `context`（`unclassified_location` / `ambiguous_sortie` 等）と sortie step を添える。v1524 以降は防御戦で切り札不可なので、side None を attack 扱いしない判断材料が必要である。少なくとも監査 issue 上で「野外/未分類のため不明」なのか「投影漏れ」なのかを区別できるようにする。

PR #5615 は恒久開封不能の再提示をカバーするが、上記 1・2 をカバーしない。PR #5619 は frozen `card_flow` の攻撃保留をカバーするが、本件の scroll 枯渇型をカバーしない。したがって既存修正で解決済みとは判定しない。

## 判定詳細

- **(a) 実欠陥**: 実測 `こうげき` 確定、携行札残存、scroll 枯渇、re-arm 猶予中の攻撃許容がコードと回帰テストで確認できる。owner ルールに反する。
- **(b) ではない**: PR #5615 は re-arm の存在を追加したが初回枯渇時の攻撃 emit を残し、PR #5619 は別機構である。現行 main にも同じ経路が残る。
- **(c) ではない**: `こうげき` を選んだ事実と携行札残存は emit 条件の実測値で、監査の static template に依存しない。後で re-arm 成功した可能性があっても、この観測の攻撃事実は消えない。
- **(d) 要素あり（結論は変わらない）**: 初回枯渇後に re-arm が発火したか、最終的にキャトルミューを開いたか、scroll が効かなかった直接原因（入力抜け/描画途中/行灰色）かは issue 単位では確定できない。必要証拠は run `g1190-ch1v9` の `hanjuku_decisions.jsonl` で、18:11:59Z 前後の `battle_summon_card_scroll` / `battle_summon_card_exhausted` / `battle_summon_card_rearm` / `battle_summon_card_open` / `battle_card_selected` / `battle_boss_card_missed` の連続時系列である。ただし re-arm 前攻撃の制御欠陥は静的に確定している。

## 検証メモ

- 実行した検証: issue 本文照合、fingerprint / occurrence の Python sha256 再計算（初回・再発コメント）、観測版 `f2409ad08` と現行 `1eb90d221f` の `git show` / `git diff` による静的照合、`_summon_card_row_hidden` / `_summon_card_rearm` / interrupted flow / emit / `_bounded_card_flow` / `_battle_context` の経路追跡、#5600/#5614/#5617/#5560/#5564/#5572/#5574/#5577/#5603/#5606、PR #5615/#5619/#5629/#5633/#5643 の確認、関連回帰テストの確認。
- ドキュメントのみの追加のためテストは実行しない。
- read-only 検証であり、本番操作・コード修正・VM への接続・ゲーム入力は行っていない。
