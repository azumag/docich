# VERIFY-5612 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: ノリウツール (ch8 チコリ)

- 対象: [docich#5612](https://github.com/azumag/docich/issues/5612) / 監査 `card_miss`（confidence `high`）
- fingerprint `ea91e6a4dcc16324` / occurrence `29a318059b2fd1a51a46`
- ラン: `g1342-1fe0734c` / generation `1342` / `hanjuku-chart-v1515-monster-menu-total-cap`
- ch8 / 月 13-10 / 未使用の携行札: **ノリウツール**（携行札候補: ノリウツール, ミックミー）
- 戦闘: ラビオリ vs チコリ / 城: 不明 / side: 不明 / 時刻 14:26:07Z
- 画面 `egg_battle_menu` / scrolls `0` / cursorY `176` / 表示文言 `こうげきもうこうげき`
- 卵状態: `empty_egg=false` / `summon_egg_unavailable=false` / `enemy_summon_observed=true`
- 検証ベース: `origin/main` = `818700b1937e4918a073c5c24df4cd781755e035`（`BOT_VERSION` `hanjuku-chart-v1518-card-purchase-priority`）
- 検証範囲: read-only。本ファイルの追加以外のコード変更なし。本番 SSH・VM 操作・ゲーム入力なし。ラン成果物 (`hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/`) は VM 側のため直接参照せず、issue 本文・git オブジェクト・現行/観測版コードを照合した。
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）。

## 結論: **(a) 実欠陥 — 開札後に残った `card_flow` が召喚メニューで進行不能になり、残り携行札の揭示を抑止した「post-open frozen flow」型。scroll 枯渇型ではなく、現行 main にも生存する**

本件は誤検知ではない。issue の `scrolls` は `不明` ではなく明示の整数 `0` であり、これは `battle['summon_card_scroll']` が実際に 0 へ書かれたことを意味する。現行コードで 0 へ書く経路は、召喚戦で携行切り札を開いたときのリセット（`battle_menus.py` L3225）だけである。したがって本戦闘では少なくとも一度は切り札一覧を開いた後、箱が先頭ページへ戻っている。

それにもかかわらず、emit 時点で `cards_left` はノリウツール・ミックミーを含み、`cards_used` は空だった（emit 条件）。`きりふだ` は表示文言に無く、画面種別と `こうげき` 文言だけを見ると `_summon_card_row_hidden` の reveal 条件は成立する。それでも scroll せず `こうげき` が確定された以上、関数先頭で reveal を止めた `battle.get('card_flow')` が真であったことがコード上強制される。`stage in ('menu','down','list')` で未選択の flow なら attack 分岐先頭の `interrupted_flow` 救済がクリアして同じ観測で scroll するため、残るのは `announce` / `selection_planned` 系（または例外的な `read_kit`）の進行不能 flow である。

この flow は `card_list` 画面でしか announce 待ちを進められない。敵召喚メニューへ置き換わると進行不能のまま真値だけが残り、reveal ゲート（`battle_menus.py` L2857）と reopen ゲート（同 L3212-L3213）を同時に塞ぐ。本件は #5538 と同じ機構家族だが、#5538 が `scrolls: 不明`（開札前）なのに対し、本件は `scrolls: 0` で**召喚戦の開札後に freeze した亜型**と分けられる。いずれにせよ、残りの携行札を開かず `こうげき` を確定した実事象であり、owner ルール（敵召喚獣戦で卵が使えず切り札を持つ場合は切り札を使う、2枚目以降も開く）に反する実欠陥である。

## 1. fingerprint / occurrence の再計算

現行 `month_audit.py` の card_miss 専用式で再計算し、どちらも一致した。

- `_fingerprint`（`month_audit.py` L3324-L3326）: `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]`。`_track_card_miss`（同 L3528-L3529）は `[card, str(row.get('castle') or '')]` を渡す。城不明のため raw は **`8|card_miss|ノリウツール+`**。Python で再計算した sha256 先頭16は **`ea91e6a4dcc16324`** で issue と一致。
- occurrence（`month_audit.py` L3477-L3480）: `sha256(f"{runtime_id}|{chapter}|{int(float(at))}|{card}")[:20]`。時刻 2026-11-25 14:26:07Z は epoch `1795616767`。raw は **`g1342-1fe0734c|8|1795616767|ノリウツール`**。sha256 先頭20は **`29a318059b2fd1a51a46`** で issue と一致。

## 2. emit 条件と証拠値の意味

- emit サイトは `egg_battle_step`（`battle_menus.py` L3342-L3375）の `battle_boss_card_missed` だけ。`move == 'here' and label == 'こうげき'`（実測カーソルが `こうげき` 行）、`missed` 非空、`cards_left` 非空、`cards_used` 空、1戦闘1件未記録が条件。
- `_carried_card_left` は `planned_cards` または確定携行キットから `{cards_selected, cards_used, cards_unclassified, cards_missing}` を差し引く。ノリウツールは boss-only 札ではないので通常の使用対象。
- `cursorY=176` は `screen.menu_cursor` の実測で、`こうげき` 行に一致したことと整合する。
- `empty_egg=false` / `summon_egg_unavailable=false` は卵の使用可否の肯定証拠ではない。卵状態のテンプレート文「卵なし・奥の手不可」は固定文であり、本件の measured evidence は false 値そのものだけである。
- `enemy_summon_observed=true` は emit の metric 合成で、少なくともコード上は召喚文脈の証拠として記録されている。ラビオリは `GENERAL_EGGS` で卵なし（`hanjuku_egg_reference.py`）、チコリは卵持ちなので、敵チコリ側の召喚観測とも矛盾しない。

## 3. `scrolls: 0` が示す post-open 型

1. 監査投影は `_bounded_int(metric.get('scrolls'), 0, 10)`（`month_audit.py` L302-L303, L3498）で、int だけを保持する。未設定の `None` は issue では `不明` と描画される。#5538 がその型。
2. `summon_card_scroll` は reveal down のたび 1,2,3 と増え（`battle_menus.py` L3194-L3203）、召喚戦で実際に `きりふだ` を開いたときだけ 0 へリセットされる（同 L3225）。
3. 本件は `0` なので、未スクロールでも scroll 枯渇でもなく、**一度開札して先頭ページへ戻った後の状態**である。
4. それでも `cards_left` に2札が残るため、少なくとも残り札については再度 reveal が必要だった。owner コメントでも、箱は一覧を閉じると先頭ページへ戻り、2枚目以降は個別の有界 reveal が要ると明示されている（同 L3222-L3225）。
5. emit 画面の `こうげきもうこうげき` には `きりふだ` が無い。`_summon_card_row_hidden` の最終条件 `screen.kind == 'egg_battle_menu' and ('こうげき' in text or 'もうこうげき' in text)`（同 L2864）は成立する。reveal しなかった理由は、同関数先頭の `battle.get('card_flow')` 以外に残らない。
6. 未選択の menu/down/list flow は attack 分岐先頭で `summon_card_resume` へ退避され `card_flow` がクリアされる（同 L3151-L3166）。クリアされれば同じ観測で reveal scroll へ進み emit に届かない。よって emit 時点の阻害 flow は `announce` / `selection_planned` 系か、救済対象外の `read_kit` に限られる。
7. `cards_left` の2札は `cards_selected` / `cards_unclassified` / `cards_missing` に入っていない。したがって freeze していた flow の札は、少なくともこの2札の残り利用を塞いだ別の開札済みフローか、未選択でも解放されない特殊フローだったと分かれる。札名の特定には VM 側 decisions が要るが、reveal 抑止の構造は issue の値だけで確定できる。

## 4. 既存 issue / PR との比較

| 対象 | 証拠値・型 | 本件との関係 |
| --- | --- | --- |
| #5600 | `scrolls=3`。揭示 down が尽きた scroll 枯渇型。PR #5615 `_summon_card_rearm` で再提示を実装（open） | **別型**。本件は `scrolls=0` で開札後。re-arm は `scrolls>=3` の経路だけを対象にし、freeze した `card_flow` を解放しない |
| #5614 | 同ラン g1342、`scrolls=3`、表示文言に `たまごをつかう` | 同ランの sibling だが #5600 と同じ scroll 枯渇型。本件の post-open frozen flow とはカウンタで区別できる |
| #5538 | `scrolls=不明`。開札前に `card_flow` が凍結した announce freeze 型 | 同じ `card_flow` 抑止家族。ただし本件は `scrolls=0` で召喚戦の開札後亜型。PR #5607 は open・conflict 中で main 未適用 |
| PR #5607（#5538/#5553 修正） | announce flow を3観測後に unclassified 解放 | root の無限凍結には対応するが、PR 本文の残件どおり猶予中の `こうげき` 確定は依然起票しうる。現行実装案も猶予中に attack へ進み、本件と同じ emit を防がない。よって完全なカバーではない |
| #5528/#5541 | `sortie_without_cards`。boss sortie がカードを携行せず出撃する上游欠陥 | 非関連。本件は携行候補が戦闘内で残り、召喚メニューで開札済みの形跡がある。出撃時の空 kit 問題ではない |

## 5. 現行 main での生存確認

- 観測版 `hanjuku-chart-v1515-monster-menu-total-cap` は commit `02e9c4fb6` の marker で、現行 main `818700b19` でも `_summon_card_row_hidden`・`interrupted_flow`・emit の該当構造は同じである。
- `fix/5538-card-flow-announce-rescue`（commit `8ef923c11`、PR #5607）は `origin/main` に ancestor として入っていない。`git merge-base --is-ancestor` は非0で、PR #5607 自体も open・merge conflict 状態である。
- PR #5615 `fix/5600-scroll-rearm` も open で、対象は `scrolls>=3` の再提示のみ。`_summon_card_rearm` は `card_flow` が真の間は reveal ゲートへ到達しないため、本件を救わない。
- したがって本件の機構は現行 main に生存する。必要な修正は、(1) 召喚メニューで進行不能な announce/selection flow を有界に解放することに加え、(2) 解放前の有界観測中は `こうげき` を確定せず入力を保留すること、である。(1) だけでは PR #5607 が残件として認める通り、解放前の攻撃で同型 issue が再発する。

## 判定詳細

- **(a) 実欠陥**: 実測 `こうげき` 確定、残り携行札、開札後状態、reveal 抑止の論理が全て揃う。owner ルールに反する。
- **(b) ではない**: PR #5615 は scroll 枯渇型専用。PR #5607 は main 未適用で、適用されても猶予中攻撃を残す。現行 main に未修正のまま生存する。
- **(c) ではない**: 少なくとも残り札を開かず攻撃した事実は emit 条件と scroll 値から確定できる。監査の静的テンプレート行は証拠扱いしないが、結論はそれに依存しない。
- **(d) 要素あり（結論は変わらない）**: freeze flow の札名、どの開札経路で作られたか、最初に開いた札が実際に発射したかは issue 単体では確定できない。確定には run `g1342-1fe0734c` の `hanjuku_decisions.jsonl` で `battle_summon_card_open` / `battle_card_selected` / `battle_boss_card_missed` の連続時系列が必要。

## 検証メモ

- 実行した検証: issue 本文照合、sha256 の再計算、観測版 `02e9c4fb6` と現行 `818700b19` の静的照合、`_summon_card_row_hidden` / `interrupted_flow` / emit / `_bounded_int` / card list announce 待ちの経路追跡、PR #5607・#5615 の diff と状態確認、VERIFY-5528/5541/5538 の比較。
- ドキュメントのみの追加のためテストは実行しない。
- read-only 検証であり、本番操作・コード修正・VM への接続は行っていない。
