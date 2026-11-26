# VERIFY-5617 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: ミックミー (ch3 メガパタ)

- 対象: [docich#5617](https://github.com/azumag/docich/issues/5617) / 監査 `card_miss`（confidence `high`）
- fingerprint `d9d591efc3c36c8d` / occurrence `1962ab0758a575a1aff9`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1516-melee-dialogue-advance`
- ch3 / 月 3-9 / 未使用の携行札: **ミックミー**（携行札候補: ミックミー, ミックミー）
- 戦闘: アテナ vs ビシソワーズ / 城: メガパタ / side: `attack` / 時刻 15:18:54Z
- 画面 `egg_battle_menu` / scrolls `3` / cursorY `176` / 表示文言 `こうげきもうこうげき`
- 卵状態: `empty_egg=false` / `summon_egg_unavailable=false` / `enemy_summon_observed=true`
- 検証ベース: `origin/main` = `ac9b6c23b3ad27cbda29ef29954a7605b6d9e9b0`（`BOT_VERSION` `hanjuku-chart-v1535-shop-reenter-limit`）。観測版 v1516 は commit `a01fa1983` の marker。
- 検証範囲: read-only。本ファイルの追加以外のコード変更なし。本番 SSH・VM 操作・ゲーム入力なし。ラン成果物 `hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/` は VM 側のため直接参照せず、issue 本文・関連 issue/PR・git オブジェクト・観測版/現行コード・合成再現を照合した。
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）。

## 結論: **(a) 実欠陥 — v1516 では scroll 枯渇型。PR #5615 は恒久開封不能を緩和するが初回枯渇の攻撃/emit を防がず、PR #5619 は別型。現行 main にも残余経路が残る。open PR #5652 が直接カバーするため、残件は重複実装ではなく PR #5652 のマージ**

本件は監査誤検知でも設計どおりでもない。v1516 時点の実事象として、敵召喚獣戦で携行したミックミーが残っているのに `こうげき` が確定された。直接の主因は、召喚戦コマンド箱で `きりふだ` 行を出す有界スクロール 3 回を使い切った後、描画途中・入力抜けへ対する再開がないまま攻撃分岐へ落ちたことである。

ただし修正車両は既に存在する。merged PR #5615 は scroll 枯渇後の re-arm を追加したが、初回枯渇では `summon_card_exhausted_tick` を置くだけで攻撃へ進む残余経路を残した。merged PR #5619 は `scrolls=0` / frozen `card_flow` 型で、本件の `scrolls=3` 型ではない。merged PR #5629 は通常メニュー側で携行札を開く拡張であり、この `egg_battle_menu` 上の reveal scroll 枯渇を直接は防がない。現行 main でも初回枯渇の観測で `pad('a')` と `battle_boss_card_missed` が再現した（§4）。open PR #5652（`verify/5632`）はその残余経路、すなわち re-arm 猶予中の攻撃確定を直接修正するため、本検証では新規の重複修正 PR を作らない。

## 1. fingerprint / occurrence の sha256 再計算

現行 `src/docich/hanjuku/month_audit.py` の card_miss 専用式で再計算し、両方一致した。

| 項目 | コード | 入力 | 再計算結果 | issue |
| --- | --- | --- | --- | --- |
| fingerprint | `_fingerprint`（現行 `month_audit.py` L3377-L3379）: `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]`。`_track_card_miss`（同 L3582-L3583）は `[card, castle]` を渡す | `3|card_miss|ミックミー+メガパタ` | `d9d591efc3c36c8d` | `d9d591efc3c36c8d` |
| occurrence | `_track_card_miss`（現行 `month_audit.py` L3531-L3534）: `sha256(f"{runtime_id}|{chapter}|{int(float(at))}|{card}")[:20]`。時刻 2026-11-25 15:18:54Z は epoch `1795619934` | `g1190-ch1v9|3|1795619934|ミックミー` | `1962ab0758a575a1aff9` | `1962ab0758a575a1aff9` |

両方とも完全一致し、issue データは監査形式と一致する。章・札・城・ラン・秒のどれかが違えば一致しないため、本件の fingerprint / occurrence は正しい。

## 2. v1516 での画面遷移と emit 条件

観測版 v1516（commit `a01fa1983`）の `egg_battle_step` では、攻撃分岐で次の順に進む。

1. `_summon_card_row_hidden`（v1516 `battle_menus.py` L2835）が、携行札残存・`card_flow` なし・`きりふだ` 非含有・`egg_battle_menu` で `こうげき` 文言ありなら真になる。
2. 真なら `scrolls < SUMMON_CARD_SCROLL_LIMIT` の間だけ `pad('down')` を返す（v1516 L3192-L3197）。上限は `SUMMON_CARD_SCROLL_LIMIT = 3`（v1516 L3433）。
3. v1516 には `_summon_card_rearm` が存在しない。`scrolls == 3` に達すると reveal 処理を抜け、そのまま通常の攻撃選択へ落ちる。
4. カーソルが `こうげき` で確定されると `battle_boss_card_missed` を emit する（v1516 L3355）。emit 条件は未使用の携行札 `missed` 非空、`cards_left` 非空、`cards_used` 空、1戦闘1件未記録である。

issue の `scrolls=3` / `cursorY=176` / `こうげきもうこうげき` はこの経路と一致する。`empty_egg=false` は卵所持を肯定する意味ではなく、issue 本文の「卵なし・奥の手不可のときに」は静的テンプレート文である。`enemy_summon_observed=true` と画面 `egg_battle_menu` が敵召喚獣戦文脈の証拠である。

ミックミーは `BOSS_ONLY_CARDS` ではなく、`_card_usable_in_battle` 上は通常の使用対象である。携行札候補が `ミックミー, ミックミー` だけなので、仮にミックミーが一度選択されていれば `cards_selected` / `cards_used` / `cards_unclassified` の attempted 集合へ同名が入り、`_carried_card_left` は重複分も含めてミックミーを残り札から外す。それでも emit されたことから、少なくとも emit 時点でミックミーの選択・使用・未分類計上はなかったと読める。v1516 の emit には `card_flow` 投影がないため厳密なflow状態は残っていないが、この札構成では `scrolls=0` 系の「別札を開いた後に announce flow が凍結した」型は成立しにくい。side も `attack` であり、defense 専用 `read_kit` 凍結型の証拠もない。

## 3. scrolls=3 系列と同札事案との異同

| 対象 | 観測版・証拠 | 本件との関係 |
| --- | --- | --- |
| #5600（バグストーム / ch2 ウラノポリス） | v1513、`scrolls=3` | 同ランの先行する scroll 枯渇型。当時は re-arm がなく、1回の入力抜けや半描画で戦闘中の開封機会を失う欠陥だった。PR #5615 の直接対象 |
| #5614（ノリウツール / ch8 おかざき） | v1515、`scrolls=3`、表示文言に `たまごをつかう` を含む | re-arm 導入前の同外型。VERIFY-5614 でも `scrolls=3` の初回枯渇で攻撃する残余経路が確認された |
| 本件 #5617（ミックミー / ch3 メガパタ） | v1516、`scrolls=3`、表示文言に `きりふだ` なし | 同じく re-arm 導入前の scroll 枯渇型。v1516 時点で系列が再発していた |
| #5606（ミックミー / ch4 四季王城(北東)） | v1513、`scrolls=0` | 同じ札だが別亜型。`scrolls=0` は少なくとも一度開札してカウンタがリセットされた post-open frozen `card_flow` 型で、PR #5619 が対象。本件の scroll 枯渇型ではない |
| #5613 | `sortie_without_cards` | 戦闘内 card_miss ではなく、出撃時の非携行/監査側の問題。本件とは別層 |

v1516 は PR #5615 の merge commit `8b7fd9232`（2026-11-26 01:51 JST）より前の観測（2026-11-26 00:18:54 JST）であり、**#5615 適用後の再発ではない**。一方で #5600 / #5614 と同じ証拠形が v1516 でも出ているため、当時の scroll 枯渇型の再発性は確認できる。

## 4. 現行 main での再現と既存修正のカバー範囲

現行 main `ac9b6c23b`（v1535）で、本件と同じ最小状態を合成した。

- `planned_cards=['ミックミー','ミックミー']`
- `summon_card_scroll=3`
- `egg_battle_menu`
- text `こうげきもうこうげき`
- cursorY 176
- side `attack` / castle メガパタ

結果:

```text
acts: [pad('a')]
battle_summon_card_exhausted
battle_egg_general_attack
battle_boss_card_missed card=ミックミー
observed_metric.cards_left=['ミックミー','ミックミー']
observed_metric.scrolls=3
observed_metric.card_flow=None
observed_metric.cursor=176
```

関連テストも実行し、現行仕様がこの残余経路を固定していることを確認した。

```text
PYTHONPATH=src:$HOME/py311shim python3 -m pytest -q tests/test_hanjuku_boss_egg_row_log.py \
  -k 'fresh_scroll_exhaustion or exhausted_card_scroll_re_arms or frozen_announce_flow_holds'
=> 3 passed, 15 deselected
```

`test_a_fresh_scroll_exhaustion_marks_evidence_before_attacking` は、初回枯渇で `battle_summon_card_exhausted` を記録したうえで攻撃し、`battle_boss_card_missed` を emit する現行 main の挙動を明示している。

### PR #5615（merged）

`_summon_card_rearm` を追加し、scroll 枯渇後にも 8 観測の間隔を置いて最大 2 回の揭示再開を試みる。方向は本件の原因に対応するが、初回枯渇時には tick を置いて `False` を返し、re-arm は後の観測まで始まらない。その猶予中に攻撃へ進めるため、**#5615 だけでは本件と同じ card_miss を防げない**。実際、§4 の合成再現では re-arm 前に `pad('a')` と `battle_boss_card_missed` が出た。

### PR #5619（merged）

`scrolls=0` / frozen `card_flow` 型の修正であり、本件の `scrolls=3` scroll 枯渇型ではない。`read_kit` 中断の fail-closed 解放と announce/`selection_planned` flow の有界保留は有用だが、本件の主因である reveal scroll 枯渇をカバーしない。

### PR #5629（merged）

召喚獣戦で希少戦術がなくても携行札を開くようにする通常メニュー側の拡張である。本件は `egg_battle_menu` で `きりふだ` 行そのものが reveal scroll 3 回後も読めなかった型なので、scroll 枯渇の残余経路を直接は防がない。

### open PR #5652（`verify/5632`）

#5632 で同定された残余経路、すなわち **re-arm 猶予中に攻撃できる問題**を修正する。修正 commit `35640b28e` は現行 `origin/main` の祖先ではなく、PR #5652 は open である。同ブランチでは `scrolls >= SUMMON_CARD_SCROLL_LIMIT` かつ `rearms < SUMMON_CARD_REARM_LIMIT` の間は `return []` で入力を保留し、re-arm 上限到達後は `battle_summon_card_reveal_failed` へ分離して `battle_boss_card_missed` を出さない。したがって本件の主因を直接カバーする。残件は重複実装ではなく **PR #5652 のマージ**である。

## 5. 判定詳細

- **(a) 実欠陥**: v1516 では scroll 枯渇後の再開がなく、現行 main でも re-arm 猶予中に攻撃と emit が発生する。owner ルール（敵召喚獣戦で卵が使えず切り札を持つ場合は切り札を使う）に反する。
- **(b) 既存修正でカバー済みではない**: merged PR #5615 は部分的緩和、PR #5619/#5629 は別経路。直接カバーする PR #5652 は open で main 未反映である。
- **(c) 設計どおりではない**: 携行札残存・実測 `こうげき` 確定は emit 条件の実測値であり、静的テンプレートに依存しない。
- **不確実な点（結論に影響なし）**: scroll が効かなかった直接原因（入力抜け/描画途中/行灰色）と、その後の戦闘でミックミーを開けたかは issue 単体では確定できない。確定には run `g1190-ch1v9` の `hanjuku_decisions.jsonl` で 15:18:54Z 前後の `battle_summon_card_scroll` / `battle_summon_card_exhausted` / `battle_summon_card_open` / `battle_boss_card_missed` の連続時系列が必要である。ただし v1516 の構造的欠陥と現行 main の残余経路は静的コードと合成再現で確認できた。

## 検証メモ

- 実行した検証: issue 本文照合、fingerprint / occurrence の Python sha256 再計算、観測版 `a01fa1983`（v1516）と現行 main `ac9b6c23b`（v1535）の `git show` / `git diff` による静的照合、`_summon_card_row_hidden` / reveal scroll / interrupted flow / emit の経路追跡、#5600/#5606/#5613/#5614/#5632、PR #5615/#5619/#5629/#5652 の確認、現行 main での合成再現、関連回帰テスト 3 件の実行。
- ドキュメントのみの追加のため新規コード変更なし。依頼どおり verify-only PR は作成しない。
- read-only 検証であり、本番操作・コード修正・VM への接続・ゲーム入力は行っていない。
