# VERIFY-5476 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: ダイチスイム (ch2 戦闘)

- 対象: [docich#5476](https://github.com/azumag/docich/issues/5476) / 監査 `card_miss`（confidence `high`）
- fingerprint `04398b6148dde53b` / occurrence `9b505794f975cea5d158`
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1486-locked-boss-activation`
- ch2 / 月 2-11 / 未使用の携行札: ダイチスイム / 時刻 18:25:37Z
- 戦闘: 不明 vs 不明 / 城: 不明 / 画面: None / 理由「敵召喚獣戦で携行切り札を開けず、こうげきへ進んだ」
- 検証ベース: `origin/main` = `49c280c8671dcfcee9280ed934573e8461c93ec7`（`BOT_VERSION` `hanjuku-chart-v1495-recall-scene-covered-dispatch-audit`）
- 検証範囲: read-only。本ファイルの追加以外のコード変更なし。本番 SSH・VM 操作・ゲーム入力なし。ラン成果物 `hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/` は VM 側のため直接参照せず、公開 issue ページ・git オブジェクト・現行/観測版コードを照合した。
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）。
- 環境制約: 本環境では `gh`・`python3`・`perl`・`openssl` 等のハッシュ計算・外部 CLI が実行拒否される（VERIFY-5431/5444 と同制約）。issue 本文は公開 GitHub issue ページの webfetch で取得済み。

## 結論: **(a) 実欠陥 — #5466 / #5469 / #5381 と同じ卵行 OCR 欠落型。観測版 v1486 は PR #5472 を含まず、現行 main には修正済み**

本件は ch2・ダイチスイムの初出で、#5259 家族の系列発火としては新しい fingerprint だが、機構は同一である。v1486 の旧揭示ゲートは「卵行が白 OCR に読める」または「卵残数0を確定測定済み」のどちらかを要求し、灰色・使用不可の卵行が OCR から落ちかつ卵0の確定記録がない画面では、携行札を一覧へ出す有界スクロールを一度も行わず `こうげき` を確定しうる。issue の emit 前提（未使用の携行札・`cards_left` 非空・`cards_used` 空・`こうげき` 実確定・未起票）は静的に確認でき、実事象の起票であり誤検知 (c) ではない。

時系列も整合する。v1486 の merge commit `8c346ca01` は 18:20:42Z、本 occurrence は 18:25:37Z、修正 PR #5472 の merge `0a99182c0` は 18:50:51Z（いずれも 2026-11-24/25）。**発火は #5472 merge 前**であり、v1486 での発火として完全に説明できる。

## 1. fingerprint / occurrence の式照合（再計算は環境制約で未実施）

観測版 `8c346ca01` の `month_audit.py` で式を確認した。

- `_fingerprint`（L3105-3108）: `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]`。`_track_card_miss`（L3290-3291）の呼出は `[card, str(row.get('castle') or '')]`。v1486 の emitter は `castle` を top-level へ渡さないため第3要素は空文字で、期待 raw は **`2|card_miss|ダイチスイム+`** → 期待値 `04398b6148dde53b`。
- `incident_id`（L3258-3261）: `sha256(f"{runtime_id}|{chapter}|{int(float(at))}|{card}")[:20]`。時刻 18:25:37Z は epoch `1795544737`（VERIFY-5469 の anchor `1795543426` = 18:03:46Z + 1311秒）。期待 raw は **`g1190-ch1v9|2|1795544737|ダイチスイム`** → 期待値 `9b505794f975cea5d158`。
- 本環境はハッシュ計算手段を拒否するため再計算は未実施。形式（16/20 hex・章・札・ラン・時刻依存）は一致し、同族 issue の実績式と同型。手段のある環境での照合を残件とする。

なお現行 main の `_track_card_miss`（`month_audit.py` L3255-3316）は同じ式だが、#5472 以降の emit は `castle` を渡すため、今後の同章同札の発火は `2|card_miss|ダイチスイム+<城>` の別 fingerprint になる。本件の fingerprint は v1486 型（城空）で、発火当時のコード系譜と一致する。

## 2. 観測版 v1486 に修正がないことの照合

- issue の marker `hanjuku-chart-v1486-locked-boss-activation` は merge commit `8c346ca01`（PR #5468、18:20:42Z）の `BOT_VERSION` と一致する。marker 元 commit `8bcc1fd09` は v1485 marker だが、`8bcc1fd09..8c346ca01` の `battle_menus.py` 差分はゼロ（card_miss 経路は無差分）。
- main 履歴の順序（git log 実測）: `8c346ca01`（v1486、#5468）→ `ad9c5c7df`（v1487、#5467）→ `0a99182c0`（#5472、実効 marker v1488-summon-card-without-egg）→ `3402a6944`（v1489、#5481）→ `cb1e21dc8`（v1492、#5499）→ … → `49c280c86`（v1495、現行 main）。
- `ad9c5c7df`（v1487、#5467）は ch4 四季王の出撃停止対策で `battle_menus.py` を変更していない（`git show --stat` で 0 件）。したがって v1486・v1487 は同じ旧ゲート。
- v1486 の揭示ゲート（`8c346ca01:src/docich/hanjuku/flows/battle_menus.py` L3116-3118）:

```python
if (cards_left and 'きりふだ' not in (screen.text or '')
        and ('たまごをつかう' in (screen.text or '') or empty_egg)
        and not battle.get('card_flow')):
```

- 一方画面分類は、卵行が灰色で白 OCR から落ちても `こうげき` / `もうこうげき` と menu row から `egg_battle_menu` を返す（`os_layer/screen.py` L1545-1557、コメントに「たまごをつかう drops out of OCR」と明記）。卵残数0の確定記録がなければ `empty_egg` も立たず（`_own_egg_empty` は fail-closed）、同じコマンド箱にいながら scroll が一度も走らず `こうげき` へ進む — VERIFY-5381/5466/5469 と同じ穴。

## 3. emit と投影欠落

- v1486 で `battle_boss_card_missed` の emit サイトは `battle_menus.py` L3269 の1箇所だけ（grep 実測）。起票条件は `move == 'here' and label == 'こうげき'` かつ `missed and cards_left and not cards_used and not boss_card_missed_noted`（L3258-3268）。理由文は issue 本文と一致。
- v1486 の emit は `_battle_labels`（chart_step 等のみ）＋ `card` ＋ `observed_metric`（screen/text/scrolls/cursor）を渡し、`ally` / `enemy` / `castle` / `side` / `screen_kind` を top-level に渡さない。監査側 `_track_card_miss` はこれらを row から読むため（L3266-3269）、issue には `不明` / `None` しか残らない（`_render_card_miss` L3700-3720）。**`戦闘: 不明 / 城: 不明 / 画面: None` は投影欠落の結果**で、実際の戦闘が不明だった証拠ではない。
- 「卵なし・奥の手不可のときに」は固定テンプレート文（L3719）で実測条件ではない。

## 4. 修正 PR との関係

### PR #5472（実効 v1488）— 本件経路をカバーする

- merge commit `0a99182c0`（18:50:51Z）は origin/main の祖先（`git log origin/main` で実測）。本 occurrence（18:25:37Z）より後の merge のため、本件は「修正前の版での発火」であり、#5472 適用後の再発証拠ではない。
- 修正内容（`0a99182c0` の diff 実測）: 旧ゲートを `_summon_card_row_hidden`（現行 `battle_menus.py` L2811-2840）へ切り出し、
  - 従来条件（`たまごをつかう` が読める / `empty_egg`）に加えて、
  - **`screen.kind == 'egg_battle_menu'` かつ `こうげき` / `もうこうげき` が読める実測コマンド箱なら卵行欠落を no-egg 証拠として有界スクロール**する分岐と、
  - 防衛側 `summon_egg_unavailable` 分岐を追加。
- 章条件・ボス城条件・攻撃側条件はない。開札は従来どおり実表示の `きりふだ` 行のみ確定する fail-closed で、scroll は `SUMMON_CARD_SCROLL_LIMIT = 3` に有界。**したがって ch2・ダイチスイム・城不明の本件経路をそのままカバーする**。
- あわせて `battle_boss_card_missed` へ `ally` / `enemy` / `castle` / `side` / `screen_kind` と `observed_metric` 拡張（`empty_egg` / `summon_egg_unavailable` / `enemy_summon_observed`）を追加し、本件型の「戦闘: 不明 / 城: 不明 / 画面: None」の投影欠落も解消済み（現行 `battle_menus.py` L3314-3345、`_record` は L3326）。

### その他の列挙 PR — いずれも非該当

- PR #5467（v1487、`ad9c5c7df`）: ch4 四季王の出撃停止対策。`battle_menus.py` 無変更、card_miss 経路に無関係。
- PR #5453（v1480、`034387473`）: ch4 四季王出撃の放置・代役移動。無関係。
- PR #5442（v1477、`911ffbb55`）/ PR #5450（v1481、`4f7d93e59`）: 月次帰還（キッシュ免除・hint/goal フォールバック）。無関係。
- PR #5499（v1492、`cb1e21dc8`）: `_card_usable_in_battle`（`battle_menus.py` L1252-1254）で `BOSS_ONLY_CARDS`（`hanjuku_reference.py` L177 = `('バルムンク',)`）を通常戦の miss 判定から除外。**ダイチスイムはボス専用札ではないため、本フィルタで本件の発火対象性は消えない**。
- PR #5347（#5259 家族の旧修正車両、CONFLICTING・未マージ、head `a9fd49d46` はローカルに残存）: `own_attack_menu` は `chart.is_boss_castle`＋`side == 'attack'`＋既知召喚獣ちょうど1体＋自将軍パネルの限定フォールバックだった。ch2 の `is_boss_castle` は `'ボス'` のみ（`hanjuku_chart.py` L455-462、L1402）であり、ダイチスイムが計画される ch2 注文は存在しない（後述）ため通常城経路が有力 — **#5347 では救えない可能性が高かったが、実際にマージされた #5472 は限定を外した実装なので本件をカバーする**。#5347 の扱いは #5472 で superseded（VERIFY-5469 と同じ整理）。

## 5. ダイチスイムの携行経路（(d) 要素、結論は変わらない）

- `hanjuku_reference.py` L33-34: ダイチスイムは id 1、`egg_drop` 1、`price` None（購入不能）。`STRONG_CARDS`（L160）には非含有、`BOSS_ONLY_CARDS` にも非含有。
- **ch2 のチャート注文でダイチスイムを携行するものはない**（`CHAPTER_2_ORDERS` L599- 実測。2-B1 ボス注文はクースカン/イッテツーン/ノリウツール）。ch2 月次購入もグリンボー/ミックミーのみ（L1150-1156）。
- `_auto_carry_safe_card`（`strategy/kit.py` L117-146）は `egg_drop == 0` のみ対象で、egg_drop 1 のダイチスイムは自動携行されない。
- 有力な経路は **`dropper_card_kit`（#1553 の卵落札追加）**: 守備将軍が実測判明している非ボス出撃へ、`卵落 > (最大HP合計 mod 16)` を満たす在庫札を1枠加える（`deploy.py` L494-546）。ダイチスイムは egg_drop 1・ID 1 で、閾値1の組合せ（max_hp_sum % 16 == 0）で選ばれうる。`_sortie_defender` はボス注文を除外するため、この経路なら通常城・攻撃側の敵召喚獣戦となる — 旧 #5347 のボス限定では救えない型であり、#5472 の無限定化が効いた形。
- いずれにせよ emit の前提は `planned_cards` または `_confirmed_carried_kit`（出撃確定の帳簿）由来の `cards_left` なので、ダイチスイムが実際に携行されて戦闘へ入ったことは起票自体が示す。携行経路の特定にはラン成果物（`dropper_card_kit` / `cards_autocarried` / departure binding レコード）が要る。

## 6. 同型家族との照合

| 記録 | 章/札 | 判定 | 本件との関係 |
| --- | --- | --- | --- |
| #5259 家族（#5259/#5328/#5331/#5352/#5362/#5373/#5398/#5433） | ch3-9 各種 | (a) | 同一機構（卵行 OCR 欠落→旧ゲート不成立） |
| VERIFY-5381（#5381） | ch4 クースカン | (a) | 同機構・同投影欠落 |
| VERIFY-5466（#5466） | ch4 クースカン v1484 | (a) | 同機構の修正前発火 |
| VERIFY-5469（#5469） | ch1 クースカン v1485 | (a) | 同ラン g1190-ch1v9・同 #5472 カバー構造 |
| #5474 | ch1 バグストーム v1486 18:13:17Z | — | 同ラン同時間帯の同型。owner が not-planned で #5476 へ集約 |
| 本件 #5476 | ch2 ダイチスイム v1486 18:25:37Z | (a) | 系列初の ch2・ダイチスイム（新 fingerprint） |
| VERIFY-5444（#5444） | ch12 sortie_failed | (d) | 別機構（出撃観測）、参照のみ |

fingerprint が新しいのは章・札の組合せが初めてだからで、別機構の証拠ではない。emit サイトは1箇所、理由文は同一、ゲートの穴も同一。

## 判定詳細

- **(a) 実欠陥**: v1486 に修正はなく、emit 条件から実際に「携行ダイチスイムを開かずこうげき確定」が起きた。修正 PR #5472 は main にマージ済み（実効 v1488）。
- **(b) ではない**: 観測版は修正前。「卵が使えないなら切り札を使う」owner 意図（`battle_menus.py` L2815-2818 コメント、2026-11-25）に反する。
- **(c) ではない**: 起票条件は確定携行・未使用・攻撃行確定を静的に要求する。投影欠落は証拠品質の問題で、起票の実事象性を否定しない。fingerprint/occurrence のハッシュ再計算のみ環境制約で未実施（期待 raw を §1 に記載）。
- **(d) 要素あり**: 実際の城・敵・side、scroll-0 型か scroll 枯渇型か、ダイチスイムの携行経路（dropper か他か）は issue 単体では確定不能。確定には `g1190-ch1v9` の `hanjuku_decisions.jsonl`（当該戦闘の `battle_boss_card_missed`/`battle_summon_card_scroll`/`dropper_card_kit`/departure binding 行）が要る。ただし結論 (a) と #5472 のカバー判定を変えない。

## 検証メモ

- 実行した検証: issue 本文の公開ページ取得、`git log`/`git show`/`git diff` による観測版 `8c346ca01` と現行 main `49c280c86`・PR #5472 `0a99182c0` の静的照合、ch2 チャート注文・ダイチスイム札定義・携行経路コードの確認、#5474 の集約状況確認。
- fingerprint / occurrence の sha256 再計算は本環境制約（`python3`/`perl`/`openssl` 拒否）で未実施。期待 raw 文字列を §1 に記載。
- ドキュメントのみの追加のためテストは実行しない。
- 本ファイルは worktree ルートに**未コミット**で置いてある。`git checkout -b` / `git switch -c` 等の書き込み系 git 操作を環境が拒否したため、verify ブランチへのコミットは別環境で `git add VERIFY-5476.md && git commit` が必要（VERIFY-5381 と同じ制約パターン）。push・PR 作成・issue コメントは依頼どおり実施しない（検品ゲート側で実施）。
- read-only 検証であり、本番操作・コード修正・VM への接続は行っていない。
