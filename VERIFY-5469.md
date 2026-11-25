# VERIFY-5469 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: クースカン (ch1 戦闘)

- 対象: [docich#5469](https://github.com/azumag/docich/issues/5469) / 監査 `card_miss`（confidence `high`）
- fingerprint `4921ad6488868192` / occurrence `993abac7404052ed77b5` / 再発 occurrence `9f6f9f8eebeaab9b9435`
- ラン: `g1190-ch1v9` / generation `1190` / issue 記載の版 `hanjuku-chart-v1485-locked-boss-activation`
- ch1 / 月 2-8 / 未使用の携行札: クースカン / 初回 18:03:46Z / 再発 18:05:38Z
- 検証ベース: `origin/main` = `cb1e21dc86bf5ea5c00dfc19e10587c4cbb07817`（`BOT_VERSION` `hanjuku-chart-v1492-boss-only-card-miss-eligibility`）
- 検証範囲: read-only。本ファイルの追加以外のコード変更なし。本番 SSH・VM 操作・ゲーム入力なし。ラン成果物 `hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/` は VM 側のため直接参照せず、issue #5469、同時刻の観測 incident を埋め込んだ [docich#5470](https://github.com/azumag/docich/issues/5470)、git オブジェクト、現行/観測版コードを照合した。
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）。

## 結論: **(a) 実欠陥 — #5466 / #5381 と同じ卵行 OCR 欠落型。PR #5472 は本件 ch1 2-8 の観測経路をカバーする**

本件は設計どおりではない。少なくとも初回 occurrence について、通常城カストーラへの攻撃側召喚獣戦で、携行したクースカンを開かず `こうげき` を確定した実事象が、同時刻の #5470 埋め込み観測ログから確認できる。原因は #5466・#5381 と同じく、灰色・使用不可の卵行が白 OCR から落ち、かつ卵残数0の確定記録がない画面で、旧揭示ゲートが一度も下スクロールしなかったことである。

切り分けの要点は次のとおり。

- 本件の初回戦闘は **ボス戦ではなく通常城カストーラへの攻撃**だった。したがって旧 PR #5347 系の「ボス城・攻撃側」限定フォールバックでは救えない可能性があった。
- しかし実際に main へ入った **PR #5472（実効 marker v1488）の修正は章・城・side に依存しない**。`egg_battle_menu` で `こうげき` / `もうこうげき` が読める実測コマンド箱なら、卵行欠落を no-egg 証拠として有界スクロールする。したがって本件 ch1 2-8 の観測経路をカバーする。
- 初回・再発とも 2026-11-24 18:03:46Z / 18:05:38Z で、PR #5472 の merge commit `0a99182c0e6cb15f0bf2ec3278ba6ce45c86effc`（2026-11-24 18:50:51Z）より前である。本 issue だけでは修正後の再発は示されていない。
- 現行 main には PR #5472 が祖先として含まれる（`git merge-base --is-ancestor 0a99182c0 origin/main` = 真）。現行 v1492 の PR #5499 はボス専用札の監査対象を絞る変更だが、クースカンは `BOSS_ONLY_CARDS` ではないため本件のカバー判定を変えない。

## 1. fingerprint / occurrence の再計算

現行 `month_audit.py` の式で再計算し、いずれも一致した。

- `_fingerprint`（`month_audit.py` L3112-3114）: `sha256("1|card_miss|クースカン+")[:16]` = `4921ad6488868192`
- `_track_card_miss`（同 L3265-3268）: `sha256("g1190-ch1v9|1|1795543426|クースカン")[:20]` = `993abac7404052ed77b5`
- 再発: `sha256("g1190-ch1v9|1|1795543538|クースカン")[:20]` = `9f6f9f8eebeaab9b9435`

`1795543426` は 18:03:46Z、`1795543538` は 18:05:38Z である。fingerprint 第3要素が空なのは、観測版の emitter が `castle` を top-level に渡していなかったためである。PR #5472 以降は `ally` / `enemy` / `castle` / `side` / `screen_kind` を emit する（現行 `battle_menus.py` L3326-3344）が、本件はその前の版なので issue 本文では「戦闘: 不明 / 城: 不明 / 画面: None」になっている。

## 2. 観測ログとの照合 — 初回は通常城カストーラ攻撃中の scroll-0 型

#5470（`battle_loss_with_resources`、ch1 2-8、ソーピニヨン）の埋め込み JSON には、#5469 初回 occurrence と同じ epoch 秒を持つ decision 列が残っている。`_track_card_miss` は `int(float(at))` を使うため、`at = 1795543426.725815` は occurrence `993abac7404052ed77b5` と一意に対応する。

同じタイムスタンプの行は次の順である。

1. `situation_held` — `screen_kind: egg_battle_menu` / 理由「たまごをつかうが使えない表示のため卵を諦めてこうげきで応戦する」
2. `battle_egg_general_attack` — step `A:b5c6ea46:M8c8457` / `screen_kind: egg_battle_menu`
3. `battle_boss_card_missed` — 同 step / 同画面 / 理由「敵召喚獣戦で携行切り札を開けず、こうげきへ進んだ」

#5470 の戦闘結果は次を示す。

- 将軍: ソーピニヨン / 敵: キャンディー / 城: カストーラ / side: attack
- 出撃 `A:b5c6ea46:M8c8457`: ジョンリギ発、通常城カストーラへの attack
- 結果: loss、HP ally 0 / enemy 26

ch1 のボス城は `けっかい`（`hanjuku_chart.py` L455-458）であり、カストーラは通常城である。ch1 chart のボス注文 `1-B1` はクースカンとノリウツールを携行するが（同 L593-595）、この観測戦闘は `1-B1` ではなく機械計画の通常城攻撃だった。したがって #5466（ch4）や #5381（ch4）と章・fingerprint は違うが、「ボス以外でも同じ summon menu 機構で発火する」点が本件固有の重要性である。

#5470 の月内 decision counts には `battle_summon_card_scroll` が存在しない。一方で `cards_autocarried` は 6 件ある。このため初回 occurrence は「scroll 上限3回を使い切った型」ではなく、旧ゲートが一度も発火しなかった scroll-0 型と判定できる。クースカンの携行経路そのもの（自動携行か strong-card 補助か）は projection だけでは確定できないが、通常城攻撃で計画外携行がありうることは `_auto_carry_safe_card`（`strategy/kit.py` L117-146）とも整合する。

再発 occurrence（18:05:38Z）についても hash は一致する。#5470 には 18:06:10Z に「ゼウス loss / 資源 egg」の再発コメントがあり、初回観測時点でゼウスは通常城ゴーメンへの行軍中だったため、こちらも通常城攻撃中の同型である可能性は高い。ただし compact comment には対象城・時刻・decision 列がなく、この対応は確定扱いしない。結論は初回 occurrence だけで成立する。

## 3. 観測版の欠陥機構

issue 記載の marker `hanjuku-chart-v1485-locked-boss-activation` を持つ commit は `8bcc1fd0931c5a7a6a317fc06ac3762f6de044eb` で、merge 版 `8c346ca0112c8c87d95e04e630264672d7a2e708` では番号衝突のため v1486 へ更新された。両者の `battle_menus.py` は無差分であり、card_miss 経路は同じである。

観測版の揭示ゲートは次の条件だった（`8c346ca01:src/docich/hanjuku/flows/battle_menus.py` L3116-3118）。

```python
if (cards_left and 'きりふだ' not in (screen.text or '')
        and ('たまごをつかう' in (screen.text or '') or empty_egg)
        and not battle.get('card_flow')):
```

一方、画面分類は卵行が OCR から落ちても、`こうげき` / `もうこうげき` と menu row から `egg_battle_menu` を返す（現行 `os_layer/screen.py` L1551-1557、コメントにも `たまごをつかう drops out of OCR` とある）。つまり #5470 に残った「`egg_battle_menu` だが卵行が使えない」という画面は、旧ゲートの `たまごをつかう in screen.text` を満たさない。卵残数0の確定記録もなければ `empty_egg` も立たず、下スクロールせずに `こうげき` へ進む。

観測版の emit は `move == 'here' and label == 'こうげき'` のときだけで、未使用の携行札・`cards_left` 非空・`cards_used` 空・未起票をすべて要求する（観測版 `battle_menus.py` L3256-3275）。したがって本起票は「カード一覧を開けず攻撃行を実際に確定した」実事象を示し、誤検知 (c) ではない。

厳密には issue の `evidence.bot_version` は `month_audit.py` L3289-3292 が示すとおり seal 時の `after` 状態から採られ、個別 decision row に埋め込まれた生成版ではない。したがって marker だけから発火瞬間の hot-load 版を完全には証明できない。ただし v1484・v1485・v1486 のいずれも同じ旧ゲートであり、両 occurrence は PR #5472 の merge 前なので、この不確かさは結論に影響しない。

## 4. PR #5472 が本件経路をカバーする根拠

PR #5472 の修正は現行 `_summon_card_row_hidden`（`battle_menus.py` L2811-2840）として残っている。新しい分岐は次のとおり。

- `cards_left` があり、`きりふだ` がまだ見えず、card_flow 中でないこと
- `たまごをつかう` が読める、または `empty_egg`、または防衛側で卵行不能が確定していれば従来どおり scroll 対象
- さらに **`screen.kind == 'egg_battle_menu'` かつ `こうげき` / `もうこうげき` が読めるなら、卵行欠落を no-egg 証拠として scroll 対象**

ここには章条件・ボス城条件・攻撃側条件はない。`orchestrator.py` L2300-2301 の `egg_battle_step` 振り分けにも章条件はない。開札は引き続き実表示の `きりふだ` 行だけを確定し、scroll は `SUMMON_CARD_SCROLL_LIMIT = 3` で有界である（現行 `battle_menus.py` L3168-3207）。したがって fail-closed を保ったまま、本件の通常城・攻撃側・ch1 という組合せをカバーする。

現行 main のまま、#5470 と同じ条件を read-only の Python 実行で再現したところ、章1・通常城カストーラ・`planned_cards=['クースカン']`・`screen.kind='egg_battle_menu'`・text `こうげきもうこうげき` では `down` が返り、`battle_summon_card_scroll` と `summon_card_scroll=1` が記録された。`tests/test_hanjuku_boss_egg_row_log.py` も 12 件すべて passed だった。これはコード変更ではなく現行コードの応答確認である。

なお現行 v1492（PR #5499）は `_card_usable_in_battle` を追加し、ボス専用札を通常戦の miss 判定から外す（`battle_menus.py` L1252-1272）。クースカンはボス専用札ではないため、この追加フィルタで本件の発火対象性が消えることはない。

## 5. #5466 / #5381 との異同

- #5381・#5466 は ch4 / クースカンで、fingerprint は `sha256("4|card_miss|クースカン+")[:16]` = `3922dfede2b0311f`。本件は ch1 なので `4921ad6488868192` になる。fingerprint の差は章の差であり、別機構の証拠ではない。
- #5466 の検証記録 VERIFY-5466.md（PR #5496、merged）が結論付けたとおり、観測版には PR #5472 の修正がなく、実効修正は v1488 で main に入った。本件も同じ修正前系列である。
- 本件固有の差分は、少なくとも初回が **ch1 の通常城カストーラへの機械計画 attack** だったことである。この点では旧 #5347 のボス限定フォールバックでは不足しえたが、実際にマージされた PR #5472 はボス限定を外した実装なのでカバーする。
- 本 issue の2件はともに PR #5472 merge 前の発火であり、現時点で「PR #5472 適用後に ch1 2-8 の別経路が再発した」証拠はない。

## 判定詳細

- **(a) 実欠陥**: 携行クースカンを残したまま `こうげき` を確定した。通常城の敵召喚獣戦でも owner 意図「卵を持っておらず切り札を持っている場合は切り札を使う」に反する。
- **(b) ではない**: 観測版は切り札行を揭示する前に攻撃へ進む旧ゲートだった。通常城だったことは設計上の温存理由にならない。
- **(c) ではない**: fingerprint / occurrence が再計算一致し、#5470 の同時刻観測列が `battle_egg_general_attack` → `battle_boss_card_missed` を直接示す。
- **(d) 要素あり**: 初回のクースカン携行経路（自動携行か strong-card 補助か）、再発 occurrence の厳密な対象城、観測瞬間の hot-load 版は issue/埋め込み JSON だけでは確定できない。ただし結論と PR #5472 のカバー判定を変えない。

## 検証メモ

- 実行した検証: fingerprint / occurrence の再計算、`git merge-base --is-ancestor`、観測版 `8bcc1fd09` / `8c346ca01` と PR #5472・現行 main の静的照合、#5470 埋め込み JSON の decision 列照合、現行コードでの章1・通常城条件の応答確認。
- 実行したテスト: `PYTHONPATH=src:$HOME/py311shim python3 -m pytest -q tests/test_hanjuku_boss_egg_row_log.py` → 12 passed。ドキュメントのみの追加であり、コード変更はない。
- read-only 検証であり、本番操作・コード修正・VM への接続は行っていない。
